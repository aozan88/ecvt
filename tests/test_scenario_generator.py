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
from ecvt.schema import ALL_CATEGORIES, read_scenarios_jsonl
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
    assert set(schema["$defs"]) >= {"ScenarioDraft", "HttpStep", "Expectation", "ScenarioCategory"}
    # The wire model makes both required, so the model has to state a number
    # per scenario rather than silently inheriting 1 (see ScenarioDraft).
    assert {"repeat", "concurrency"} <= set(schema["$defs"]["ScenarioDraft"]["required"])
    assert schema["$defs"]["ScenarioCategory"]["enum"] == [c.value for c in ALL_CATEGORIES]


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
    """CLAUDE.md for this agent: system_prompt.md max 2 pages."""
    lines = sg.load_system_prompt().splitlines()
    assert len(lines) <= 160, f"system_prompt.md is {len(lines)} lines"


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


def test_retry_when_the_set_never_races_or_replays():
    """A wholly sequential set is rejected and the omission is fed back.

    This is the defect the live eval caught: 14 scenarios, every one at
    concurrency=1 and repeat=1, including one titled "Many parallel transfers
    racing". Such a set cannot reach a race or a double-spend, so the prompt
    asked for both — and was ignored twice. The requirement now lives in the
    validator, where the model is shown what it missed.
    """
    sequential = {
        "scenarios": [
            make_scenario(f"sc-{i:03d}", c.value) for i, c in enumerate(ALL_CATEGORIES, start=1)
        ]
    }
    client = FakeClient([tool_use(sequential), tool_use(full_payload())])

    scenario_set, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )

    assert result.attempts == 2
    correction = correction_text(client)
    assert "`concurrency` above 1" in correction
    assert "`repeat` above 1" in correction
    assert any(s.concurrency > 1 for s in scenario_set.scenarios)
    assert any(s.repeat > 1 for s in scenario_set.scenarios)


def test_a_read_only_set_need_not_replay():
    """The requirement is scoped to sets that write.

    A spec with only GETs has nothing to duplicate, so demanding a replay
    there would reject correct output (sg-007's single-endpoint fixture).
    """
    reads = []
    for i, c in enumerate(ALL_CATEGORIES, start=1):
        s = make_scenario(f"sc-{i:03d}", c.value)
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
    client = FakeClient([tool_use({"scenarios": "not json at all"})] * 3)
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
    assert "not a string containing one" in prompt


def test_text_only_response_is_retried():
    client = FakeClient([FakeText(text="Here are some scenarios..."), tool_use(full_payload())])
    _, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )
    assert result.attempts == 2
    assert "no scenario_generator_output tool call" in result.corrections[0]


def test_gives_up_after_max_attempts_and_keeps_usage():
    partial = {"scenarios": [make_scenario("sc-001", "happy")]}
    client = FakeClient([tool_use(partial)] * 3)

    with pytest.raises(StructuredCallError) as excinfo:
        sg.generate_scenario_set(
            spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
        )

    error = excinfo.value
    assert len(error.attempts) == 3
    assert error.usage.calls == 3
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
