"""`ecvt serve` — the HTTP trigger n8n calls (M3-T5) and the browser UI behind it (v1.1).

Offline: the runner is faked, so no run spends anything.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ecvt import trigger_server as ts

WORKFLOW = Path("workflows/n8n/ecvt-run.json")


@pytest.fixture
def server(tmp_path):
    """Start a trigger on a free port; yields (base_url, calls, state).

    `state["runner"]` is what a run does; `state["out_root"]` is where runs land.
    """
    calls: list[ts.RunRequest] = []
    state = {"runner": lambda req, cfg, out: {"status": "completed", "run_dir": "x"}}

    def runner(request, config, out_dir):
        calls.append(request)
        return state["runner"](request, config, out_dir)

    state["out_root"] = tmp_path / "triggered"
    config = ts.TriggerConfig(token="s3cret", out_root=state["out_root"])
    httpd = ts.build_server("127.0.0.1", 0, config, runner)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", calls, state
    httpd.shutdown()
    httpd.server_close()


def _fetch(url, body=None, token="s3cret", raw=None):
    """(status, headers, bytes) — for pages as well as JSON."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header(ts.TOKEN_HEADER, token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def _call(url, body=None, token="s3cret", raw=None):
    status, _, data = _fetch(url, body, token, raw)
    return status, json.loads(data)


WALLET = {"target": "examples/wallet-api", "plugin": "wallet"}
SYNC = "/runs?wait=true"  # what the n8n workflow calls


def _wait_for(url, run_id, statuses=("completed", "failed"), timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, job = _call(f"{url}/runs/{run_id}")
        if job.get("status") in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} never reached {statuses}: {job}")


def _idle(url, timeout=5.0):
    """The slot is freed just after the terminal status is written."""
    deadline = time.monotonic() + timeout
    while _call(f"{url}/health")[1]["busy"]:
        assert time.monotonic() < deadline, "the run slot was never released"
        time.sleep(0.02)
    return True


def test_health(server):
    url, _, _ = server
    assert _call(f"{url}/health") == (200, {"status": "ok", "busy": False, "auth": True})


def test_a_valid_request_runs_once_with_parsed_fields(server):
    url, calls, _ = server
    status, body = _call(f"{url}{SYNC}", WALLET)
    assert status == 200 and body["status"] == "completed"
    assert calls == [ts.RunRequest("examples/wallet-api", "wallet", manager=False, judge=True)]


@pytest.mark.parametrize("path", ["/runs", SYNC])
@pytest.mark.parametrize("token", [None, "wrong"])
def test_token_is_required_when_configured(server, token, path):
    url, calls, _ = server
    status, body = _call(f"{url}{path}", WALLET, token=token)
    assert status == 401 and ts.TOKEN_HEADER in body["error"] and calls == []


@pytest.mark.parametrize("path", ["/runs", "/targets", "/runs/0123456789ab",
                                  "/runs/0123456789ab/report-business.html"])  # fmt: skip
@pytest.mark.parametrize("token", [None, "wrong"])
def test_every_data_endpoint_needs_the_token(server, path, token):
    url, _, _ = server
    status, body = _call(f"{url}{path}", token=token)
    assert status == 401 and ts.TOKEN_HEADER in body["error"]


def test_the_page_and_health_need_no_token_and_the_page_holds_no_data(server):
    url, _, _ = server
    status, headers, page = _fetch(f"{url}/", token=None)
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert b"<title>ECVT Console</title>" in page and b"s3cret" not in page
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert _call(f"{url}/health", token=None)[0] == 200


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
    assert _call(f"{url}{SYNC}", raw=b"not json")[0] == 400
    assert _call(f"{url}/runs", raw=b"[1, 2]")[0] == 400
    assert _call(f"{url}/runs", raw=b"x" * (ts.MAX_BODY_BYTES + 1))[0] == 413
    assert calls == []


def test_one_run_at_a_time_sync(server):
    url, _, state = server
    started, release = threading.Event(), threading.Event()

    def slow(request, config, out_dir):
        started.set()
        release.wait(5)
        return {"status": "completed"}

    state["runner"] = slow
    first = threading.Thread(target=_call, args=(f"{url}{SYNC}", WALLET))
    first.start()
    assert started.wait(5)
    assert _call(f"{url}/health")[1]["busy"] is True
    assert _call(f"{url}{SYNC}", WALLET)[0] == 409
    assert _call(f"{url}/runs", WALLET)[0] == 409
    release.set()
    first.join(5)


def test_a_failed_run_is_a_502_with_its_log(server):
    url, _, state = server
    state["runner"] = lambda r, c, o: {
        "status": "failed",
        "exit_code": 1,
        "log_tail": ["Stopped: x"],
    }
    status, body = _call(f"{url}{SYNC}", WALLET)
    assert status == 502 and body["log_tail"] == ["Stopped: x"]


def test_a_failed_run_says_why_in_one_line_without_its_log(server):
    url, _, state = server
    log = [
        "[1/3] generating scenarios",
        "Traceback ...",
        "Stopped: no openapi.json",
        "      catch rate: 1/10",
    ]
    state["runner"] = lambda r, c, o: {"status": "failed", "exit_code": 1, "log_tail": log}
    run_id = _call(f"{url}/runs", WALLET)[1]["run_id"]
    job = _wait_for(url, run_id)
    assert job["status"] == "failed" and job["summary"]["error"] == "Stopped: no openapi.json"
    assert "log_tail" not in job["summary"]


def test_sync_answer_keeps_the_n8n_contract(server):
    """G3-11 routes on these keys; the synchronous answer must not lose any of them."""
    url, _, state = server
    result = {"status": "completed", "run_dir": "artifacts/triggered/x", "plugin": "wallet",
              "recommendation": "Fix before release — 1 high-severity issue(s) found.",
              "catch_rate": "6/10", "reports": ["report-business.html"]}  # fmt: skip
    state["runner"] = lambda r, c, o: dict(result)
    status, body = _call(f"{url}{SYNC}", WALLET)
    assert status == 200 and body["request"]["plugin"] == "wallet"
    assert {k: body[k] for k in result} == result and len(body["run_id"]) == 12


# --- async lifecycle (the UI's path) -----------------------------------------


def test_async_run_lifecycle_queued_running_completed(server):
    url, calls, state = server
    started, release = threading.Event(), threading.Event()

    def staged(request, config, out_dir):
        # What `ecvt run --status-file` keeps while it works (ADR 0016).
        steps = ["generate", "execute", "evaluate", "report"]
        (out_dir / ts.STATUS_FILE).write_text(
            json.dumps({"steps": steps, "current": "execute", "detail": "9 scenarios"})
        )
        started.set()
        release.wait(5)
        return {"status": "completed", "recommendation": "No issues found in what was tested.",
                "catch_rate": "10/10", "log_tail": ["secret"],
                "reports": ["report-business.html", "report-dev.html", "report.md"]}  # fmt: skip

    state["runner"] = staged
    status, body = _call(f"{url}/runs", WALLET)
    assert status == 202 and body["status"] == "queued"
    run_id = body["run_id"]
    assert body["links"]["self"] == f"/runs/{run_id}"
    assert started.wait(5)

    job = _wait_for(url, run_id, ("running",))
    assert job["progress"] == {
        "steps": ["generate", "execute", "evaluate", "report"],
        "current": "execute",
        "detail": "9 scenarios",
    }
    assert _call(f"{url}/health")[1]["busy"] is True
    assert _call(f"{url}/runs")[1]["active"] == run_id

    release.set()
    job = _wait_for(url, run_id)
    assert job["status"] == "completed" and job["finished_at"]
    assert job["summary"]["recommendation"].startswith("No issues found")
    # No ground truth and no log in what the UI reads (ADR 0007).
    assert "catch_rate" not in job["summary"] and "log_tail" not in job["summary"]
    assert job["summary"]["reports"] == ["report-business.html", "report-dev.html"]
    assert calls == [ts.RunRequest("examples/wallet-api", "wallet", manager=False, judge=True)]
    assert _idle(url)


def test_async_run_holds_the_single_run_slot(server):
    url, _, state = server
    release = threading.Event()
    state["runner"] = lambda r, c, o: (release.wait(5), {"status": "completed"})[1]
    status, first = _call(f"{url}/runs", WALLET)
    assert status == 202
    status, body = _call(f"{url}/runs", WALLET)
    assert status == 409 and body["active"] == first["run_id"]
    assert _call(f"{url}{SYNC}", WALLET)[0] == 409
    release.set()
    _wait_for(url, first["run_id"])
    assert _idle(url) and _call(f"{url}/runs", WALLET)[0] == 202  # the slot is free again


def test_a_crashed_runner_fails_the_run_and_frees_the_slot(server):
    url, _, state = server

    def boom(request, config, out_dir):
        raise RuntimeError("disk on fire")

    state["runner"] = boom
    run_id = _call(f"{url}/runs", WALLET)[1]["run_id"]
    job = _wait_for(url, run_id)
    assert job["status"] == "failed" and "disk on fire" in job["summary"]["error"]
    assert _idle(url)


def test_history_is_newest_first_and_survives_a_restart(server, tmp_path):
    url, _, state = server
    ids = []
    for _ in range(3):
        ids.append(_call(f"{url}/runs", WALLET)[1]["run_id"])
        _wait_for(url, ids[-1])
        _idle(url)
    status, body = _call(f"{url}/runs")
    assert status == 200 and [r["run_id"] for r in body["runs"]] == ids[::-1]
    assert body["active"] is None

    # A run left "running" by a server that died is shown as interrupted, not live.
    store = ts.RunStore(ts.TriggerConfig(out_root=state["out_root"]))
    job = json.loads((state["out_root"] / ids[0] / ts.JOB_FILE).read_text())
    store.save({**job, "status": "running"})
    assert store.load(ids[0])["status"] == "failed"
    assert "interrupted" in store.load(ids[0])["error"]


def test_unknown_run_is_404(server):
    url, _, _ = server
    assert _call(f"{url}/runs/0123456789ab")[0] == 404
    assert _call(f"{url}/runs/0123456789ab/report-dev.html")[0] == 404


# --- reports and path traversal ------------------------------------------------


@pytest.fixture
def finished(server):
    """A completed async run whose directory holds real reports."""
    from ecvt.audience_reports import write_reports
    from tests.test_report import _audience_run, _case

    url, _, state = server

    def real(request, config, out_dir):
        for f in _audience_run(out_dir, [_case("a", "critical")]).iterdir():
            f.rename(out_dir / f.name)  # `ecvt run --out-dir` writes the run here, flat
        write_reports(out_dir)
        (out_dir / "report.md").write_text("**Catch rate: 9/10** (ground truth)\n")
        return {"status": "completed", "reports": ["report-business.html", "report-dev.html"]}

    state["runner"] = real
    run_id = _call(f"{url}/runs", WALLET)[1]["run_id"]
    _wait_for(url, run_id)
    return url, run_id, state["out_root"]


@pytest.mark.parametrize("name", ["report-business.html", "report-dev.html"])
def test_reports_are_served_as_html_with_a_closed_csp(finished, name):
    url, run_id, _ = finished
    status, headers, page = _fetch(f"{url}/runs/{run_id}/{name}")
    assert status == 200 and page.startswith(b"<!doctype html>")
    assert headers["Content-Type"].startswith("text/html")
    assert headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    [
        "/runs/{id}/report.md",  # catch rate lives here: never served (ADR 0007)
        "/runs/{id}/run.json",
        "/runs/{id}/scores.jsonl",
        "/runs/{id}/../../../etc/passwd",
        "/runs/{id}/%2e%2e/%2e%2e/pyproject.toml",
        "/runs/../../pyproject.toml",
        "/runs/%2e%2e%2fetc",
        "/runs/{id}/run/report-dev.html",
        "/runs/{id}x",
        "/runs/{id}/report-dev.html/..",
        "/runs/ABCDEF012345",
        "/runs/{id}//report-dev.html",
        "/examples/wallet-api/ground_truth.yaml",
        "/ecvt/ui.html",
    ],
)
def test_nothing_outside_the_two_reports_is_reachable(finished, path):
    url, run_id, _ = finished
    status, _, data = _fetch(url + path.format(id=run_id))
    assert status in (400, 404), (path, status)
    assert b"Catch rate" not in data and b"[project]" not in data and b"root:" not in data


def test_a_report_symlinked_out_of_the_run_dir_is_refused(finished, tmp_path):
    url, run_id, out_root = finished
    secret = tmp_path / "outside.html"
    secret.write_text("<p>outside</p>")
    report = next((out_root / run_id).rglob("report-dev.html"))
    report.unlink()
    report.symlink_to(secret)
    assert _fetch(f"{url}/runs/{run_id}/report-dev.html")[0] == 404


def test_run_ids_cannot_reach_the_disk_unvalidated(tmp_path):
    store = ts.RunStore(ts.TriggerConfig(out_root=tmp_path))
    for bad in ("..", "../x", "0123456789ab/..", "0123456789AB", ""):
        with pytest.raises(ValueError):
            store.dir(bad)
    assert store.report("0123456789ab", "report.md") is None


# --- /targets -----------------------------------------------------------------------


def test_targets_lists_specs_under_examples_and_loadable_plugins(server):
    url, _, _ = server
    status, body = _call(f"{url}/targets")
    assert status == 200
    assert {"target": "examples/wallet-api", "title": "Wallet API (buggy demo)",
            "version": "0.1.0"} in body["targets"]  # fmt: skip
    assert all((ts.REPO_ROOT / t["target"] / "openapi.json").is_file() for t in body["targets"])
    assert "examples/ccaf" not in {t["target"] for t in body["targets"]}  # no spec there
    assert {"rest_api", "wallet"} <= set(body["plugins"])
    assert "ikys" not in body["plugins"]  # a stub refuses to load (G3-1), so it is not offered


def test_targets_root_is_the_only_place_targets_come_from(tmp_path):
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "openapi.json").write_text('{"info": {"title": "Svc"}}')
    (tmp_path / "nospec").mkdir()
    listed = ts.list_targets(ts.TriggerConfig(targets_root=tmp_path))["targets"]
    assert [t["title"] for t in listed] == ["Svc"]


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
    out_dir = Path(cmd[cmd.index("--out-dir") + 1])
    assert cmd[cmd.index("--status-file") + 1] == str(out_dir / ts.STATUS_FILE)
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
    # The synchronous path: the webhook answers with the finished run, not a 202.
    assert "/runs?wait=true" in call["parameters"]["url"]
    headers = call["parameters"]["headerParameters"]["parameters"]
    assert any(h["name"] == ts.TOKEN_HEADER for h in headers)
    assert wf["connections"][hook["name"]]["main"][0][0]["node"] == call["name"]
    assert wf["connections"][call["name"]]["main"][0][0]["node"] == reply["name"]
