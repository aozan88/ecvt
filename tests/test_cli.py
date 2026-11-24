"""`ecvt run` chain tests (M1-T7).

The chain is exercised against a live wallet-api on a real port, with
scenarios supplied via --scenarios so no model call is needed.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from ecvt.cli import build_parser, main, unsupported_with_manager
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


# --- --manager cannot honour every flag of the fixed chain -----------------


@pytest.mark.parametrize(
    "flag",
    [
        ["--scenarios", "some/scenarios.jsonl"],
        ["--no-judge"],
        ["--per-category", "5"],
        ["--timeout", "30"],
        ["--max-parallel", "2"],
    ],
)
def test_manager_refuses_flags_it_cannot_honour(flag, capsys):
    """These reach the stage runners through nothing, so the Manager would ignore
    them. `n_run.py --manager --scenarios <set>` reported a frozen scenario set
    while generating a fresh one every run — refuse rather than mismeasure."""
    assert main(["run", "--manager", *flag]) == 2
    err = capsys.readouterr().err
    assert flag[0] in err
    assert "cannot honour" in err


def test_manager_accepts_the_flags_it_does_honour():
    """No false positives: these are threaded through and must not be refused."""
    parser = build_parser()
    args = parser.parse_args(
        ["run", "--manager", "--base-url", "http://localhost:9999", "--model", "x",
         "--plugin", "rest_api", "--max-budget", "1.0", "--report"]
    )
    assert unsupported_with_manager(parser, args) == []


# --- report integration ----------------------------------------------------


def test_run_with_record_writes_report_and_trend(
    live_target, scenarios_file, tmp_path, monkeypatch, capsys
):
    """One command produces the number, instead of a step you have to remember."""
    # conftest already redirects both generated docs into tmp; read where.
    from ecvt import reporting

    trend = reporting.TREND_DOC
    latest = reporting.LATEST_DOC

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

    assert latest.exists(), "--record must also snapshot the full report"
    snapshot = latest.read_text(encoding="utf-8")
    assert "## Scenario results" in snapshot
    assert "sc-overdraft" in snapshot

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


# --- every entry point reads .env ------------------------------------------


def test_main_reads_dotenv_before_running(monkeypatch):
    """`.env` is where the project keeps ANTHROPIC_API_KEY (.env.example), but
    only the eval runner read it: `ecvt run` died with "Could not resolve
    authentication method" unless the key happened to be exported."""
    called: list[bool] = []
    monkeypatch.setattr("ecvt.cli.load_env", lambda: called.append(True))
    main([])  # bare command: prints help, but load_env must already have run
    assert called == [True]


def test_entry_points_call_load_env():
    """The entry points that need a key, so the next one added is noticed."""
    import agents.a02_domain_intelligence.agent as a02
    import agents.a03_scenario_generator.agent as a03
    import agents.a06_evaluation.agent as a06
    from ecvt import evals

    for module in (a02, a03, a06, evals):
        src = inspect.getsource(module.main)
        assert "load_env()" in src, module.__name__


# --- the gate decides whether a milestone closes, so it reads .env too ------


def test_gate_check_loads_env_before_running_checks():
    """The gate reads os.environ directly. With the key in .env — where the
    project tells people to keep it — it skipped five of G2's nine checks and
    still printed a result, reporting the milestone as not closable for the
    wrong reason."""
    src = (REPO_ROOT / "scripts" / "gate_check.py").read_text(encoding="utf-8")
    main_src = src[src.index("def main()"):]
    assert "load_env()" in main_src
    # Before the checks run, or it has not helped.
    assert main_src.index("load_env()") < main_src.index("run_check")


def test_no_gate_checks_its_tag_locally():
    """`git tag -l` passes for a tag that never left the machine — a release
    gate reporting a release no one else can see."""
    import yaml

    for gate in sorted((REPO_ROOT / "gates").glob("M*.yaml")):
        checks = yaml.safe_load(gate.read_text(encoding="utf-8"))["checks"]
        for check in checks:
            assert "git tag -l" not in check["cmd"], f"{gate.name} {check['id']}"
        assert any(
            "ls-remote" in c["cmd"] and "refs/tags/" in c["cmd"] for c in checks
        ), f"{gate.name} has no remote tag check"
