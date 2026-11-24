"""Stage runners — the Manager's adapters to a02/a03/a05/a06 (offline, no model call)."""

from __future__ import annotations

import json

import pytest

from agents.a01_manager import runners
from agents.a01_manager.agent import StageError, load_stage_runners
from agents.a03_scenario_generator.agent import build_user_prompt
from ecvt.llm import StructuredCallError, Usage
from ecvt.openapi_reader import read_openapi

WALLET = "examples/wallet-api"


def _input(stage, slots=None, upstream=None):
    return {"run_id": "run_t", "stage": stage, "slots": slots or {}, "upstream": upstream or {}}


def test_all_four_stages_are_wired():
    assert set(load_stage_runners()) == {"domain_intel", "scenario_gen", "execution", "evaluation"}


def test_missing_spec_is_not_retryable(tmp_path):
    with pytest.raises(StageError) as exc:
        runners.run_domain_intel(_input("domain_intel", {"target_system": str(tmp_path)}))
    assert exc.value.retryable is False


def test_domain_intel_decline_is_not_retryable(monkeypatch):
    class Declined:
        declined = type("D", (), {"detail": "not a domain task"})()

    monkeypatch.setattr(
        "agents.a02_domain_intelligence.agent.build_domain_model", lambda **_: Declined()
    )
    with pytest.raises(StageError) as exc:
        runners.run_domain_intel(_input("domain_intel", {"target_system": WALLET}))
    assert exc.value.retryable is False


def test_invalid_model_output_is_retryable(monkeypatch):
    def boom(**_):
        raise StructuredCallError("no valid output", [], Usage(model="m"))

    monkeypatch.setattr("agents.a02_domain_intelligence.agent.build_domain_model", boom)
    with pytest.raises(StageError) as exc:
        runners.run_domain_intel(_input("domain_intel", {"target_system": WALLET}))
    assert exc.value.retryable is True


def test_scenario_gen_receives_the_domain_model(monkeypatch, tmp_path):
    dm = {"rules": [{"id": "r1"}], "gaps": [], "workflows": []}
    ref = tmp_path / "domain_intel.json"
    ref.write_text(json.dumps(dm), encoding="utf-8")
    seen = {}

    def fake(**kw):
        seen.update(kw)
        raise StructuredCallError("stop here", [], Usage(model="m"))

    monkeypatch.setattr("agents.a03_scenario_generator.agent.generate_scenario_set", fake)
    with pytest.raises(StageError):
        runners.run_scenario_gen(
            _input("scenario_gen", {"target_system": WALLET}, {"domain_intel": str(ref)})
        )
    assert seen["domain_model"] == dm


def test_generator_prompt_with_and_without_domain_model():
    digest = read_openapi(f"{WALLET}/openapi.json")
    assert "<domain_model>" not in build_user_prompt(digest, WALLET)  # M1 baseline unchanged
    dm = {"rules": [{"id": "no_negative_balance"}], "gaps": [], "workflows": [], "entities": [1]}
    prompt = build_user_prompt(digest, WALLET, domain_model=dm)
    assert "no_negative_balance" in prompt
    assert '"entities"' not in prompt  # only rules, gaps, workflows are passed on


def test_unreachable_target_is_retryable(monkeypatch, tmp_path):
    from tests.test_schema import make_scenario

    ref = tmp_path / "scenario_gen.json"
    ref.write_text(
        json.dumps({"scenario_set": {"scenarios": [make_scenario()]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("ecvt.evals.target_reachable", lambda *_a, **_k: False)
    with pytest.raises(StageError) as exc:
        runners.run_execution(_input("execution", upstream={"scenario_gen": str(ref)}))
    assert exc.value.retryable is True


def test_execution_without_scenarios_is_not_retryable():
    with pytest.raises(StageError) as exc:
        runners.run_execution(_input("execution"))
    assert exc.value.retryable is False


def test_manager_run_exports_the_m1_file_set(tmp_path):
    """`ecvt run --manager` writes the same files as the M1 chain, so the report works."""
    from agents.a01_manager.agent import STAGES, StateStore
    from ecvt.cli import export_run_files
    from ecvt.results import ExecutionRecord, RunOutcome
    from ecvt.schema import Scenario, ScenarioSet
    from ecvt.scoring import score_with_rules
    from tests.test_schema import make_scenario

    scenario = Scenario.model_validate(make_scenario())
    record = ExecutionRecord(scenario_id=scenario.id, category=scenario.category.value,
                             base_url="http://x", outcome=RunOutcome.COMPLETED)
    score = score_with_rules(scenario, record)
    store = StateStore(tmp_path)
    state = store.create("run_x", "x", "cli")
    state["intent"] = {"name": "full_validation", "slots": {"target_system": WALLET}}
    state["plan"] = list(STAGES)
    artifacts = {
        "domain_intel": {"rules": []},
        "scenario_gen": {"scenario_set": ScenarioSet(target=WALLET, spec_title="t",
                                                     scenarios=[scenario]).model_dump(mode="json")},
        "execution": {"scenarios": [], "records": [record.model_dump(mode="json")]},
        "evaluation": {"scores": [score.model_dump(mode="json")], "stats": {}},
    }
    for stage, payload in artifacts.items():
        state["stages"][stage].update(status="succeeded",
                                      artifact_ref=store.write_artifact("run_x", stage, payload),
                                      usage={"total_tokens": 10, "cost_usd": 0.01})
    state["status"] = "completed"
    run_dir = export_run_files(state, tmp_path)
    for name in ("scenarios.jsonl", "executions.jsonl", "scores.jsonl", "run.json"):
        assert (run_dir / name).is_file(), name
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["via"] == "manager" and meta["usage"]["total_tokens"] == 40


def test_stage_failure_carries_the_validator_error(monkeypatch):
    """The reason, not just the count.

    StructuredCallError's message says only "produced no valid output in N
    attempts"; the validator errors live in `.attempts`. Dropping them left
    state.json recording a failure with nothing to act on, and diagnosing one
    live scenario_gen failure cost a second live run.
    """
    boom = StructuredCallError(
        "scenario_generator_output produced no valid output in 4 attempts",
        attempts=["attempt 4: 20 validation errors for ScenarioSetDraft\nscenarios.0.repeat"],
        usage=Usage(model="m"),
    )

    def explode(**_kwargs):
        raise boom

    monkeypatch.setattr(runners, "_spec_path", lambda target: target)
    monkeypatch.setattr(
        "agents.a03_scenario_generator.agent.generate_scenario_set", explode
    )
    with pytest.raises(StageError) as exc:
        runners.run_scenario_gen(_input("scenario_gen", {"target_system": WALLET}))

    message = str(exc.value)
    assert "no valid output in 4 attempts" in message
    assert "scenarios.0.repeat" in message  # the rule that was broken


def test_carried_validator_error_is_bounded(monkeypatch):
    """A 20-error Pydantic dump should not land in state.json whole."""
    boom = StructuredCallError(
        "no valid output", attempts=["x" * 5000], usage=Usage(model="m")
    )

    def explode(**_kwargs):
        raise boom

    monkeypatch.setattr(runners, "_spec_path", lambda target: target)
    monkeypatch.setattr(
        "agents.a03_scenario_generator.agent.generate_scenario_set", explode
    )
    with pytest.raises(StageError) as exc:
        runners.run_scenario_gen(_input("scenario_gen", {"target_system": WALLET}))

    assert len(str(exc.value)) < runners.LAST_ERROR_CHARS + 200
