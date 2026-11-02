"""Evaluation judge tests — offline, with a faked Messages client (M1-T5)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agents.a06_evaluation import agent as ev
from ecvt.execution import RunnerConfig, run_scenario
from ecvt.scoring import Dimension, Verdict
from tests.test_scenario_generator import FakeClient, FakeText, FakeToolUse
from tests.test_scoring import PROBES, build, transfer

BASE = "http://wallet.test"


@pytest.fixture
def config() -> RunnerConfig:
    return RunnerConfig(base_url=BASE, timeout_s=5.0, max_transport_retries=0, backoff_s=0.0)


@pytest.fixture
async def client(wallet_app: Any):
    transport = httpx.ASGITransport(app=wallet_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


def verdict_payload(
    verdict: str = "fail",
    rationale: str = "the source balance went negative",
    invariants: list[dict[str, str]] | None = None,
) -> FakeToolUse:
    return FakeToolUse(
        name=ev.TOOL_NAME,
        input={
            "verdict": verdict,
            "rationale": rationale,
            "invariants": invariants
            if invariants is not None
            else [
                {
                    "invariant": "source balance is still 50.0",
                    "verdict": "fail",
                    "evidence": "read_src reports -450.0",
                }
            ],
        },
    )


# --- tool schema and prompt ------------------------------------------------


def test_tool_schema_from_pydantic():
    tool = ev.build_tool()
    assert tool["name"] == ev.TOOL_NAME
    schema = tool["input_schema"]
    assert set(schema["required"]) == {"verdict", "rationale"}
    assert "invariants" in schema["properties"]


def test_committed_tools_json_matches_generated_schema():
    from pathlib import Path

    committed = json.loads(
        Path("agents/a06_evaluation/tools.json").read_text(encoding="utf-8")
    )
    assert committed == [ev.build_tool()], (
        "tools.json is stale — regenerate it from JudgeOutput.model_json_schema()"
    )


def test_skill_within_page_limit():
    from pathlib import Path

    lines = Path("agents/a06_evaluation/SKILL.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 240, f"SKILL.md is {len(lines)} lines (max 3 pages)"


async def test_observation_carries_numbers_and_no_ground_truth(client, config):
    scenario = build(
        "sc-overdraft",
        "edge",
        transfer(500.0),
        [400, 422],
        invariants=["source balance is still 50.0"],
    )
    record = await run_scenario(scenario, client, config)
    prompt = ev.build_observation(scenario, record)

    assert "source balance is still 50.0" in prompt
    assert "-450" in prompt, "the judge must see the figure it has to weigh"
    assert '"expected_status": [' in prompt
    assert "read_src" in prompt

    # ADR 0007: the judge is never handed the defect list.
    for marker in ("GT-0", "ground_truth", "deliberate"):
        assert marker not in prompt


async def test_observation_trims_long_bodies(client, config):
    scenario = build("sc-1", "happy", transfer(10.0), [200])
    record = await run_scenario(scenario, client, config)
    record.actions[0].response_text = "x" * 5000
    record.actions[0].response_json = None

    prompt = ev.build_observation(scenario, record)
    assert "…" in prompt
    assert len(prompt) < 5000


def test_system_prompt_within_page_limit():
    lines = ev.load_system_prompt().splitlines()
    assert len(lines) <= 160, f"system_prompt.md is {len(lines)} lines"


# --- judging ---------------------------------------------------------------


async def test_judge_fail_overrides_rule_pass(client, config):
    """Status was as expected, but the judge reads the balances and objects."""
    scenario = build(
        "sc-sneaky", "happy", transfer(10.0), [200], invariants=["total is unchanged"]
    )
    record = await run_scenario(scenario, client, config)
    fake = FakeClient([verdict_payload(rationale="the totals no longer add up")])

    scores, usage = ev.score_scenarios([scenario], [record], client=fake)
    score = scores[0]

    assert score.verdict is Verdict.FAIL
    assert score.defect_suspected is True
    assert score.summary == "the totals no longer add up"
    assert score.dimension(Dimension.RULE).verdict is Verdict.PASS
    assert score.dimension(Dimension.JUDGE).model == "claude-haiku-5-5"
    assert usage.calls == 1


async def test_judge_not_called_when_rules_suffice(client, config):
    scenario = build("sc-clean", "happy", transfer(10.0), [200])
    record = await run_scenario(scenario, client, config)
    fake = FakeClient([])  # any call would raise IndexError

    scores, usage = ev.score_scenarios([scenario], [record], client=fake)

    assert scores[0].verdict is Verdict.PASS
    assert scores[0].dimension(Dimension.JUDGE) is None
    assert usage.calls == 0
    assert fake.messages.calls == []


async def test_judge_is_not_told_the_rule_verdict(client, config):
    """Independent second opinion: no anchoring on what the rules concluded."""
    scenario = build("sc-overdraft", "edge", transfer(500.0), [422])
    record = await run_scenario(scenario, client, config)
    fake = FakeClient([verdict_payload()])

    ev.score_scenarios([scenario], [record], client=fake)
    sent = fake.messages.calls[0]["messages"][0]["content"]

    for leak in ("status_expected", "no_server_error", "rule", "verdict\": \"fail"):
        assert leak not in sent


async def test_invariants_become_checks(client, config):
    scenario = build(
        "sc-inv",
        "edge",
        transfer(500.0),
        [422],
        invariants=["source balance is still 50.0", "target balance is still 0.0"],
    )
    record = await run_scenario(scenario, client, config)
    fake = FakeClient(
        [
            verdict_payload(
                invariants=[
                    {
                        "invariant": "source balance is still 50.0",
                        "verdict": "fail",
                        "evidence": "read_src reports -450.0",
                    },
                    {
                        "invariant": "target balance is still 0.0",
                        "verdict": "inconclusive",
                        "evidence": "no probe read it",
                    },
                ]
            )
        ]
    )

    scores, _ = ev.score_scenarios([scenario], [record], client=fake)
    judge = scores[0].dimension(Dimension.JUDGE)

    assert [c.verdict for c in judge.checks] == [Verdict.FAIL, Verdict.INCONCLUSIVE]
    assert judge.checks[0].observed == "read_src reports -450.0"


async def test_judge_failure_degrades_to_inconclusive(client, config):
    """A judge that never answers must not sink the whole run (ADR 0006)."""
    scenario = build(
        "sc-inv", "happy", transfer(10.0), [200], invariants=["total is unchanged"]
    )
    record = await run_scenario(scenario, client, config)
    fake = FakeClient([FakeText(text="I think it is fine")] * 3)

    scores, usage = ev.score_scenarios([scenario], [record], client=fake)
    judge = scores[0].dimension(Dimension.JUDGE)

    assert judge.verdict is Verdict.INCONCLUSIVE
    assert "no usable verdict" in judge.rationale
    assert scores[0].verdict is Verdict.INCONCLUSIVE
    assert scores[0].defect_suspected is False
    assert usage.calls == 3, "a failed judge still costs tokens, so it is still logged"


async def test_judge_rejects_unknown_fields(client, config):
    """extra=forbid, so field drift is retried rather than silently accepted."""
    scenario = build("sc-x", "edge", transfer(500.0), [422])
    record = await run_scenario(scenario, client, config)
    bad = FakeToolUse(
        name=ev.TOOL_NAME,
        input={"verdict": "fail", "rationale": "r", "confidence": 0.9},
    )
    fake = FakeClient([bad, verdict_payload()])

    scores, _ = ev.score_scenarios([scenario], [record], client=fake)
    assert scores[0].dimension(Dimension.JUDGE).verdict is Verdict.FAIL
    assert len(fake.messages.calls) == 2


async def test_no_judge_mode_makes_no_calls(client, config):
    scenario = build(
        "sc-inv", "edge", transfer(500.0), [422], invariants=["source is still 50.0"]
    )
    record = await run_scenario(scenario, client, config)
    fake = FakeClient([])

    scores, usage = ev.score_scenarios([scenario], [record], client=fake, use_judge=False)
    assert scores[0].verdict is Verdict.FAIL  # rules alone still catch it
    assert scores[0].dimension(Dimension.JUDGE) is None
    assert usage.calls == 0


async def test_scenario_without_execution_is_skipped(client, config):
    scenario = build("sc-a", "happy", transfer(10.0), [200])
    other = build("sc-b", "happy", transfer(10.0), [200])
    record = await run_scenario(scenario, client, config)

    scores, _ = ev.score_scenarios([scenario, other], [record], client=FakeClient([]))
    assert [s.scenario_id for s in scores] == ["sc-a"]


# --- usage accounting ------------------------------------------------------


async def test_usage_accumulates_across_scenarios(client, config):
    scenarios = [
        build(f"sc-{i}", "edge", transfer(500.0), [422], probes=PROBES) for i in range(3)
    ]
    records = [await run_scenario(s, client, config) for s in scenarios]
    fake = FakeClient([verdict_payload() for _ in scenarios])

    scores, usage = ev.score_scenarios(scenarios, records, client=fake)

    assert len(scores) == 3
    assert usage.calls == 3
    assert usage.input_tokens == 3 * 1200
    assert usage.cost_usd() is not None


# --- CLI -------------------------------------------------------------------


async def test_cli_no_judge_end_to_end(client, config, tmp_path, monkeypatch):
    from ecvt.results import write_executions_jsonl
    from ecvt.schema import ScenarioSet, write_scenarios_jsonl

    scenario = build("sc-overdraft", "edge", transfer(500.0), [422])
    record = await run_scenario(scenario, client, config)

    scenarios_path = tmp_path / "scenarios.jsonl"
    write_scenarios_jsonl(
        scenarios_path,
        ScenarioSet(target="t", spec_title="Wallet API", scenarios=[scenario]),
    )
    exec_path = write_executions_jsonl(tmp_path / "executions.jsonl", [record])
    out_path = tmp_path / "scores.jsonl"

    code = ev.main(
        [
            "--scenarios",
            str(scenarios_path),
            "--executions",
            str(exec_path),
            "--out",
            str(out_path),
            "--no-judge",
        ]
    )
    assert code == 0

    written = [json.loads(line) for line in out_path.read_text().strip().splitlines()]
    assert written[0]["verdict"] == "fail"
    assert written[0]["defect_suspected"] is True


def test_cli_reports_missing_input(tmp_path, capsys):
    assert ev.main(["--scenarios", str(tmp_path / "nope.jsonl"), "--no-judge"]) == 1
    assert "Could not read inputs" in capsys.readouterr().err
