"""Execution Agent — runs a ScenarioSet against a live target (M1-T3).

Pipeline position: second agent of the M1 chain. Reads `scenarios.jsonl` from
the Scenario Generator, runs every scenario against the target over HTTP, and
writes `executions.jsonl` for Evaluation.

Makes no model calls (ADR 0009): scoring is Evaluation's job, and running HTTP
needs no reasoning. The 5 agent files land in M1-T4, when the Manager starts
dispatching this agent as a subagent.

CLI:
    python -m agents.a05_execution.agent \
        --scenarios artifacts/scenarios.jsonl \
        --base-url http://localhost:8000 \
        --out artifacts/executions.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from ecvt.execution import DEFAULT_BASE_URL, RunnerConfig, run_scenarios
from ecvt.results import ExecutionRecord, RunOutcome, write_executions_jsonl
from ecvt.schema import read_scenarios_jsonl

AGENT_DIR = Path(__file__).resolve().parent
AGENT_NAME = "execution"


def load_system_prompt() -> str:
    return (AGENT_DIR / "system_prompt.md").read_text(encoding="utf-8")


def summarize(records: list[ExecutionRecord]) -> str:
    outcomes = Counter(r.outcome.value for r in records)
    statuses = Counter(
        str(step.status_code) if step.status_code is not None else "no-response"
        for r in records
        for step in r.actions
    )
    lines = [
        f"{len(records)} scenarios run",
        "outcomes: " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())),
        "action statuses: " + ", ".join(f"{k}={v}" for k, v in sorted(statuses.items())),
    ]
    stalled = [r for r in records if r.outcome is not RunOutcome.COMPLETED]
    if stalled:
        lines.append("did not complete:")
        lines.extend(f"  {r.scenario_id} [{r.outcome.value}] {r.note or ''}" for r in stalled)
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ECVT scenarios against a live target.")
    parser.add_argument("--scenarios", default="artifacts/scenarios.jsonl")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--out", default="artifacts/executions.jsonl")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout (s).")
    parser.add_argument(
        "--retries", type=int, default=2, help="Retries for no-response failures only."
    )
    parser.add_argument("--only", help="Run a single scenario by id.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        scenarios = read_scenarios_jsonl(args.scenarios)
    except (OSError, ValueError) as exc:
        print(f"Could not read scenarios: {exc}", file=sys.stderr)
        return 1

    if args.only:
        scenarios = [s for s in scenarios if s.id == args.only]
        if not scenarios:
            print(f"No scenario with id {args.only!r}", file=sys.stderr)
            return 1

    config = RunnerConfig(
        base_url=args.base_url, timeout_s=args.timeout, max_transport_retries=args.retries
    )
    records = asyncio.run(run_scenarios(scenarios, config))

    out_path = write_executions_jsonl(args.out, records)
    print(summarize(records))
    print(f"-> {out_path}")

    # Exit 0 even when scenarios failed: a 500 from the target is a finding,
    # not a runner error. Only an empty run is a failure of this step.
    return 0 if records else 1


if __name__ == "__main__":
    sys.exit(main())
