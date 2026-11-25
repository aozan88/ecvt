"""Catch rate over N runs — min / median / max instead of one noisy number (M2-T2).

One run is not a measurement: with the same code the M1 chain measured 8/10 once and
3/10 another time, because the generator writes a different scenario set every run.
This script repeats the run and reports the spread, so "did the DomainModel help?"
can be answered by comparing two spreads rather than two single draws.

    # M1 baseline: spec only, no domain model
    python scripts/n_run.py --runs 3 --label m1
    # M2: through the Manager, a02's DomainModel feeds the generator
    python scripts/n_run.py --runs 3 --label m2 --manager

    # Execution/scoring noise only: replay one frozen scenario set
    python scripts/n_run.py --runs 3 --label fixed --scenarios path/to/scenarios.jsonl

The target must be up (uvicorn app:app --app-dir examples/wallet-api --port 8000).
Results go to artifacts/nrun/<label>/ and a one-line summary to stdout.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ecvt import cli
from ecvt.reporting import build_report


def _run_dir(out_dir: Path, manager: bool) -> Path | None:
    if not manager:
        return out_dir
    runs = sorted((out_dir / "runs").glob("run_*"))
    return runs[-1] if runs else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--label", required=True, help="Folder name under artifacts/nrun/.")
    p.add_argument("--target", default="examples/wallet-api")
    p.add_argument("--manager", action="store_true", help="Route each run through the Manager.")
    p.add_argument("--plugin", default="rest_api", help="Plugin for every run (M3).")
    p.add_argument("--scenarios", help="Replay this frozen scenarios.jsonl every run (M1 chain).")
    args = p.parse_args(argv)

    baseline = Path(args.target) / "ground_truth.yaml"
    root = REPO_ROOT / "artifacts" / "nrun" / args.label
    results: list[dict] = []
    for i in range(1, args.runs + 1):
        out_dir = root / f"run{i}"
        cli_args = ["run", "--target", args.target, "--out-dir", str(out_dir)]
        cli_args += ["--plugin", args.plugin]
        if args.manager:
            cli_args.append("--manager")
        if args.scenarios:
            cli_args += ["--scenarios", args.scenarios]
        print(f"=== {args.label} {i}/{args.runs}", flush=True)
        code = cli.main(cli_args)
        run_dir = _run_dir(out_dir, args.manager)
        if code != 0 or run_dir is None or not (run_dir / "scores.jsonl").exists():
            results.append({"run": i, "ok": False})
            continue
        _, stats = build_report(run_dir, baseline)
        results.append({"run": i, "ok": True, "caught": stats["caught"],
                        "known": stats["known_bugs"], "cost_usd": stats.get("cost_usd")})

    caught = [r["caught"] for r in results if r["ok"]]
    known = next((r["known"] for r in results if r["ok"]), None)
    summary = {
        "label": args.label,
        "runs": args.runs,
        "completed": len(caught),
        "known_bugs": known,
        "min": min(caught) if caught else None,
        "median": statistics.median(caught) if caught else None,
        "max": max(caught) if caught else None,
        "per_run": results,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if caught:
        print(f"\n{args.label}: catch rate over {len(caught)} runs — "
              f"min {summary['min']}/{known} · median {summary['median']}/{known} · "
              f"max {summary['max']}/{known}  → {root}/summary.json")
    else:
        print(f"\n{args.label}: no run completed", file=sys.stderr)
    return 0 if caught else 1


if __name__ == "__main__":
    sys.exit(main())
