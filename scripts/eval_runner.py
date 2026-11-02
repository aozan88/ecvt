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
MIN_CASES = 10


def load_dataset(path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read an eval dataset, checking each line against the agent standard.

    Returns the cases and any note lines. JSONL has no comment syntax, so a
    line whose keys all begin with `_` is treated as a note rather than a case
    — that is how a draft dataset records what is still missing.
    """
    path = Path(path)
    cases: list[dict[str, Any]] = []
    notes: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno} is not valid JSON: {exc}") from exc

        if not isinstance(case, dict):
            # Bad dataset content, not a caller type error: main() reports
            # every dataset problem as a ValueError with its line number.
            raise ValueError(  # noqa: TRY004
                f"{path}:{lineno} each line must be a JSON object"
            )
        if case and all(str(k).startswith("_") for k in case):
            notes.extend(f"{path}:{lineno} {v}" for v in case.values())
            continue

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
    return cases, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", help="Path to an agent's eval_dataset.jsonl")
    args = parser.parse_args(argv)

    try:
        cases, notes = load_dataset(args.dataset)
    except (OSError, ValueError) as exc:
        print(f"Dataset problem: {exc}", file=sys.stderr)
        return 1

    by_scoring = Counter(c["scoring"] for c in cases)
    by_category = Counter(c["category"] for c in cases)

    print(f"{len(cases)} cases in {args.dataset} — dataset is well-formed")
    print("scoring: " + ", ".join(f"{k}={v}" for k, v in sorted(by_scoring.items())))
    print("categories: " + ", ".join(f"{k}={v}" for k, v in sorted(by_category.items())))

    if len(cases) < MIN_CASES:
        print(f"\nIncomplete: the agent standard wants >= {MIN_CASES} cases.")
    for note in notes:
        print(f"note: {note}")

    print("\nNot scored: this runner is a placeholder (M1-T2b).")
    print(f"Pass bar when implemented: {PASS_THRESHOLD:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
