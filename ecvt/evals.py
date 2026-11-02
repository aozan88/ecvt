"""Agent eval runner — measures each agent against its own dataset (M1-T2b).

The agent standard says an agent is not done until it scores >= 80% on its
`eval_dataset.jsonl`, and gate G1 asks for that number for all three M1
agents. This module produces it.

An eval case is only worth having if it can be run. Three scoring modes, from
cheapest to most expensive — the same split C-T2 calls code-based vs
model-based evaluation:

- `schema-valid`   the output validates against a named model. Free, exact.
- `keyword-match`  required strings appear in the output. Free, blunt.
- `llm-judge`      a model scores the output against the case's rubric.
                   Costs a call, so it is for claims the first two cannot
                   express ("does it avoid stating a verdict?").

Each agent needs an **adapter**: the function that turns a case's `input` into
an actual invocation of that agent. Without one a dataset is prose, and prose
cannot be scored — the first version of these datasets described situations
("overdraft transfer, expected_status [400,422]") instead of supplying them,
so nothing could execute them.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ecvt.llm import DEFAULT_SUBAGENT_MODEL, StructuredCallError, Usage, structured_call

PASS_THRESHOLD = 0.80
MIN_CASES = 10
REQUIRED_FIELDS = ("id", "input", "category", "scoring")
KNOWN_SCORING = ("schema-valid", "keyword-match", "llm-judge")

JUDGE_SYSTEM = """\
You grade one test of an AI agent against a rubric.

You are given the agent's output and a single rubric sentence stating what a
correct output must do. Decide whether the output satisfies it.

Rules:
- Judge only the output you were given, against only that rubric.
- The rubric is the whole standard. Do not add requirements of your own, and
  do not fail an output for something the rubric does not ask for.
- A rubric that says the agent must decline something is satisfied when the
  output declines it — an empty or refusing output can be a pass.
- When the output is too short, truncated or unrelated to tell, answer `false`
  and say what was missing. Guessing defeats the measurement.
- Quote the part of the output that decided it.
"""


@dataclass
class EvalCase:
    id: str
    input: dict[str, Any]
    category: str
    scoring: str
    expected_schema: str | None = None
    expected_keywords: list[str] = field(default_factory=list)
    rubric: str | None = None


@dataclass
class Invocation:
    """What an agent produced for one case."""

    text: str
    """Rendered output — what keyword-match and the judge read."""

    obj: Any | None = None
    """Structured output — what schema-valid validates."""

    error: str | None = None
    """Set when the agent declined or could not run. Not a failure by itself:
    a case may exist precisely to check that it declines."""

    usage: Usage | None = None


@dataclass
class CaseResult:
    case_id: str
    scoring: str
    category: str
    passed: bool
    detail: str


@dataclass
class EvalReport:
    agent: str
    results: list[CaseResult]
    usage: Usage
    skipped: list[tuple[str, str]] = field(default_factory=list)
    """(case_id, reason) for cases that could not be run — counted as neither
    pass nor fail, and called out, because a silent skip inflates the rate."""

    @property
    def pass_rate(self) -> float | None:
        if not self.results:
            return None
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def meets_bar(self) -> bool:
        """Skipped cases keep the bar unmet: an unmeasured case is not a pass."""
        rate = self.pass_rate
        return bool(rate is not None and rate >= PASS_THRESHOLD and not self.skipped)

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "cases": len(self.results),
            "passed": sum(1 for r in self.results if r.passed),
            "pass_rate": round(self.pass_rate, 3) if self.pass_rate is not None else None,
            "threshold": PASS_THRESHOLD,
            "meets_bar": self.meets_bar,
            "skipped": [{"case": c, "reason": r} for c, r in self.skipped],
            "results": [
                {
                    "case": r.case_id,
                    "scoring": r.scoring,
                    "category": r.category,
                    "passed": r.passed,
                    "detail": r.detail,
                }
                for r in self.results
            ],
            "usage": self.usage.as_dict(),
        }


# --- dataset ---------------------------------------------------------------


def load_dataset(path: str | Path) -> tuple[list[EvalCase], list[str]]:
    """Read a dataset, checking each line against the agent standard.

    Returns the cases and any note lines. JSONL has no comment syntax, so a
    line whose keys all begin with `_` is treated as a note rather than a case.
    """
    path = Path(path)
    cases: list[EvalCase] = []
    notes: list[str] = []

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            # Dataset content, not a caller type error: every dataset problem
            # reaches the CLI as a ValueError carrying its line number.
            raise ValueError(  # noqa: TRY004
                f"{path}:{lineno} each line must be a JSON object"
            )
        if raw and all(str(k).startswith("_") for k in raw):
            notes.extend(f"{path}:{lineno} {v}" for v in raw.values())
            continue

        missing = [f for f in REQUIRED_FIELDS if f not in raw]
        if missing:
            raise ValueError(f"{path}:{lineno} case missing {missing}")
        if raw["scoring"] not in KNOWN_SCORING:
            raise ValueError(
                f"{path}:{lineno} unknown scoring {raw['scoring']!r}; "
                f"expected one of {KNOWN_SCORING}"
            )
        if not isinstance(raw["input"], dict):
            raise ValueError(  # noqa: TRY004
                f"{path}:{lineno} `input` must be an object the adapter can run, "
                "not a description of one"
            )
        cases.append(
            EvalCase(
                id=str(raw["id"]),
                input=raw["input"],
                category=str(raw["category"]),
                scoring=str(raw["scoring"]),
                expected_schema=raw.get("expected_schema"),
                expected_keywords=list(raw.get("expected_keywords") or []),
                rubric=raw.get("rubric"),
            )
        )

    dupes = [i for i, n in Counter(c.id for c in cases).items() if n > 1]
    if dupes:
        raise ValueError(f"{path}: duplicate case ids {dupes}")
    return cases, notes


# --- scorers ---------------------------------------------------------------


def _import_model(dotted: str) -> Any:
    module_name, _, attr = dotted.rpartition(".")
    if not module_name:
        raise ValueError(f"expected_schema must be a dotted path, got {dotted!r}")
    return getattr(importlib.import_module(module_name), attr)


def score_schema_valid(case: EvalCase, inv: Invocation) -> CaseResult:
    def result(passed: bool, detail: str) -> CaseResult:
        return CaseResult(case.id, case.scoring, case.category, passed, detail)

    if not case.expected_schema:
        return result(False, "case has no expected_schema")
    if inv.obj is None:
        return result(False, f"no structured output (error: {inv.error})")
    try:
        model = _import_model(case.expected_schema)
    except (ImportError, AttributeError, ValueError) as exc:
        return result(False, f"cannot import {case.expected_schema}: {exc}")
    try:
        model.model_validate(inv.obj)
    except Exception as exc:  # noqa: BLE001 — any validation error is the answer
        return result(False, f"does not match {case.expected_schema}: {exc}"[:300])
    return result(True, f"validates against {case.expected_schema}")


def score_keyword_match(case: EvalCase, inv: Invocation) -> CaseResult:
    def result(passed: bool, detail: str) -> CaseResult:
        return CaseResult(case.id, case.scoring, case.category, passed, detail)

    if not case.expected_keywords:
        return result(False, "case has no expected_keywords")
    haystack = inv.text.lower()
    missing = [k for k in case.expected_keywords if k.lower() not in haystack]
    if missing:
        return result(False, f"missing: {', '.join(missing)}")
    return result(True, f"found all {len(case.expected_keywords)} keywords")


def score_llm_judge(
    case: EvalCase,
    inv: Invocation,
    *,
    client: Any | None = None,
    model: str = DEFAULT_SUBAGENT_MODEL,
    usage: Usage | None = None,
    max_chars: int = 6000,
) -> CaseResult:
    def result(passed: bool, detail: str) -> CaseResult:
        return CaseResult(case.id, case.scoring, case.category, passed, detail)

    if not case.rubric:
        return result(False, "case has no rubric")

    output = inv.text if not inv.error else f"[agent declined or failed] {inv.error}\n{inv.text}"
    if len(output) > max_chars:
        output = output[:max_chars] + "\n…[truncated]"

    tool = {
        "name": "grade",
        "description": "Return the grade for this one case. Use it for every response.",
        "input_schema": {
            "type": "object",
            "properties": {
                "satisfies_rubric": {"type": "boolean"},
                "reason": {
                    "type": "string",
                    "description": "One sentence, quoting what decided it.",
                },
            },
            "required": ["satisfies_rubric", "reason"],
        },
    }
    user = (
        f"<rubric>\n{case.rubric}\n</rubric>\n\n"
        f"<agent_output>\n{output}\n</agent_output>\n\n"
        "Call grade."
    )

    try:
        call = structured_call(
            system=JUDGE_SYSTEM,
            user=user,
            tool=tool,
            validate=lambda d: _require_bool(d, "satisfies_rubric"),
            client=client,
            model=model,
            max_tokens=1024,
        )
    except StructuredCallError as exc:
        if usage is not None:
            _merge(usage, exc.usage)
        return result(False, f"grader produced no verdict: {exc}"[:300])

    if usage is not None:
        _merge(usage, call.usage)
    passed = bool(call.data["satisfies_rubric"])
    return result(passed, str(call.data.get("reason", ""))[:300])


def _require_bool(data: dict[str, Any], key: str) -> dict[str, Any]:
    if not isinstance(data.get(key), bool):
        # Model output, not a caller type error: structured_call feeds this
        # message back to the grader as a correction and retries.
        raise ValueError(  # noqa: TRY004
            f"{key} must be a boolean, got {data.get(key)!r}"
        )
    return data


def _merge(total: Usage, part: Usage) -> None:
    for name in ("standard", "long_prompt"):
        into, src = getattr(total, name), getattr(part, name)
        into.input_tokens += src.input_tokens
        into.output_tokens += src.output_tokens
        into.cache_read_tokens += src.cache_read_tokens
        into.cache_write_tokens += src.cache_write_tokens
        into.calls += src.calls


# --- adapters -------------------------------------------------------------


@dataclass
class AdapterContext:
    base_url: str = "http://localhost:8000"
    client: Any | None = None
    model: str = DEFAULT_SUBAGENT_MODEL


Adapter = Callable[[EvalCase, AdapterContext], Invocation]


def _render(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(obj)


def adapt_scenario_generator(case: EvalCase, ctx: AdapterContext) -> Invocation:
    """Run the Scenario Generator on this case's spec."""
    from agents.a03_scenario_generator.agent import generate_scenario_set

    spec = case.input.get("spec")
    if not spec:
        # The case supplies no spec on purpose: the agent must refuse rather
        # than invent an API. Refusing is the behaviour under test.
        return Invocation(
            text="No OpenAPI spec was supplied, so no scenarios were generated.",
            error="no spec supplied",
        )

    try:
        scenario_set, result = generate_scenario_set(
            spec_path=spec,
            target=case.input.get("target", "unknown"),
            client=ctx.client,
            model=ctx.model,
            per_category=int(case.input.get("per_category", 2)),
        )
    except FileNotFoundError as exc:
        return Invocation(text=f"spec not found: {exc}", error=str(exc))
    except StructuredCallError as exc:
        return Invocation(text=str(exc), error=str(exc), usage=exc.usage)

    payload = {"scenarios": [s.model_dump(mode="json", exclude_none=True) for s in scenario_set.scenarios]}
    return Invocation(text=_render(payload), obj=payload, usage=result.usage)


def _inline_scenario(case: EvalCase) -> Any:
    from ecvt.schema import Scenario

    raw = case.input.get("scenario")
    if not isinstance(raw, dict):
        raise ValueError(  # noqa: TRY004
            f"case {case.id}: `input.scenario` must be a scenario object the "
            "runner can execute, not a description"
        )
    return Scenario.model_validate(raw)


def adapt_execution(case: EvalCase, ctx: AdapterContext) -> Invocation:
    """Run one scenario against the live target and return its record."""
    import asyncio

    from ecvt.execution import RunnerConfig, run_scenarios

    scenario = _inline_scenario(case)
    base_url = case.input.get("base_url") or ctx.base_url
    config = RunnerConfig(
        base_url=base_url,
        timeout_s=float(case.input.get("timeout", 5.0)),
        max_transport_retries=int(case.input.get("retries", 1)),
        backoff_s=0.0,
    )
    records = asyncio.run(run_scenarios([scenario], config))
    record = records[0].model_dump(mode="json", exclude_none=True)
    return Invocation(text=_render(record), obj=record)


def adapt_evaluation(case: EvalCase, ctx: AdapterContext) -> Invocation:
    """Run one scenario, then score it — the Evaluation agent's real input."""
    import asyncio

    from agents.a06_evaluation.agent import score_scenarios
    from ecvt.execution import RunnerConfig, run_scenarios

    scenario = _inline_scenario(case)
    config = RunnerConfig(
        base_url=case.input.get("base_url") or ctx.base_url,
        timeout_s=5.0,
        max_transport_retries=1,
        backoff_s=0.0,
    )
    records = asyncio.run(run_scenarios([scenario], config))
    scores, usage = score_scenarios(
        [scenario],
        records,
        client=ctx.client,
        model=ctx.model,
        use_judge=bool(case.input.get("judge", True)),
    )
    if not scores:
        return Invocation(text="no score produced", error="no score produced", usage=usage)
    score = scores[0].model_dump(mode="json", exclude_none=True)
    return Invocation(text=_render(score), obj=score, usage=usage)


ADAPTERS: dict[str, Adapter] = {
    "a03_scenario_generator": adapt_scenario_generator,
    "a05_execution": adapt_execution,
    "a06_evaluation": adapt_evaluation,
}


def adapter_for(dataset_path: str | Path) -> tuple[str, Adapter | None]:
    """Pick the adapter from the dataset's agent directory."""
    agent = Path(dataset_path).resolve().parent.name
    return agent, ADAPTERS.get(agent)


# --- runner ---------------------------------------------------------------


def target_reachable(base_url: str, timeout: float = 2.0) -> bool:
    try:
        httpx.get(f"{base_url.rstrip('/')}/openapi.json", timeout=timeout)
    except httpx.HTTPError:
        return False
    return True


def run_dataset(
    dataset_path: str | Path,
    *,
    ctx: AdapterContext | None = None,
    only: str | None = None,
    skip_judge: bool = False,
) -> EvalReport:
    """Run every case in a dataset and score it.

    One bad case never stops the run: both the agent call and the scoring are
    guarded, and a case that could not be measured is recorded as skipped
    rather than counted. `skip_judge` leaves out the cases that cost a model
    call, so the free scorers can run anywhere — including CI without a key.
    """
    ctx = ctx or AdapterContext()
    agent, adapter = adapter_for(dataset_path)
    cases, _ = load_dataset(dataset_path)
    if only:
        cases = [c for c in cases if c.id == only]

    usage = Usage(model=ctx.model)
    report = EvalReport(agent=agent, results=[], usage=usage)

    if adapter is None:
        report.skipped = [(c.id, f"no adapter for {agent}") for c in cases]
        return report

    for case in cases:
        if skip_judge and case.scoring == "llm-judge":
            report.skipped.append((case.id, "llm-judge skipped (--skip-judge)"))
            continue

        try:
            inv = adapter(case, ctx)
        except Exception as exc:  # noqa: BLE001 — a broken case must not stop the run
            report.skipped.append((case.id, f"{type(exc).__name__}: {exc}"[:200]))
            continue

        if inv.usage is not None:
            _merge(usage, inv.usage)

        try:
            if case.scoring == "schema-valid":
                report.results.append(score_schema_valid(case, inv))
            elif case.scoring == "keyword-match":
                report.results.append(score_keyword_match(case, inv))
            else:
                report.results.append(
                    score_llm_judge(case, inv, client=ctx.client, model=ctx.model, usage=usage)
                )
        except Exception as exc:  # noqa: BLE001 — scoring must not kill the run either
            # Without this a missing API key took down the whole dataset,
            # including the cases that need no model at all.
            report.skipped.append((case.id, f"scoring failed: {type(exc).__name__}: {exc}"[:200]))

    return report


def render_report(report: EvalReport, verbose: bool = False) -> str:
    rate = report.pass_rate
    lines = [
        f"{report.agent}: "
        + (f"{rate:.0%}" if rate is not None else "no cases run")
        + f" ({sum(1 for r in report.results if r.passed)}/{len(report.results)})"
        + f" · bar {PASS_THRESHOLD:.0%} · "
        + ("MEETS BAR" if report.meets_bar else "BELOW BAR")
    ]
    for result in report.results:
        if verbose or not result.passed:
            mark = "pass" if result.passed else "FAIL"
            lines.append(f"  {mark}  {result.case_id}  [{result.scoring}]  {result.detail}")
    for case_id, reason in report.skipped:
        lines.append(f"  SKIP  {case_id}  {reason}")
    if report.usage.calls:
        lines.append(f"  usage: {json.dumps(report.usage.as_dict())}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "datasets",
        nargs="+",
        help="One or more agents' eval_dataset.jsonl (the agent is read from the path).",
    )
    parser.add_argument("--base-url", default="http://localhost:8000", help="Live target.")
    parser.add_argument("--model", default=DEFAULT_SUBAGENT_MODEL)
    parser.add_argument("--only", help="Run a single case by id.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show passing cases too.")
    parser.add_argument("--out", help="Write the JSON results here.")
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Run only the free scorers (schema-valid, keyword-match). No API key needed.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate the datasets without running anything.",
    )
    args = parser.parse_args(argv)

    if args.check_only:
        for path in args.datasets:
            try:
                cases, notes = load_dataset(path)
            except (OSError, ValueError) as exc:
                print(f"Dataset problem: {exc}", file=sys.stderr)
                return 1
            short = f" — below the {MIN_CASES}-case bar" if len(cases) < MIN_CASES else ""
            print(f"{path}: {len(cases)} cases, well-formed{short}")
            for note in notes:
                print(f"  note: {note}")
            if len(cases) < MIN_CASES:
                return 1
        return 0

    wants_model = not args.skip_judge
    if wants_model and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set. Scenario generation and the llm-judge "
            "scorer both call the API.\n"
            "Run the free scorers only with --skip-judge, or set the key.",
            file=sys.stderr,
        )
        return 1

    needs_target = any(adapter_for(p)[0] != "a03_scenario_generator" for p in args.datasets)
    if needs_target and not target_reachable(args.base_url):
        print(
            f"No target at {args.base_url}. Execution and Evaluation cases run against a "
            "live system:\n  uvicorn app:app --app-dir examples/wallet-api --port 8000",
            file=sys.stderr,
        )
        return 1

    ctx = AdapterContext(base_url=args.base_url, model=args.model)
    reports = []
    for path in args.datasets:
        try:
            report = run_dataset(
                path, ctx=ctx, only=args.only, skip_judge=args.skip_judge
            )
        except (OSError, ValueError) as exc:
            print(f"Dataset problem: {exc}", file=sys.stderr)
            return 1
        reports.append(report)
        print(render_report(report, verbose=args.verbose))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps([r.as_dict() for r in reports], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"-> {args.out}")

    below = [r.agent for r in reports if not r.meets_bar]
    if below:
        print(f"\nBelow the {PASS_THRESHOLD:.0%} bar: {', '.join(below)}")
        return 1
    print(f"\nAll agents meet the {PASS_THRESHOLD:.0%} bar.")
    return 0
