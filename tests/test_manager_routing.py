"""Manager routing and stage preconditions — agents/a01_manager/agent.py.

No LLM and no SDK call: only the code that decides, `INTENT_PLAN` + `precondition()`.

  1. Routing      — the intent picks the plan; the model never picks the stage order.
  2. Plan guard   — a stage outside the plan is blocked.
  3. Chaining     — a stage runs only after every earlier stage in the plan succeeded.
  4. Retry limit  — a stage stops after MAX_ATTEMPTS, even if the error is retryable.
  5. No retry     — a non-retryable failure is never retried.

Why 5 holds (ADR 0006): when the target system has already answered (any HTTP status),
the call reached it. Sending it again can repeat a side effect — a second transfer —
and a 4xx/5xx is a finding to report, not a glitch to hide. Only transient faults
(timeout, rate limit) are retryable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import jsonschema
import pytest

from agents.a01_manager import planner as pl
from agents.a01_manager.agent import (
    INTENT_PLAN,
    MAX_ATTEMPTS,
    STAGES,
    TOOL_SPECS,
    StateStore,
    precondition,
)
from ecvt.evals import _manager_stub_runners


def make_state(tmp_path, intent: str) -> dict:
    """A fresh pipeline state with the intent recorded and its plan derived, as the
    Manager's record_intent tool leaves it. Every stage starts with attempts=0."""
    state = StateStore(tmp_path).create("run_test", "x", "cli")
    state["intent"] = {"name": intent}
    state["plan"] = list(INTENT_PLAN[intent])
    return state


def test_routing_intent_picks_the_plan():
    assert INTENT_PLAN["generate_scenarios"] == ("domain_intel", "scenario_gen")
    assert INTENT_PLAN["domain_analysis"] == ("domain_intel",)
    assert INTENT_PLAN["out_of_scope"] == ()  # an empty plan runs nothing


def test_stage_outside_plan_is_blocked(tmp_path):
    state = make_state(tmp_path, "generate_scenarios")
    assert precondition(state, "execution") == "stage_not_in_plan"


def test_chaining_waits_for_upstream(tmp_path):
    state = make_state(tmp_path, "full_validation")
    assert precondition(state, "scenario_gen") == "upstream_not_succeeded:domain_intel"

    state["stages"]["domain_intel"]["status"] = "succeeded"
    assert precondition(state, "scenario_gen") is None

    state["stages"]["scenario_gen"]["status"] = "succeeded"
    assert precondition(state, "evaluation") == "upstream_not_succeeded:execution"


def test_retry_limit_stops_a_retryable_failure(tmp_path):
    # domain_intel is first in the plan, so the upstream check cannot fire first.
    state = make_state(tmp_path, "full_validation")
    rec = state["stages"]["domain_intel"]

    rec.update(attempts=1, status="failed", error={"retryable": True})
    assert precondition(state, "domain_intel") is None  # one retry is still allowed

    rec.update(attempts=MAX_ATTEMPTS)
    assert precondition(state, "domain_intel") == "max_attempts_reached"


def test_non_retryable_failure_is_not_retried(tmp_path):
    state = make_state(tmp_path, "full_validation")
    state["stages"]["domain_intel"].update(
        attempts=1, status="failed", error={"retryable": False}
    )
    assert precondition(state, "domain_intel") == "not_retryable"


# --- planner mode (M4-T3, ADR 0020) -------------------------------------------
#
# The model's one call is faked; everything after it is the code under test:
# plan derivation, stage order, the retry rule, manager_output.

PLUGINS = ["rest_api", "wallet"]
NO_SLOTS = dict.fromkeys(
    ("target_system", "plugin", "scope", "scenario_set_ref", "execution_ref", "run_ref"), ""
)


@dataclass
class _Use:
    input: dict[str, Any]
    name: str = pl.PLAN_TOOL
    type: str = "tool_use"
    id: str = "toolu_plan"


@dataclass
class _Usage:
    input_tokens: int = 900
    output_tokens: int = 120
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Messages:
    replies: list[dict[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return type("R", (), {"content": [_Use(self.replies.pop(0))], "usage": _Usage(),
                              "stop_reason": "tool_use"})()  # fmt: skip


class _Client:
    def __init__(self, *replies: dict[str, Any]) -> None:
        self.messages = _Messages(list(replies))


def plan(intent: str, **slots: str) -> dict[str, Any]:
    return {
        "intent": intent, "confidence": 0.9, "slots": {**NO_SLOTS, **slots},
        "out_of_scope_reason": "", "rationale": "test", "clarifying_question": "",
        "redirect": "", "language": "en",
    }  # fmt: skip


def run(tmp_path, reply, faults=None, model="claude-haiku-5-5", **kw):
    events: list[tuple[str, dict]] = []
    client = _Client(reply)
    state = pl.run_planned(
        "a request", runs_dir=tmp_path, runners=_manager_stub_runners(faults or {}),
        plugins=PLUGINS, client=client, model=model,
        on_event=lambda k, p: events.append((k, p)), **kw,
    )  # fmt: skip
    jsonschema.validate(state["result"], TOOL_SPECS["manager_output"]["input_schema"])
    return state, events, client


def test_planner_runs_the_plan_in_order_and_completes(tmp_path):
    state, events, client = run(tmp_path, plan("full_validation", target_system="w", plugin="wallet"))
    started = [p["stage"] for k, p in events if k == "stage_start"]
    assert started == list(STAGES)
    out = state["result"]
    assert out["status"] == "completed" and [e["stage"] for e in out["stages_run"]] == list(STAGES)
    assert all(e["artifact_ref"] for e in out["stages_run"])
    assert len(client.messages.calls) == 1  # one model call for the whole run
    assert state["manager_session"]["mode"] == "planner"


def test_planner_retries_a_retryable_failure_once_then_stops(tmp_path):
    state, events, _ = run(tmp_path, plan("full_validation", target_system="w", plugin="wallet"),
                           faults={"scenario_gen": "fail_retryable"})  # fmt: skip
    started = [p["stage"] for k, p in events if k == "stage_start"]
    assert started == ["domain_intel", "scenario_gen", "scenario_gen"]
    assert state["stages"]["scenario_gen"]["attempts"] == MAX_ATTEMPTS
    assert state["stages"]["execution"]["status"] == "pending"
    assert state["result"]["status"] == "failed" and "scenario_gen" in state["result"]["summary"]


def test_planner_never_retries_a_permanent_failure(tmp_path):
    state, events, _ = run(tmp_path, plan("domain_analysis", target_system="w", plugin="wallet"),
                           faults={"domain_intel": "fail"})  # fmt: skip
    assert [p["stage"] for k, p in events if k == "stage_start"] == ["domain_intel"]
    assert state["result"]["status"] == "failed"


def test_planner_asks_with_the_models_question_and_runs_nothing(tmp_path):
    reply = {**plan("full_validation", target_system="SAP payroll", plugin="sap"),
             "clarifying_question": "Hangi plugin: rest_api mı wallet mı?", "language": "tr"}
    state, events, _ = run(tmp_path, reply)
    assert not [e for e in events if e[0] == "stage_start"]
    assert state["intent"]["invalid_slots"] == ["plugin"]  # the code, not the model, rejects it
    assert state["result"]["status"] == "needs_input"
    assert state["result"]["clarifying_question"].startswith("Hangi plugin")
    assert state["result"]["summary"].startswith("Hiçbir aşama")


def test_planner_falls_back_to_a_question_when_the_model_gave_none(tmp_path):
    state, _, _ = run(tmp_path, plan("full_validation", target_system="w"))
    assert "rest_api, wallet" in state["result"]["clarifying_question"]


def test_planner_rejects_out_of_scope_with_the_models_redirect(tmp_path):
    reply = {**plan("out_of_scope"), "out_of_scope_reason": "stage_not_available",
             "redirect": "Regression is not available yet."}  # fmt: skip
    state, _, _ = run(tmp_path, reply)
    assert state["result"]["status"] == "rejected"
    assert state["result"]["redirect"] == "Regression is not available yet."


def test_planner_reports_a_previous_runs_status(tmp_path):
    from ecvt.evals import _seed_run

    _seed_run(StateStore(tmp_path), "run_20261101T090000Z_a1b2:completed")
    state, _, _ = run(tmp_path, plan("run_status", run_ref="run_20261101T090000Z_a1b2"))
    assert state["result"]["status"] == "status_reported"
    assert "run_20261101T090000Z_a1b2 is completed" in state["result"]["summary"]


@pytest.mark.parametrize(
    ("model", "choice"),
    [("claude-haiku-5-5", {"type": "tool", "name": pl.PLAN_TOOL}),
     ("claude-sonnet-5-5", {"type": "auto"})],  # Sonnet 5.5 answers a forced choice with 400
)  # fmt: skip
def test_planner_call_is_strict_low_effort_and_model_aware(tmp_path, model, choice):
    _, _, client = run(tmp_path, plan("domain_analysis", target_system="w", plugin="wallet"),
                       model=model)  # fmt: skip
    call = client.messages.calls[0]
    assert call["tool_choice"] == {**choice, "disable_parallel_tool_use": True}
    assert call["tools"][0]["strict"] is True
    assert call["output_config"] == {"effort": pl.PLANNER_EFFORT}


def test_planner_prompt_examples_match_the_strict_tool():
    tool = pl.build_plan_tool()
    examples = pl.load_planner_prompt().split("plan_run {")[1:]
    assert len(examples) == 2
    for chunk in examples:
        jsonschema.validate(json.loads("{" + chunk.split("\n")[0]), tool["input_schema"])
