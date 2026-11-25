"""ecvt-eval MCP server (M3-T3, ADR 0014) — offline, no model call."""

from __future__ import annotations

import asyncio
import sys

import pytest
from mcp import Client, StdioServerParameters

from agents.a06_evaluation import agent as a06
from ecvt.eval_server import build_server
from ecvt.scoring import DimensionScore, run_rules
from plugins.wallet.rules import WALLET_RULES
from tests.test_scoring import _probe, _wallet_case, _xfer

CASES = {
    "correct": (
        [_xfer("1", "2", 10.0, after=(90.0, 10.0))],
        [_probe("1", 90.0), _probe("2", 10.0)],
    ),
    "self_transfer": ([_xfer("1", "1", 10.0, after=(100.0, 100.0))], []),
    "overdraft": ([_xfer("1", "2", 150.0, after=(-50.0, 150.0))], [_probe("1", -50.0)]),
    "server_error": ([_xfer("1", "2", 10.0, status=500)], []),
}


def _call(tool, args, server=None):
    async def go():
        async with Client(server or build_server()) as client:
            return await client.call_tool(tool, args)

    return asyncio.run(go())


def _args(scenario, record, plugin):
    return {
        "scenario": scenario.model_dump(mode="json"),
        "execution": record.model_dump(mode="json"),
        "plugin": plugin,
    }


def test_server_publishes_both_tools_with_schemas():
    async def go():
        async with Client(build_server()) as client:
            return (await client.list_tools()).tools

    tools = {t.name: t for t in asyncio.run(go())}
    assert set(tools) == {"score_response", "list_rules"}
    assert {"scenario", "execution", "plugin"} <= set(
        tools["score_response"].input_schema["properties"]
    )
    assert "verdict" in tools["score_response"].output_schema["properties"]


@pytest.mark.parametrize("plugin", ["rest_api", "wallet"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_mcp_score_equals_in_process_rules(case, plugin):
    """The move behind MCP changes nothing: same verdict, score and checks."""
    scenario, record = _wallet_case(*CASES[case])
    result = _call("score_response", _args(scenario, record, plugin))
    assert not result.is_error
    remote = DimensionScore.model_validate(result.structured_content)
    local = run_rules(scenario, record, WALLET_RULES if plugin == "wallet" else ())
    assert remote == local


def test_list_rules_names_generic_and_plugin_rules():
    rest = _call("list_rules", {"plugin": "rest_api"}).structured_content
    wallet = _call("list_rules", {"plugin": "wallet"}).structured_content
    assert {r["source"] for r in rest["rules"]} == {"generic"}
    assert len(wallet["rules"]) == len(rest["rules"]) + len(WALLET_RULES)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda a: a.update(plugin="ikys"), "no implementation"),
        (lambda a: a["execution"].update(scenario_id="other"), "execution is for"),
        (lambda a: a["scenario"].pop("action"), "invalid input"),
    ],
)
def test_bad_input_is_a_tool_error_not_a_crash(mutate, message):
    scenario, record = _wallet_case(*CASES["correct"])
    args = _args(scenario, record, "rest_api")
    mutate(args)
    result = _call("score_response", args)
    assert result.is_error
    assert message in " ".join(c.text for c in result.content)


def test_a06_refusal_surfaces_as_eval_server_error():
    scenario, record = _wallet_case(*CASES["correct"])
    with pytest.raises(a06.EvalServerError, match="no implementation"):
        a06.score_scenarios([scenario], [record], use_judge=False, plugin="ikys")


def test_no_silent_local_fallback_when_the_server_cannot_start():
    """A06 has no in-process scoring path: no server, no scores."""
    scenario, record = _wallet_case(*CASES["correct"])
    dead = StdioServerParameters(command=sys.executable, args=["-c", "import sys; sys.exit(1)"])
    with pytest.raises(a06.EvalServerError, match="unavailable"):
        a06.score_scenarios([scenario], [record], use_judge=False, server=dead)


def test_scoring_over_real_stdio_subprocess():
    """Production's path: `python -m ecvt.eval_server` started by a06."""
    scenario, record = _wallet_case(*CASES["self_transfer"])
    scores, _ = a06.score_scenarios(
        [scenario], [record], use_judge=False, plugin="wallet", server=a06._stdio_eval_server()
    )
    assert scores[0].defect_suspected and "itself" in scores[0].summary


def test_manager_evaluation_stage_maps_a_refusal_to_a_final_stage_error(tmp_path, monkeypatch):
    import json

    from agents.a01_manager import runners
    from agents.a01_manager.agent import StageError

    scenario, record = _wallet_case(*CASES["correct"])
    ref = tmp_path / "execution.json"
    ref.write_text(
        json.dumps(
            {
                "scenarios": [scenario.model_dump(mode="json")],
                "records": [record.model_dump(mode="json")],
            }
        ),
        encoding="utf-8",
    )

    def refuse(*_a, **_k):
        raise a06.EvalServerError("ecvt-eval unavailable: test")

    monkeypatch.setattr(a06, "rule_scores", refuse)
    stage = {"run_id": "r", "stage": "evaluation", "slots": {}, "upstream": {"execution": str(ref)}}
    with pytest.raises(StageError) as exc:
        runners.run_evaluation(stage)
    assert exc.value.retryable is False and "ecvt-eval" in str(exc.value)
