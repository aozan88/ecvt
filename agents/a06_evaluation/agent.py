"""Evaluation Agent — scores executions with rules, then an LLM judge (M1-T5).

Pipeline position: last agent of the M1 chain. Reads `scenarios.jsonl` and
`executions.jsonl`, scores each scenario, writes `scores.jsonl`.

Two dimensions in M1 (Mapping v3 lists five; see `ecvt/scoring.py`):

- **rule** — deterministic, free, runs on everything (`ecvt.scoring.run_rules`)
- **judge** — one model call, and only where `needs_judge()` says the rules
  could not settle it or an invariant still needs weighing (ADR 0009)

The judge is **not** told the rule verdict. Anchoring it on a verdict we
already have would turn a second opinion into an echo; kept independent, the
two dimensions disagree when the case is genuinely unclear, which is the
signal worth having.

`ground_truth.yaml` never reaches the judge (ADR 0007).

CLI:
    python -m agents.a06_evaluation.agent \
        --scenarios artifacts/scenarios.jsonl \
        --executions artifacts/executions.jsonl \
        --out artifacts/scores.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ecvt.llm import DEFAULT_SUBAGENT_MODEL, StructuredCallError, Usage, structured_call
from ecvt.results import ExecutionRecord, StepResult, read_executions_jsonl
from ecvt.schema import Scenario, read_scenarios_jsonl
from ecvt.scoring import (
    Dimension,
    DimensionScore,
    ScenarioScore,
    Verdict,
    combine,
    needs_judge,
    run_rules,
    summarize,
    write_scores_jsonl,
)

AGENT_DIR = Path(__file__).resolve().parent
AGENT_NAME = "evaluation"
TOOL_NAME = "evaluation_judge_output"
BODY_CHARS = 600
"""Response bodies are trimmed before they reach the prompt: a judgement needs
the figures, not every byte, and the prompt prefix stays cacheable."""


def load_system_prompt() -> str:
    return (AGENT_DIR / "system_prompt.md").read_text(encoding="utf-8")


# --- judge wire format -----------------------------------------------------


class JudgeVerdict(BaseModel):
    """One invariant, judged."""

    model_config = ConfigDict(extra="forbid")

    invariant: str = Field(description="The invariant text, copied verbatim.")
    verdict: Verdict = Field(description="pass, fail, or inconclusive.")
    evidence: str = Field(
        description="The specific figure or response from the observation that settles it."
    )


class JudgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict = Field(description="The scenario as a whole: pass, fail or inconclusive.")
    rationale: str = Field(description="One or two sentences naming the evidence used.")
    invariants: list[JudgeVerdict] = Field(
        default_factory=list, description="One entry per stated invariant."
    )


def build_tool() -> dict[str, Any]:
    schema = JudgeOutput.model_json_schema()
    schema.pop("title", None)
    return {
        "name": TOOL_NAME,
        "description": (
            "Return the judgement for one scenario. Use this tool for every "
            "response: it is the only way to deliver a verdict."
        ),
        "input_schema": schema,
    }


# --- observation payload ---------------------------------------------------


def _trim(step: StepResult) -> dict[str, Any]:
    out: dict[str, Any] = {"name": step.name, "endpoint": step.endpoint}
    if step.is_error:
        out["error"] = step.error
        return out
    out["status"] = step.status_code
    body: Any = step.response_json if step.response_json is not None else step.response_text
    if isinstance(body, str) and len(body) > BODY_CHARS:
        body = body[:BODY_CHARS] + "…"
    elif body is not None:
        text = json.dumps(body, ensure_ascii=False)
        if len(text) > BODY_CHARS:
            body = text[:BODY_CHARS] + "…"
    out["body"] = body
    return out


def build_observation(scenario: Scenario, record: ExecutionRecord) -> str:
    payload = {
        "scenario": {
            "id": scenario.id,
            "title": scenario.title,
            "category": scenario.category.value,
            "why_it_matters": scenario.rationale,
            "action": {
                "method": scenario.action.method.value,
                "path": scenario.action.path,
                "body": scenario.action.body,
            },
            "sent_times": scenario.repeat * scenario.concurrency,
        },
        "expectation": {
            "expected_status": scenario.expectation.expected_status,
            "expected_behavior": scenario.expectation.expected_behavior,
            "invariants": scenario.expectation.invariants,
        },
        "observed": {
            "setup": [_trim(s) for s in record.setup],
            "actions": [_trim(s) for s in record.actions],
            "probes": [_trim(s) for s in record.probes],
        },
    }
    return (
        "Judge this single test run.\n\n<run>\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + f"\n</run>\n\nCall {TOOL_NAME} with your judgement."
    )


# --- judging ---------------------------------------------------------------


def judge_scenario(
    scenario: Scenario,
    record: ExecutionRecord,
    *,
    client: Any | None = None,
    model: str = DEFAULT_SUBAGENT_MODEL,
    usage: Usage | None = None,
) -> DimensionScore:
    """One model call for one scenario. Never raises: a judge that could not
    answer yields an `inconclusive` dimension, so the run still produces a
    score (ADR 0006)."""
    try:
        result = structured_call(
            system=load_system_prompt(),
            user=build_observation(scenario, record),
            tool=build_tool(),
            validate=JudgeOutput.model_validate,
            client=client,
            model=model,
            max_tokens=2048,
        )
    except StructuredCallError as exc:
        if usage is not None:
            _merge_usage(usage, exc.usage)
        return DimensionScore(
            dimension=Dimension.JUDGE,
            verdict=Verdict.INCONCLUSIVE,
            rationale=f"the judge produced no usable verdict: {exc}",
            model=model,
        )

    if usage is not None:
        _merge_usage(usage, result.usage)

    output = JudgeOutput.model_validate(result.data)
    checks = [
        {
            "name": "invariant",
            "verdict": item.verdict,
            "detail": item.invariant,
            "observed": item.evidence,
        }
        for item in output.invariants
    ]
    return DimensionScore(
        dimension=Dimension.JUDGE,
        verdict=output.verdict,
        rationale=output.rationale,
        checks=checks,
        model=model,
    )


def _merge_usage(total: Usage, part: Usage) -> None:
    for name in ("standard", "long_prompt"):
        into, src = getattr(total, name), getattr(part, name)
        into.input_tokens += src.input_tokens
        into.output_tokens += src.output_tokens
        into.cache_read_tokens += src.cache_read_tokens
        into.cache_write_tokens += src.cache_write_tokens
        into.calls += src.calls


def score_scenarios(
    scenarios: list[Scenario],
    records: list[ExecutionRecord],
    *,
    client: Any | None = None,
    model: str = DEFAULT_SUBAGENT_MODEL,
    use_judge: bool = True,
) -> tuple[list[ScenarioScore], Usage]:
    """Score every scenario that has an execution record."""
    by_id = {r.scenario_id: r for r in records}
    usage = Usage(model=model)
    scores: list[ScenarioScore] = []

    for scenario in scenarios:
        record = by_id.get(scenario.id)
        if record is None:
            continue
        dimensions = [run_rules(scenario, record)]
        if use_judge and needs_judge(scenario, dimensions[0]):
            dimensions.append(
                judge_scenario(
                    scenario, record, client=client, model=model, usage=usage
                )
            )
        scores.append(combine(scenario, record, dimensions))

    return scores, usage


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score ECVT executions.")
    parser.add_argument("--scenarios", default="artifacts/scenarios.jsonl")
    parser.add_argument("--executions", default="artifacts/executions.jsonl")
    parser.add_argument("--out", default="artifacts/scores.jsonl")
    parser.add_argument("--model", default=DEFAULT_SUBAGENT_MODEL)
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Rules only — no model calls, no API key needed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        scenarios = read_scenarios_jsonl(args.scenarios)
        records = read_executions_jsonl(args.executions)
    except (OSError, ValueError) as exc:
        print(f"Could not read inputs: {exc}", file=sys.stderr)
        return 1

    scores, usage = score_scenarios(
        scenarios, records, model=args.model, use_judge=not args.no_judge
    )
    if not scores:
        print("No scenario had a matching execution record.", file=sys.stderr)
        return 1

    out_path = write_scores_jsonl(args.out, scores)
    stats = summarize(scores)

    print(f"{stats['scored']} scored · " + ", ".join(
        f"{k}={v}" for k, v in sorted(stats["verdicts"].items())
    ))
    print(f"defects suspected: {stats['defects_suspected']} · judged: {stats['judged']}")
    for score in scores:
        if score.verdict is not Verdict.PASS:
            print(f"  {score.verdict.value:12} {score.scenario_id:12} {score.summary}")
    print(f"-> {out_path}")
    if usage.calls:
        print(f"usage: {json.dumps(usage.as_dict())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
