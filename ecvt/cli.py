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
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.a06_evaluation.agent import DEFAULT_JUDGE_PARALLEL
from ecvt.execution import DEFAULT_BASE_URL
from ecvt.llm import DEFAULT_SUBAGENT_MODEL, Usage, load_env
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
from plugins._base.plugin import PluginContext
from plugins._base.plugin import load as load_plugin

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
    # Resolved before anything is spent: an unknown or unimplemented plugin
    # must fail here, not after a paid scenario-generation call.
    plugin = load_plugin(args.plugin, PluginContext(base_url=args.base_url, timeout_s=args.timeout))
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
        # A run directory stands on its own: the reports read the scenarios from
        # it, so a reused set is copied in rather than referenced.
        if Path(args.scenarios).resolve() != scenarios_path.resolve():
            shutil.copyfile(args.scenarios, scenarios_path)
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
    _stage(f"running against {args.base_url} via {plugin.name}", 2, total)
    records = asyncio.run(plugin.execute(scenarios))
    write_executions_jsonl(executions_path, records)
    outcomes: dict[str, int] = {}
    for record in records:
        outcomes[record.outcome.value] = outcomes.get(record.outcome.value, 0) + 1
    print("      " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))

    # --- 3. evaluate
    from agents.a06_evaluation.agent import EvalServerError, score_scenarios

    _stage("scoring" + (" (rules only)" if args.no_judge else " (rules + judge)"), 3, total)
    try:
        scores, judge_usage = score_scenarios(
            scenarios,
            records,
            model=args.model,
            use_judge=not args.no_judge,
            max_parallel=args.max_parallel,
            plugin=plugin.name,
        )
    except EvalServerError as exc:
        raise StageError(str(exc)) from exc
    if not scores:
        raise StageError("no scenario had a matching execution record")
    _merge(usage, judge_usage)
    write_scores_jsonl(scores_path, scores)

    stats = summarize(scores)
    elapsed = time.perf_counter() - started
    run_meta: dict[str, Any] = {
        "target": args.target,
        "base_url": args.base_url,
        "plugin": plugin.name,
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


def manager_request(target: str, plugin: str) -> str:
    """What `ecvt run --manager` asks the Manager. Slots are spelled out so the
    intent and slot extraction is not what a CLI run is testing."""
    return (
        f"Run a full validation of target_system={target} with plugin={plugin}: "
        "build the domain model, generate scenarios, execute them and score the results."
    )


def _a03_gets_domain_model() -> bool:
    from agents.a01_manager.runners import a03_gets_domain_model

    return a03_gets_domain_model()


def export_run_files(state: dict[str, Any], runs_dir: Path, elapsed_s: float = 0.0) -> Path:
    """Turn the Manager's per-stage JSON artifacts into the M1 file set
    (scenarios / executions / scores .jsonl + run.json) so reporting is unchanged."""
    from ecvt.results import ExecutionRecord
    from ecvt.schema import ScenarioSet
    from ecvt.scoring import ScenarioScore

    run_dir = runs_dir / state["run_id"]

    def artifact(stage: str) -> dict[str, Any] | None:
        ref = state["stages"][stage]["artifact_ref"]
        return json.loads((runs_dir / ref).read_text(encoding="utf-8")) if ref else None

    if (sg := artifact("scenario_gen")) is not None:
        write_scenarios_jsonl(
            run_dir / "scenarios.jsonl", ScenarioSet.model_validate(sg["scenario_set"])
        )
    if (ex := artifact("execution")) is not None:
        write_executions_jsonl(
            run_dir / "executions.jsonl",
            [ExecutionRecord.model_validate(r) for r in ex["records"]],
        )
    if (ev := artifact("evaluation")) is not None:
        write_scores_jsonl(
            run_dir / "scores.jsonl", [ScenarioScore.model_validate(x) for x in ev["scores"]]
        )

    stage_usage = [state["stages"][s].get("usage") or {} for s in state["plan"]]
    session = state.get("manager_session") or {}
    subagent_cost = sum(float(u.get("cost_usd") or 0) for u in stage_usage)
    manager_cost = float(session.get("total_cost_usd") or 0)
    meta = {
        "target": state["intent"]["slots"].get("target_system") if state["intent"] else None,
        "base_url": os.environ.get("ECVT_BASE_URL", DEFAULT_BASE_URL),
        "plugin": (state["intent"]["slots"].get("plugin") if state["intent"] else None)
        or "rest_api",
        "via": "manager",
        "run_id": state["run_id"],
        "elapsed_s": round(elapsed_s, 3),
        "status": state["status"],
        "stages": {s: state["stages"][s]["status"] for s in state["plan"]},
        "judged": (state["stages"]["evaluation"].get("metrics") or {}).get("judged"),
        # Always on: --no-judge is refused with --manager (MANAGER_UNSUPPORTED),
        # so there is no run through here with the judge off.
        "judge_enabled": True,
        "model": os.environ.get("ECVT_SUBAGENT_MODEL"),
        # Which arm of ADR 0012 this run belongs to; without it two runs that
        # differ only in what a03 was handed cannot be told apart afterwards.
        "a03_domain_model": "on" if _a03_gets_domain_model() else "off",
        "usage": {
            "total_tokens": sum(int(u.get("total_tokens") or 0) for u in stage_usage),
            "cost_usd": round(subagent_cost + manager_cost, 6),
            "subagent_cost_usd": round(subagent_cost, 6),
            "manager_cost_usd": round(manager_cost, 6),
            "manager_turns": session.get("num_turns"),
            # Tokens and cost per model inside the Manager's own session, as the
            # SDK reports them. manager_cost_usd above is their total and stays;
            # this says which model spent it. The Manager runs on Sonnet, so a
            # Haiku row here is what relaying stage results cost, not the work
            # itself — the stage runners' own spend is subagent_cost_usd.
            "manager_model_usage": session.get("model_usage") or {},
        },
    }
    (run_dir / "run.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return run_dir


# Flags run_chain honours that run_via_manager cannot: a stage runner is called
# with a `stage_input` dict (run_id, stage, slots, upstream — see
# agents/a01_manager/runners.py) that has nowhere to carry them. Ignoring one
# silently turned a measurement into a wrong measurement: `n_run.py --manager
# --scenarios <set>` reported the scenario set as frozen while the Manager
# generated a fresh one every run. They are refused instead.
MANAGER_UNSUPPORTED = ("scenarios", "no_judge", "per_category", "timeout", "max_parallel")


def unsupported_with_manager(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> list[str]:
    """The --manager-incompatible flags this invocation actually set.

    Compared against a bare `run`, not `parser.get_default()`: these options are
    declared on the `run` subparser, so the top-level parser reports None for
    every one of them and each would read as changed.
    """
    bare = parser.parse_args(["run"])
    return [
        "--" + dest.replace("_", "-")
        for dest in MANAGER_UNSUPPORTED
        if getattr(args, dest, None) != getattr(bare, dest, None)
    ]


def run_via_manager(args: argparse.Namespace) -> int:
    """M2-T4: the same run, routed through the Manager (intent -> plan -> 4 stages)."""
    from agents.a01_manager.agent import new_run_id, run_manager

    # Same fail-fast as run_chain: the Manager would otherwise spend a02 and a03
    # before its execution stage found the plugin unusable.
    load_plugin(args.plugin, PluginContext(base_url=args.base_url))
    os.environ["ECVT_BASE_URL"] = args.base_url
    os.environ["ECVT_SUBAGENT_MODEL"] = args.model
    runs_dir = Path(args.out_dir) / "runs"
    run_id = new_run_id()
    started = time.perf_counter()
    print(f"[manager] {run_id}", flush=True)

    async def drive() -> None:
        async for msg in run_manager(
            manager_request(args.target, args.plugin),
            run_id=run_id,
            runs_dir=runs_dir,
            max_budget_usd=args.max_budget,
        ):
            for block in getattr(msg, "content", None) or []:
                if type(block).__name__ == "ToolUseBlock":
                    print(f"      -> {block.name.split('__')[-1]}", flush=True)

    asyncio.run(drive())
    state = json.loads((runs_dir / run_id / "state.json").read_text(encoding="utf-8"))
    for s in state["plan"]:
        rec = state["stages"][s]
        print(f"      {s}: {rec['status']} (attempts {rec['attempts']}) {rec['summary'] or ''}")
    out_dir = export_run_files(state, runs_dir, time.perf_counter() - started)
    print(f"      {state['status']} in {time.perf_counter() - started:.1f}s → {out_dir}/")
    if (state["result"] or {}).get("summary"):
        print(f"      {state['result']['summary']}")
    if state["status"] == "completed" and (args.report or args.record):
        _write_report(args, out_dir)
    return 0 if state["status"] == "completed" else 1


def _write_report(args: argparse.Namespace, out_dir: Path) -> None:
    """Catch rate against the target's baseline, if it has one.

    Part of `run` rather than a separate command: as its own step it was one
    more thing to remember, and a run without its number is half a result.
    A target with no baseline is normal — not every system ships one — so the
    absence is reported, not treated as a failure.
    """
    from ecvt.audience_reports import write_reports

    written = write_reports(out_dir)
    print(f"      Dev + Business reports → {', '.join(p.name for p in written)}")

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

    report = sub.add_parser(
        "report", help="Write the Dev and Business reports (Markdown + HTML) for a run."
    )
    report.add_argument("run_dir", help="A run directory written by `ecvt run`.")

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
    run.add_argument("--scenarios", help="Reuse an existing scenarios.jsonl instead of generating.")
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
        help="Also write the Dev + Business reports and, with a baseline, report.md (catch rate).",
    )
    run.add_argument(
        "--manager",
        action="store_true",
        help="Route the run through the Manager (M2): intent, plan, then all four stages.",
    )
    run.add_argument(
        "--plugin",
        default="rest_api",
        help="Plugin that executes the scenarios (a directory under plugins/).",
    )
    run.add_argument(
        "--max-budget", type=float, default=2.0, help="USD cap on the Manager's own turns."
    )
    run.add_argument(
        "--record",
        action="store_true",
        help=f"Write the report and append a row to {TREND_DOC.name}. Implies --report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "report":
        from ecvt.audience_reports import write_reports

        try:
            written = write_reports(args.run_dir)
        except (OSError, ValueError) as exc:
            print(f"Could not write the reports: {exc}", file=sys.stderr)
            return 1
        for path in written:
            print(path)
        return 0

    if args.command != "run":
        parser.print_help()
        return 1

    if args.manager and (ignored := unsupported_with_manager(parser, args)):
        print(
            f"--manager cannot honour {', '.join(ignored)}. "
            "Drop the flag, or run the fixed chain without --manager.",
            file=sys.stderr,
        )
        return 2

    try:
        return run_via_manager(args) if args.manager else run_chain(args)
    except StageError as exc:
        print(f"\nStopped: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"\nStopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
