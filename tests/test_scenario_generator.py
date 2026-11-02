"""Scenario Generator tests — offline, with a faked Messages client (M1-T1).

No API key is used: a stub client replays canned tool_use responses, so the
prompt assembly, schema enforcement and retry path are all covered in CI.
"""

from __future__ import annotations

import json
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


@dataclass
class FakeText:
    text: str
    type: str = "text"


@dataclass
class FakeResponse:
    content: list[Any]
    usage: FakeUsage


class FakeMessages:
    def __init__(self, payloads: list[Any]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        payload = self._payloads.pop(0)
        content = [payload] if not isinstance(payload, list) else payload
        return FakeResponse(content=content, usage=FakeUsage())


class FakeClient:
    def __init__(self, payloads: list[Any]) -> None:
        self.messages = FakeMessages(payloads)


def full_payload() -> dict[str, Any]:
    return {
        "scenarios": [
            make_scenario(f"sc-{i:03d}", c.value) for i, c in enumerate(ALL_CATEGORIES, start=1)
        ]
    }


def tool_use(payload: dict[str, Any]) -> FakeToolUse:
    return FakeToolUse(name=sg.TOOL_NAME, input=payload)


# --- tool schema -----------------------------------------------------------


def test_tool_schema_generated_from_pydantic():
    tool = sg.build_tool()
    assert tool["name"] == sg.TOOL_NAME
    assert "when" in tool["description"].lower() or "use this tool" in tool["description"].lower()

    schema = tool["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["scenarios"]
    assert set(schema["$defs"]) >= {"Scenario", "HttpStep", "Expectation", "ScenarioCategory"}
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

    correction = client.messages.calls[1]["messages"][-1]["content"]
    assert "rejected by schema validation" in correction
    assert "adversarial" in correction  # the actual missing category is named
    assert result.usage.calls == 2


def test_retry_on_invalid_schema():
    broken = make_scenario()
    broken["action"]["path"] = "transfers"  # missing leading slash
    client = FakeClient([tool_use({"scenarios": [broken]}), tool_use(full_payload())])

    _, result = sg.generate_scenario_set(
        spec_path=WALLET_SPEC, target="examples/wallet-api", client=client
    )
    assert result.attempts == 2
    assert "must start with" in result.corrections[0]


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


def test_usage_cost_is_none_without_pricing():
    usage = Usage(model="claude-haiku-5-5", input_tokens=1000, output_tokens=500, calls=1)
    assert usage.total_tokens == 1500
    # config/pricing.json ships empty on purpose; cost stays unreported.
    assert usage.cost_usd() is None
    assert usage.as_dict()["total_tokens"] == 1500


def test_usage_cost_computed_when_pricing_present(monkeypatch, tmp_path):
    pricing = tmp_path / "pricing.json"
    pricing.write_text(
        json.dumps({"models": {"m": {"input": 1.0, "output": 5.0}}}), encoding="utf-8"
    )
    monkeypatch.setattr("ecvt.llm.PRICING_PATH", pricing)

    usage = Usage(model="m", input_tokens=1_000_000, output_tokens=200_000, calls=1)
    assert usage.cost_usd() == pytest.approx(2.0)


# --- CLI -------------------------------------------------------------------


def test_dry_run_makes_no_api_call(capsys):
    assert sg.main(["--spec", WALLET_SPEC, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "POST /transfers" in out
    assert sg.TOOL_NAME in out
