"""Agent eval runner — PLACEHOLDER (M1-T2b).

The agent standard requires each agent to score >= 80% on its
`eval_dataset.jsonl` before it is "done", and the three M1 gates depend on
that number. This script is the stub for that runner: it loads a dataset,
validates its shape, and reports what it would score. It does **not** score
anything yet.

Scoring per `scoring` field, once implemented:
  schema-valid   run the agent, validate output against `expected_schema`
  keyword-match  check `expected_keywords` appear in the output
  llm-judge      score against `rubric` with a judge model

Usage:
    python scripts/eval_runner.py agents/a03_scenario_generator/eval_dataset.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = ("id", "input", "category", "scoring")
KNOWN_SCORING = ("schema-valid", "keyword-match", "llm-judge")
PASS_THRESHOLD = 0.80


def load_dataset(path: str | Path) -> list[dict[str, Any]]:
    """Read an eval dataset, checking each line against the agent standard."""
    path = Path(path)
    cases: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno} is not valid JSON: {exc}") from exc

        missing = [f for f in REQUIRED_FIELDS if f not in case]
        if missing:
            raise ValueError(f"{path}:{lineno} case missing {missing}")
        if case["scoring"] not in KNOWN_SCORING:
            raise ValueError(
                f"{path}:{lineno} unknown scoring {case['scoring']!r}; "
                f"expected one of {KNOWN_SCORING}"
            )
        cases.append(case)

    ids = [c["id"] for c in cases]
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    if dupes:
        raise ValueError(f"{path}: duplicate case ids {dupes}")
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", help="Path to an agent's eval_dataset.jsonl")
    args = parser.parse_args(argv)

    try:
        cases = load_dataset(args.dataset)
    except (OSError, ValueError) as exc:
        print(f"Dataset problem: {exc}", file=sys.stderr)
        return 1

    by_scoring = Counter(c["scoring"] for c in cases)
    by_category = Counter(c["category"] for c in cases)

    print(f"{len(cases)} cases in {args.dataset} — dataset is well-formed")
    print("scoring: " + ", ".join(f"{k}={v}" for k, v in sorted(by_scoring.items())))
    print("categories: " + ", ".join(f"{k}={v}" for k, v in sorted(by_category.items())))
    print("\nNot scored: this runner is a placeholder (M1-T2b).")
    print(f"Pass bar when implemented: {PASS_THRESHOLD:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
