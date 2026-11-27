"""Scenario Generator tests — offline, with a faked Messages client (M1-T1).

No API key is used: a stub client replays canned tool_use responses, so the
prompt assembly, schema enforcement and retry path are all covered in CI.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agents.a03_scenario_generator import agent as sg
from ecvt.llm import StructuredCallError, Usage
from ecvt.schema import ALL_CATEGORIES, ScenarioCategory, read_scenarios_jsonl
from tests.test_schema import make_scenario

WALLET_SPEC = "examples/wallet-api/openapi.json"
TOOLS_JSON = Path("agents/a03_scenario_generator/tools.json")


# --- fake client -----------------------------------------------------------


@dataclass
class FakeUsage:
    input_tokens: int = 1200
    output_tokens: int = 800
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeToolUse:
    name: str
    input: dict[str, Any]
    type: str = "tool_use"
    id: str = "toolu_fake"
    """A real tool_use block always carries an id, and the correction turn
    answers it with a tool_result — without one here the retry path under test
    would be a different shape from the live one."""


@dataclass
class FakeText:
    text: str
    type: str = "text"


@dataclass
class FakeResponse:
    content: list[Any]
    usage: FakeUsage


class FakeMessages:
    """Replays canned responses. Thread-safe, because the Evaluation agent
    judges several scenarios in parallel and would otherwise race here."""

    def __init__(self, payloads: list[Any]) -> None:
        self._payloads = list(payloads)
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def create(self, **kwargs: Any) -> FakeResponse:
        with self._lock:
            self.calls.append(kwargs)
            payload = self._payloads.pop(0)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(0.02)  # long enough for overlap to be observable
            content = [payload] if not isinstance(payload, list) else payload
            return FakeResponse(content=content, usage=FakeUsage())
        finally:
            with self._lock:
                self.in_flight -= 1


class FakeClient:
    def __init__(self, payloads: list[Any]) -> None:
        self.messages = FakeMessages(payloads)


def full_payload() -> dict[str, Any]:
    """Seven scenarios, one per category.

    `stress` races and `adversarial` replays. A set where every scenario is a
    single sequential request is rejected by the generator's validator, so a
    fixture built that way would exercise the retry path rather than the
    happy one.
    """
    modes: dict[str, dict[str, int]] = {
        "stress": {"concurrency": 4},
        "adversarial": {"repeat": 2},
    }
    return {
        "scenarios": [
            make_scenario(f"sc-{i:03d}", c.value, **modes.get(c.value, {}))
            for i, c in enumerate(ALL_CATEGORIES, start=1)
        ]
    }


def tool_use(payload: dict[str, Any]) -> FakeToolUse:
    return FakeToolUse(name=sg.TOOL_NAME, input=payload)


def correction_text(client: FakeClient, call: int = 1) -> str:
    """The validation feedback the retry carried.

    Normally the `tool_result` that answers the rejected tool_use; a plain user
    turn when there was no tool call to answer (see `_correction_messages`).
    """
    content = client.messages.calls[call]["messages"][-1]["content"]
    if isinstance(content, str):
        return content
    return "\n".join(str(block.get("content", "")) for block in content)


# --- tool schema -----------------------------------------------------------


def test_tool_schema_generated_from_pydantic():
    tool = sg.build_tool()
    assert tool["name"] == sg.TOOL_NAME
    assert "when" in tool["description"].lower() or "use this tool" in tool["description"].lower()

    schema = tool["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["scenarios"]

    # Three branches over `category` (ADR 0011): the numbers are asked for where
    # they are the probe and absent where they would only ever be 1.
    defs = schema["$defs"]
    assert {"StressScenarioDraft", "AdversarialScenarioDraft", "PlainScenarioDraft"} <= set(defs)
    assert {"HttpStep", "Expectation"} <= set(defs)

    item = schema["properties"]["scenarios"]["items"]
    # Strict schemas take `anyOf`, not `oneOf` + discriminator (ADR 0019); the
    # branches stay apart by their `category` const / enum.
    assert [b["$ref"].split("/")[-1] for b in item["anyOf"]] == [
        "StressScenarioDraft",
        "AdversarialScenarioDraft",
        "PlainScenarioDraft",
    ]
    routed = {defs["StressScenarioDraft"]["properties"]["category"]["const"],
              defs["AdversarialScenarioDraft"]["properties"]["category"]["const"],
              *defs["PlainScenarioDraft"]["properties"]["category"]["enum"]}  # fmt: skip
    # Every category routes somewhere, so no category can fall between branches.
    assert routed == {c.value for c in ALL_CATEGORIES}

    for name in ("StressScenarioDraft", "AdversarialScenarioDraft"):
        assert {"repeat", "concurrency"} <= set(defs[name]["required"]), name
    # Bounds a strict grammar cannot carry are stated in the description and
    # enforced by the Pydantic validator.
    assert "minimum: 2" in defs["StressScenarioDraft"]["properties"]["concurrency"]["description"]
    assert "minimum: 1" in defs["AdversarialScenarioDraft"]["properties"]["repeat"]["description"]

    plain = defs["PlainScenarioDraft"]["properties"]
    assert "repeat" not in plain and "concurrency" not in plain

    # Read order: the discriminator, then what the branch is about.
    for name in ("StressScenarioDraft", "AdversarialScenarioDraft"):
        assert list(defs[name]["properties"])[:3] == ["category", "repeat", "concurrency"], name
    assert next(iter(plain)) == "category"


def test_tool_is_strict_and_within_the_api_limits():
    """M4-T1 / ADR 0019: `strict: true` with a schema the API will compile."""
    from ecvt.llm import strict_counts

    tool = sg.build_tool()
    assert tool["strict"] is True
    counts = strict_counts(tool["input_schema"])
    assert counts["optional"] == 0  # API limit: 24 per request
    assert counts["union"] <= 16  # API limit: 16 per request

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    for obj in objects(tool["input_schema"]):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    text = json.dumps(tool)
    for keyword in ('"oneOf"', '"discriminator"', '"default"', '"minimum"', '"maximum"'):
        assert keyword not in text, keyword


def test_wire_step_has_no_free_form_object():
    """A strict grammar can only emit `{}` for an object with unlisted keys, so
    body / query / headers / save travel as JSON text and name/value lists."""
    step = sg.build_tool()["input_schema"]["$defs"]["HttpStep"]["properties"]
    assert step["body"]["type"] == "string"
    for key in ("query", "headers", "save"):
        assert step[key]["type"] == "array", key
        assert step[key]["items"]["$ref"].endswith("/WireField"), key


def test_few_shot_matches_the_strict_schema_and_round_trips():
    """The example in system_prompt.md is written in the wire form and is valid
    against the very schema the model is held to."""
    import jsonschema

    from ecvt.schema import ScenarioSetDraft, from_wire

    prompt = sg.load_system_prompt()
    example = json.loads(prompt[prompt.index("<output>") + 8 : prompt.index("</output>")])
    jsonschema.validate(example, sg.build_tool()["input_schema"])
    draft = ScenarioSetDraft.model_validate(from_wire(example))
    loan = draft.scenarios[0].action
    assert loan.body == {"member_id": "{borrower}", "title_id": "{item}", "days": 36500}
    assert draft.scenarios[0].setup[0].save == {"borrower": "id"}
    assert draft.scenarios[0].expectation.probes[0].body is None


def test_committed_tools_json_matches_generated_schema():
    """tools.json is generated; this guards against it drifting from ecvt.schema."""
    committed = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))
    assert committed == [sg.build_tool()], (
        "tools.json is stale — regenerate it from ScenarioSetDraft.model_json_schema()"
    )


# --- prompt assembly -------------------------------------------------------


def test_user_prompt_contains_surface_and_no_ground_truth():
    from ecvt.openapi_reader import read_openapi

    digest = read_openapi(WALLET_SPEC)
    prompt = sg.build_user_prompt(digest, "examples/wallet-api", per_category=2)

    assert "POST /transfers" in prompt
    assert "examples/wallet-api" in prompt
    for category in ALL_CATEGORIES:
        assert category.value in prompt

    # Ground truth must never reach the generator (ADR 0007).
    ground_truth = Path("examples/wallet-api/ground_truth.yaml").read_text(encoding="utf-8")
    for marker in ("GT-01", "GT-05", "overdraft", "idempotency key"):
        assert marker.lower() not in prompt.lower(), f"ground-truth leak: {marker}"
    assert "ground_truth" not in prompt
    assert len(ground_truth) > 0  # fixture sanity


def test_system_prompt_within_page_limit():
    """Raised from 160 on Ozan's call, for one reason: the example now shows all
    three branches of the category union.

    The wire schema is a union over `category`, and the generator omitted
    `repeat`/`concurrency` far more often when the example never used them —
    first-attempt generations ran at 21% with a single `edge` scenario against
    71% on the prompt that preceded the union work. Covering `stress` and
    `adversarial` as well costs lines no other content can give back.

    a03's own CLAUDE.md still says "max 2 pages". That line and this bound
    disagree now; which one moves is a conventions decision, not this test's.
    """
    lines = sg.load_system_prompt().splitlines()
    assert len(lines) <= 180, f"system_prompt.md is {len(lines)} lines"


# --- generation ------------------------------------------------------------


def test_generate_happy_path(tmp_path):
    client = FakeClient([tool_use(full_payload())])
    scenario_set, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )

    assert scenario_set.covers_all_categories()
    assert len(scenario_set.scenarios) == 7
    assert scenario_set.spec_title == "Wallet API (buggy demo)"
    assert scenario_set.model == "claude-haiku-5-5"
    assert result.attempts == 1
    assert result.corrections == []
    assert result.usage.input_tokens == 1200

    out = sg.write_scenarios_jsonl(tmp_path / "scenarios.jsonl", scenario_set)
    assert len(read_scenarios_jsonl(out)) == 7


def test_request_forces_the_tool_and_caches_system():
    client = FakeClient([tool_use(full_payload())])
    sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )

    call = client.messages.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": sg.TOOL_NAME}
    assert call["model"] == "claude-haiku-5-5"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    # One task message: no agent-harness context (ADR 0005).
    assert len(call["messages"]) == 1


def test_retry_on_missing_category():
    """A set that misses a category is rejected and the error is fed back."""
    partial = {"scenarios": [make_scenario("sc-001", "happy")]}
    client = FakeClient([tool_use(partial), tool_use(full_payload())])

    scenario_set, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )

    assert result.attempts == 2
    assert len(result.corrections) == 1
    assert "missing" in result.corrections[0]
    assert scenario_set.covers_all_categories()

    correction = correction_text(client)
    assert "rejected by schema validation" in correction
    assert "adversarial" in correction  # the actual missing category is named
    assert result.usage.calls == 2

    # The rejected attempt is still in the transcript, so "keep everything else"
    # is something the model can act on.
    retry = client.messages.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert retry[-1]["content"][0]["tool_use_id"] == "toolu_fake"
    assert retry[-1]["content"][0]["is_error"] is True


def test_a_stress_scenario_cannot_sit_at_concurrency_one():
    """The schema refuses it now, so this never reaches the set-level check.

    A `stress` scenario at `concurrency: 1` is a single request with a label —
    the generator shipped one titled "Many parallel transfers racing". The
    category's branch requires 2 or more, so the lie is a validation error
    rather than something the set check has to notice afterwards (ADR 0011).
    """
    flat = {"scenarios": [make_scenario("sc-001", "stress", concurrency=1, repeat=1)]}
    client = FakeClient([tool_use(flat), tool_use(full_payload())])

    _, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )

    assert result.attempts == 2
    correction = correction_text(client)
    assert "concurrency" in correction
    assert "greater than or equal to 2" in correction


def test_retry_when_a_writing_set_never_replays():
    """The set-level check is the net for the replay side.

    `stress` guarantees the race by schema, but `adversarial` asks for `repeat`
    without forcing it above 1 — the category covers ordering tricks that need
    no duplicate. So a writing set in which nothing replays is still possible,
    and `missing_execution_modes` is what rejects it.
    """
    no_replay = {
        "scenarios": [
            make_scenario(
                f"sc-{i:03d}",
                c.value,
                **({"concurrency": 4} if c is ScenarioCategory.STRESS else {}),
            )
            for i, c in enumerate(ALL_CATEGORIES, start=1)
        ]
    }
    client = FakeClient([tool_use(no_replay), tool_use(full_payload())])

    scenario_set, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )

    assert result.attempts == 2
    correction = correction_text(client)
    assert "`repeat` above 1" in correction
    assert "`concurrency` above 1" not in correction  # the race was already there
    assert any(s.repeat > 1 for s in scenario_set.scenarios)


def test_a_read_only_set_need_not_replay():
    """The requirement is scoped to sets that write.

    A spec with only GETs has nothing to duplicate, so demanding a replay
    there would reject correct output (sg-007's single-endpoint fixture).
    """
    reads = []
    for i, c in enumerate(ALL_CATEGORIES, start=1):
        extra = {"concurrency": 4} if c is ScenarioCategory.STRESS else {}
        s = make_scenario(f"sc-{i:03d}", c.value, **extra)
        s["setup"] = []
        s["action"] = {"name": "read", "method": "GET", "path": "/accounts/1/balance"}
        s["expectation"]["probes"] = []
        reads.append(s)
    client = FakeClient([tool_use({"scenarios": reads})])

    _, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )

    assert result.attempts == 1
    assert result.corrections == []


def test_retry_on_invalid_schema():
    broken = make_scenario()
    broken["action"]["path"] = "transfers"  # missing leading slash
    client = FakeClient([tool_use({"scenarios": [broken]}), tool_use(full_payload())])

    _, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )
    assert result.attempts == 2
    assert "must start with" in result.corrections[0]


def test_scenarios_sent_as_json_string_is_accepted():
    """Observed in the first live run: the array arrived as a JSON string.

    The content was valid, only the encoding was off, so it must not cost a
    retry (that one cost ~10k output tokens).
    """
    payload = {"scenarios": json.dumps(full_payload()["scenarios"])}
    client = FakeClient([tool_use(payload)])

    scenario_set, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )

    assert result.attempts == 1
    assert result.corrections == []
    assert scenario_set.covers_all_categories()
    assert result.usage.calls == 1


def test_coercion_leaves_genuine_type_errors_alone():
    client = FakeClient([tool_use({"scenarios": "not json at all"})] * sg.DEFAULT_MAX_ATTEMPTS)
    with pytest.raises(StructuredCallError):
        sg.generate_scenario_set(
            spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
        )


def test_coerce_json_strings_is_conservative():
    from ecvt.llm import coerce_json_strings

    assert coerce_json_strings({"a": "[1, 2]"})["a"] == [1, 2]
    assert coerce_json_strings({"a": '{"k": 1}'})["a"] == {"k": 1}
    # Left untouched: plain text, numbers inside strings, broken JSON.
    assert coerce_json_strings({"a": "hello"})["a"] == "hello"
    assert coerce_json_strings({"a": "42"})["a"] == "42"
    assert coerce_json_strings({"a": "[1, 2"})["a"] == "[1, 2"
    assert coerce_json_strings({"a": 5})["a"] == 5


def test_prompt_states_per_category_quota():
    from ecvt.openapi_reader import read_openapi

    prompt = sg.build_user_prompt(read_openapi(WALLET_SPEC), "t", per_category=2)
    for category in ALL_CATEGORIES:
        assert f"- {category.value}: 2" in prompt
    assert "14 in total" in prompt
    # Strict sampling (ADR 0019) makes `scenarios` an array; the prompt no
    # longer has to plead for one.
    assert "not a string containing one" not in prompt


def test_text_only_response_is_retried():
    client = FakeClient([FakeText(text="Here are some scenarios..."), tool_use(full_payload())])
    _, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )
    assert result.attempts == 2
    assert "no scenario_generator_output tool call" in result.corrections[0]


def test_gives_up_after_max_attempts_and_keeps_usage():
    partial = {"scenarios": [make_scenario("sc-001", "happy")]}
    client = FakeClient([tool_use(partial)] * sg.DEFAULT_MAX_ATTEMPTS)

    with pytest.raises(StructuredCallError) as excinfo:
        sg.generate_scenario_set(
            spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
        )

    error = excinfo.value
    assert len(error.attempts) == sg.DEFAULT_MAX_ATTEMPTS
    assert error.usage.calls == sg.DEFAULT_MAX_ATTEMPTS
    assert error.usage.total_tokens > 0


def test_missing_spec_raises_before_any_call():
    client = FakeClient([])
    with pytest.raises(FileNotFoundError):
        sg.generate_scenario_set(
            spec_path="does/not/exist.json", target="nope", client=client
        )
    assert client.messages.calls == []


# --- usage accounting ------------------------------------------------------


def record(usage: Usage, *, inp: int = 0, out: int = 0, cache_read: int = 0) -> Usage:
    """Feed one fake API response into a Usage, as structured_call would."""
    fake = FakeUsage(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=0,
    )
    usage.add_response(FakeResponse(content=[], usage=fake))
    return usage


def write_pricing(monkeypatch, tmp_path, models: dict) -> None:
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({"models": models}), encoding="utf-8")
    monkeypatch.setattr("ecvt.llm.PRICING_PATH", pricing)


def test_usage_cost_is_none_for_unpriced_model(monkeypatch, tmp_path):
    write_pricing(monkeypatch, tmp_path, {"other": {"input": 1.0}})
    usage = record(Usage(model="unlisted"), inp=1000, out=500)
    assert usage.total_tokens == 1500
    # An unpriced model is unknown, not free.
    assert usage.cost_usd() is None
    assert usage.as_dict()["total_tokens"] == 1500


def test_usage_cost_computed_when_pricing_present(monkeypatch, tmp_path):
    write_pricing(monkeypatch, tmp_path, {"m": {"input": 1.0, "output": 5.0}})
    usage = record(Usage(model="m"), inp=1_000_000, out=200_000)
    assert usage.cost_usd() == pytest.approx(2.0)


def test_real_pricing_file_prices_haiku():
    """config/pricing.json is filled in, so runs log a cost and not null."""
    usage = record(Usage(model="claude-haiku-5-5"), inp=1000, out=1000, cache_read=1000)
    cost = usage.cost_usd()
    assert cost is not None and cost > 0
    assert "cost_usd" in usage.as_dict()


def test_long_prompt_tier_applied_per_call(monkeypatch, tmp_path):
    """Two small calls stay on the cheap tier even when their sum crosses it."""
    write_pricing(
        monkeypatch,
        tmp_path,
        {
            "m": {
                "input": 1.0,
                "output": 1.0,
                "long_prompt_threshold": 1000,
                "long_prompt": {"input": 5.0, "output": 5.0},
            }
        },
    )

    small = Usage(model="m")
    record(small, inp=600, out=0)
    record(small, inp=600, out=0)
    assert small.long_prompt.calls == 0
    assert small.cost_usd() == pytest.approx(1200 / 1_000_000)

    big = record(Usage(model="m"), inp=1200, out=0)
    assert big.long_prompt.calls == 1
    assert big.cost_usd() == pytest.approx(1200 * 5 / 1_000_000)
    assert big.as_dict()["long_prompt_calls"] == 1


def test_long_prompt_without_tier_prices_reports_none(monkeypatch, tmp_path):
    write_pricing(monkeypatch, tmp_path, {"m": {"input": 1.0, "long_prompt_threshold": 100}})
    usage = record(Usage(model="m"), inp=500, out=0)
    assert usage.long_prompt.calls == 1
    assert usage.cost_usd() is None  # priced model, but not for the tier used


# --- CLI -------------------------------------------------------------------


def test_dry_run_makes_no_api_call(capsys):
    assert sg.main(["--spec", WALLET_SPEC, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "POST /transfers" in out
    assert sg.TOOL_NAME in out


def test_broken_json_string_correction_names_the_syntax_error():
    """Measured live: `scenarios` came back as JSON text with one stray `}`, and the
    correction said only "Input should be a valid list" — so the model re-sent the
    identical broken string four times. The correction must say where it broke."""
    text = json.dumps(full_payload()["scenarios"], separators=(",", ":"))
    cut = text.index('},{"id"') + 1
    broken = text[:cut] + "}" + text[cut:]  # one extra closing brace between scenarios
    client = FakeClient([tool_use({"scenarios": broken}), tool_use(full_payload())])

    _, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )

    assert result.attempts == 2
    correction = result.corrections[0]
    assert "not valid JSON" in correction and f"character {cut}" in correction
    assert "as a JSON array itself, not as a string" in correction
    sent_back = json.dumps(client.messages.calls[1]["messages"], default=str)
    assert "not valid JSON" in sent_back  # the model sees it, not just our log


def test_json_string_problems_ignores_valid_and_non_json_strings():
    from ecvt.llm import json_string_problems

    assert json_string_problems({"a": "[1, 2]", "b": "hello", "c": 3}) == []
    assert "character" in json_string_problems({"a": "[1,}"})[0]


def test_same_rejection_twice_starts_the_next_attempt_fresh():
    """Every a03 run that exhausted four attempts failed all four the same way: the
    correction turn shows the model its own object and it re-sends it. After two
    identical rejections the third attempt drops the rejected turns."""
    stuck = tool_use({"scenarios": "[1,}"})
    client = FakeClient([stuck, stuck, tool_use(full_payload())])

    _, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )

    assert result.attempts == 3
    calls = client.messages.calls
    assert len(calls[1]["messages"]) == 3  # attempt 2: a correction turn
    assert len(calls[2]["messages"]) == 1  # attempt 3: the original request only
    assert calls[2]["messages"][0] == calls[0]["messages"][0]


def test_different_rejections_keep_correcting():
    first = tool_use({"scenarios": "[1,}"})
    second = tool_use({"scenarios": "[2,}"})
    client = FakeClient([first, second, tool_use(full_payload())])
    sg.generate_scenario_set(spec_path=WALLET_SPEC, target="examples/wallet-api", client=client)
    assert len(client.messages.calls[2]["messages"]) == 3


# --- strict wire form (M4-T1, ADR 0019) --------------------------------------


def to_wire(payload: dict[str, Any]) -> dict[str, Any]:
    """`full_payload()` as the strict tool returns it: every step field present,
    the body as JSON text, maps as name/value lists."""

    def step(s: dict[str, Any]) -> dict[str, Any]:
        def pairs(m: dict[str, str] | None) -> list[dict[str, str]]:
            return [{"name": k, "value": v} for k, v in (m or {}).items()]

        return {
            "name": s["name"],
            "method": s["method"],
            "path": s["path"],
            "body": json.dumps(s["body"]) if s.get("body") is not None else "",
            "query": pairs(s.get("query")),
            "headers": pairs(s.get("headers")),
            "save": pairs(s.get("save")),
        }

    out = json.loads(json.dumps(payload))
    for sc in out["scenarios"]:
        sc["setup"] = [step(x) for x in sc["setup"]]
        sc["action"] = step(sc["action"])
        sc["expectation"]["probes"] = [step(x) for x in sc["expectation"]["probes"]]
        if sc["category"] not in ("stress", "adversarial"):
            sc.pop("repeat", None)
            sc.pop("concurrency", None)
    return out


def test_wire_payload_is_valid_against_the_strict_schema():
    import jsonschema

    jsonschema.validate(to_wire(full_payload()), sg.build_tool()["input_schema"])


def test_generates_from_the_wire_form():
    client = FakeClient([tool_use(to_wire(full_payload()))])
    scenario_set, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )
    assert result.attempts == 1
    first = scenario_set.scenarios[0]
    assert first.setup[0].body == {"owner": "alice", "initial_balance": 50.0}
    assert first.setup[0].save == {"src_id": "id"}
    assert first.action.query is None and first.expectation.probes[0].body is None
    assert client.messages.calls[0]["tools"][0]["strict"] is True


def test_body_that_is_not_json_goes_back_as_a_correction():
    broken = to_wire(full_payload())
    broken["scenarios"][0]["action"]["body"] = '{"amount": 500.0, "from_account": '
    client = FakeClient([tool_use(broken), tool_use(to_wire(full_payload()))])
    _, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )
    assert result.attempts == 2
    text = correction_text(client)
    assert "sc-001 action.body is not valid JSON" in text and "Field required" not in text
