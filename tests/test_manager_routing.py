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

Remove the `pytest.skip(...)` line of a test once its body is written.
After all five pass: comment out BOTH lines of `if stage not in plan:` / `return ...`
in precondition(), watch test 2 fail, then restore. Which exception do you get, and why?
"""

from __future__ import annotations

import pytest

from agents.a01_manager.agent import INTENT_PLAN, StateStore

# TODO: import precondition and MAX_ATTEMPTS from agents.a01_manager.agent
#       (ruff fails on unused imports, so add each one when a test first uses it)


def make_state(tmp_path, intent: str) -> dict:
    state = StateStore(tmp_path).create("run_test", "x", "cli")
    state["intent"] = {"name": intent}
    state["plan"] = list(INTENT_PLAN[intent])
    return state


def test_routing_intent_picks_the_plan():
    pytest.skip("TODO: write me")
    # TODO: INTENT_PLAN["generate_scenarios"] == ("domain_intel", "scenario_gen")


def test_stage_outside_plan_is_blocked(tmp_path):
    pytest.skip("TODO: write me")
    # TODO: under generate_scenarios, precondition(state, "execution") == "stage_not_in_plan"


def test_chaining_waits_for_upstream(tmp_path):
    pytest.skip("TODO: write me")
    # TODO: under full_validation, scenario_gen -> "upstream_not_succeeded:domain_intel"
    # TODO: set state["stages"]["domain_intel"]["status"] = "succeeded" -> precondition is None


def test_retry_limit_stops_a_retryable_failure(tmp_path):
    pytest.skip("TODO: write me")
    # TODO: use stage "domain_intel" (first in plan). With "scenario_gen" the upstream
    #       check fires first and you get the wrong reason — see the order in precondition().
    # TODO: attempts = MAX_ATTEMPTS, status "failed", error {"retryable": True}
    #       -> "max_attempts_reached"


def test_non_retryable_failure_is_not_retried(tmp_path):
    pytest.skip("TODO: write me")
    # TODO: stage "domain_intel", attempts = 1, status "failed", error {"retryable": False}
    #       -> "not_retryable"
