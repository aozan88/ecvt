"""`ecvt run` — the M1 chain in one command (M1-T7).

    ecvt run --target examples/wallet-api

Generate → Execute → Evaluate, writing `scenarios.jsonl`, `executions.jsonl`
and `scores.jsonl` plus a `run.json` holding tokens, cost and timing.

The chain is deliberately a fixed sequence here, not an orchestrator: the
Manager that routes by intent arrives in M2 (ADR 0008). Each stage calls the
agent's own entry point, so when the Manager takes over it wraps these same
functions rather than replacing them (ADR 0009).

A stage that fails stops the chain and says which one, but anything already
written stays on disk — a run that died in Evaluation still leaves its
executions to look at.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.a06_evaluation.agent import DEFAULT_JUDGE_PARALLEL
from ecvt.execution import DEFAULT_BASE_URL, RunnerConfig, run_scenarios
from ecvt.llm import DEFAULT_SUBAGENT_MODEL, Usage
from ecvt.reporting import (
    REPO_ROOT,
    TREND_DOC,
    build_report,
    current_commit,
    record_latest,
    record_trend,
)
from ecvt.results import write_executions_jsonl
from ecvt.schema import read_scenarios_jsonl, write_scenarios_jsonl
from ecvt.scoring import summarize, write_scores_jsonl

SPEC_NAME = "openapi.json"


class StageError(RuntimeError):
    """A stage could not produce its output; the chain stops here."""


def _spec_path(target: str) -> Path:
    path = Path(target)
    if path.is_file():
        return path
    spec = path / SPEC_NAME
    if not spec.exists():
        raise StageError(
            f"no {SPEC_NAME} in {target}. Start the target and snapshot its spec, "
            f"e.g. curl -s {DEFAULT_BASE_URL}/openapi.json -o {spec}"
        )
    return spec


def _stage(label: str, index: int, total: int) -> None:
    print(f"[{index}/{total}] {label}", flush=True)


def run_chain(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    if args.timestamped:
        out_dir = out_dir / "runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)

    scenarios_path = out_dir / "scenarios.jsonl"
    executions_path = out_dir / "executions.jsonl"
    scores_path = out_dir / "scores.jsonl"
    total = 3
    started = time.perf_counter()
    usage = Usage(model=args.model)

    # --- 1. generate (or reuse)
    if args.scenarios:
        _stage(f"reusing scenarios from {args.scenarios}", 1, total)
        scenarios = read_scenarios_jsonl(args.scenarios)
        spec_title = None
    else:
        from agents.a03_scenario_generator.agent import generate_scenario_set
        from ecvt.llm import StructuredCallError

        _stage("generating scenarios", 1, total)
        try:
            scenario_set, result = generate_scenario_set(
                spec_path=_spec_path(args.target),
                target=args.target,
                model=args.model,
                per_category=args.per_category,
            )
        except StructuredCallError as exc:
            raise StageError(f"scenario generation failed: {exc}") from exc
        _merge(usage, result.usage)
        write_scenarios_jsonl(scenarios_path, scenario_set)
        scenarios = scenario_set.scenarios
        spec_title = scenario_set.spec_title
        counts = ", ".join(f"{k}={v}" for k, v in scenario_set.category_counts().items())
        print(f"      {len(scenarios)} scenarios · {counts}")

    if not scenarios:
        raise StageError("no scenarios to run")

    # --- 2. execute
    _stage(f"running against {args.base_url}", 2, total)
    config = RunnerConfig(base_url=args.base_url, timeout_s=args.timeout)
    records = asyncio.run(run_scenarios(scenarios, config))
    write_executions_jsonl(executions_path, records)
    outcomes: dict[str, int] = {}
    for record in records:
        outcomes[record.outcome.value] = outcomes.get(record.outcome.value, 0) + 1
    print("      " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))

    # --- 3. evaluate
    from agents.a06_evaluation.agent import score_scenarios

    _stage("scoring" + (" (rules only)" if args.no_judge else " (rules + judge)"), 3, total)
    scores, judge_usage = score_scenarios(
        scenarios,
        records,
        model=args.model,
        use_judge=not args.no_judge,
        max_parallel=args.max_parallel,
    )
    if not scores:
        raise StageError("no scenario had a matching execution record")
    _merge(usage, judge_usage)
    write_scores_jsonl(scores_path, scores)

    stats = summarize(scores)
    elapsed = time.perf_counter() - started
    run_meta: dict[str, Any] = {
        "target": args.target,
        "base_url": args.base_url,
        "spec_title": spec_title,
        "finished_at": datetime.now(UTC).isoformat(),
        # Millisecond precision on purpose: run.json is machine-read for the
        # trend table, and a fast run rounded to 0.0s loses the measurement.
        # The printed line below rounds for humans instead.
        "elapsed_s": round(elapsed, 3),
        "model": args.model,
        "judge_enabled": not args.no_judge,
        "scenarios": len(scenarios),
        **stats,
        "usage": usage.as_dict(),
    }
    (out_dir / "run.json").write_text(
        json.dumps(run_meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print()
    print("      " + ", ".join(f"{k}={v}" for k, v in sorted(stats["verdicts"].items())))
    print(f"      defects suspected: {stats['defects_suspected']} · judged: {stats['judged']}")
    print(f"      tokens: {usage.total_tokens} · cost: {usage.cost_usd()}")
    print(f"      {elapsed:.1f}s → {out_dir}/")

    if args.report or args.record:
        _write_report(args, out_dir)
    return 0


def _write_report(args: argparse.Namespace, out_dir: Path) -> None:
    """Catch rate against the target's baseline, if it has one.

    Part of `run` rather than a separate command: as its own step it was one
    more thing to remember, and a run without its number is half a result.
    A target with no baseline is normal — not every system ships one — so the
    absence is reported, not treated as a failure.
    """
    baseline = Path(args.target) / "ground_truth.yaml"
    if not baseline.exists():
        print(f"      no baseline at {baseline}, so no catch rate")
        return

    report, stats = build_report(out_dir, baseline)
    (out_dir / "report.md").write_text(report + "\n", encoding="utf-8")
    print(f"      catch rate: {stats['catch_rate']} known defects → {out_dir}/report.md")
    if stats["unattributed_failures"]:
        print(
            f"      {stats['unattributed_failures']} failure(s) fit no known defect"
            " — read them: outside the baseline, or false positives"
        )
    if args.record:
        commit = current_commit()
        trend = record_trend(stats, commit)
        latest = record_latest(report, commit)
        print(f"      recorded in {_display(trend)} and {_display(latest)}")


def _display(path: Path) -> str:
    """Repo-relative when it can be, absolute otherwise.

    Formatting a path must not be able to fail a run that already did its
    work: `relative_to` raises for anything outside the repo.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _merge(total: Usage, part: Usage) -> None:
    for name in ("standard", "long_prompt"):
        into, src = getattr(total, name), getattr(part, name)
        into.input_tokens += src.input_tokens
        into.output_tokens += src.output_tokens
        into.cache_read_tokens += src.cache_read_tokens
        into.cache_write_tokens += src.cache_write_tokens
        into.calls += src.calls


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecvt", description="ECVT — AI Quality Engineering.")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Generate, execute and score against a target.")
    run.add_argument(
        "--target",
        default="examples/wallet-api",
        help="Target directory holding openapi.json, or the spec file itself.",
    )
    run.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Where the target is running.")
    run.add_argument("--out-dir", default="artifacts", help="Where to write the three files.")
    run.add_argument(
        "--timestamped",
        action="store_true",
        help="Write to <out-dir>/runs/<UTC timestamp>/ instead of <out-dir>/.",
    )
    run.add_argument("--model", default=DEFAULT_SUBAGENT_MODEL)
    run.add_argument("--per-category", type=int, default=2)
    run.add_argument("--timeout", type=float, default=10.0)
    run.add_argument(
        "--scenarios", help="Reuse an existing scenarios.jsonl instead of generating."
    )
    run.add_argument(
        "--no-judge", action="store_true", help="Rules only — no model call while scoring."
    )
    run.add_argument(
        "--max-parallel",
        type=int,
        default=DEFAULT_JUDGE_PARALLEL,
        help="Judgements in flight at once.",
    )
    run.add_argument(
        "--report",
        action="store_true",
        help="Also write report.md with the catch rate against the target's baseline.",
    )
    run.add_argument(
        "--record",
        action="store_true",
        help=f"Write the report and append a row to {TREND_DOC.name}. Implies --report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "run":
        parser.print_help()
        return 1

    try:
        return run_chain(args)
    except StageError as exc:
        print(f"\nStopped: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"\nStopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
