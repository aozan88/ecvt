"""`ecvt run` chain tests (M1-T7).

The chain is exercised against a live wallet-api on a real port, with
scenarios supplied via --scenarios so no model call is needed.
"""

from __future__ import annotations

import contextlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from ecvt.cli import build_parser, main
from ecvt.schema import ScenarioSet, write_scenarios_jsonl
from tests.test_scoring import build, transfer

REPO_ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def live_target():
    """A real uvicorn process, so the CLI talks HTTP over a socket."""
    port = free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--app-dir",
            "examples/wallet-api",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        cwd=REPO_ROOT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            if proc.poll() is not None:
                raise RuntimeError("uvicorn exited during startup")
            try:
                httpx.get(f"{url}/openapi.json", timeout=0.5)
                break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            raise RuntimeError("target never came up")
        yield url
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)


@pytest.fixture
def scenarios_file(tmp_path: Path) -> Path:
    scenarios = [
        build("sc-happy", "happy", transfer(10.0), [200]),
        build("sc-overdraft", "edge", transfer(500.0), [400, 422]),
        build("sc-replay", "adversarial", transfer(10.0), [200, 409], repeat=2),
    ]
    path = tmp_path / "given.jsonl"
    write_scenarios_jsonl(
        path,
        ScenarioSet(target="examples/wallet-api", spec_title="Wallet API", scenarios=scenarios),
    )
    return path


def test_run_writes_three_files_and_run_json(live_target, scenarios_file, tmp_path, capsys):
    out = tmp_path / "artifacts"
    code = main(
        [
            "run",
            "--scenarios",
            str(scenarios_file),
            "--base-url",
            live_target,
            "--out-dir",
            str(out),
            "--no-judge",
        ]
    )
    assert code == 0

    assert not (out / "scenarios.jsonl").exists(), "reused scenarios are not rewritten"
    executions = [json.loads(x) for x in (out / "executions.jsonl").read_text().splitlines()]
    scores = [json.loads(x) for x in (out / "scores.jsonl").read_text().splitlines()]
    assert len(executions) == 3
    assert len(scores) == 3

    meta = json.loads((out / "run.json").read_text())
    assert meta["scenarios"] == 3
    assert meta["judge_enabled"] is False
    assert meta["verdicts"]["fail"] >= 1
    assert meta["elapsed_s"] > 0
    assert meta["usage"]["calls"] == 0  # rules only

    printed = capsys.readouterr().out
    assert "[1/3]" in printed and "[2/3]" in printed and "[3/3]" in printed
    assert "defects suspected" in printed


def test_run_finds_the_real_overdraft_defect(live_target, scenarios_file, tmp_path):
    out = tmp_path / "artifacts"
    main(
        [
            "run",
            "--scenarios",
            str(scenarios_file),
            "--base-url",
            live_target,
            "--out-dir",
            str(out),
            "--no-judge",
        ]
    )
    scores = {
        json.loads(line)["scenario_id"]: json.loads(line)
        for line in (out / "scores.jsonl").read_text().splitlines()
    }
    assert scores["sc-overdraft"]["verdict"] == "fail"
    assert scores["sc-overdraft"]["defect_suspected"] is True
    assert scores["sc-happy"]["verdict"] == "pass"
    assert scores["sc-replay"]["verdict"] == "inconclusive"


def test_timestamped_run_dir(live_target, scenarios_file, tmp_path):
    out = tmp_path / "artifacts"
    assert (
        main(
            [
                "run",
                "--scenarios",
                str(scenarios_file),
                "--base-url",
                live_target,
                "--out-dir",
                str(out),
                "--timestamped",
                "--no-judge",
            ]
        )
        == 0
    )
    runs = list((out / "runs").iterdir())
    assert len(runs) == 1
    assert (runs[0] / "scores.jsonl").exists()


def test_unreachable_target_still_scores_every_scenario(scenarios_file, tmp_path):
    """The target being down is not a crash: every scenario gets a not_run."""
    out = tmp_path / "artifacts"
    code = main(
        [
            "run",
            "--scenarios",
            str(scenarios_file),
            "--base-url",
            f"http://127.0.0.1:{free_port()}",
            "--out-dir",
            str(out),
            "--timeout",
            "1",
            "--no-judge",
        ]
    )
    assert code == 0

    scores = [json.loads(x) for x in (out / "scores.jsonl").read_text().splitlines()]
    assert len(scores) == 3
    assert all(s["verdict"] == "not_run" for s in scores)
    assert all(s["defect_suspected"] is False for s in scores), (
        "infrastructure failure must never be reported as a target defect"
    )


def test_missing_spec_is_explained(tmp_path, capsys):
    code = main(["run", "--target", str(tmp_path), "--out-dir", str(tmp_path / "a")])
    assert code == 1
    err = capsys.readouterr().err
    assert "no openapi.json" in err
    assert "curl" in err, "the error should say how to produce the spec"


def test_target_may_be_the_spec_file_itself(tmp_path):
    from ecvt.cli import _spec_path

    assert _spec_path("examples/wallet-api").name == "openapi.json"
    assert _spec_path("examples/wallet-api/openapi.json").name == "openapi.json"


def test_bare_command_prints_help(capsys):
    assert main([]) == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_parser_defaults():
    args = build_parser().parse_args(["run"])
    assert args.target == "examples/wallet-api"
    assert args.base_url == "http://localhost:8000"
    assert args.per_category == 2
    assert args.no_judge is False


# --- report integration ----------------------------------------------------


def test_run_with_record_writes_report_and_trend(
    live_target, scenarios_file, tmp_path, monkeypatch, capsys
):
    """One command produces the number, instead of a step you have to remember."""
    trend = tmp_path / "catch-rate.md"
    monkeypatch.setattr("ecvt.reporting.TREND_DOC", trend)
    monkeypatch.setattr("ecvt.cli.TREND_DOC", trend)

    out = tmp_path / "artifacts"
    code = main(
        [
            "run",
            "--scenarios",
            str(scenarios_file),
            "--base-url",
            live_target,
            "--out-dir",
            str(out),
            "--no-judge",
            "--record",
        ]
    )
    assert code == 0

    report = (out / "report.md").read_text(encoding="utf-8")
    assert "Catch rate:" in report
    assert "GT-03" in report

    assert trend.exists(), "--record must create the trend table"
    assert "# Catch rate over time" in trend.read_text(encoding="utf-8")
    assert "catch rate:" in capsys.readouterr().out


def test_run_without_report_flag_writes_no_report(live_target, scenarios_file, tmp_path):
    out = tmp_path / "artifacts"
    main(
        [
            "run",
            "--scenarios",
            str(scenarios_file),
            "--base-url",
            live_target,
            "--out-dir",
            str(out),
            "--no-judge",
        ]
    )
    assert not (out / "report.md").exists()


def test_report_on_target_without_baseline(live_target, scenarios_file, tmp_path, capsys):
    """A target with no ground truth is normal, not an error."""
    bare = tmp_path / "bare-target"
    bare.mkdir()
    (bare / "openapi.json").write_text("{}", encoding="utf-8")

    out = tmp_path / "artifacts"
    code = main(
        [
            "run",
            "--scenarios",
            str(scenarios_file),
            "--target",
            str(bare),
            "--base-url",
            live_target,
            "--out-dir",
            str(out),
            "--no-judge",
            "--report",
        ]
    )
    assert code == 0
    assert "no baseline" in capsys.readouterr().out
    assert not (out / "report.md").exists()
