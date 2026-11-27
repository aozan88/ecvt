"""Catch-rate report for one run (M1-T8).

Reads a run directory (`scores.jsonl`, `executions.jsonl`, `run.json`) plus the
target's `ground_truth.yaml`, and answers gate G1's question: of the defects we
know are in the target, how many did this run catch?

**This is the only place ground truth is read.** Scenario Generator, Execution
and Evaluation never see it (ADR 0007) — a pipeline that knows the answers
measures nothing.

Attribution is a heuristic and the report says so. A failing scenario is
attributed to a known bug when it hit the same endpoint and its QE category
belongs to that bug's family. It can over- and under-attribute, so the report
prints the pairing it used: the number is reviewable, not an oracle.

Usually reached through the chain rather than directly:

    ecvt run --target examples/wallet-api --record

Also available on its own, to re-report an earlier run:

    python scripts/report.py artifacts/runs/20261102T125003Z --record
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ecvt.results import read_executions_jsonl
from ecvt.scoring import Dimension, ScenarioScore, Verdict, read_scores_jsonl

REPO_ROOT = Path(__file__).resolve().parent.parent
TREND_DOC = REPO_ROOT / "docs" / "09-evaluation" / "catch-rate.md"
LATEST_DOC = REPO_ROOT / "docs" / "09-evaluation" / "latest-run.md"

#: Failures first — the rows a reader opens the report for.
VERDICT_ORDER = {
    Verdict.FAIL: 0,
    Verdict.INCONCLUSIVE: 1,
    Verdict.NOT_RUN: 2,
    Verdict.PASS: 3,
}
TREND_MARKER = "<!-- rows appended by scripts/report.py --record -->"

TREND_HEADER = """# Catch rate over time

One row per recorded run of `ecvt run` against a target with a known-defect
baseline. Written by `ecvt run --record`.

`Caught` is heuristic and one-to-one: each failing scenario is credited with at
most one defect, so the rate cannot be inflated by one scenario tripping many.
See "How attribution works" in any run report. `Unattributed` counts failing
scenarios that fit no known defect — findings outside the baseline, or false
positives.

This table is the Regression Agent's baseline (v1.2, M5): it compares new runs
against these rows rather than starting from nothing.

| Date | Commit | Caught | Unattributed | Scenarios | Judged | Tokens | Cost USD | Seconds | Model |
|---|---|---|---|---|---|---|---|---|---|
"""


#: Which scenario categories plausibly expose each ground-truth category.
#: Ground truth is written in defect language ("adversarial-idempotency");
#: scenarios are written in QE-category language ("adversarial"). This table
#: is the bridge, and it is deliberately generous — a missed attribution looks
#: like a missed defect, which is the more misleading error of the two.
CATEGORY_FAMILIES: dict[str, set[str]] = {
    "numeric": {"edge", "negative", "stress"},
    "negative-validation": {"negative", "edge", "adversarial"},
    "boundary": {"edge", "stress"},
    "security-authz": {"security", "adversarial", "persona"},
    "adversarial-idempotency": {"adversarial", "stress"},
    "stress-race": {"stress", "adversarial"},
    "error-handling": {"negative", "edge", "persona"},
    "edge": {"edge", "persona", "happy"},
    "validation-stress": {"negative", "stress", "edge"},
}


@dataclass
class KnownBug:
    id: str
    category: str
    description: str
    endpoints: list[tuple[str, str]]
    """(method, path tail) pairs, parsed from the ground-truth `endpoint` field."""

    caught_by: list[str] = field(default_factory=list)

    @property
    def caught(self) -> bool:
        return bool(self.caught_by)


def _path_tail(path: str) -> str:
    """Last path segment that is not a {placeholder}, lowercased.

    Ground truth writes endpoints loosely ("GET /balance") while a scenario
    carries the real path ("GET /accounts/{account_id}/balance"), so the tail
    is what the two have in common.
    """
    parts = [p for p in path.strip("/").split("/") if p and not p.startswith("{")]
    return parts[-1].lower() if parts else ""


def parse_endpoints(raw: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for chunk in raw.split(";"):
        bits = chunk.strip().split(None, 1)
        if len(bits) == 2:
            out.append((bits[0].upper(), _path_tail(bits[1])))
    return out


def load_ground_truth(path: str | Path) -> list[KnownBug]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    bugs = []
    for item in data.get("bugs", []):
        bugs.append(
            KnownBug(
                id=str(item["id"]),
                category=str(item["category"]),
                description=str(item.get("description", "")),
                endpoints=parse_endpoints(str(item.get("endpoint", ""))),
            )
        )
    if not bugs:
        raise ValueError(f"{path}: no bugs listed")
    return bugs


@dataclass
class Attribution:
    unmatched: list[str] = field(default_factory=list)
    """Failing scenarios that fit no known defect: a finding outside the
    baseline, or a false positive. Either way, a human has to read it."""

    redundant: list[str] = field(default_factory=list)
    """Failing scenarios that fit a defect another scenario was already
    credited with — extra coverage, not extra defects."""


def attribute(
    scores: list[ScenarioScore], endpoints: dict[str, str], bugs: list[KnownBug]
) -> Attribution:
    """Credit failing scenarios to known defects, at most one each way.

    The pairing is one-to-one on purpose. Several of wallet-api's defects sit
    on `POST /transfers`, so crediting every plausible pair lets a single
    overdraft scenario claim six defects and report 8/10 — a number that moves
    with the category table rather than with ECVT's actual detection. Capping
    it at one defect per scenario keeps the rate bounded by how many distinct
    things the run actually found, which is what M2/M3 compare across runs.

    Pairs are assigned most-specific-family first, so a narrow category wins
    the defect it fits best before a broad one takes it.
    """
    result = Attribution()
    pairs: list[tuple[int, str, str, KnownBug]] = []
    failing = [s for s in scores if s.defect_suspected]

    for score in failing:
        method, _, path = (endpoints.get(score.scenario_id) or "").partition(" ")
        tail = _path_tail(path)
        for bug in bugs:
            family = CATEGORY_FAMILIES.get(bug.category, set())
            same_endpoint = any(m == method.upper() and t == tail for m, t in bug.endpoints)
            if same_endpoint and score.category in family:
                pairs.append((len(family), bug.id, score.scenario_id, bug))

    pairs.sort(key=lambda p: (p[0], p[1], p[2]))
    used_scenarios: set[str] = set()
    for _, _, scenario_id, bug in pairs:
        if scenario_id in used_scenarios or bug.caught:
            continue
        bug.caught_by.append(scenario_id)
        used_scenarios.add(scenario_id)

    had_candidate = {p[2] for p in pairs}
    for score in failing:
        if score.scenario_id in used_scenarios:
            continue
        if score.scenario_id in had_candidate:
            result.redundant.append(score.scenario_id)
        else:
            result.unmatched.append(score.scenario_id)

    return result


def action_endpoints(run_dir: Path) -> dict[str, str]:
    """scenario_id -> the action's endpoint, taken from the execution records."""
    out: dict[str, str] = {}
    for record in read_executions_jsonl(run_dir / "executions.jsonl"):
        if record.actions:
            out[record.scenario_id] = record.actions[0].endpoint
    return out


def build_report(run_dir: Path, ground_truth: Path) -> tuple[str, dict[str, Any]]:
    scores = read_scores_jsonl(run_dir / "scores.jsonl")
    endpoints = action_endpoints(run_dir)
    bugs = load_ground_truth(ground_truth)
    attribution = attribute(scores, endpoints, bugs)

    meta: dict[str, Any] = {}
    run_json = run_dir / "run.json"
    if run_json.exists():
        meta = json.loads(run_json.read_text(encoding="utf-8"))

    caught = [b for b in bugs if b.caught]
    missed = [b for b in bugs if not b.caught]
    verdicts: dict[str, int] = {}
    for score in scores:
        verdicts[score.verdict.value] = verdicts.get(score.verdict.value, 0) + 1

    usage = meta.get("usage") or {}
    stats = {
        "run": run_dir.name,
        "catch_rate": f"{len(caught)}/{len(bugs)}",
        "caught": len(caught),
        "known_bugs": len(bugs),
        "unattributed_failures": len(attribution.unmatched),
        "redundant_failures": len(attribution.redundant),
        "scenarios": len(scores),
        "verdicts": verdicts,
        "judged": meta.get("judged"),
        "tokens": usage.get("total_tokens"),
        "cost_usd": usage.get("cost_usd"),
        "elapsed_s": meta.get("elapsed_s"),
        "model": meta.get("model"),
        "judge_enabled": meta.get("judge_enabled"),
    }

    lines = [
        f"# ECVT run report — {run_dir.name}",
        "",
        f"**Catch rate: {len(caught)}/{len(bugs)}** known defects detected.",
        "",
        f"- Target: `{meta.get('target', 'unknown')}` at {meta.get('base_url', '?')}",
        f"- Scenarios: {len(scores)} · "
        + ", ".join(f"{k}={v}" for k, v in sorted(verdicts.items())),
        f"- Judge: {'on' if meta.get('judge_enabled') else 'off'}"
        + (f" · {meta['judged']} scenarios judged" if meta.get("judged") is not None else ""),
        (
            f"- Tokens: {usage.get('total_tokens', '?')} · "
            f"cost: {usage.get('cost_usd', '?')} USD · {meta.get('elapsed_s', '?')}s"
        ),
        "",
        "## Scenario results",
        "",
        "Every scenario this run produced, with the verdict and why. `Judged`",
        "marks the ones that cost a model call; the rest were settled by rules.",
        "",
        "| Scenario | Category | Verdict | Judged | Why |",
        "|---|---|---|---|---|",
    ]
    for score in sorted(scores, key=lambda s: (VERDICT_ORDER.get(s.verdict, 9), s.scenario_id)):
        judged = "yes" if score.dimension(Dimension.JUDGE) is not None else "—"
        summary = score.summary.replace("|", "/")
        lines.append(
            f"| `{score.scenario_id}` | {score.category} | "
            f"{score.verdict.value} | {judged} | {summary} |"
        )

    lines += [
        "",
        "## Known defects",
        "",
        "| Bug | Category | Caught by | Description |",
        "|---|---|---|---|",
    ]
    for bug in bugs:
        mark = ", ".join(bug.caught_by) if bug.caught_by else "—"
        lines.append(f"| {bug.id} | {bug.category} | {mark} | {bug.description} |")

    lines += ["", "## Not detected", ""]
    lines += (
        [f"- **{b.id}** ({b.category}) — {b.description}" for b in missed]
        if missed
        else ["Every known defect was attributed to at least one failing scenario."]
    )

    lines += ["", "## Failures with no known defect", ""]
    if attribution.unmatched:
        lines.append(
            "These scenarios failed but fit no entry in the baseline. Each is either "
            "a defect outside the baseline or a false positive — both need a human "
            "read, and the difference matters:"
        )
        lines += [f"- `{sid}`" for sid in attribution.unmatched]
    else:
        lines.append("None.")

    if attribution.redundant:
        lines += [
            "",
            "## Redundant failures",
            "",
            (
                "These fit a defect another scenario was already credited with. "
                "Extra coverage of the same problem, not extra problems:"
            ),
            *[f"- `{sid}`" for sid in attribution.redundant],
        ]

    lines += [
        "",
        "## How attribution works",
        "",
        "A failing scenario is credited to a known defect when it hit the same",
        "endpoint and its QE category belongs to that defect's family (see",
        "`CATEGORY_FAMILIES` in `ecvt/reporting.py`).",
        "",
        "The pairing is **one-to-one**: each failing scenario is credited with at",
        "most one defect, and each defect to at most one scenario. Without that cap,",
        "a single overdraft scenario on `POST /transfers` could claim six of the",
        "defects that live on that endpoint and report 8/10 — a number that moves",
        "with the category table rather than with what ECVT actually detected. The",
        "rate is therefore bounded by how many distinct problems the run found.",
        "",
        "**Which** scenario is paired with **which** defect is indicative only.",
        "Several defects share an endpoint and a category family, so a tie is broken",
        "arbitrarily: a negative-amount scenario may be credited to the float-money",
        "defect rather than the missing-validation one. What a category cannot tell",
        "us, the scenario's own reasoning and the judge's evidence could — matching",
        "on that text instead is the improvement to make when regression comparison",
        "starts depending on per-defect rows (v1.2, M5).",
        "",
        "The count is the metric: how many distinct known defects this run plausibly",
        "surfaced. It is bounded by the number of failing scenarios and computed the",
        "same way every run, so it is comparable across runs even where individual",
        "rows are off. Ground truth is read here and nowhere else (ADR 0007).",
        "",
    ]
    return "\n".join(lines), stats


def trend_row(stats: dict[str, Any], commit: str) -> str:
    cost = stats.get("cost_usd")
    return (
        f"| {datetime.now(UTC).strftime('%Y-%m-%d')} "
        f"| `{commit}` "
        f"| {stats['catch_rate']} "
        f"| {stats['unattributed_failures']} "
        f"| {stats['scenarios']} "
        f"| {stats.get('judged', '?')} "
        f"| {stats.get('tokens', '?')} "
        f"| {cost if cost is not None else '?'} "
        f"| {stats.get('elapsed_s', '?')} "
        f"| {stats.get('model', '?')} |"
    )


def record_latest(report: str, commit: str, doc: Path | None = None) -> Path:
    """Keep the newest recorded run's full report in the repo.

    The trend table answers "is the catch rate moving"; this answers "which
    scenarios, and why". The per-run copy lives in `artifacts/`, which is
    gitignored, so without this the scenario-level results existed only on
    whichever machine ran the chain.

    One file, overwritten, so the repo gains a readable snapshot and git
    history gains the diff between runs — which is what the Regression agent
    (v1.2, M5) will want to read. It is also the sample report M2-T7 publishes.
    """
    doc = doc or LATEST_DOC
    doc.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "<!-- Generated by `ecvt run --record`. Overwritten each recorded run;\n"
        "     the per-run copies stay in artifacts/. Do not edit by hand. -->\n"
        f"<!-- commit {commit} -->\n\n"
    )
    doc.write_text(header + report.rstrip("\n") + "\n", encoding="utf-8")
    return doc


def record_trend(stats: dict[str, Any], commit: str, doc: Path | None = None) -> Path:
    """Append one row to the committed trend table — the Regression baseline.

    `doc` resolves at call time rather than as a default argument value, so a
    caller (or a test) that redirects TREND_DOC is honoured. Bound as a
    default it was read once at import and a test wrote into the real repo.
    """
    doc = doc or TREND_DOC
    if not doc.exists():
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            TREND_HEADER + TREND_MARKER + "\n",
            encoding="utf-8",
        )

    text = doc.read_text(encoding="utf-8")
    row = trend_row(stats, commit)
    if TREND_MARKER in text:
        text = text.replace(TREND_MARKER, f"{TREND_MARKER}\n{row}")
    else:
        text = text.rstrip("\n") + f"\n{row}\n"
    doc.write_text(text, encoding="utf-8")
    return doc


def current_commit() -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", help="A run directory written by `ecvt run`.")
    parser.add_argument(
        "--ground-truth",
        help="Baseline file. Defaults to <target>/ground_truth.yaml from run.json.",
    )
    parser.add_argument("--out", help="Where to write the report (default: <run_dir>/report.md).")
    parser.add_argument(
        "--record", action="store_true", help=f"Also append a row to {TREND_DOC.name}."
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    if not (run_dir / "scores.jsonl").exists():
        print(f"No scores.jsonl in {run_dir}", file=sys.stderr)
        return 1

    ground_truth = args.ground_truth
    if ground_truth is None:
        meta_path = run_dir / "run.json"
        target = (
            json.loads(meta_path.read_text(encoding="utf-8")).get("target")
            if meta_path.exists()
            else None
        )
        if not target:
            print("Could not infer the baseline; pass --ground-truth.", file=sys.stderr)
            return 1
        ground_truth = str(Path(target) / "ground_truth.yaml")

    if not Path(ground_truth).exists():
        print(f"No baseline at {ground_truth}", file=sys.stderr)
        return 1

    try:
        report, stats = build_report(run_dir, Path(ground_truth))
    except (OSError, ValueError) as exc:
        print(f"Could not build the report: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else run_dir / "report.md"
    out_path.write_text(report + "\n", encoding="utf-8")

    print(f"catch rate: {stats['catch_rate']} known defects")
    print(f"unattributed failures: {stats['unattributed_failures']}")
    print(f"-> {out_path}")
    if args.record:
        commit = current_commit()
        trend = record_trend(stats, commit)
        latest = record_latest(report, commit)
        print(f"-> {trend.relative_to(REPO_ROOT)} (one row appended)")
        print(f"-> {latest.relative_to(REPO_ROOT)} (snapshot replaced)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
