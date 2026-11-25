"""`ecvt serve` — the HTTP trigger n8n calls (M3-T5). Offline: the runner is faked."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ecvt import trigger_server as ts

WORKFLOW = Path("workflows/n8n/ecvt-run.json")


@pytest.fixture
def server():
    """Start a trigger on a free port; yields (base_url, calls, set_runner)."""
    calls: list[ts.RunRequest] = []
    state = {"runner": lambda req, cfg: {"status": "completed", "run_dir": "x"}}

    def runner(request, config):
        calls.append(request)
        return state["runner"](request, config)

    httpd = ts.build_server("127.0.0.1", 0, ts.TriggerConfig(token="s3cret"), runner)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", calls, state
    httpd.shutdown()
    httpd.server_close()


def _call(url, body=None, token="s3cret", raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header(ts.TOKEN_HEADER, token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


WALLET = {"target": "examples/wallet-api", "plugin": "wallet"}


def test_health(server):
    url, _, _ = server
    assert _call(f"{url}/health") == (200, {"status": "ok", "busy": False})


def test_a_valid_request_runs_once_with_parsed_fields(server):
    url, calls, _ = server
    status, body = _call(f"{url}/runs", WALLET)
    assert status == 200 and body["status"] == "completed"
    assert calls == [ts.RunRequest("examples/wallet-api", "wallet", manager=False, judge=True)]


@pytest.mark.parametrize("token", [None, "wrong"])
def test_token_is_required_when_configured(server, token):
    url, calls, _ = server
    status, body = _call(f"{url}/runs", WALLET, token=token)
    assert status == 401 and ts.TOKEN_HEADER in body["error"] and calls == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"target": "../../etc"}, "must be a directory under examples"),
        ({"target": "examples"}, "holding openapi.json"),
        ({}, "`target` is required"),
        ({**WALLET, "plugin": "nope"}, "unknown plugin"),
        ({**WALLET, "base_url": "http://elsewhere"}, "unknown field(s): base_url"),
        ({**WALLET, "manager": "yes"}, "must be true or false"),
    ],
)
def test_bad_requests_are_refused_before_any_run(server, body, message):
    url, calls, _ = server
    status, reply = _call(f"{url}/runs", body)
    assert status == 400 and message in reply["error"] and calls == []


def test_body_must_be_a_json_object(server):
    url, calls, _ = server
    assert _call(f"{url}/runs", raw=b"not json")[0] == 400
    assert _call(f"{url}/runs", raw=b"[1, 2]")[0] == 400
    assert _call(f"{url}/runs", raw=b"x" * (ts.MAX_BODY_BYTES + 1))[0] == 413
    assert calls == []


def test_one_run_at_a_time(server):
    url, _, state = server
    started, release = threading.Event(), threading.Event()

    def slow(request, config):
        started.set()
        release.wait(5)
        return {"status": "completed"}

    state["runner"] = slow
    first = threading.Thread(target=_call, args=(f"{url}/runs", WALLET))
    first.start()
    assert started.wait(5)
    assert _call(f"{url}/health")[1]["busy"] is True
    assert _call(f"{url}/runs", WALLET)[0] == 409
    release.set()
    first.join(5)


def test_a_failed_run_is_a_502_with_its_log(server):
    url, _, state = server
    state["runner"] = lambda r, c: {"status": "failed", "exit_code": 1, "log_tail": ["Stopped: x"]}
    status, body = _call(f"{url}/runs", WALLET)
    assert status == 502 and body["log_tail"] == ["Stopped: x"]


def test_refuses_to_listen_beyond_loopback_without_a_token():
    with pytest.raises(ValueError, match="without a token"):
        ts.build_server("0.0.0.0", 0, ts.TriggerConfig(token=None))


@pytest.mark.parametrize(
    ("request_", "present", "absent"),
    [
        (
            ts.RunRequest("examples/wallet-api", "wallet", False, True),
            [],
            ["--manager", "--no-judge"],
        ),
        (
            ts.RunRequest("examples/wallet-api", "rest_api", False, False),
            ["--no-judge"],
            ["--manager"],
        ),
        (
            ts.RunRequest("examples/wallet-api", "rest_api", True, False),
            ["--manager"],
            ["--no-judge"],
        ),
    ],
)
def test_run_ecvt_builds_the_cli_run_and_keeps_base_url_server_side(
    monkeypatch, tmp_path, request_, present, absent
):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": "Stopped: test"})()

    monkeypatch.setattr(ts.subprocess, "run", fake_run)
    config = ts.TriggerConfig(base_url="http://configured:8000", out_root=tmp_path)
    result = ts.run_ecvt(request_, config)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--base-url") + 1] == "http://configured:8000"
    assert cmd[cmd.index("--plugin") + 1] == request_.plugin and "--report" in cmd
    assert all(flag in cmd for flag in present) and not any(flag in cmd for flag in absent)
    assert result["status"] == "failed" and result["log_tail"] == ["Stopped: test"]


def test_summary_carries_recommendation_and_reports(tmp_path, monkeypatch):
    from ecvt.audience_reports import write_reports
    from tests.test_report import _audience_run, _case

    run_dir = _audience_run(tmp_path, [_case("a", "critical")])
    write_reports(run_dir)
    monkeypatch.setattr(ts, "REPO_ROOT", tmp_path)
    summary = ts.summarize_run(run_dir)
    assert summary["recommendation"].startswith("Not ready to release")
    assert "report-business.html" in summary["reports"] and summary["plugin"] == "wallet"


def test_n8n_workflow_is_webhook_to_trigger_to_response():
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    nodes = {n["name"]: n for n in wf["nodes"]}
    hook = next(n for n in nodes.values() if n["type"] == "n8n-nodes-base.webhook")
    call = next(n for n in nodes.values() if n["type"] == "n8n-nodes-base.httpRequest")
    reply = next(n for n in nodes.values() if n["type"] == "n8n-nodes-base.respondToWebhook")
    assert hook["parameters"]["path"] == "ecvt-run"
    assert hook["parameters"]["responseMode"] == "responseNode"  # answers when the run is done
    assert "/runs" in call["parameters"]["url"]
    headers = call["parameters"]["headerParameters"]["parameters"]
    assert any(h["name"] == ts.TOKEN_HEADER for h in headers)
    assert wf["connections"][hook["name"]]["main"][0][0]["node"] == call["name"]
    assert wf["connections"][call["name"]]["main"][0][0]["node"] == reply["name"]
