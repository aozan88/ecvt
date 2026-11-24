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

from agents.a01_manager.agent import INTENT_PLAN, MAX_ATTEMPTS, StateStore, precondition


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
