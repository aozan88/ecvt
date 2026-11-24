"""Stage runners — the Manager's adapters to each subagent's real entry point (ADR 0009).

Contract (see agent.py):
    run(stage_input) -> {"artifact": <json>, "summary": str, "metrics": dict, "usage": dict}
    stage_input = {"run_id", "stage", "slots", "upstream": {stage: absolute artifact path}}

Each runner wraps the same function the M1 CLI calls, so the Manager adds routing and
state without changing what a stage does. Errors follow StageError: retryable for a
model that produced no valid output, non-retryable for bad input or a decline.

Artifacts are self-contained JSON so a later stage — or a later run, through a slot
such as `scenario_set_ref` — can start from one file:
    domain_intel -> DomainModel
    scenario_gen -> {"scenario_set": ScenarioSet}
    execution    -> {"scenarios": [...], "records": [...]}
    evaluation   -> {"scores": [...], "stats": {...}}
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from ecvt.llm import DEFAULT_SUBAGENT_MODEL, StructuredCallError

SPEC_NAMES = ("openapi.json", "openapi.yaml", "openapi.yml")


def _error_cls():
    from agents.a01_manager.agent import StageError  # late: agent.py imports this module

    return StageError


def _spec_path(target: str) -> Path:
    path = Path(target)
    if path.is_file():
        return path
    for name in SPEC_NAMES:
        if (path / name).is_file():
            return path / name
    raise _error_cls()(f"no OpenAPI spec found for target {target!r}", retryable=False)


def _load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _model() -> str:
    return os.environ.get("ECVT_SUBAGENT_MODEL", DEFAULT_SUBAGENT_MODEL)


# How much of the last validator error to carry into the stage record. Long
# enough for the first few of a multi-error Pydantic dump, which is what names
# the pattern; the whole dump of 20 errors does not belong in state.json.
LAST_ERROR_CHARS = 1200


def _why(what: str, exc: StructuredCallError) -> str:
    """A stage failure message that says what was wrong, not just how often.

    StructuredCallError's own message is "<tool> produced no valid output in N
    attempts" — it names no rule. The validator errors are in `exc.attempts`,
    and dropping them meant state.json recorded a stage failure with nothing to
    act on: diagnosing one live scenario_gen failure took a second live run to
    recover errors the first run already had (2026-11-24).
    """
    last = exc.attempts[-1] if exc.attempts else ""
    if not last:
        return f"{what}: {exc}"
    return f"{what}: {exc}; last error: {last[:LAST_ERROR_CHARS]}"


def _base_url() -> str:
    from ecvt.execution import DEFAULT_BASE_URL

    return os.environ.get("ECVT_BASE_URL", DEFAULT_BASE_URL)


# --------------------------------------------------------------------------- a02
def run_domain_intel(stage_input: dict[str, Any]) -> dict[str, Any]:
    from agents.a02_domain_intelligence.agent import build_domain_model, summarize

    StageError = _error_cls()
    spec = _spec_path(stage_input["slots"]["target_system"])
    try:
        res = build_domain_model(spec_path=spec, model=_model())
    except StructuredCallError as e:
        raise StageError(_why("domain model invalid after correction", e), retryable=True) from e
    if res.declined:
        raise StageError(f"a02 declined: {res.declined.detail}", retryable=False)
    counts = summarize(res.model)
    return {
        "artifact": res.payload(),
        "summary": "domain model: " + ", ".join(f"{k}={v}" for k, v in counts.items()),
        "metrics": counts,
        "usage": res.call.usage.as_dict(),
    }


# --------------------------------------------------------------------------- a03
def run_scenario_gen(stage_input: dict[str, Any]) -> dict[str, Any]:
    from agents.a03_scenario_generator.agent import generate_scenario_set

    StageError = _error_cls()
    target = stage_input["slots"]["target_system"]
    domain_ref = stage_input["upstream"].get("domain_intel")
    try:
        scenario_set, result = generate_scenario_set(
            spec_path=_spec_path(target),
            target=target,
            model=_model(),
            domain_model=_load(domain_ref) if domain_ref else None,
        )
    except StructuredCallError as e:
        raise StageError(_why("scenario set invalid", e), retryable=True) from e
    counts = scenario_set.category_counts()
    return {
        "artifact": {"scenario_set": scenario_set.model_dump(mode="json", exclude_none=True)},
        "summary": f"{len(scenario_set.scenarios)} scenarios; "
        + ", ".join(f"{k}={v}" for k, v in counts.items()),
        "metrics": {"scenarios": len(scenario_set.scenarios), **counts},
        "usage": result.usage.as_dict(),
    }


def _scenarios_from(ref: str) -> list[Any]:
    from ecvt.schema import Scenario, read_scenarios_jsonl

    if ref.endswith(".jsonl"):
        return read_scenarios_jsonl(ref)
    data = _load(ref)
    raw = (data.get("scenario_set") or {}).get("scenarios") or data.get("scenarios") or []
    return [Scenario.model_validate(s) for s in raw]


# --------------------------------------------------------------------------- a05
def run_execution(stage_input: dict[str, Any]) -> dict[str, Any]:
    from ecvt.execution import RunnerConfig, run_scenarios

    StageError = _error_cls()
    ref = stage_input["upstream"].get("scenario_gen") or stage_input["slots"].get("scenario_set_ref")
    if not ref:
        raise StageError("no scenario set: neither upstream nor scenario_set_ref", retryable=False)
    scenarios = _scenarios_from(ref)
    if not scenarios:
        raise StageError(f"scenario set {ref} is empty", retryable=False)
    from ecvt.evals import target_reachable

    if not target_reachable(_base_url()):
        # Nothing answered: a transport fault, not a finding (ADR 0006). Worth one retry.
        raise StageError(f"target unreachable at {_base_url()}", retryable=True)
    records = asyncio.run(run_scenarios(scenarios, RunnerConfig(base_url=_base_url())))
    outcomes: dict[str, int] = {}
    for r in records:
        outcomes[r.outcome.value] = outcomes.get(r.outcome.value, 0) + 1
    return {
        "artifact": {
            "scenarios": [s.model_dump(mode="json", exclude_none=True) for s in scenarios],
            "records": [r.model_dump(mode="json", exclude_none=True) for r in records],
        },
        "summary": f"{len(records)} executed; " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())),
        "metrics": {"executed": len(records), **outcomes},
        "usage": {},
    }


# --------------------------------------------------------------------------- a06
def run_evaluation(stage_input: dict[str, Any]) -> dict[str, Any]:
    from agents.a06_evaluation.agent import score_scenarios
    from ecvt.results import ExecutionRecord
    from ecvt.schema import Scenario
    from ecvt.scoring import summarize

    StageError = _error_cls()
    ref = stage_input["upstream"].get("execution") or stage_input["slots"].get("execution_ref")
    if not ref:
        raise StageError("no execution results: neither upstream nor execution_ref", retryable=False)
    data = _load(ref)
    scenarios = [Scenario.model_validate(s) for s in data.get("scenarios", [])]
    records = [ExecutionRecord.model_validate(r) for r in data.get("records", [])]
    scores, usage = score_scenarios(scenarios, records, model=_model())
    if not scores:
        raise StageError("no scenario had a matching execution record", retryable=False)
    stats = summarize(scores)
    return {
        "artifact": {
            "scores": [s.model_dump(mode="json", exclude_none=True) for s in scores],
            "stats": stats,
        },
        "summary": f"{stats['scored']} scored; defects suspected: {stats['defects_suspected']}",
        "metrics": stats,
        "usage": usage.as_dict(),
    }


RUNNERS = {
    "domain_intel": run_domain_intel,
    "scenario_gen": run_scenario_gen,
    "execution": run_execution,
    "evaluation": run_evaluation,
}
