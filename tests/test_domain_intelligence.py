"""Domain Intelligence (a02) — offline, with a faked Messages client.

The parcel few-shot in system_prompt.md is used as both spec and expected output, so
these tests also prove the prompt's example is consistent with the code's inventory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.a02_domain_intelligence import agent as di
from ecvt.evals import AdapterContext, EvalCase, adapt_domain_intelligence, score_schema_valid
from ecvt.llm import StructuredCallError
from tests.test_scenario_generator import FakeClient, FakeToolUse, correction_text

PROMPT = Path("agents/a02_domain_intelligence/system_prompt.md").read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    i = text.index(start) + len(start)
    return text[i : text.index(end, i)]


@pytest.fixture
def parcel_spec(tmp_path) -> Path:
    path = tmp_path / "parcel.openapi.yaml"
    examples = PROMPT[PROMPT.index("<examples>") :]
    path.write_text(_between(examples, "<spec>", "</spec>"), encoding="utf-8")
    return path


def parcel_model() -> dict:
    return json.loads(_between(PROMPT, "emit_domain_model:", "</output>"))


def decline() -> dict:
    return {
        "reason": "out_of_scope",
        "requested": "Write test scenarios.",
        "detail": "a02 only builds a domain model from the spec.",
    }


def test_inventory_ids_follow_the_rule():
    spec = {
        "paths": {
            "/orders": {"post": {}},
            "/orders/{orderId}": {"get": {}, "delete": {"operationId": "cancelOrder"}},
        }
    }
    assert di.build_inventory(spec) == {
        "post_orders": ("POST", "/orders"),
        "get_orders_order_id": ("GET", "/orders/{orderId}"),
        "cancel_order": ("DELETE", "/orders/{orderId}"),
    }


def test_inventory_matches_the_few_shot(parcel_spec):
    inv = di.build_inventory(di.load_spec(parcel_spec))
    assert list(inv) == [op["id"] for op in parcel_model()["operations"]]


def test_spec_comments_never_reach_the_model(tmp_path):
    spec = tmp_path / "s.yaml"
    spec.write_text("# eval hint: bug in renew\npaths:\n  /a: {get: {}}\n", encoding="utf-8")
    prompt = di.build_user_prompt(di.DEFAULT_TASK, {}, di.load_spec(spec))
    assert "eval hint" not in prompt


def test_request_shape(parcel_spec):
    client = FakeClient([FakeToolUse("emit_domain_model", parcel_model())])
    res = di.build_domain_model(spec_path=parcel_spec, client=client)
    assert res.model is not None and res.declined is None

    call = client.messages.calls[0]
    assert call["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}
    assert {t["name"] for t in call["tools"]} == {"emit_domain_model", "decline_task"}
    system = call["system"]
    assert len(system) == 2  # system prompt, then the skill
    assert "cache_control" not in system[0] and system[1]["cache_control"]["type"] == "ephemeral"
    assert not system[1]["text"].startswith("---")  # front matter stripped
    assert "create_parcel | POST | /parcels" in call["messages"][0]["content"]


def test_decline_is_returned_not_raised(parcel_spec):
    client = FakeClient([FakeToolUse("decline_task", decline())])
    res = di.build_domain_model(spec_path=parcel_spec, client=client)
    assert res.model is None and res.declined.reason == "out_of_scope"
    assert res.call.tool_name == "decline_task"


def test_bad_pointer_is_sent_back_then_fixed(parcel_spec):
    bad = parcel_model()
    bad["entities"][0]["evidence"] = ["#/components/schemas/Nope"]
    client = FakeClient([FakeToolUse("emit_domain_model", bad),
                         FakeToolUse("emit_domain_model", parcel_model())])
    res = di.build_domain_model(spec_path=parcel_spec, client=client)
    assert res.call.attempts == 2
    retry = correction_text(client)
    assert "#/components/schemas/Nope" in retry and "does not resolve" in retry


def test_gives_up_after_the_last_correction(parcel_spec):
    """Three attempts, then StructuredCallError — DomainModel validates in two
    layers that Pydantic reports in sequence, so the budget is first try plus two
    corrections (see build_domain_model)."""
    bad = parcel_model()
    bad["operations"] = bad["operations"][:1]  # misses deliver_parcel
    client = FakeClient([FakeToolUse("emit_domain_model", bad)] * 3)
    with pytest.raises(StructuredCallError):
        di.build_domain_model(spec_path=parcel_spec, client=client)
    assert len(client.messages.calls) == 3


def test_eval_adapter_validates_against_the_spec(parcel_spec):
    case = EvalCase(
        id="t", input={"task": di.DEFAULT_TASK, "spec": str(parcel_spec)},
        category="happy-path", scoring="schema-valid",
        expected_schema="agents.a02_domain_intelligence.schema.DomainModel",
    )
    ok = adapt_domain_intelligence(
        case, AdapterContext(client=FakeClient([FakeToolUse("emit_domain_model", parcel_model())]))
    )
    assert score_schema_valid(case, ok).passed

    ok.obj["entities"][0]["evidence"] = ["#/nowhere"]  # context is what catches this
    assert not score_schema_valid(case, ok).passed


def test_committed_tools_json_matches_build_tools():
    from agents.a02_domain_intelligence.schema import build_tools

    committed = json.loads(Path("agents/a02_domain_intelligence/tools.json").read_text(encoding="utf-8"))
    assert committed == build_tools(), "regenerate: python -m agents.a02_domain_intelligence.schema"
