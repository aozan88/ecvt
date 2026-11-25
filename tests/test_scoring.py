"""Evaluation rule engine tests (M1-T5).

Scenarios are run against the real wallet-api, then scored, so the rule engine
is exercised on genuine observations of the injected bugs rather than
hand-written records.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ecvt.execution import RunnerConfig, run_scenario
from ecvt.results import ExecutionRecord, RunOutcome
from ecvt.schema import Scenario
from ecvt.scoring import (
    Dimension,
    DimensionScore,
    Verdict,
    combine,
    needs_judge,
    read_scores_jsonl,
    run_rules,
    score_with_rules,
    summarize,
    write_scores_jsonl,
)

BASE = "http://wallet.test"
PROBES = [
    {"name": "read_src", "method": "GET", "path": "/accounts/{src}/balance"},
    {"name": "read_dst", "method": "GET", "path": "/accounts/{dst}/balance"},
]


@pytest.fixture
def config() -> RunnerConfig:
    return RunnerConfig(base_url=BASE, timeout_s=5.0, max_transport_retries=0, backoff_s=0.0)


@pytest.fixture
async def client(wallet_app: Any):
    transport = httpx.ASGITransport(app=wallet_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


def build(
    sid: str,
    category: str,
    action: dict[str, Any],
    expected_status: list[int],
    *,
    invariants: list[str] | None = None,
    probes: list[dict[str, Any]] | None = PROBES,
    setup: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> Scenario:
    data: dict[str, Any] = {
        "id": sid,
        "title": sid,
        "category": category,
        "rationale": "test",
        "setup": setup
        if setup is not None
        else [
            {
                "name": "mk_src",
                "method": "POST",
                "path": "/accounts",
                "body": {"owner": "alice", "initial_balance": 50.0},
                "save": {"src": "id"},
            },
            {
                "name": "mk_dst",
                "method": "POST",
                "path": "/accounts",
                "body": {"owner": "bob", "initial_balance": 0.0},
                "save": {"dst": "id"},
            },
        ],
        "action": action,
        "expectation": {
            "expected_status": expected_status,
            "expected_behavior": "as a correct system would",
            "invariants": invariants or [],
            "probes": probes or [],
        },
    }
    data.update(extra)
    return Scenario.model_validate(data)


def transfer(amount: float, **kw: Any) -> dict[str, Any]:
    body = {"from_account": "{src}", "to_account": "{dst}", "amount": amount}
    return {"name": "t", "method": "POST", "path": "/transfers", "body": body, **kw}


# --- rules catching real bugs ---------------------------------------------


async def test_happy_path_passes(client, config):
    scenario = build("sc-happy", "happy", transfer(10.0), [200])
    score = score_with_rules(scenario, await run_scenario(scenario, client, config))

    assert score.verdict is Verdict.PASS
    assert score.defect_suspected is False
    assert score.summary == "behaved as a correct implementation should"
    assert score.dimension(Dimension.RULE).score == 1.0
    assert score.latency_ms_max is not None


async def test_overdraft_flagged_by_status_rule(client, config):
    """GT-03: a correct API rejects it; the target returns 200."""
    scenario = build("sc-overdraft", "edge", transfer(500.0), [400, 409, 422])
    score = score_with_rules(scenario, await run_scenario(scenario, client, config))

    assert score.verdict is Verdict.FAIL
    assert score.defect_suspected is True
    check = next(c for c in score.dimension(Dimension.RULE).checks if c.name == "status_expected")
    assert check.verdict is Verdict.FAIL
    assert "200" in check.observed
    assert "should not" in check.detail


async def test_missing_account_500_flagged_twice(client, config):
    """GT-07: both the status rule and the 5xx rule fire."""
    scenario = build(
        "sc-404",
        "negative",
        {"name": "t", "method": "GET", "path": "/accounts/nope/balance"},
        [404],
        probes=[],
        setup=[],
    )
    score = score_with_rules(scenario, await run_scenario(scenario, client, config))

    names = {c.name: c for c in score.dimension(Dimension.RULE).checks}
    assert names["status_expected"].verdict is Verdict.FAIL
    assert names["no_server_error"].verdict is Verdict.FAIL
    assert "server error" in names["no_server_error"].detail
    assert score.verdict is Verdict.FAIL


async def test_server_error_fails_even_when_expected(client, config):
    """A scenario that expects 500 is still a defect: no API should 500."""
    scenario = build(
        "sc-500-expected",
        "negative",
        {"name": "t", "method": "GET", "path": "/accounts/nope/balance"},
        [500],
        probes=[],
        setup=[],
    )
    score = score_with_rules(scenario, await run_scenario(scenario, client, config))

    rule = score.dimension(Dimension.RULE)
    assert next(c for c in rule.checks if c.name == "status_expected").verdict is Verdict.PASS
    assert next(c for c in rule.checks if c.name == "no_server_error").verdict is Verdict.FAIL
    assert score.verdict is Verdict.FAIL


async def test_replay_all_accepted_is_inconclusive(client, config):
    """GT-05: rules see two accepted copies but cannot rule on idempotency."""
    scenario = build("sc-replay", "adversarial", transfer(10.0), [200, 409], repeat=2)
    score = score_with_rules(scenario, await run_scenario(scenario, client, config))

    check = next(c for c in score.dimension(Dimension.RULE).checks if c.name == "replay_rejected")
    assert check.verdict is Verdict.INCONCLUSIVE
    assert "idempotency" in check.detail
    assert score.verdict is Verdict.INCONCLUSIVE
    assert score.defect_suspected is False, "inconclusive is not an accusation"


async def test_replay_check_skipped_for_single_attempt(client, config):
    scenario = build("sc-once", "happy", transfer(10.0), [200])
    rule = run_rules(scenario, await run_scenario(scenario, client, config))
    assert all(c.name != "replay_rejected" for c in rule.checks)


async def test_replay_check_skipped_for_get(client, config):
    scenario = build(
        "sc-get-repeat",
        "stress",
        {"name": "t", "method": "GET", "path": "/accounts/{src}/balance"},
        [200],
        repeat=3,
    )
    rule = run_rules(scenario, await run_scenario(scenario, client, config))
    assert all(c.name != "replay_rejected" for c in rule.checks)


async def test_concurrency_counts_toward_replay_check(client, config):
    scenario = build("sc-race", "stress", transfer(5.0), [200, 409], concurrency=4)
    rule = run_rules(scenario, await run_scenario(scenario, client, config))
    check = next(c for c in rule.checks if c.name == "replay_rejected")
    assert check.verdict is Verdict.INCONCLUSIVE


async def test_setup_failure_is_not_run_not_fail(client, config):
    scenario = build(
        "sc-broken-setup",
        "edge",
        transfer(10.0),
        [200],
        setup=[
            {
                "name": "mk_src",
                "method": "POST",
                "path": "/accounts",
                "body": {"owner": "alice"},
                "save": {"src": "nope"},
            }
        ],
    )
    record = await run_scenario(scenario, client, config)
    score = score_with_rules(scenario, record)

    assert record.outcome is RunOutcome.SETUP_FAILED
    assert score.verdict is Verdict.NOT_RUN
    assert score.defect_suspected is False, "a broken scenario is not a target defect"
    assert "nope" in score.summary


async def test_probe_failure_flagged(client, config):
    """An unreadable probe means invariants cannot be checked at all."""
    scenario = build(
        "sc-bad-probe",
        "happy",
        transfer(10.0),
        [200],
        probes=[{"name": "read_missing", "method": "GET", "path": "/accounts/nope/balance"}],
    )
    score = score_with_rules(scenario, await run_scenario(scenario, client, config))
    check = next(c for c in score.dimension(Dimension.RULE).checks if c.name == "probes_readable")
    assert check.verdict is Verdict.FAIL
    assert "could not be read back" in check.detail


# --- judge gating ----------------------------------------------------------


async def test_judge_skipped_when_rules_pass_and_no_invariants(client, config):
    scenario = build("sc-clean", "happy", transfer(10.0), [200])
    rule = run_rules(scenario, await run_scenario(scenario, client, config))
    assert rule.verdict is Verdict.PASS
    assert needs_judge(scenario, rule) is False


async def test_judge_needed_for_invariants_even_when_rules_pass(client, config):
    scenario = build(
        "sc-invariant", "happy", transfer(10.0), [200], invariants=["total is unchanged"]
    )
    rule = run_rules(scenario, await run_scenario(scenario, client, config))
    assert rule.verdict is Verdict.PASS
    assert needs_judge(scenario, rule) is True


async def test_judge_needed_on_rule_failure(client, config):
    scenario = build("sc-fail", "edge", transfer(500.0), [422])
    rule = run_rules(scenario, await run_scenario(scenario, client, config))
    assert needs_judge(scenario, rule) is True


async def test_judge_skipped_when_nothing_was_observed(client, config):
    scenario = build(
        "sc-nothing",
        "edge",
        transfer(10.0),
        [200],
        invariants=["total is unchanged"],
        setup=[
            {
                "name": "mk_src",
                "method": "POST",
                "path": "/accounts",
                "body": {"owner": "a"},
                "save": {"src": "nope"},
            }
        ],
    )
    rule = run_rules(scenario, await run_scenario(scenario, client, config))
    assert rule.verdict is Verdict.NOT_RUN
    assert needs_judge(scenario, rule) is False, "no point paying for a judgement on nothing"


# --- combining dimensions --------------------------------------------------


def blank_record() -> ExecutionRecord:
    return ExecutionRecord(
        scenario_id="s", category="happy", base_url=BASE, outcome=RunOutcome.COMPLETED
    )


def test_judge_fail_carries_over_rule_pass():
    scenario = build("s", "happy", transfer(1.0), [200])
    dimensions = [
        DimensionScore(dimension=Dimension.RULE, verdict=Verdict.PASS, score=1.0),
        DimensionScore(
            dimension=Dimension.JUDGE,
            verdict=Verdict.FAIL,
            rationale="the balances do not add up",
            model="claude-haiku-5-5",
        ),
    ]
    score = combine(scenario, blank_record(), dimensions)
    assert score.verdict is Verdict.FAIL
    assert score.defect_suspected is True
    assert score.summary == "the balances do not add up"


def test_inconclusive_judge_does_not_overturn_rule_pass():
    scenario = build("s", "happy", transfer(1.0), [200])
    dimensions = [
        DimensionScore(dimension=Dimension.RULE, verdict=Verdict.PASS, score=1.0),
        DimensionScore(dimension=Dimension.JUDGE, verdict=Verdict.INCONCLUSIVE),
    ]
    assert combine(scenario, blank_record(), dimensions).verdict is Verdict.INCONCLUSIVE


def test_summary_prefers_the_concrete_failed_check():
    scenario = build("s", "edge", transfer(1.0), [422])
    dimensions = [
        DimensionScore(
            dimension=Dimension.RULE,
            verdict=Verdict.FAIL,
            checks=[
                {
                    "name": "status_expected",
                    "verdict": "fail",
                    "detail": "returned [200], which a correct implementation should not",
                }
            ],
        ),
        DimensionScore(dimension=Dimension.JUDGE, verdict=Verdict.FAIL, rationale="vaguer wording"),
    ]
    score = combine(scenario, blank_record(), dimensions)
    assert "returned [200]" in score.summary


# --- persistence and summary ----------------------------------------------


async def test_scores_jsonl_round_trip(client, config, tmp_path):
    scenario = build("sc-overdraft", "edge", transfer(500.0), [422])
    score = score_with_rules(scenario, await run_scenario(scenario, client, config))

    path = write_scores_jsonl(tmp_path / "scores.jsonl", [score])
    reread = read_scores_jsonl(path)
    assert len(reread) == 1
    assert reread[0].scenario_id == "sc-overdraft"
    assert reread[0].verdict is Verdict.FAIL
    assert reread[0].dimension(Dimension.RULE) is not None


def test_scores_jsonl_reports_bad_line(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad\.jsonl:1"):
        read_scores_jsonl(path)


def test_summarize_counts():
    def score(verdict: Verdict, judged: bool = False):
        dims = [DimensionScore(dimension=Dimension.RULE, verdict=verdict)]
        if judged:
            dims.append(DimensionScore(dimension=Dimension.JUDGE, verdict=verdict))
        return combine(build("s", "happy", transfer(1.0), [200]), blank_record(), dims)

    out = summarize([score(Verdict.PASS), score(Verdict.FAIL, judged=True), score(Verdict.FAIL)])
    assert out["scored"] == 3
    assert out["verdicts"]["fail"] == 2
    assert out["defects_suspected"] == 2
    assert out["judged"] == 1


# --- M3-T2: wallet domain rules, run by a06 beside its own ------------------

from ecvt.results import StepResult
from plugins.wallet import rules as wallet


def _step(name, endpoint, status, body=None, reply=None):
    return StepResult(
        name=name, endpoint=endpoint, status_code=status, request_body=body, response_json=reply
    )


def _account(acc_id, balance):
    return _step(
        f"create_{acc_id}",
        "POST /accounts",
        200,
        {"owner": acc_id, "initial_balance": balance},
        {"id": acc_id, "balance": balance},
    )


def _xfer(src, dst, amount, status=200, after=None):
    reply = (
        {"status": "ok"} if after is None else {"from_balance": after[0], "to_balance": after[1]}
    )
    return _step(
        "xfer",
        "POST /transfers",
        status,
        {"from_account": src, "to_account": dst, "amount": amount},
        reply if status < 300 else {"detail": "refused"},
    )


def _probe(acc_id, balance):
    return _step(
        f"read_{acc_id}",
        "GET /accounts/{x}/balance",
        200,
        None,
        {"account": acc_id, "balance": balance},
    )


def _wallet_case(actions, probes, setup=None, repeat=1, concurrency=1, amount=10.0):
    from tests.test_schema import make_scenario

    data = make_scenario(repeat=repeat, concurrency=concurrency)
    data["action"]["body"] = {"from_account": "{src_id}", "to_account": "{dst}", "amount": amount}
    data["expectation"]["expected_status"] = [200, 400, 422]  # isolate the wallet rules
    scenario = Scenario.model_validate(data)
    record = ExecutionRecord(
        scenario_id=scenario.id,
        category="edge",
        base_url="http://x",
        outcome=RunOutcome.COMPLETED,
        setup=setup if setup is not None else [_account("1", 100.0), _account("2", 0.0)],
        actions=actions,
        probes=probes,
    )
    return scenario, record


def _verdicts(scenario, record):
    return {
        c.name: c.verdict
        for c in run_rules(scenario, record, wallet.WALLET_RULES).checks
        if c.name.startswith("wallet.")
    }


def test_correct_transfer_passes_every_wallet_rule():
    s, r = _wallet_case(
        [_xfer("1", "2", 10.0, after=(90.0, 10.0))], [_probe("1", 90.0), _probe("2", 10.0)]
    )
    v = _verdicts(s, r)
    assert v and set(v.values()) == {Verdict.PASS}
    assert run_rules(s, r, wallet.WALLET_RULES).verdict is Verdict.PASS


def test_idempotent_wallet_passes_replay_and_reconcile():
    """A correct wallet may answer a replay 2xx with the original result."""
    same = (90.0, 10.0)
    s, r = _wallet_case(
        [_xfer("1", "2", 10.0, after=same)] * 3, [_probe("1", 90.0), _probe("2", 10.0)], repeat=3
    )
    v = _verdicts(s, r)
    assert v["wallet.replay_applied_once"] is Verdict.PASS
    assert v["wallet.balances_reconcile"] is Verdict.PASS


def test_replay_applied_twice_fails():
    s, r = _wallet_case(
        [_xfer("1", "2", 10.0, after=(90.0, 10.0)), _xfer("1", "2", 10.0, after=(80.0, 20.0))],
        [_probe("1", 80.0), _probe("2", 20.0)],
        repeat=2,
    )
    assert _verdicts(s, r)["wallet.replay_applied_once"] is Verdict.FAIL


def test_concurrency_alone_is_not_a_replay():
    s, r = _wallet_case(
        [_xfer("1", "2", 10.0, after=(90.0, 10.0)), _xfer("1", "2", 10.0, after=(80.0, 20.0))],
        [_probe("1", 80.0), _probe("2", 20.0)],
        concurrency=2,
    )
    v = _verdicts(s, r)
    assert "wallet.replay_applied_once" not in v
    assert v["wallet.balances_reconcile"] is Verdict.PASS  # two applied, both accepted


def test_overdraft_fails_negative_balance():
    s, r = _wallet_case(
        [_xfer("1", "2", 150.0, after=(-50.0, 150.0))],
        [_probe("1", -50.0), _probe("2", 150.0)],
        amount=150.0,
    )
    assert _verdicts(s, r)["wallet.no_negative_balance"] is Verdict.FAIL


@pytest.mark.parametrize("amount", [0.0, -5.0])
def test_non_positive_amount_accepted_fails_refused_passes(amount):
    s, r = _wallet_case([_xfer("1", "2", amount, after=(100.0 - amount, amount))], [])
    assert _verdicts(s, r)["wallet.transfer_amount_positive"] is Verdict.FAIL
    s, r = _wallet_case([_xfer("1", "2", amount, status=422)], [])
    assert _verdicts(s, r)["wallet.transfer_amount_positive"] is Verdict.PASS


def test_self_transfer_accepted_fails():
    s, r = _wallet_case([_xfer("1", "1", 10.0, after=(100.0, 100.0))], [])
    assert _verdicts(s, r)["wallet.no_self_transfer"] is Verdict.FAIL
    s, r = _wallet_case([_xfer("1", "1", 10.0, status=400)], [])
    assert _verdicts(s, r)["wallet.no_self_transfer"] is Verdict.PASS


def test_sub_cent_amount_and_float_residue_fail():
    s, r = _wallet_case([_xfer("1", "2", 0.333, after=(99.667, 0.333))], [])
    v = _verdicts(s, r)
    assert v["wallet.amount_in_minor_units"] is Verdict.FAIL
    s, r = _wallet_case([_xfer("1", "2", 0.1, after=(0.30000000000000004, 0.1))], [])
    assert _verdicts(s, r)["wallet.balance_in_minor_units"] is Verdict.FAIL


def test_lost_money_and_unexplained_move_fail_reconcile():
    created = _wallet_case(
        [_xfer("1", "2", 10.0, after=(90.0, 20.0))], [_probe("1", 90.0), _probe("2", 20.0)]
    )
    assert _verdicts(*created)["wallet.balances_reconcile"] is Verdict.FAIL
    # two accepted, source moved by one and a half transfers: a lost/partial update
    partial = _wallet_case(
        [_xfer("1", "2", 10.0)] * 2, [_probe("1", 85.0), _probe("2", 15.0)], concurrency=2
    )
    assert _verdicts(*partial)["wallet.balances_reconcile"] is Verdict.FAIL
    # refused, yet money moved
    refused = _wallet_case(
        [_xfer("1", "2", 10.0, status=422)], [_probe("1", 90.0), _probe("2", 10.0)]
    )
    assert _verdicts(*refused)["wallet.balances_reconcile"] is Verdict.FAIL


def test_a_rule_that_raises_is_inconclusive_not_a_crash():
    from plugins._base.plugin import DomainRule

    def broken(_s, _r):
        raise RuntimeError("bug in the rule")

    s, r = _wallet_case([_xfer("1", "2", 10.0, after=(90.0, 10.0))], [])
    checks = run_rules(s, r, [DomainRule("x.broken", "", broken)]).checks
    assert next(c for c in checks if c.name == "x.broken").verdict is Verdict.INCONCLUSIVE


def test_rules_do_not_run_on_an_execution_that_never_ran():
    s, r = _wallet_case([], [])
    r = r.model_copy(update={"outcome": RunOutcome.SETUP_FAILED})
    assert _verdicts(s, r) == {}


def test_generic_plugin_has_no_domain_rules_wallet_has_seven():
    from plugins._base.plugin import PluginContext, load

    ctx = PluginContext(base_url="http://x")
    assert load("rest_api", ctx).domain_rules() == []
    rules = load("wallet", ctx).domain_rules()
    assert 5 <= len(rules) <= 10 and len({r.id for r in rules}) == len(rules)


def test_score_scenarios_applies_the_plugin_rules():
    from agents.a06_evaluation.agent import score_scenarios

    s, r = _wallet_case([_xfer("1", "1", 10.0, after=(100.0, 100.0))], [])
    plain, _ = score_scenarios([s], [r], use_judge=False)
    ruled, _ = score_scenarios([s], [r], use_judge=False, plugin="wallet")
    assert not any(c.name.startswith("wallet.") for d in plain[0].dimensions for c in d.checks)
    assert ruled[0].defect_suspected and "itself" in ruled[0].summary


@pytest.mark.parametrize(
    ("status", "verdict"),
    [(200, Verdict.PASS), (404, Verdict.INCONCLUSIVE), (500, Verdict.FAIL)],
)
def test_probe_404_is_undecided_not_a_failure(status, verdict):
    """Petstore: a rename scenario was failed because the old name answered 404 —
    which is the correct answer. A 4xx probe is left to the invariants and judge."""
    s, r = _wallet_case([_xfer("1", "2", 10.0, after=(90.0, 10.0))], [])
    probe = StepResult(
        name="read_old", endpoint="GET /user/{old}", status_code=status, response_json={"x": 1}
    )
    r = r.model_copy(update={"probes": [probe]})
    check = next(c for c in run_rules(s, r).checks if c.name == "probes_readable")
    assert check.verdict is verdict
