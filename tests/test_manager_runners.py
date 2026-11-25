"""Stage runners — the Manager's adapters to a02/a03/a05/a06 (offline, no model call)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agents.a01_manager import agent as mgr
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


def test_scenario_gen_withholds_the_domain_model_unless_asked(monkeypatch, tmp_path):
    """ADR 0012, option B: a02 still runs and its output is upstream, but a03 is
    not handed it unless ECVT_A03_DOMAIN_MODEL=on — M2-T2 kept for measurement."""
    dm = {"rules": [{"id": "r1"}], "gaps": [], "workflows": []}
    ref = tmp_path / "domain_intel.json"
    ref.write_text(json.dumps(dm), encoding="utf-8")
    seen = {}

    def fake(**kw):
        seen.update(kw)
        raise StructuredCallError("stop here", [], Usage(model="m"))

    monkeypatch.setattr("agents.a03_scenario_generator.agent.generate_scenario_set", fake)
    stage = _input("scenario_gen", {"target_system": WALLET}, {"domain_intel": str(ref)})

    monkeypatch.delenv(runners.A03_DOMAIN_MODEL_ENV, raising=False)
    with pytest.raises(StageError):
        runners.run_scenario_gen(stage)
    assert seen["domain_model"] is None

    monkeypatch.setenv(runners.A03_DOMAIN_MODEL_ENV, "on")
    with pytest.raises(StageError):
        runners.run_scenario_gen(stage)
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
    record = ExecutionRecord(
        scenario_id=scenario.id,
        category=scenario.category.value,
        base_url="http://x",
        outcome=RunOutcome.COMPLETED,
    )
    score = score_with_rules(scenario, record)
    store = StateStore(tmp_path)
    state = store.create("run_x", "x", "cli")
    state["intent"] = {"name": "full_validation", "slots": {"target_system": WALLET}}
    state["plan"] = list(STAGES)
    artifacts = {
        "domain_intel": {"rules": []},
        "scenario_gen": {
            "scenario_set": ScenarioSet(
                target=WALLET, spec_title="t", scenarios=[scenario]
            ).model_dump(mode="json")
        },
        "execution": {"scenarios": [], "records": [record.model_dump(mode="json")]},
        "evaluation": {"scores": [score.model_dump(mode="json")], "stats": {}},
    }
    for stage, payload in artifacts.items():
        state["stages"][stage].update(
            status="succeeded",
            artifact_ref=store.write_artifact("run_x", stage, payload),
            usage={"total_tokens": 10, "cost_usd": 0.01},
        )
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
    monkeypatch.setattr("agents.a03_scenario_generator.agent.generate_scenario_set", explode)
    with pytest.raises(StageError) as exc:
        runners.run_scenario_gen(_input("scenario_gen", {"target_system": WALLET}))

    message = str(exc.value)
    assert "no valid output in 4 attempts" in message
    assert "scenarios.0.repeat" in message  # the rule that was broken


def test_carried_validator_error_is_bounded(monkeypatch):
    """A 20-error Pydantic dump should not land in state.json whole."""
    boom = StructuredCallError("no valid output", attempts=["x" * 5000], usage=Usage(model="m"))

    def explode(**_kwargs):
        raise boom

    monkeypatch.setattr(runners, "_spec_path", lambda target: target)
    monkeypatch.setattr("agents.a03_scenario_generator.agent.generate_scenario_set", explode)
    with pytest.raises(StageError) as exc:
        runners.run_scenario_gen(_input("scenario_gen", {"target_system": WALLET}))

    assert len(str(exc.value)) < runners.LAST_ERROR_CHARS + 200


# --- a cancelled stage must not leave the record 'running' -----------------


def _stage_handler(server_tools, stage: str):
    name = mgr.mcp_name(f"run_{stage}").split("__")[-1]
    return next(t.handler for t in server_tools if t.name.endswith(name))


@pytest.mark.asyncio
async def test_cancelled_stage_is_closed_as_retryable(tmp_path):
    """A tool-call timeout cancels the awaiting coroutine. CancelledError is a
    BaseException, so `except Exception` never saw it and the record stayed
    status="running" — precondition() then answered the retry with
    "already_running" and the run died on a stage tried once."""

    def cancel(_stage_input):
        raise asyncio.CancelledError

    store = mgr.StateStore(tmp_path)
    state = store.create("run_c", "x", "cli")
    state["intent"] = {"name": "full_validation", "slots": {"target_system": WALLET}}
    state["plan"] = list(mgr.STAGES)
    store.save(state)

    tools = mgr.build_tools(store, dict.fromkeys(mgr.STAGES, cancel), ["rest_api"])
    with pytest.raises(asyncio.CancelledError):
        await _stage_handler(tools, "domain_intel")({"run_id": "run_c"})

    rec = store.load("run_c")["stages"]["domain_intel"]
    assert rec["status"] == "failed"  # not "running"
    assert rec["error"]["retryable"] is True  # the Manager may try again
    assert "cancelled" in rec["error"]["message"]
    assert mgr.precondition(store.load("run_c"), "domain_intel") is None


def test_stage_tool_timeout_is_raised_above_the_cli_default():
    """The CLI default is 60s; a stage is a whole subagent run."""
    options = mgr.build_options(mgr.build_server(mgr.StateStore(Path("/tmp")), {}, []))
    assert options.env["MCP_TOOL_TIMEOUT"] == str(mgr.STAGE_TOOL_TIMEOUT_MS)
    assert mgr.STAGE_TOOL_TIMEOUT_MS > 60_000


# --- Görev 3: context hygiene and per-model accounting ---------------------


def test_manager_loads_no_project_settings():
    """`setting_sources=["project"]` pulled the repository's CLAUDE.md files into
    the Manager's context: development-coach rules written for whoever is building
    ECVT, "Turkish responses" among them. They cost tokens every turn and
    contradict the language the Manager's own prompt sets."""
    options = mgr.build_options(mgr.build_server(mgr.StateStore(Path("/tmp")), {}, []))
    assert options.setting_sources == []


def test_model_usage_is_reduced_to_reportable_rows():
    """One row per model, so orchestration spend can be read apart from the work."""

    class Usage:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    result = Usage(
        model_usage={
            "claude-sonnet-5-5": Usage(input_tokens=100, output_tokens=20, cost_usd=0.5),
            "claude-haiku-5-5": {"input_tokens": 9, "output_tokens": 1, "cost_usd": 0.001},
            "noise": Usage(something_else=1),  # nothing reportable
        }
    )

    rows = mgr._model_usage(result)
    assert set(rows) == {"claude-sonnet-5-5", "claude-haiku-5-5"}  # "noise" dropped
    assert rows["claude-sonnet-5-5"]["cost_usd"] == 0.5
    assert rows["claude-haiku-5-5"]["input_tokens"] == 9


def test_model_usage_survives_a_message_without_it():
    assert mgr._model_usage(object()) == {}


def test_run_json_carries_per_model_usage(tmp_path):
    """`manager_model_usage` reaches run.json beside the total it breaks down."""
    from ecvt.cli import export_run_files

    store = mgr.StateStore(tmp_path)
    state = store.create("run_mu", "x", "cli")
    state["intent"] = {"name": "domain_analysis", "slots": {"target_system": WALLET}}
    state["plan"] = ["domain_intel"]
    state["stages"]["domain_intel"].update(status="succeeded", usage={"cost_usd": 0.01})
    state["manager_session"] = {
        "total_cost_usd": 0.5,
        "num_turns": 3,
        "model_usage": {"claude-sonnet-5-5": {"cost_usd": 0.5, "input_tokens": 100}},
    }
    state["status"] = "completed"

    meta = json.loads((export_run_files(state, tmp_path) / "run.json").read_text("utf-8"))
    assert meta["usage"]["manager_cost_usd"] == 0.5  # the existing field is unchanged
    assert meta["usage"]["manager_model_usage"]["claude-sonnet-5-5"]["cost_usd"] == 0.5


# --- ADR 0012: the DomainModel can be withheld from a03 --------------------


def test_a03_does_not_get_the_domain_model_by_default(monkeypatch):
    """ADR 0012, option B: no measured benefit, one measured harm."""
    monkeypatch.delenv(runners.A03_DOMAIN_MODEL_ENV, raising=False)
    assert runners.a03_gets_domain_model() is False


def test_domain_model_can_be_withheld_from_a03(monkeypatch, tmp_path):
    """`off` keeps the Manager run as it is and only stops a02's output reaching
    a03 — the comparison ADR 0012 measures."""
    dm = tmp_path / "domain_intel.json"
    dm.write_text('{"rules": []}', encoding="utf-8")
    seen = {}

    def capture(**kwargs):
        seen["domain_model"] = kwargs["domain_model"]
        raise StructuredCallError("stop here", attempts=["x"], usage=Usage(model="m"))

    monkeypatch.setattr(runners, "_spec_path", lambda target: target)
    monkeypatch.setattr("agents.a03_scenario_generator.agent.generate_scenario_set", capture)
    stage = _input("scenario_gen", {"target_system": WALLET}, {"domain_intel": str(dm)})

    for value, expected in (("on", {"rules": []}), ("off", None)):
        monkeypatch.setenv(runners.A03_DOMAIN_MODEL_ENV, value)
        with pytest.raises(StageError):
            runners.run_scenario_gen(stage)
        assert seen["domain_model"] == expected, value


def test_a03_domain_model_setting_is_checked(monkeypatch):
    monkeypatch.setenv(runners.A03_DOMAIN_MODEL_ENV, "maybe")
    with pytest.raises(ValueError, match="'on' or 'off'"):
        runners.a03_gets_domain_model()


def test_run_json_records_the_adr_0012_arm(monkeypatch, tmp_path):
    from ecvt.cli import export_run_files

    store = mgr.StateStore(tmp_path)
    state = store.create("run_arm", "x", "cli")
    state["intent"] = {"name": "domain_analysis", "slots": {"target_system": WALLET}}
    state["plan"] = ["domain_intel"]
    state["stages"]["domain_intel"].update(status="succeeded", usage={})
    state["status"] = "completed"

    monkeypatch.setenv(runners.A03_DOMAIN_MODEL_ENV, "off")
    meta = json.loads((export_run_files(state, tmp_path) / "run.json").read_text("utf-8"))
    assert meta["a03_domain_model"] == "off"


def test_execution_stage_uses_the_plugin_slot(monkeypatch, tmp_path):
    """A stub plugin is a non-retryable stage failure, never a REST fallback."""
    from tests.test_schema import make_scenario

    ref = tmp_path / "scenario_gen.json"
    ref.write_text(json.dumps({"scenario_set": {"scenarios": [make_scenario()]}}), encoding="utf-8")
    monkeypatch.setattr("ecvt.evals.target_reachable", lambda *_a, **_k: True)
    with pytest.raises(StageError) as exc:
        runners.run_execution(_input("execution", {"plugin": "ikys"}, {"scenario_gen": str(ref)}))
    assert exc.value.retryable is False
    assert "no implementation" in str(exc.value)
