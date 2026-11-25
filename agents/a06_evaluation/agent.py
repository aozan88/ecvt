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
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ecvt.llm import DEFAULT_SUBAGENT_MODEL, StructuredCallError, Usage, load_env, structured_call
from ecvt.results import ExecutionRecord, StepResult, read_executions_jsonl
from ecvt.schema import Scenario, read_scenarios_jsonl
from ecvt.scoring import (
    Dimension,
    DimensionScore,
    ScenarioScore,
    Verdict,
    combine,
    needs_judge,
    summarize,
    write_scores_jsonl,
)

AGENT_DIR = Path(__file__).resolve().parent
AGENT_NAME = "evaluation"
TOOL_NAME = "evaluation_judge_output"
DEFAULT_JUDGE_PARALLEL = 6
"""Judgements in flight at once. Independent calls, so the only reasons to cap
are socket pressure on a large suite and the per-minute rate limit."""

REPO_ROOT = AGENT_DIR.parent.parent
EVAL_TOOL = "score_response"


class EvalServerError(RuntimeError):
    """ecvt-eval could not score: it did not start, or refused an input."""


def eval_server() -> Any:
    """How a06 reaches ecvt-eval (M3-T3, ADR 0014): a stdio subprocess of this
    interpreter, started in the repo root so it imports this checkout's code.
    Anything `mcp.Client` accepts works — tests hand it the in-memory server."""
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=sys.executable, args=["-m", "ecvt.eval_server"], cwd=str(REPO_ROOT)
    )


async def _rule_scores_async(
    pairs: list[tuple[Scenario, ExecutionRecord]], plugin: str, server: Any
) -> list[DimensionScore]:
    from mcp import Client

    scores: list[DimensionScore] = []
    refusal: str | None = None
    # A refusal is raised after the session closes: raised inside it, anyio's
    # task group wraps it in an ExceptionGroup and the message is lost.
    async with Client(server) as session:
        for scenario, record in pairs:
            result = await session.call_tool(
                EVAL_TOOL,
                {
                    "scenario": scenario.model_dump(mode="json"),
                    "execution": record.model_dump(mode="json"),
                    "plugin": plugin,
                },
            )
            if result.is_error or result.structured_content is None:
                text = " ".join(getattr(c, "text", "") for c in result.content)
                refusal = f"ecvt-eval refused {scenario.id}: {text or 'no result'}"
                break
            scores.append(DimensionScore.model_validate(result.structured_content))
    if refusal is not None:
        raise EvalServerError(refusal)
    return scores


def _run_sync(make_coro: Any) -> Any:
    """Run a coroutine from sync code, also when the caller is inside an event
    loop (an async test, an async host): then it gets its own loop in a thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make_coro())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(make_coro())).result()


def rule_scores(
    pairs: list[tuple[Scenario, ExecutionRecord]], plugin: str = "rest_api", server: Any = None
) -> list[DimensionScore]:
    """The rule dimension of every pair, from ecvt-eval — one session per call."""
    if not pairs:
        return []
    target = eval_server() if server is None else server
    try:
        return _run_sync(lambda: _rule_scores_async(pairs, plugin, target))
    except EvalServerError:
        raise
    except Exception as exc:  # the server did not start or the session broke
        raise EvalServerError(f"ecvt-eval unavailable: {type(exc).__name__}: {exc}") from exc


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
) -> tuple[DimensionScore, Usage]:
    """One model call for one scenario.

    Returns its own Usage rather than mutating a shared one, so judgements can
    run in parallel without threads racing on the counters.

    Never raises: a judge that could not answer yields an `inconclusive`
    dimension, so the run still produces a score (ADR 0006).
    """
    own_usage = Usage(model=model)
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
        _merge_usage(own_usage, exc.usage)
        return (
            DimensionScore(
                dimension=Dimension.JUDGE,
                verdict=Verdict.INCONCLUSIVE,
                rationale=f"the judge produced no usable verdict: {exc}",
                model=model,
            ),
            own_usage,
        )

    _merge_usage(own_usage, result.usage)
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
    return (
        DimensionScore(
            dimension=Dimension.JUDGE,
            verdict=output.verdict,
            rationale=output.rationale,
            checks=checks,
            model=model,
        ),
        own_usage,
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
    max_parallel: int = DEFAULT_JUDGE_PARALLEL,
    plugin: str = "rest_api",
    server: Any = None,
) -> tuple[list[ScenarioScore], Usage]:
    """Score every scenario that has an execution record.

    Rules run first, through the ecvt-eval MCP server (M3-T3): the generic
    checks plus `plugin`'s domain rules, deterministic and free. The judgements that
    survive `needs_judge` then run `max_parallel` at a time: each is an
    independent call about one scenario, so scenario 7's verdict never waits
    on scenario 6's. Sequential judging dominated the wall clock (14 scenarios
    took ~38s of a 50s run); bounded parallelism keeps that in hand without
    opening 1000 sockets on a large suite, and leaves room under the
    per-minute rate limit (ADR 0009).
    """
    by_id = {r.scenario_id: r for r in records}
    matched = [(s, by_id[s.id]) for s in scenarios if s.id in by_id]
    ruled = rule_scores(matched, plugin, server)
    pairs: list[tuple[Scenario, ExecutionRecord, DimensionScore]] = [
        (s, r, rule) for (s, r), rule in zip(matched, ruled, strict=True)
    ]

    to_judge = [
        index
        for index, (scenario, _, rule) in enumerate(pairs)
        if use_judge and needs_judge(scenario, rule)
    ]

    judged: dict[int, tuple[DimensionScore, Usage]] = {}
    if to_judge:
        workers = max(1, min(max_parallel, len(to_judge)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    judge_scenario,
                    pairs[index][0],
                    pairs[index][1],
                    client=client,
                    model=model,
                ): index
                for index in to_judge
            }
            for future in as_completed(futures):
                judged[futures[future]] = future.result()

    # Merge in scenario order, not completion order, so the usage log and the
    # scores file read the same whichever call finished first.
    usage = Usage(model=model)
    scores: list[ScenarioScore] = []
    for index, (scenario, record, rule) in enumerate(pairs):
        dimensions = [rule]
        if index in judged:
            judge_dimension, judge_usage = judged[index]
            dimensions.append(judge_dimension)
            _merge_usage(usage, judge_usage)
        scores.append(combine(scenario, record, dimensions))

    return scores, usage


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score ECVT executions.")
    parser.add_argument("--scenarios", default="artifacts/scenarios.jsonl")
    parser.add_argument("--executions", default="artifacts/executions.jsonl")
    parser.add_argument("--out", default="artifacts/scores.jsonl")
    parser.add_argument("--model", default=DEFAULT_SUBAGENT_MODEL)
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=DEFAULT_JUDGE_PARALLEL,
        help="Judgements in flight at once.",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Rules only — no model calls, no API key needed.",
    )
    parser.add_argument(
        "--plugin",
        default="rest_api",
        help="Plugin whose domain rules run beside the generic checks (M3-T2).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env()
    args = _parse_args(argv)

    from plugins._base.plugin import PluginContext
    from plugins._base.plugin import load as load_plugin

    try:
        scenarios = read_scenarios_jsonl(args.scenarios)
        records = read_executions_jsonl(args.executions)
        load_plugin(args.plugin, PluginContext(base_url=""))  # fail before any work
    except (OSError, ValueError) as exc:
        print(f"Could not read inputs: {exc}", file=sys.stderr)
        return 1

    try:
        scores, usage = score_scenarios(
            scenarios,
            records,
            model=args.model,
            use_judge=not args.no_judge,
            max_parallel=args.max_parallel,
            plugin=args.plugin,
        )
    except EvalServerError as exc:
        print(f"Scoring failed: {exc}", file=sys.stderr)
        return 1
    if not scores:
        print("No scenario had a matching execution record.", file=sys.stderr)
        return 1

    out_path = write_scores_jsonl(args.out, scores)
    stats = summarize(scores)

    print(
        f"{stats['scored']} scored · "
        + ", ".join(f"{k}={v}" for k, v in sorted(stats["verdicts"].items()))
    )
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
