"""Manager routing and stage preconditions — agents/a01_manager/agent.py.

Hand-written by Ozan (course practice: Agents and workflows).
No LLM and no SDK call: only the code that decides, `INTENT_PLAN` + `precondition()`.
The import still loads claude_agent_sdk, so run it in the project venv
(`pip install -r requirements.txt`); no Codespace needed.

Each test is one sentence of the mechanism map (docs/02-architecture/pipeline.md, PR #25):
  1. Routing      — the intent picks the plan; the model never picks the stage order.
  2. Plan guard   — a stage outside the plan is blocked.
  3. Chaining     — a stage runs only after every earlier stage in the plan succeeded.
  4. Retry limit  — a stage stops after MAX_ATTEMPTS, even if the error is retryable.
  5. No retry     — a non-retryable failure is never retried.

How to work through this file
  - Read precondition() in agent.py first (~20 lines). The ORDER of its checks matters.
  - Run only this file:  pytest -q tests/test_manager_routing.py
  - Done: 5 passed, 0 skipped, `ruff check .` clean.

Deliberate break (after all five pass)
  In precondition(), comment out BOTH lines:
      if stage not in plan:
          return "stage_not_in_plan"
  (commenting only the `if` leaves a dangling `return` -> IndentationError, nothing runs).
  Run the file: test 2 fails. Note WHICH exception you get — it is not an AssertionError.
  Why? (hint: the next line calls plan.index(stage)). Then restore the two lines.
"""

from __future__ import annotations

from agents.a01_manager.agent import (
    INTENT_PLAN,
    MAX_ATTEMPTS,
    StateStore,
    precondition,
)


def make_state(tmp_path, intent: str) -> dict:
    """A fresh pipeline state with the intent recorded and its plan derived, as the
    Manager's record_intent tool would leave it. Every stage starts as attempts=0."""
    state = StateStore(tmp_path).create("run_test", "x", "cli")
    state["intent"] = {"name": intent}
    state["plan"] = list(INTENT_PLAN[intent])
    return state


def test_routing_intent_picks_the_plan():
    # Pattern (a different intent, so you still write yours):
    # NOTE: the trailing comma makes it a 1-tuple. ("domain_intel") is a plain string.
    assert INTENT_PLAN["domain_analysis"] == ("domain_intel",)
    # Yours: generate_scenarios -> ("domain_intel", "scenario_gen")
    assert INTENT_PLAN["generate_scenarios"] == ("domain_intel", "scenario_gen")
    # Bonus: assert INTENT_PLAN["out_of_scope"] == ()  — an empty plan runs nothing.
    assert INTENT_PLAN["out_of_scope"] == ()


def test_stage_outside_plan_is_blocked(tmp_path):
    # Pattern:
    state = make_state(tmp_path, "generate_scenarios")
    assert precondition(state, "execution") == "stage_not_in_plan"
    # Yours: intent generate_scenarios, stage execution -> "stage_not_in_plan"


def test_chaining_waits_for_upstream(tmp_path):
    # Two asserts in one test — before and after the upstream stage succeeds:
    state = make_state(tmp_path, "full_validation")
    assert precondition(state, "scenario_gen") == "upstream_not_succeeded:domain_intel"
    state["stages"]["domain_intel"]["status"] = "succeeded"
    assert precondition(state, "scenario_gen") is None
    # Write it yourself from the lines above; then add a third assert of your own:
    # under full_validation, what does precondition(state, "evaluation") return now?
    state["stages"]["scenario_gen"]["status"] = "succeeded"
    assert precondition(state, "evaluation") == "upstream_not_succeeded:execution"


def test_retry_limit_stops_a_retryable_failure(tmp_path):
    # Use stage "domain_intel" (first in the plan). With "scenario_gen" the upstream
    # check fires first and you get "upstream_not_succeeded:..." — the wrong reason.
    # Pattern for editing one stage record:
    state = make_state(tmp_path, "generate_scenarios")
    rec = state["stages"]["domain_intel"]
    rec.update(attempts=MAX_ATTEMPTS, status="failed", error={"retryable": True})
    # Yours: attempts = MAX_ATTEMPTS, retryable True -> "max_attempts_reached"
    # Bonus: same record with attempts = 1 -> None (one retry is still allowed).
    assert precondition(state, "domain_intel") == "max_attempts_reached"
    # Only attempts changes — status stays "failed", so the bonus really tests the
    # attempts counter. ("succeeded" would short-circuit at "already_succeeded".)
    rec.update(attempts=1, status="failed", error={"retryable": True})
    assert precondition(state, "domain_intel") is None


def test_non_retryable_failure_is_not_retried(tmp_path):
    # Same pattern as test 4.
    # Yours: stage "domain_intel", attempts = 1, status "failed",
    #        error {"retryable": False} -> "not_retryable"
    state = make_state(tmp_path, "generate_scenarios")
    rec = state["stages"]["domain_intel"]
    rec.update(attempts=1, status="failed", error={"retryable": False})
    assert precondition(state, "domain_intel") == "not_retryable"
    # Question: why does a received HTTP status count as non-retryable? (ADR 0006)
    # Answer : the status came back from the TARGET system, not from Claude — the
    #          request was delivered and answered. That status IS the observation we
    #          are testing for, so a retry would both mask the finding and repeat a
    #          possibly non-idempotent call. Only transport-level failures retry.

