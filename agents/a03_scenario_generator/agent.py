"""Scenario Generator — OpenAPI spec in, schema-valid ScenarioSet out (M1-T1).

Pipeline position: first agent of the M1 chain. Reads the target's OpenAPI spec,
asks Claude for edge-case scenarios across the 7 QE categories, validates the
result against `ecvt.schema`, and writes `scenarios.jsonl` for Execution.

It never sees `ground_truth.yaml` — catch rate is only meaningful if the bugs
were not handed to the agent that goes looking for them (ADR 0007).

CLI:
    python -m agents.a03_scenario_generator.agent \
        --spec examples/wallet-api/openapi.json \
        --target examples/wallet-api \
        --out artifacts/scenarios.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ecvt.llm import DEFAULT_SUBAGENT_MODEL, StructuredCallError, StructuredResult, structured_call
from ecvt.openapi_reader import ApiDigest, read_openapi
from ecvt.schema import ALL_CATEGORIES, ScenarioSet, ScenarioSetDraft, write_scenarios_jsonl

AGENT_DIR = Path(__file__).resolve().parent
AGENT_NAME = "scenario_generator"
TOOL_NAME = "scenario_generator_output"
DEFAULT_PER_CATEGORY = 2


def load_system_prompt() -> str:
    return (AGENT_DIR / "system_prompt.md").read_text(encoding="utf-8")


def build_tool() -> dict[str, Any]:
    """The forced tool, with its input schema generated from the Pydantic model.

    Generating it keeps `tools.json` from drifting away from `ecvt.schema`;
    `tests/test_tools_json_in_sync.py` asserts the committed copy matches.
    """
    schema = ScenarioSetDraft.model_json_schema()
    schema.pop("title", None)
    return {
        "name": TOOL_NAME,
        "description": (
            "Return the generated edge-case scenarios. Use this tool for every "
            "response: it is the only way to deliver scenarios."
        ),
        "input_schema": schema,
    }


DOMAIN_KEYS = ("rules", "gaps", "workflows")


def render_domain_model(domain_model: dict[str, Any]) -> str:
    """The parts of a02's DomainModel that point at test ideas: rules to break, gaps
    the spec leaves open, and state transitions. Entities and operations are left out:
    the API surface already carries them, and repeating them costs tokens."""
    compact = {k: domain_model.get(k, []) for k in DOMAIN_KEYS}
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def build_user_prompt(
    digest: ApiDigest,
    target: str,
    per_category: int = DEFAULT_PER_CATEGORY,
    domain_model: dict[str, Any] | None = None,
) -> str:
    total = per_category * len(ALL_CATEGORIES)
    quota = "\n".join(f"- {c.value}: {per_category}" for c in ALL_CATEGORIES)
    domain = ""
    if domain_model:
        domain = (
            "Below is the domain analysis of the same spec: the rules the system should "
            "obey, the questions the spec leaves open (gaps) and the state transitions. "
            "Use it to aim scenarios — a rule is something to try to break, a gap is "
            "behaviour nobody specified. It is derived from the spec, not from a bug list, "
            "and every scenario must still use only the API surface above.\n\n"
            f"<domain_model>\n{render_domain_model(domain_model)}\n</domain_model>\n\n"
        )
    return (
        f"Target system: {target}\n\n"
        "Below is the target's API surface, distilled from its OpenAPI spec. It is "
        "the only information you have about the system; there is no bug list.\n\n"
        "<api_surface>\n"
        f"{digest.render()}\n"
        "</api_surface>\n\n"
        f"{domain}"
        f"Write exactly {per_category} scenarios for each category — {total} in total:\n"
        f"{quota}\n\n"
        "Hold to that count per category. `negative` and `edge` ideas come easily and "
        "tend to crowd out the rest; `happy`, `security` and `persona` need the same "
        "share. If a category is hard for this API, think harder about it rather than "
        "filing another scenario under an easier label.\n\n"
        "Every scenario must be runnable exactly as written against this API: use only "
        "the paths and fields above, create any state you depend on in `setup`, and "
        "reference captured values with {placeholders}.\n\n"
        "At least one scenario must set `concurrency` above 1 and at least one must set "
        "`repeat` above 1 — a race and a replay cannot be seen from one sequential "
        "request.\n\n"
        f"Call {TOOL_NAME} with the result. The `scenarios` field must be a JSON array, "
        "not a string containing one."
    )


def _make_validator(require_all_categories: bool = True) -> Any:
    def validate(data: dict[str, Any]) -> ScenarioSetDraft:
        draft = ScenarioSetDraft.model_validate(data)
        if require_all_categories:
            missing = draft.missing_categories()
            if missing:
                raise ValueError(
                    "every category needs at least one scenario; missing: "
                    + ", ".join(missing)
                )
        # Asking for this in the prompt was not enough: every scenario came
        # back at 1. Raising it here puts the requirement in the retry loop,
        # where the model is shown what it missed and sends a corrected set.
        unused = draft.missing_execution_modes()
        if unused:
            raise ValueError(
                "the set never uses " + " or ".join(f"`{u}` above 1" for u in unused) + "; "
                "a race and a replay cannot be observed from one sequential request. "
                "Raise `concurrency` on a scenario that probes a race and `repeat` on "
                "one that probes a duplicated write."
            )
        return draft

    return validate


def generate_scenario_set(
    *,
    spec_path: str | Path,
    target: str,
    client: Any | None = None,
    model: str = DEFAULT_SUBAGENT_MODEL,
    per_category: int = DEFAULT_PER_CATEGORY,
    max_attempts: int = 3,
    domain_model: dict[str, Any] | None = None,
) -> tuple[ScenarioSet, StructuredResult]:
    """Generate scenarios for `spec_path`. Returns the set and the call result.

    `domain_model` is a02's output (M2-T2). Without it the generator works from the
    spec alone, exactly as in M1 — the baseline the M2 comparison is measured against.
    """
    digest = read_openapi(spec_path)
    result = structured_call(
        system=load_system_prompt(),
        user=build_user_prompt(digest, target, per_category, domain_model),
        tool=build_tool(),
        validate=_make_validator(),
        client=client,
        model=model,
        max_attempts=max_attempts,
    )
    draft = ScenarioSetDraft.model_validate(result.data)
    scenario_set = ScenarioSet(
        target=target,
        spec_title=digest.title,
        scenarios=draft.scenarios,
        model=model,
    )
    return scenario_set, result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate ECVT scenarios from an OpenAPI spec.")
    parser.add_argument(
        "--spec",
        default="examples/wallet-api/openapi.json",
        help="Path to the target's openapi.json.",
    )
    parser.add_argument(
        "--target", default="examples/wallet-api", help="Target system identifier."
    )
    parser.add_argument(
        "--out", default="artifacts/scenarios.jsonl", help="Where to write scenarios.jsonl."
    )
    parser.add_argument("--model", default=DEFAULT_SUBAGENT_MODEL, help="Model to call.")
    parser.add_argument(
        "--per-category",
        type=int,
        default=DEFAULT_PER_CATEGORY,
        help="Target scenarios per category.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the prompt and tool schema without calling the API.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.dry_run:
        digest = read_openapi(args.spec)
        print(build_user_prompt(digest, args.target, args.per_category))
        print("\n--- tool schema ---")
        print(json.dumps(build_tool(), indent=2)[:2000])
        return 0

    try:
        scenario_set, result = generate_scenario_set(
            spec_path=args.spec,
            target=args.target,
            model=args.model,
            per_category=args.per_category,
        )
    except StructuredCallError as exc:
        print(f"Scenario generation failed: {exc}", file=sys.stderr)
        for line in exc.attempts:
            print(f"  - {line}", file=sys.stderr)
        print(f"  usage: {json.dumps(exc.usage.as_dict())}", file=sys.stderr)
        return 1

    out_path = write_scenarios_jsonl(args.out, scenario_set)
    counts = ", ".join(f"{k}={v}" for k, v in scenario_set.category_counts().items())
    print(f"{len(scenario_set.scenarios)} scenarios -> {out_path}")
    print(f"categories: {counts}")
    print(f"attempts: {result.attempts}")
    for correction in result.corrections:
        print(f"  retry after: {correction}")
    print(f"usage: {json.dumps(result.usage.as_dict())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
