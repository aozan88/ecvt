"""a03 generation reliability: how often a set validates, and on which attempt.

The question is narrow and keeps coming back: the wire schema asks the model for
`repeat` and `concurrency`, and when it omits one the whole set is rejected and
regenerated. That costs an attempt and says nothing about scenario quality, so
any change to the schema or the prompt has to be judged on the rate — not on
whether one generation happened to work.

Two arms, because they differ: a03 called with the spec alone is the M1 chain,
and a03 called with a02's DomainModel is what the Manager does. The DomainModel
makes the prompt longer and the omission more likely.

    python scripts/measure_a03.py --runs 8 --label after
    python scripts/measure_a03.py --runs 8 --label after --arm spec-only

Writes artifacts/measure-a03/<label>.json and prints a one-line summary. Each
run costs a real generation, so `--runs` is money: 8 runs per arm is about
$0.04 on the spec-only arm and $0.07 with a DomainModel.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ecvt.llm import DEFAULT_SUBAGENT_MODEL, StructuredCallError, load_env

ARMS = ("spec-only", "with-domain-model")
DEFAULT_SPEC = "examples/wallet-api/openapi.json"
DEFAULT_TARGET = "examples/wallet-api"

# Error classes worth counting apart. The first is the omission this measurement
# exists for; the rest are the ways the category union can go wrong, kept
# separate so a regression in the union is not read as an omission.
ERROR_CLASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("field_required", ("Field required",)),
    ("plain_carries_modes", ("send the action more than once",)),
    ("stress_not_a_race", ("greater than or equal to 2",)),
    ("union_branch", ("discriminator", "does not match any of the expected tags")),
    ("no_race_or_replay", ("never uses",)),
    ("missing_category", ("every category needs",)),
    # `scenarios` sent as a string of JSON text that does not parse (M3).
    ("json_string", ("type=list_type",)),
    # The strict wire form's own failures (M4-T1, ADR 0019): a body whose JSON
    # text does not parse or is not an object, a name given twice in a list.
    ("body_json", ("body is not valid JSON", "body must be JSON text of an object")),
    ("wire_duplicate", ("the same name appears twice",)),
)


UNCLASSIFIED_CHARS = 160


def classify(error: str) -> str:
    """A class name, or `other:` followed by the text.

    A bare "other" is useless: one run came back with five of them and the
    message had already been thrown away, so the measurement said something
    went wrong and nothing about what. An unrecognised error carries its own
    first line instead.
    """
    for name, needles in ERROR_CLASSES:
        if any(n in error for n in needles):
            return name
    return "other: " + " ".join(error.split())[:UNCLASSIFIED_CHARS]


def domain_model(path: str | None) -> dict[str, Any] | None:
    """a02's output for the with-domain-model arm.

    Taken from a file so the arm is reproducible: generating a fresh DomainModel
    per run would vary the input and measure two things at once.
    """
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    found = sorted(REPO_ROOT.glob("artifacts/**/domain_intel.json"))
    if not found:
        raise SystemExit(
            "no artifacts/**/domain_intel.json to feed a03. Run a Manager chain first "
            "(ecvt run --manager ...) or pass --domain-model <path>."
        )
    return json.loads(found[-1].read_text(encoding="utf-8"))


def measure(runs: int, arm: str, model: str, spec: str, target: str, dm_path: str | None) -> dict:
    from agents.a03_scenario_generator.agent import generate_scenario_set

    dm = domain_model(dm_path) if arm == "with-domain-model" else None
    rows: list[dict[str, Any]] = []
    cost = 0.0
    for i in range(1, runs + 1):
        try:
            scenario_set, result = generate_scenario_set(
                spec_path=spec, target=target, model=model, domain_model=dm
            )
        except StructuredCallError as exc:
            errors = [classify(a) for a in exc.attempts]
            cost += exc.usage.cost_usd() or 0.0
            rows.append(
                {"run": i, "ok": False, "attempts": len(exc.attempts), "errors": errors}
            )
            print(f"  {i}: FAILED after {len(exc.attempts)} {errors}", flush=True)
            continue
        errors = [classify(c) for c in result.corrections]
        cost += result.usage.cost_usd() or 0.0
        rows.append(
            {
                "run": i,
                "ok": True,
                "attempts": result.attempts,
                "scenarios": len(scenario_set.scenarios),
                "errors": errors,
            }
        )
        print(f"  {i}: ok in {result.attempts} attempt(s) {errors}", flush=True)

    ok = [r for r in rows if r["ok"]]
    counted = Counter(e for r in rows for e in r["errors"])
    return {
        "arm": arm,
        "model": model,
        "runs": runs,
        "valid": len(ok),
        "first_attempt": sum(1 for r in ok if r["attempts"] == 1),
        "median_attempts_when_valid": (
            statistics.median(r["attempts"] for r in ok) if ok else None
        ),
        "field_required_generations": sum(1 for r in rows if "field_required" in r["errors"]),
        "error_classes": dict(counted),
        "cost_usd": round(cost, 4),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--label", required=True, help="File name under artifacts/measure-a03/.")
    parser.add_argument("--arm", choices=ARMS, default="with-domain-model")
    parser.add_argument("--model", default=DEFAULT_SUBAGENT_MODEL)
    parser.add_argument("--spec", default=DEFAULT_SPEC)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--domain-model", help="a02 output to feed a03; newest artifact if unset.")
    args = parser.parse_args(argv)

    load_env()
    print(f"=== {args.label} · {args.arm} · {args.model} · {args.runs} runs", flush=True)
    summary = measure(args.runs, args.arm, args.model, args.spec, args.target, args.domain_model)

    out_dir = REPO_ROOT / "artifacts" / "measure-a03"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.label}.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(
        f"\n{args.label} ({summary['arm']}): {summary['valid']}/{summary['runs']} valid · "
        f"{summary['first_attempt']}/{summary['runs']} first attempt · "
        f"Field required in {summary['field_required_generations']}/{summary['runs']} · "
        f"errors {summary['error_classes']} · ${summary['cost_usd']:.4f}\n-> {out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
