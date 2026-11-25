"""Catch-rate report tests (M1-T8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecvt import reporting as rp
from ecvt.scoring import ScenarioScore, Verdict

GT_PATH = Path("examples/wallet-api/ground_truth.yaml")


def write_run(
    tmp_path: Path,
    scores: list[ScenarioScore],
    endpoints: dict[str, str],
    meta: dict | None = None,
) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "scores.jsonl").write_text(
        "\n".join(s.model_dump_json(exclude_none=True) for s in scores) + "\n",
        encoding="utf-8",
    )
    records = [
        {
            "scenario_id": sid,
            "category": "edge",
            "base_url": "http://t",
            "outcome": "completed",
            "actions": [
                {
                    "name": "a",
                    "endpoint": endpoint,
                    "outcome": "ok",
                    "status_code": 200,
                }
            ],
        }
        for sid, endpoint in endpoints.items()
    ]
    (run_dir / "executions.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    (run_dir / "run.json").write_text(
        json.dumps(
            meta
            or {
                "target": "examples/wallet-api",
                "base_url": "http://t",
                "judged": len(scores),
                "judge_enabled": True,
                "model": "claude-haiku-5-5",
                "elapsed_s": 12.345,
                "usage": {"total_tokens": 1000, "cost_usd": 0.001},
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def score(sid: str, category: str, verdict: str = "fail") -> ScenarioScore:
    return ScenarioScore(
        scenario_id=sid,
        category=category,
        verdict=Verdict(verdict),
        defect_suspected=verdict == "fail",
        summary="s",
    )


# --- endpoint parsing ------------------------------------------------------


def test_path_tail_ignores_placeholders():
    assert rp._path_tail("/accounts/{account_id}/balance") == "balance"
    assert rp._path_tail("/transfers") == "transfers"
    assert rp._path_tail("/") == ""


def test_parse_multiple_endpoints():
    assert rp.parse_endpoints("POST /accounts; POST /transfers") == [
        ("POST", "accounts"),
        ("POST", "transfers"),
    ]


def test_loose_ground_truth_endpoint_matches_real_path():
    """GT says 'GET /balance'; the scenario carries the full path."""
    bugs = rp.load_ground_truth(GT_PATH)
    gt07 = next(b for b in bugs if b.id == "GT-07")
    assert ("GET", "balance") in gt07.endpoints

    result = rp.attribute(
        [score("sc-1", "negative")],
        {"sc-1": "GET /accounts/{account_id}/balance"},
        bugs,
    )
    assert result.unmatched == []
    assert gt07.caught_by == ["sc-1"]


# --- ground truth ----------------------------------------------------------


def test_ground_truth_loads_ten_bugs():
    bugs = rp.load_ground_truth(GT_PATH)
    assert len(bugs) == 10
    assert {b.id for b in bugs} == {f"GT-{i:02d}" for i in range(1, 11)}


def test_every_ground_truth_category_has_a_family():
    """A category with no family can never be attributed, so it must not exist."""
    bugs = rp.load_ground_truth(GT_PATH)
    missing = {b.category for b in bugs if b.category not in rp.CATEGORY_FAMILIES}
    assert not missing, f"no scenario-category family for: {missing}"


def test_empty_baseline_rejected(tmp_path):
    path = tmp_path / "gt.yaml"
    path.write_text("bugs: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no bugs listed"):
        rp.load_ground_truth(path)


# --- attribution -----------------------------------------------------------


def test_passing_scenario_is_never_attributed():
    bugs = rp.load_ground_truth(GT_PATH)
    rp.attribute([score("sc-1", "edge", verdict="pass")], {"sc-1": "POST /transfers"}, bugs)
    assert all(not b.caught for b in bugs)


def test_not_run_scenario_is_never_attributed():
    bugs = rp.load_ground_truth(GT_PATH)
    rp.attribute([score("sc-1", "edge", verdict="not_run")], {"sc-1": "POST /transfers"}, bugs)
    assert all(not b.caught for b in bugs)


def test_category_outside_the_family_does_not_attribute():
    bugs = rp.load_ground_truth(GT_PATH)
    # GT-05 is adversarial-idempotency: {adversarial, stress}. A security
    # scenario on the same endpoint must not be credited with it.
    result = rp.attribute([score("sc-1", "security")], {"sc-1": "POST /transfers"}, bugs)
    gt05 = next(b for b in bugs if b.id == "GT-05")
    assert not gt05.caught
    assert result.unmatched == [], "it still fits GT-04 security-authz"


def test_wrong_endpoint_does_not_attribute():
    bugs = rp.load_ground_truth(GT_PATH)
    result = rp.attribute(
        [score("sc-1", "adversarial")], {"sc-1": "GET /accounts/{id}/transactions"}, bugs
    )
    assert all(not b.caught for b in bugs)
    assert result.unmatched == ["sc-1"]
    assert result.redundant == []


def test_one_scenario_is_credited_with_at_most_one_bug():
    """The cap that keeps the rate honest.

    Six of wallet-api's defects sit on POST /transfers and an `edge` scenario
    fits several of their families. Crediting them all would report 6/10 for a
    run that found one problem.
    """
    bugs = rp.load_ground_truth(GT_PATH)
    rp.attribute([score("sc-1", "edge")], {"sc-1": "POST /transfers"}, bugs)
    assert len([b for b in bugs if b.caught]) == 1


def test_most_specific_family_wins_the_defect():
    """`boundary` is {edge, stress} — narrower than the other edge families."""
    bugs = rp.load_ground_truth(GT_PATH)
    rp.attribute([score("sc-1", "edge")], {"sc-1": "POST /transfers"}, bugs)
    credited = next(b for b in bugs if b.caught)
    assert credited.category == "boundary"


def test_extra_failures_on_a_taken_defect_are_redundant_not_missing():
    bugs = rp.load_ground_truth(GT_PATH)
    result = rp.attribute(
        [score("sc-1", "security"), score("sc-2", "security")],
        {"sc-1": "POST /transfers", "sc-2": "POST /transfers"},
        bugs,
    )
    assert len([b for b in bugs if b.caught]) == 1
    assert result.redundant == ["sc-2"]
    assert result.unmatched == []


# --- report ----------------------------------------------------------------


def test_report_counts_and_markdown(tmp_path):
    run_dir = write_run(
        tmp_path,
        [score("sc-1", "edge"), score("sc-2", "adversarial"), score("sc-3", "happy", "pass")],
        {
            "sc-1": "POST /transfers",
            "sc-2": "POST /transfers",
            "sc-3": "POST /transfers",
        },
    )
    text, stats = rp.build_report(run_dir, GT_PATH)

    assert stats["known_bugs"] == 10
    assert 0 < stats["caught"] <= 2, "two failing scenarios cannot find more than two"
    assert stats["catch_rate"] == f"{stats['caught']}/10"
    assert stats["scenarios"] == 3
    assert stats["cost_usd"] == 0.001
    assert stats["elapsed_s"] == 12.345

    assert "# ECVT run report" in text
    assert "GT-01" in text and "GT-10" in text
    assert "How attribution works" in text
    assert "ADR 0007" in text


def test_report_lists_misses_and_unattributed(tmp_path):
    run_dir = write_run(
        tmp_path,
        [score("sc-odd", "persona")],
        {"sc-odd": "GET /accounts/{id}/transactions"},
    )
    text, stats = rp.build_report(run_dir, GT_PATH)

    assert stats["caught"] == 0
    assert stats["unattributed_failures"] == 1
    assert "sc-odd" in text
    assert "false positive" in text
    assert "## Not detected" in text


def test_report_handles_missing_run_json(tmp_path):
    run_dir = write_run(tmp_path, [score("sc-1", "edge")], {"sc-1": "POST /transfers"})
    (run_dir / "run.json").unlink()
    text, stats = rp.build_report(run_dir, GT_PATH)
    assert stats["tokens"] is None
    assert "unknown" in text


# --- trend table -----------------------------------------------------------


def test_trend_doc_created_then_appended(tmp_path):
    doc = tmp_path / "catch-rate.md"
    stats = {
        "catch_rate": "4/10",
        "unattributed_failures": 1,
        "scenarios": 14,
        "judged": 14,
        "tokens": 59894,
        "cost_usd": 0.007013,
        "elapsed_s": 49.6,
        "model": "claude-haiku-5-5",
    }

    rp.record_trend(stats, "abc1234", doc=doc)
    first = doc.read_text(encoding="utf-8")
    assert "# Catch rate over time" in first
    assert "| 4/10 |" in first
    assert "Regression Agent" in first

    rp.record_trend({**stats, "catch_rate": "6/10"}, "def5678", doc=doc)
    second = doc.read_text(encoding="utf-8")
    assert "| 4/10 |" in second and "| 6/10 |" in second
    assert second.count(rp.TREND_MARKER) == 1
    assert second.index("6/10") < second.index("4/10"), "newest row first"


def test_trend_row_tolerates_missing_cost():
    row = rp.trend_row(
        {"catch_rate": "0/10", "unattributed_failures": 0, "scenarios": 1, "cost_usd": None},
        "aaa",
    )
    assert "| ? |" in row


# --- CLI -------------------------------------------------------------------


def test_cli_writes_report(tmp_path, capsys):
    run_dir = write_run(tmp_path, [score("sc-1", "edge")], {"sc-1": "POST /transfers"})
    assert rp.main([str(run_dir), "--ground-truth", str(GT_PATH)]) == 0
    assert (run_dir / "report.md").exists()
    assert "catch rate" in capsys.readouterr().out


def test_cli_infers_ground_truth_from_run_json(tmp_path):
    run_dir = write_run(tmp_path, [score("sc-1", "edge")], {"sc-1": "POST /transfers"})
    assert rp.main([str(run_dir)]) == 0


def test_cli_reports_missing_run_dir(tmp_path, capsys):
    assert rp.main([str(tmp_path / "nope")]) == 1
    assert "No scores.jsonl" in capsys.readouterr().err


def test_cli_reports_missing_baseline(tmp_path, capsys):
    run_dir = write_run(tmp_path, [score("sc-1", "edge")], {"sc-1": "POST /transfers"})
    assert rp.main([str(run_dir), "--ground-truth", str(tmp_path / "nope.yaml")]) == 1
    assert "No baseline" in capsys.readouterr().err


# --- scenario results table ------------------------------------------------


def test_report_lists_every_scenario_with_its_verdict(tmp_path):
    """The results list: scores.jsonl is machine-readable and gitignored, so
    without this section the per-scenario outcome is documented nowhere."""
    scores = [
        score("sc-pass", "happy", "pass"),
        score("sc-fail", "edge", "fail"),
        score("sc-unknown", "stress", "inconclusive"),
        score("sc-broken", "persona", "not_run"),
    ]
    run_dir = write_run(tmp_path, scores, {s.scenario_id: "POST /transfers" for s in scores})
    text, _ = rp.build_report(run_dir, GT_PATH)

    assert "## Scenario results" in text
    for sid in ("sc-pass", "sc-fail", "sc-unknown", "sc-broken"):
        assert f"`{sid}`" in text

    # Failures first: that is what a reader opens the report for.
    assert text.index("sc-fail") < text.index("sc-unknown") < text.index("sc-pass")


def test_scenario_table_marks_which_cost_a_model_call(tmp_path):
    from ecvt.scoring import Dimension, DimensionScore

    judged = score("sc-judged", "edge", "fail")
    judged.dimensions = [DimensionScore(dimension=Dimension.JUDGE, verdict=Verdict.FAIL)]
    plain = score("sc-rules", "edge", "fail")

    run_dir = write_run(
        tmp_path, [judged, plain], {"sc-judged": "POST /transfers", "sc-rules": "POST /transfers"}
    )
    text, _ = rp.build_report(run_dir, GT_PATH)

    judged_row = next(ln for ln in text.splitlines() if "sc-judged" in ln)
    plain_row = next(ln for ln in text.splitlines() if "sc-rules" in ln)
    assert "| yes |" in judged_row
    assert "| — |" in plain_row


def test_pipe_in_a_summary_does_not_break_the_table(tmp_path):
    bad = score("sc-pipe", "edge", "fail")
    bad.summary = "returned [200] | should have rejected"
    run_dir = write_run(tmp_path, [bad], {"sc-pipe": "POST /transfers"})
    text, _ = rp.build_report(run_dir, GT_PATH)

    row = next(ln for ln in text.splitlines() if "sc-pipe" in ln)
    assert row.count("|") == 6, "an unescaped pipe would add a phantom column"


# --- latest-run snapshot ---------------------------------------------------


def test_record_latest_writes_snapshot(tmp_path):
    doc = tmp_path / "latest-run.md"
    rp.record_latest("# ECVT run report\n\nbody here", "abc1234", doc=doc)

    text = doc.read_text(encoding="utf-8")
    assert "body here" in text
    assert "commit abc1234" in text
    assert "Do not edit by hand" in text


def test_record_latest_overwrites_rather_than_appending(tmp_path):
    doc = tmp_path / "latest-run.md"
    rp.record_latest("first run", "aaa", doc=doc)
    rp.record_latest("second run", "bbb", doc=doc)

    text = doc.read_text(encoding="utf-8")
    assert "second run" in text
    assert "first run" not in text, "one file, replaced; history lives in git"


# --- gate definition -------------------------------------------------------


def test_m1_gate_covers_every_g1_criterion():
    """The gate file must not quietly omit a criterion from the plan."""
    import yaml

    spec = yaml.safe_load(Path("gates/M1.yaml").read_text(encoding="utf-8"))
    assert spec["gate"] == "G1" and spec["milestone"] == "M1"

    descriptions = " ".join(c["desc"].lower() for c in spec["checks"])
    for criterion in (
        "one command",  # ecvt run finishes, exit 0
        "7 categories",  # scenarios/executions/scores complete
        "pass rate",  # three agents >= 80%
        "pytest",  # unit + integration green
        "catch rate",  # X/10 against ground truth
        "tokens and cost",  # logged per run
        "tag v0.3.0",  # release tag
    ):
        assert criterion in descriptions, f"G1 criterion not checked: {criterion}"

    ids = [c["id"] for c in spec["checks"]]
    assert len(ids) == len(set(ids)), "duplicate check ids"


def test_gate_never_writes_into_docs():
    """Running the gate must not dirty the repo: --report, never --record.

    Comments are stripped first — the file explains the rule in a comment,
    which would otherwise trip this check.
    """
    lines = Path("gates/M1.yaml").read_text(encoding="utf-8").splitlines()
    commands = "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))
    assert "--record" not in commands
    assert "--report" in commands


# --- M3-T4: Dev + Business reports (no ground truth, no model call) ---------

from ecvt import audience_reports as ar
from ecvt.scoring import Dimension, DimensionScore


def _audience_run(tmp_path: Path, cases: list[dict], meta: dict | None = None) -> Path:
    """cases: {id, severity, verdict, judge (None|verdict), title}."""
    from ecvt.results import ExecutionRecord, RunOutcome, StepResult, write_executions_jsonl
    from ecvt.schema import Scenario, ScenarioSet, write_scenarios_jsonl
    from ecvt.scoring import RuleCheck, combine, write_scores_jsonl
    from tests.test_schema import make_scenario

    run_dir = tmp_path / "run"
    scenarios, records, scores = [], [], []
    for c in cases:
        s = Scenario.model_validate(
            make_scenario(c["id"], severity=c["severity"], title=c["title"])
        )
        outcome = RunOutcome.SETUP_FAILED if c["verdict"] == "not_run" else RunOutcome.COMPLETED
        step = StepResult(
            name="overdraw",
            endpoint="POST /transfers",
            status_code=200,
            url="http://t/transfers",
            request_body={"amount": 500.0},
            response_json={"status": "ok"},
        )
        r = ExecutionRecord(
            scenario_id=s.id,
            category=s.category.value,
            base_url="http://t",
            outcome=outcome,
            actions=[step] if outcome is RunOutcome.COMPLETED else [],
            note="setup step 'create_source' returned 422" if c["verdict"] == "not_run" else None,
        )
        v = Verdict(c["verdict"])
        dims = [
            DimensionScore(
                dimension=Dimension.RULE,
                verdict=v,
                checks=[
                    RuleCheck(
                        name="status_expected",
                        verdict=v,
                        detail=f"rule detail for {s.id}",
                        expected="[400, 422]",
                        observed="[200]",
                    )
                ],
            )
        ]
        if c.get("judge"):
            dims.append(
                DimensionScore(
                    dimension=Dimension.JUDGE,
                    verdict=Verdict(c["judge"]),
                    rationale=f"Judge says {c['judge']} for {s.id}. Second sentence.",
                )
            )
        scenarios.append(s)
        records.append(r)
        scores.append(combine(s, r, dims))
    write_scenarios_jsonl(
        run_dir / "scenarios.jsonl",
        ScenarioSet(target="t", spec_title="Demo API", scenarios=scenarios),
    )
    write_executions_jsonl(run_dir / "executions.jsonl", records)
    write_scores_jsonl(run_dir / "scores.jsonl", scores)
    (run_dir / "run.json").write_text(
        json.dumps(
            meta
            or {
                "target": "examples/demo",
                "plugin": "wallet",
                "elapsed_s": 3.2,
                "usage": {"cost_usd": 0.0042, "total_tokens": 900},
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def _case(sid, severity="medium", verdict="fail", judge=None, title=None):
    return {
        "id": sid,
        "severity": severity,
        "verdict": verdict,
        "judge": judge,
        "title": title or f"Scenario {sid}",
    }


@pytest.mark.parametrize(
    ("cases", "headline"),
    [
        ([_case("a", "critical"), _case("b", "high")], "Not ready to release"),
        ([_case("a", "high"), _case("b", "low")], "Fix before release"),
        ([_case("a", "medium"), _case("b", "low", "pass")], "Releasable with known issues"),
        ([_case("a", verdict="pass")], "No issues found"),
        ([_case("a", verdict="not_run")], "No result"),
    ],
)
def test_release_recommendation_follows_the_stated_rule(tmp_path, cases, headline):
    run = ar.load_run(_audience_run(tmp_path, cases))
    assert headline in ar.recommendation(run)


def test_business_shows_the_failing_rule_when_the_judge_passed(tmp_path):
    """A judge that passed a scenario a rule failed must not explain the issue away."""
    run = ar.load_run(
        _audience_run(tmp_path, [_case("ruled", judge="pass"), _case("judged", judge="fail")])
    )
    md = ar.render_markdown(ar.build_business(run))
    assert "rule detail for ruled" in md and "Judge says pass" not in md
    assert "Judge says fail for judged." in md and "Second sentence" not in md  # first sentence


def test_dev_report_has_checks_judge_repro_and_not_run_reason(tmp_path):
    run = ar.load_run(
        _audience_run(
            tmp_path, [_case("f1", "critical", judge="fail"), _case("nr", verdict="not_run")]
        )
    )
    md = ar.render_markdown(ar.build_dev(run))
    assert "`status_expected`" in md and "[400, 422]" in md
    assert "**Judge (fail):**" in md
    assert "action  POST http://t/transfers" in md and '"amount": 500.0' in md
    assert "returned 422" in md  # why it did not run
    assert "plugin `wallet`" in md


def test_findings_are_ordered_by_severity(tmp_path):
    run = ar.load_run(
        _audience_run(tmp_path, [_case("z", "low"), _case("y", "critical"), _case("x", "medium")])
    )
    assert [f.scenario.id for f in run.failures] == ["y", "x", "z"]


def test_write_reports_writes_four_files_and_escapes(tmp_path):
    hostile = "<script>alert(1)</script> | pipe"
    run_dir = _audience_run(tmp_path, [_case("h", title=hostile)])
    written = {p.name for p in ar.write_reports(run_dir)}
    assert written == {
        "report-business.md",
        "report-business.html",
        "report-dev.md",
        "report-dev.html",
    }
    page = (run_dir / "report-business.html").read_text(encoding="utf-8")
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert "\\| pipe" in (run_dir / "report-business.md").read_text(encoding="utf-8")


def test_reports_need_no_ground_truth(tmp_path):
    """They are for real targets, which ship no defect list (ADR 0007)."""
    run_dir = _audience_run(tmp_path, [_case("a")], meta={"target": str(tmp_path / "nowhere")})
    assert len(ar.write_reports(run_dir)) == 4
    assert "plugin `not recorded`" in (run_dir / "report-dev.md").read_text(encoding="utf-8")


def test_ecvt_report_command(tmp_path, capsys):
    from ecvt import cli

    run_dir = _audience_run(tmp_path, [_case("a")])
    assert cli.main(["report", str(run_dir)]) == 0
    assert "report-business.html" in capsys.readouterr().out
    assert cli.main(["report", str(tmp_path / "missing")]) == 1
    assert "is it a run directory" in capsys.readouterr().err
