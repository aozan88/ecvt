"""Milestone gate checker (Master Plan v7, M0-T5).

Usage: python scripts/gate_check.py M0 [--verbose]

Reads gates/<milestone>.yaml, runs each check from the repo root and prints a
PASS / FAIL / SKIP table. Exit code: 0 = all PASS, 1 = any FAIL, 2 = no FAIL
but some SKIP (gate not closable until the skipped checks run).

Check fields:
  id, desc, cmd          required; cmd runs under bash -c
  expect_exit            default 0
  expect_stdout          substring that must appear in stdout
  requires_env           env vars that must be set, else SKIP
  timeout                seconds, default 60
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def run_check(check: dict) -> tuple[str, str, float]:
    missing = [v for v in check.get("requires_env", []) if not os.environ.get(v)]
    if missing:
        return "SKIP", f"missing env: {', '.join(missing)}", 0.0

    start = time.monotonic()
    try:
        proc = subprocess.run(
            ["bash", "-c", check["cmd"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=check.get("timeout", 60),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "FAIL", "timeout", time.monotonic() - start
    elapsed = time.monotonic() - start

    out = (proc.stdout + proc.stderr).strip()
    tail = out.splitlines()[-1] if out else ""
    if proc.returncode != check.get("expect_exit", 0):
        return "FAIL", f"exit {proc.returncode}: {tail}"[:120], elapsed
    want = check.get("expect_stdout")
    if want is not None and str(want) not in proc.stdout:
        return "FAIL", f"stdout missing {want!r}: {tail}"[:120], elapsed
    return "PASS", tail[:120], elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("milestone", help="e.g. M0")
    parser.add_argument("--verbose", "-v", action="store_true", help="show detail column")
    parser.add_argument(
        "--format",
        choices=("table", "plain"),
        default="table",
        help="table = markdown (default); plain = text that pastes into Google Docs.",
    )
    args = parser.parse_args()

    # Before any requires_env test. The gate reads os.environ directly, so with
    # the key in .env — where the project tells people to keep it — it skipped
    # five of G2's nine checks and still printed a result, which is how a gate
    # reports a milestone as not closable for the wrong reason.
    sys.path.insert(0, str(ROOT))
    from ecvt.llm import load_env

    load_env()

    gate_file = ROOT / "gates" / f"{args.milestone.upper()}.yaml"
    if not gate_file.exists():
        print(f"No gate file: {gate_file.relative_to(ROOT)}", file=sys.stderr)
        return 1
    spec = yaml.safe_load(gate_file.read_text(encoding="utf-8"))

    rows = []
    for check in spec["checks"]:
        status, detail, secs = run_check(check)
        rows.append((check["id"], check["desc"], status, secs, detail))

    if args.format == "table":
        print(f"Gate {spec['gate']} · {spec['milestone']}\n")
        print(f"| ID | Check | Result | s |{' Detail |' if args.verbose else ''}")
        print(f"|---|---|---|---|{'---|' if args.verbose else ''}")
        for cid, desc, status, secs, detail in rows:
            extra = f" {detail.replace('|', '/')} |" if args.verbose else ""
            print(f"| {cid} | {desc} | {status} | {secs:.1f} |{extra}")
    else:
        # Plain text pastes cleanly into Google Docs, where a markdown table
        # arrives as a wall of pipe characters rather than a table.
        print(f"Gate {spec['gate']} · {spec['milestone']}")
        for cid, desc, status, secs, detail in rows:
            print(f"  {status:4}  {cid}  {desc} ({secs:.1f}s)")
            if args.verbose and detail:
                print(f"            {detail}")

    statuses = [r[2] for r in rows]
    n_pass, n_fail, n_skip = (statuses.count(s) for s in ("PASS", "FAIL", "SKIP"))
    verdict = "PASS" if n_pass == len(statuses) else ("FAIL" if n_fail else "INCOMPLETE")
    print(f"\n{spec['gate']}: {verdict} ({n_pass} pass · {n_fail} fail · {n_skip} skip)")
    return {"PASS": 0, "FAIL": 1}.get(verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
