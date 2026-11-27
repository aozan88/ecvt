"""Agent eval runner — measures each agent against its own dataset (M1-T2b).

The agent standard says an agent is not done until it scores >= 80% on its
`eval_dataset.jsonl`, and gate G1 asks for that number for all three M1
agents. This module produces it.

An eval case is only worth having if it can be run. Three scoring modes, from
cheapest to most expensive — the same split C-T2 calls code-based vs
model-based evaluation:

- `schema-valid`   the output validates against a named model. Free, exact.
- `keyword-match`  required strings appear in the output. Free, blunt.
- `python-check`   a named function decides it. Free and exact, for facts a
                   parser can settle: is every placeholder bound, is every
                   endpoint in the spec. Asking a model these spends money to
                   add noise — the grader rambled about which scenario
                   violated what, and once called spec fields "invented".
- `llm-judge`      a model scores the output against the case's rubric.
                   Costs a call, so it is for claims the others cannot
                   express ("does this expectation describe correct behaviour
                   or the defect?").

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

from ecvt.llm import DEFAULT_SUBAGENT_MODEL, StructuredCallError, Usage, load_env, structured_call

PASS_THRESHOLD = 0.80
MIN_CASES = 10
REQUIRED_FIELDS = ("id", "input", "category", "scoring")
KNOWN_SCORING = ("schema-valid", "keyword-match", "python-check", "llm-judge")

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
- Write `reason` first, then `satisfies_rubric`. The verdict must follow the
  reasoning you just wrote, not contradict it.
- Answer about the OUTPUT, not about the rubric. `true` means the output does
  what the rubric asks; `false` means it does not. Never return `false` while
  explaining that the rubric's condition holds.
- If the output is unrelated or too short to tell, answer `false` and say what
  was missing. Guessing defeats the measurement.
- Quote the part of the output that decided it.
"""


class Unmeasurable(RuntimeError):
    """The case could not be graded for a harness reason, not an agent one.

    Raised so the runner records a skip rather than a failure: the first live
    run truncated a 19k-character ScenarioSet at 6k and the grader correctly
    reported it could not verify anything, which came out as seven FAILs
    against an agent that had done nothing wrong.
    """


@dataclass
class EvalCase:
    id: str
    input: dict[str, Any]
    category: str
    scoring: str
    expected_schema: str | None = None
    expected_keywords: list[str] = field(default_factory=list)
    check: str | None = None
    """Dotted path to a `(case, invocation) -> (passed, detail)` function."""

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

    context: dict[str, Any] | None = None
    """Validation context for schema-valid, for models whose checks need the input
    (a02: the spec and operation inventory, so evidence pointers are verified)."""


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
                check=raw.get("check"),
                rubric=raw.get("rubric"),
            )
        )

    dupes = [i for i, n in Counter(c.id for c in cases).items() if n > 1]
    if dupes:
        raise ValueError(f"{path}: duplicate case ids {dupes}")
    return cases, notes


# --- scorers ---------------------------------------------------------------


def _tools_json_validator(ref: str) -> Any:
    """`<path>/tools.json::<tool>` -> an object with model_validate(obj, context=None),
    for agents whose output contract is a JSON Schema rather than a Pydantic model."""
    import jsonschema

    path, _, name = ref.partition("::")
    tools = json.loads(Path(path).read_text(encoding="utf-8"))
    schema = next(t["input_schema"] for t in tools if t["name"] == name)

    class _Schema:
        @staticmethod
        def model_validate(obj: Any, context: Any = None) -> Any:
            jsonschema.validate(obj, schema)
            return obj

    return _Schema


def _import_model(dotted: str) -> Any:
    if "::" in dotted:
        return _tools_json_validator(dotted)
    module_name, _, attr = dotted.rpartition(".")
    if not module_name:
        raise ValueError(f"expected_schema must be a dotted path, got {dotted!r}")
    return getattr(importlib.import_module(module_name), attr)


# What a result keeps, and what the terminal shows of it. They differ because a
# judge's reason is the only thing that says which rubric clause failed, and at
# 300 characters it was cut mid-sentence: diagnosing a failed case meant running
# the eval again. The stored detail is now long enough to name the clause, while
# the per-case line stays one screenful (the full text is in the --out JSON).
DETAIL_CHARS = 1200
DISPLAY_DETAIL_CHARS = 300


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
        model.model_validate(inv.obj, context=inv.context)
    except Exception as exc:  # noqa: BLE001 — any validation error is the answer
        return result(False, f"does not match {case.expected_schema}: {exc}"[:DETAIL_CHARS])
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


def score_python_check(case: EvalCase, inv: Invocation) -> CaseResult:
    def result(passed: bool, detail: str) -> CaseResult:
        return CaseResult(case.id, case.scoring, case.category, passed, detail)

    if not case.check:
        return result(False, "case has no check")
    try:
        fn = _import_model(case.check)
    except (ImportError, AttributeError, ValueError) as exc:
        return result(False, f"cannot import {case.check}: {exc}")
    try:
        passed, detail = fn(case, inv)
    except Exception as exc:  # noqa: BLE001 — a broken check is a failed check
        return result(False, f"{case.check} raised {type(exc).__name__}: {exc}"[:DETAIL_CHARS])
    return result(bool(passed), str(detail)[:DETAIL_CHARS])


def score_llm_judge(
    case: EvalCase,
    inv: Invocation,
    *,
    client: Any | None = None,
    model: str = DEFAULT_SUBAGENT_MODEL,
    usage: Usage | None = None,
    max_chars: int = 200_000,
) -> CaseResult:
    def result(passed: bool, detail: str) -> CaseResult:
        return CaseResult(case.id, case.scoring, case.category, passed, detail)

    if not case.rubric:
        return result(False, "case has no rubric")

    output = inv.text if not inv.error else f"[agent declined or failed] {inv.error}\n{inv.text}"
    if len(output) > max_chars:
        # Grading a fragment measures the harness, not the agent.
        raise Unmeasurable(
            f"output is {len(output)} chars, over the {max_chars} the grader is given; "
            "grading a fragment would score the truncation"
        )

    # `reason` is declared FIRST on purpose. With the boolean first, the model
    # committed to a verdict and then reasoned — and twice talked itself round
    # without being able to revise it: "wait—re-evaluating: the output contains
    # only observations" (returned false) and "the rubric's conditions are met
    # and it should pass" (returned false). Reasoning before the verdict lets
    # the conclusion follow the argument instead of preceding it.
    tool = {
        "name": "grade",
        "description": "Return the grade for this one case. Use it for every response.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Work it out here, quoting the part of the output that decides "
                        "it. Write this before the verdict."
                    ),
                },
                "satisfies_rubric": {
                    "type": "boolean",
                    "description": "Your conclusion, consistent with the reason above.",
                },
            },
            "required": ["reason", "satisfies_rubric"],
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
        return result(False, f"grader produced no verdict: {exc}"[:DETAIL_CHARS])

    if usage is not None:
        _merge(usage, call.usage)
    passed = bool(call.data["satisfies_rubric"])
    return result(passed, str(call.data.get("reason", ""))[:DETAIL_CHARS])


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

    if ctx.client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        # This adapter calls the model to produce the output being scored, so
        # --skip-judge does not help here: the agent itself needs the key.
        raise Unmeasurable(
            "generating scenarios needs ANTHROPIC_API_KEY; --skip-judge only "
            "skips the grader, not the agent under test"
        )

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


def adapt_domain_intelligence(case: EvalCase, ctx: AdapterContext) -> Invocation:
    """Run a02 on `input.spec` with `input.task`. Its output is a DomainModel or a
    DeclineTask; schema-valid checks it against the spec it was built from."""
    from agents.a02_domain_intelligence.agent import (
        DEFAULT_TASK,
        build_domain_model,
        build_inventory,
        load_spec,
    )

    if ctx.client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        raise Unmeasurable("domain modelling needs ANTHROPIC_API_KEY")

    spec_path = case.input.get("spec")
    if not spec_path:
        return Invocation(text="No OpenAPI spec was supplied.", error="no spec supplied")
    try:
        spec = load_spec(spec_path)
        res = build_domain_model(
            spec_path=spec_path,
            task=case.input.get("task", DEFAULT_TASK),
            client=ctx.client,
            model=ctx.model,
        )
    except (FileNotFoundError, ValueError) as exc:
        return Invocation(text=f"cannot read spec: {exc}", error=str(exc))
    except StructuredCallError as exc:
        return Invocation(text=str(exc), error=str(exc), usage=exc.usage)

    payload = res.payload()
    tool = res.call.tool_name or "emit_domain_model"
    return Invocation(
        text=f"{tool}:\n{_render(payload)}",
        obj=payload,
        error="declined" if res.declined else None,
        usage=res.call.usage,
        context={"spec": spec, "inventory": build_inventory(spec)},
    )


MANAGER_EVAL_BUDGET_USD = 0.50


def _manager_stub_runners(faults: dict[str, str]) -> dict[str, Any]:
    """Deterministic stages for Manager evals: each succeeds unless `setup.stub_runners`
    injects a fault — "fail_retryable" (transient) or "fail" (permanent)."""
    from agents.a01_manager.agent import STAGES, StageError

    def make(stage: str) -> Any:
        def run(stage_input: dict[str, Any]) -> dict[str, Any]:
            fault = faults.get(stage)
            if fault == "fail_retryable":
                raise StageError(f"{stage}: injected transient fault", retryable=True)
            if fault == "fail":
                raise StageError(f"{stage}: injected permanent fault", retryable=False)
            return {"artifact": {"stub": stage, "upstream": sorted(stage_input["upstream"])},
                    "summary": f"{stage} ok (stub)", "metrics": {}, "usage": {}}

        return run

    return {s: make(s) for s in STAGES}


def _seed_run(store: Any, spec: str) -> None:
    """`run_id:status` -> a finished run in the eval's runs dir (for run_status cases)."""
    from agents.a01_manager.agent import STAGES

    run_id, _, status = spec.partition(":")
    state = store.create(run_id, "seeded for eval", "cli")
    state["intent"] = {"name": "full_validation", "slots": {}}
    state["plan"] = list(STAGES)
    for s in STAGES:
        state["stages"][s].update(status="succeeded", attempts=1)
    state["status"] = status or "completed"
    state["result"] = {"run_id": run_id, "status": state["status"], "summary": "seeded run"}
    store.save(state)


def adapt_manager(case: EvalCase, ctx: AdapterContext) -> Invocation:
    """Run the real Manager (live model) over stub stages. The output the graders read is
    the ordered tool-call trace, the stage records and the manager_output."""
    import asyncio
    import tempfile

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise Unmeasurable("the Manager needs ANTHROPIC_API_KEY (it calls a model in either mode)")
    from agents.a01_manager import agent as mgr

    request = case.input.get("request")
    if not isinstance(request, str):
        raise ValueError(f"case {case.id}: input.request must be the user's message")  # noqa: TRY004
    setup = case.input.get("setup") or {}
    runs_dir = Path(tempfile.mkdtemp(prefix="ecvt-mgr-eval-"))
    store = mgr.StateStore(runs_dir)
    for seed in setup.get("seed_runs", []):
        _seed_run(store, seed)
    run_id = mgr.new_run_id()
    trace: list[dict[str, Any]] = []
    cost: list[float] = []

    if os.environ.get("ECVT_MANAGER_MODE", "planner") == "planner":
        # ADR 0020: one model call, then code. The trace uses the agent loop's
        # tool names for the same steps, so one rubric reads either mode.
        from agents.a01_manager.planner import run_planned

        def on_event(kind: str, payload: dict[str, Any]) -> None:
            if kind == "plan":
                trace.append({"tool": "plan_run", "input": {"intent": payload["intent"],
                              "next_action": payload["next_action"]}})  # fmt: skip
            elif kind == "stage_start":
                trace.append({"tool": f"run_{payload['stage']}",
                              "input": {"attempt": payload["attempt"]}})  # fmt: skip
            elif kind == "done":
                trace.append({"tool": "manager_output", "input": payload})

        state = run_planned(
            request,
            run_id=run_id,
            runs_dir=runs_dir,
            runners=_manager_stub_runners(setup.get("stub_runners") or {}),
            on_event=on_event,
        )
        cost.append(float((state.get("manager_session") or {}).get("total_cost_usd") or 0))
    else:
        asyncio.run(_drive_agent_manager(mgr, request, run_id, runs_dir, setup, trace, cost))
    state = store.load(run_id)
    stages = {
        s: {"status": state["stages"][s]["status"], "attempts": state["stages"][s]["attempts"]}
        for s in state["plan"]
    }
    view = {"tool_calls": trace, "intent": state["intent"], "stages": stages,
            "manager_output": state["result"], "cost_usd": cost[-1] if cost else None}
    return Invocation(text=_render(view), obj=state["result"])


async def _drive_agent_manager(
    mgr: Any, request: str, run_id: str, runs_dir: Path, setup: dict[str, Any],
    trace: list[dict[str, Any]], cost: list[float],
) -> None:  # fmt: skip
    """The M2 agent loop (`ECVT_MANAGER_MODE=agent`): its SDK messages, as a trace."""
    async for msg in mgr.run_manager(
        request,
        run_id=run_id,
        runs_dir=runs_dir,
        runners=_manager_stub_runners(setup.get("stub_runners") or {}),
        max_budget_usd=MANAGER_EVAL_BUDGET_USD,
    ):
        for block in getattr(msg, "content", None) or []:
            if type(block).__name__ == "ToolUseBlock":
                name = block.name.removeprefix(f"mcp__{mgr.SERVER}__")
                trace.append({"tool": name, "input": block.input})
        if getattr(msg, "total_cost_usd", None) is not None:
            cost.append(float(msg.total_cost_usd))


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
    "a01_manager": adapt_manager,
    "a02_domain_intelligence": adapt_domain_intelligence,
    "a03_scenario_generator": adapt_scenario_generator,
    "a05_execution": adapt_execution,
    "a06_evaluation": adapt_evaluation,
}


TARGET_AGENTS = ("a05_execution", "a06_evaluation")


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
        except Unmeasurable as exc:
            report.skipped.append((case.id, f"unmeasurable: {exc}"[:200]))
            continue
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
            elif case.scoring == "python-check":
                report.results.append(score_python_check(case, inv))
            else:
                report.results.append(
                    score_llm_judge(case, inv, client=ctx.client, model=ctx.model, usage=usage)
                )
        except Unmeasurable as exc:
            report.skipped.append((case.id, f"unmeasurable: {exc}"[:200]))
        except Exception as exc:  # noqa: BLE001 — scoring must not kill the run either
            # Without this a missing API key took down the whole dataset,
            # including the cases that need no model at all.
            report.skipped.append((case.id, f"scoring failed: {type(exc).__name__}: {exc}"[:200]))

    return report


# --- repeated runs ---------------------------------------------------------
#
# One run of an eval is one draw. a03's measured 90% and 70% on the same code
# and the same dataset, and its dominant failure — a generation exhausting its
# attempts — landed on a different case every time. A gate that reads one draw
# against an 80% bar passes or fails by luck, so a dataset can be run N times
# and judged on the pooled rate, with each case's k/N kept so a flaky case is
# told apart from a broken one.

STABLE_PASS = "stable_pass"
FLAKY = "flaky"
STABLE_FAIL = "stable_fail"
UNMEASURED = "unmeasured"


@dataclass
class CaseTally:
    """One case across N runs: how often it passed, of the runs that measured it."""

    case_id: str
    scoring: str
    category: str
    passed: int = 0
    measured: int = 0
    last_failure: str = ""

    @property
    def verdict(self) -> str:
        """`stable_fail` (0/N) is kept apart from `flaky` (some but not all):
        the first is a broken case or a broken behaviour, the second is noise
        around a working one, and they call for different fixes."""
        if self.measured == 0:
            return UNMEASURED
        if self.passed == self.measured:
            return STABLE_PASS
        if self.passed == 0:
            return STABLE_FAIL
        return FLAKY


@dataclass
class RepeatedReport:
    agent: str
    runs: list[EvalReport]
    tallies: list[CaseTally]
    usage: Usage

    @property
    def pooled(self) -> tuple[int, int]:
        """(passes, results) over every run — skipped cases count in neither."""
        return (
            sum(t.passed for t in self.tallies),
            sum(t.measured for t in self.tallies),
        )

    @property
    def pass_rate(self) -> float | None:
        passed, total = self.pooled
        return passed / total if total else None

    @property
    def skipped(self) -> list[tuple[int, str, str]]:
        """(run number, case_id, reason), run numbers from 1."""
        return [(i, c, r) for i, run in enumerate(self.runs, start=1) for c, r in run.skipped]

    @property
    def meets_bar(self) -> bool:
        """Same rule as one run: any skip, in any run, keeps the bar unmet.

        A case measured in two runs of three is one the gate did not fully see,
        and the pooled rate would otherwise quietly drop its missing draw.
        """
        rate = self.pass_rate
        return bool(rate is not None and rate >= PASS_THRESHOLD and not self.skipped)

    def by_verdict(self, verdict: str) -> list[CaseTally]:
        return [t for t in self.tallies if t.verdict == verdict]

    def as_dict(self) -> dict[str, Any]:
        passed, total = self.pooled
        rate = self.pass_rate
        return {
            "agent": self.agent,
            "runs": len(self.runs),
            "passed": passed,
            "results": total,
            "pass_rate": round(rate, 3) if rate is not None else None,
            "threshold": PASS_THRESHOLD,
            "meets_bar": self.meets_bar,
            "per_run_pass_rate": [
                round(r.pass_rate, 3) if r.pass_rate is not None else None for r in self.runs
            ],
            "cases": [
                {
                    "case": t.case_id,
                    "scoring": t.scoring,
                    "category": t.category,
                    "passed": t.passed,
                    "measured": t.measured,
                    "verdict": t.verdict,
                }
                for t in self.tallies
            ],
            "flaky": [t.case_id for t in self.by_verdict(FLAKY)],
            "stable_fail": [t.case_id for t in self.by_verdict(STABLE_FAIL)],
            "skipped": [{"run": i, "case": c, "reason": r} for i, c, r in self.skipped],
            "usage": self.usage.as_dict(),
            "per_run": [r.as_dict() for r in self.runs],
        }


def run_dataset_repeated(
    dataset_path: str | Path,
    runs: int,
    *,
    ctx: AdapterContext | None = None,
    only: str | None = None,
    skip_judge: bool = False,
) -> RepeatedReport:
    """Run a dataset `runs` times and pool the results per case.

    Each run is an ordinary `run_dataset`, so everything that keeps one bad
    case from stopping a run holds here too. Cases are tallied in dataset
    order, including ones that were skipped in every run.
    """
    if runs < 1:
        raise ValueError("runs must be at least 1")
    ctx = ctx or AdapterContext()
    agent, _ = adapter_for(dataset_path)
    cases, _ = load_dataset(dataset_path)
    if only:
        cases = [c for c in cases if c.id == only]
    tallies = {c.id: CaseTally(c.id, c.scoring, c.category) for c in cases}

    reports: list[EvalReport] = []
    usage = Usage(model=ctx.model)
    for _ in range(runs):
        report = run_dataset(dataset_path, ctx=ctx, only=only, skip_judge=skip_judge)
        reports.append(report)
        _merge(usage, report.usage)
        for result in report.results:
            tally = tallies.setdefault(
                result.case_id, CaseTally(result.case_id, result.scoring, result.category)
            )
            tally.measured += 1
            if result.passed:
                tally.passed += 1
            else:
                tally.last_failure = result.detail

    return RepeatedReport(agent=agent, runs=reports, tallies=list(tallies.values()), usage=usage)


def render_repeated_report(report: RepeatedReport, verbose: bool = False) -> str:
    passed, total = report.pooled
    rate = report.pass_rate
    n = len(report.runs)
    per_run = " · ".join(
        f"{r.pass_rate:.0%}" if r.pass_rate is not None else "—" for r in report.runs
    )
    lines = [
        f"{report.agent}: "
        + (f"{rate:.0%} pooled" if rate is not None else "no cases run")
        + f" ({passed}/{total}) over {n} runs"
        + f" · bar {PASS_THRESHOLD:.0%} · "
        + ("MEETS BAR" if report.meets_bar else "BELOW BAR"),
        f"  runs: {per_run}",
    ]
    for verdict, label in ((STABLE_FAIL, "stable fail"), (FLAKY, "flaky")):
        group = report.by_verdict(verdict)
        if group:
            lines.append(
                f"  {label} ({len(group)}): "
                + " · ".join(f"{t.case_id} {t.passed}/{t.measured}" for t in group)
            )
    # Why a broken case fails is worth a line every time; a flaky case's
    # reason changes run to run, so it is only shown when asked for.
    shown = report.by_verdict(STABLE_FAIL) + (report.by_verdict(FLAKY) if verbose else [])
    for t in shown:
        lines.append(f"    {t.case_id}: {t.last_failure[:DISPLAY_DETAIL_CHARS]}")
    if verbose:
        for t in report.by_verdict(STABLE_PASS):
            lines.append(f"  pass  {t.case_id}  {t.passed}/{t.measured}  [{t.scoring}]")
    for i, case_id, reason in report.skipped:
        lines.append(f"  SKIP  run {i}  {case_id}  {reason}")
    if report.usage.calls:
        lines.append(f"  usage: {json.dumps(report.usage.as_dict())}")
    return "\n".join(lines)


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
            lines.append(
                f"  {mark}  {result.case_id}  [{result.scoring}]  "
                f"{result.detail[:DISPLAY_DETAIL_CHARS]}"
            )
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
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Run each dataset N times and judge the pooled rate; cases passing in some "
        "runs but not all are listed as flaky, 0/N as stable fail. Each run costs a "
        "full dataset's worth of calls.",
    )
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    load_env()

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

    # Only Execution and Evaluation cases call the target; a01 runs on stub stages.
    needs_target = any(adapter_for(p)[0] in TARGET_AGENTS for p in args.datasets)
    if needs_target and not target_reachable(args.base_url):
        print(
            f"No target at {args.base_url}. Execution and Evaluation cases run against a "
            "live system:\n  uvicorn app:app --app-dir examples/wallet-api --port 8000",
            file=sys.stderr,
        )
        return 1

    ctx = AdapterContext(base_url=args.base_url, model=args.model)
    reports: list[EvalReport | RepeatedReport] = []
    for path in args.datasets:
        try:
            if args.runs == 1:
                report: EvalReport | RepeatedReport = run_dataset(
                    path, ctx=ctx, only=args.only, skip_judge=args.skip_judge
                )
            else:
                report = run_dataset_repeated(
                    path, args.runs, ctx=ctx, only=args.only, skip_judge=args.skip_judge
                )
        except (OSError, ValueError) as exc:
            print(f"Dataset problem: {exc}", file=sys.stderr)
            return 1
        reports.append(report)
        if isinstance(report, RepeatedReport):
            print(render_repeated_report(report, verbose=args.verbose))
        else:
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
