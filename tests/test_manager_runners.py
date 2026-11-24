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
