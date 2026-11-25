"""Execution runner tests (M1-T3).

These run against the real wallet-api in-process over ASGI, so the scenarios
hit the actual buggy target rather than a mock of it. A few transport-level
cases use a stub transport, since a working server cannot produce a timeout
on demand.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ecvt.execution import RunnerConfig, run_scenario, run_scenarios
from ecvt.results import (
    ErrorType,
    ExecutionRecord,
    RunOutcome,
    StepOutcome,
    read_executions_jsonl,
    write_executions_jsonl,
)
from ecvt.schema import Scenario

BASE = "http://wallet.test"


@pytest.fixture
def config() -> RunnerConfig:
    return RunnerConfig(base_url=BASE, timeout_s=5.0, max_transport_retries=1, backoff_s=0.0)


@pytest.fixture
async def client(wallet_app: Any):
    # raise_app_exceptions=False makes the in-process target behave like a real
    # server: an unhandled exception becomes a 500 response rather than
    # propagating into the test. GT-07 depends on seeing that 500.
    transport = httpx.ASGITransport(app=wallet_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c


def scenario(**overrides: Any) -> Scenario:
    data: dict[str, Any] = {
        "id": "sc-001",
        "title": "Transfer above available balance",
        "category": "edge",
        "rationale": "No stated minimum balance.",
        "setup": [
            {
                "name": "create_source",
                "method": "POST",
                "path": "/accounts",
                "body": {"owner": "alice", "initial_balance": 50.0},
                "save": {"src_id": "id"},
            },
            {
                "name": "create_target",
                "method": "POST",
                "path": "/accounts",
                "body": {"owner": "bob", "initial_balance": 0.0},
                "save": {"dst_id": "id"},
            },
        ],
        "action": {
            "name": "overdraw",
            "method": "POST",
            "path": "/transfers",
            "body": {"from_account": "{src_id}", "to_account": "{dst_id}", "amount": 500.0},
        },
        "expectation": {
            "expected_status": [400, 422],
            "expected_behavior": "Rejected; balances unchanged.",
            "invariants": ["source balance is still 50.0"],
            "probes": [
                {"name": "read_source", "method": "GET", "path": "/accounts/{src_id}/balance"}
            ],
        },
    }
    data.update(overrides)
    return Scenario.model_validate(data)


# --- happy path against the real target ------------------------------------


async def test_setup_bindings_and_action_recorded(client, config):
    record = await run_scenario(scenario(), client, config)

    assert record.outcome is RunOutcome.COMPLETED
    assert record.bindings == {"src_id": "1", "dst_id": "2"}
    assert [s.status_code for s in record.setup] == [200, 200]
    assert len(record.actions) == 1
    assert record.actions[0].request_body == {
        "from_account": "1",
        "to_account": "2",
        "amount": 500.0,
    }
    assert record.probes[0].status_code == 200
    assert record.elapsed_ms > 0


async def test_overdraft_bug_is_observed_not_judged(client, config):
    """The target wrongly allows the overdraft; the runner only records it."""
    record = await run_scenario(scenario(), client, config)

    assert record.actions[0].status_code == 200  # a correct API would reject
    assert record.probes[0].response_json["balance"] == pytest.approx(-450.0)
    # Nothing in the record claims this is wrong — that is Evaluation's call.
    assert record.outcome is RunOutcome.COMPLETED


async def test_placeholder_keeps_bound_value_type(client, config):
    """A lone {placeholder} passes the captured value through unstringified."""
    record = await run_scenario(
        scenario(
            action={
                "name": "transfer_zero",
                "method": "POST",
                "path": "/transfers",
                "body": {
                    "from_account": "{src_id}",
                    "to_account": "{dst_id}",
                    "amount": 0.0,
                },
            }
        ),
        client,
        config,
    )
    assert record.actions[0].status_code == 200
    assert record.actions[0].request_body["amount"] == 0.0


async def test_path_placeholder_substituted(client, config):
    record = await run_scenario(
        scenario(
            action={
                "name": "read_balance",
                "method": "GET",
                "path": "/accounts/{src_id}/balance",
            },
            expectation={
                "expected_status": [200],
                "expected_behavior": "Returns the balance.",
            },
        ),
        client,
        config,
    )
    assert record.actions[0].status_code == 200
    assert "/accounts/1/balance" in record.actions[0].url


# --- error handling (ADR 0006) ---------------------------------------------


async def test_500_from_target_is_a_finding_not_a_retry(client, config):
    """GT-07: a missing account raises KeyError -> 500. Recorded once, as ok."""
    record = await run_scenario(
        scenario(
            setup=[],
            action={
                "name": "read_missing",
                "method": "GET",
                "path": "/accounts/does-not-exist/balance",
            },
            expectation={
                "expected_status": [404],
                "expected_behavior": "Unknown account yields 404.",
            },
        ),
        client,
        config,
    )

    action = record.actions[0]
    assert action.status_code == 500
    assert action.outcome is StepOutcome.OK  # a response arrived
    assert action.http_failed is True
    assert action.attempts == 1, "an HTTP status must never be retried"
    assert record.outcome is RunOutcome.COMPLETED


async def test_unbound_placeholder_reported_without_raising(client, config):
    record = await run_scenario(
        scenario(
            setup=[],
            action={
                "name": "transfer",
                "method": "POST",
                "path": "/transfers",
                "body": {"from_account": "{nope}", "to_account": "x", "amount": 1.0},
            },
        ),
        client,
        config,
    )

    action = record.actions[0]
    assert action.error_type is ErrorType.PLACEHOLDER
    assert "{nope} is not bound" in action.error
    assert record.outcome is RunOutcome.ACTION_ERROR


async def test_failed_capture_stops_setup_with_reason(client, config):
    record = await run_scenario(
        scenario(
            setup=[
                {
                    "name": "create_source",
                    "method": "POST",
                    "path": "/accounts",
                    "body": {"owner": "alice"},
                    "save": {"src_id": "account_number"},  # field does not exist
                }
            ]
        ),
        client,
        config,
    )

    assert record.outcome is RunOutcome.SETUP_FAILED
    assert "no 'account_number'" in record.note
    assert record.setup[-1].error_type is ErrorType.CAPTURE
    assert record.actions == [], "action must not run without its preconditions"


async def test_setup_http_failure_skips_action(client, config):
    record = await run_scenario(
        scenario(
            setup=[
                {
                    "name": "bad_create",
                    "method": "POST",
                    "path": "/accounts",
                    "body": {"nope": 1},  # 422 from FastAPI validation
                    "save": {"src_id": "id"},
                }
            ]
        ),
        client,
        config,
    )
    assert record.outcome is RunOutcome.SETUP_FAILED
    assert "returned 422" in record.note
    assert record.actions == []


async def test_transport_error_is_retried_then_recorded(config):
    """No response at all: retried up to the limit, then recorded as an error."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        record = await run_scenario(
            scenario(
                setup=[],
                action={
                    "name": "ping",
                    "method": "GET",
                    "path": "/accounts/1/balance",
                },
            ),
            c,
            config,
        )

    assert attempts["n"] == config.max_transport_retries + 1
    action = record.actions[0]
    assert action.error_type is ErrorType.CONNECT
    assert action.attempts == config.max_transport_retries + 1
    assert record.outcome is RunOutcome.ACTION_ERROR


async def test_timeout_classified(config):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE) as c:
        record = await run_scenario(
            scenario(
                setup=[], action={"name": "slow", "method": "GET", "path": "/accounts/1/balance"}
            ),
            c,
            config,
        )
    assert record.actions[0].error_type is ErrorType.TIMEOUT


async def test_non_json_response_kept_as_text(config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE) as c:
        record = await run_scenario(
            scenario(
                setup=[], action={"name": "html", "method": "GET", "path": "/accounts/1/balance"}
            ),
            c,
            config,
        )
    action = record.actions[0]
    assert action.status_code == 503
    assert action.response_json is None
    assert action.response_text == "upstream unavailable"
    assert action.outcome is StepOutcome.OK


# --- repeat and concurrency ------------------------------------------------


async def test_repeat_fires_action_n_times(client, config):
    """GT-05: no idempotency key, so a replay double-charges. Both recorded."""
    record = await run_scenario(
        scenario(
            repeat=2,
            action={
                "name": "transfer",
                "method": "POST",
                "path": "/transfers",
                "body": {"from_account": "{src_id}", "to_account": "{dst_id}", "amount": 10.0},
            },
        ),
        client,
        config,
    )

    assert len(record.actions) == 2
    assert all(a.status_code == 200 for a in record.actions)
    assert record.probes[0].response_json["balance"] == pytest.approx(30.0)  # 50 - 10 - 10


async def test_concurrency_fires_in_parallel(client, config):
    """GT-06: read-modify-write without a lock. Ten parallel transfers."""
    record = await run_scenario(
        scenario(
            concurrency=10,
            action={
                "name": "transfer",
                "method": "POST",
                "path": "/transfers",
                "body": {"from_account": "{src_id}", "to_account": "{dst_id}", "amount": 10.0},
            },
        ),
        client,
        config,
    )

    assert len(record.actions) == 10
    assert record.outcome is RunOutcome.COMPLETED
    assert record.probes[0].status_code == 200


async def test_repeat_times_concurrency(client, config):
    record = await run_scenario(
        scenario(
            repeat=2,
            concurrency=3,
            action={
                "name": "transfer",
                "method": "POST",
                "path": "/transfers",
                "body": {"from_account": "{src_id}", "to_account": "{dst_id}", "amount": 1.0},
            },
        ),
        client,
        config,
    )
    assert len(record.actions) == 6


# --- batch runs and persistence -------------------------------------------


async def test_run_scenarios_isolates_failures(client, config):
    broken = scenario(
        id="sc-broken",
        setup=[],
        action={
            "name": "bad",
            "method": "POST",
            "path": "/transfers",
            "body": {"from_account": "{missing}", "to_account": "x", "amount": 1.0},
        },
    )
    records = await run_scenarios([broken, scenario(id="sc-ok")], config, client=client)

    assert [r.scenario_id for r in records] == ["sc-broken", "sc-ok"]
    assert records[0].outcome is RunOutcome.ACTION_ERROR
    assert records[1].outcome is RunOutcome.COMPLETED, "one failure must not stop the chain"


async def test_executions_jsonl_round_trip(client, config, tmp_path):
    records = await run_scenarios([scenario()], config, client=client)
    path = write_executions_jsonl(tmp_path / "executions.jsonl", records)

    reread = read_executions_jsonl(path)
    assert len(reread) == 1
    assert reread[0].scenario_id == "sc-001"
    assert reread[0].actions[0].status_code == 200


def test_executions_jsonl_reports_bad_line(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"broken\.jsonl:1"):
        read_executions_jsonl(path)


# --- tool_result shape (ADR 0006) ------------------------------------------


async def test_step_result_as_tool_result(client, config):
    record = await run_scenario(scenario(), client, config)

    ok = record.actions[0].as_tool_result("tu_1")
    assert ok["type"] == "tool_result" and ok["tool_use_id"] == "tu_1"
    assert ok["is_error"] is False
    assert "status_code" in ok["content"]

    failed = await run_scenario(
        scenario(
            setup=[],
            action={"name": "x", "method": "GET", "path": "/accounts/{unbound}/balance"},
        ),
        client,
        config,
    )
    err = failed.actions[0].as_tool_result()
    assert err["is_error"] is True
    assert "placeholder" in err["content"]


def test_execution_record_helpers():
    record = ExecutionRecord(
        scenario_id="s", category="edge", base_url=BASE, outcome=RunOutcome.COMPLETED
    )
    assert record.action_statuses == []
    assert record.all_steps == []


# --- M3-T1: execution goes through a plugin, loaded by name -----------------


def _ctx():
    from plugins._base.plugin import PluginContext

    return PluginContext(base_url="http://test")


def test_registry_is_the_plugins_directory():
    from agents.a01_manager.agent import list_plugins
    from plugins._base.plugin import available

    names = available()
    assert "rest_api" in names and "_base" not in names
    assert list_plugins() == names  # the Manager reads the same registry


def test_rest_api_plugin_loads_by_name():
    from plugins._base.plugin import Plugin, load

    plugin = load("rest_api", _ctx())
    assert isinstance(plugin, Plugin) and plugin.name == "rest_api"


def test_unknown_plugin_fails_and_names_the_registry():
    from plugins._base.plugin import PluginError, load

    with pytest.raises(PluginError, match="registered: .*rest_api"):
        load("nope", _ctx())


def test_stub_plugin_fails_instead_of_falling_back():
    """ikys is a directory without an implementation: never silently run REST."""
    from plugins._base.plugin import PluginError, load

    with pytest.raises(PluginError, match="no implementation"):
        load("ikys", _ctx())


def test_plugin_without_execute_cannot_be_built():
    from plugins._base.plugin import Plugin

    class Half(Plugin):
        name, version = "half", "0"

    with pytest.raises(TypeError):
        Half(_ctx())


@pytest.mark.asyncio
async def test_optional_hook_defaults_to_nothing():
    from plugins._base.plugin import load

    plugin = load("rest_api", _ctx())
    assert await plugin.discover_domain() is None
    assert not hasattr(plugin, "validate_response")  # ADR 0013: a06 alone scores


@pytest.mark.asyncio
async def test_rest_api_plugin_matches_the_runner(monkeypatch):
    """The plugin is a wrapper: same records as calling run_scenarios directly."""
    from plugins._base.plugin import PluginContext, load

    seen = {}

    async def fake(scenarios, config, client=None):
        seen["config"] = config
        return ["r"] * len(scenarios)

    monkeypatch.setattr("ecvt.execution.run_scenarios", fake)
    plugin = load("rest_api", PluginContext(base_url="http://x", timeout_s=3.0, max_parallel=4))
    assert await plugin.execute([scenario(), scenario()]) == ["r", "r"]
    cfg = seen["config"]
    assert (cfg.base_url, cfg.timeout_s, cfg.max_parallel) == ("http://x", 3.0, 4)


def test_cli_rejects_a_stub_plugin_before_generation(monkeypatch, tmp_path, capsys):
    from ecvt import cli

    def never(**_):
        raise AssertionError("a03 must not be called")

    monkeypatch.setattr("agents.a03_scenario_generator.agent.generate_scenario_set", never)
    code = cli.main(
        ["run", "--target", "examples/wallet-api", "--plugin", "ikys", "--out-dir", str(tmp_path)]
    )
    assert code != 0
    assert "no implementation" in capsys.readouterr().err
