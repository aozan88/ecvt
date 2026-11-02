"""Evaluation scoring — schema and deterministic rule engine (M1-T5).

Mapping v3 gives Evaluation five scoring dimensions: rule engine, LLM judge,
similarity, semantic, and latency/cost. M1 implements the first two (Master
Plan v7, M1-T5), so `Dimension` names all five and the schema carries a list
of dimension scores rather than two fixed fields — adding similarity later is
then a new entry, not a schema change.

Order matters for cost (ADR 0009): rules are free and run on everything; the
judge is a model call per scenario and runs only where rules cannot decide.

The rule engine is deliberately **domain-agnostic** (ADR 0003). It checks
things true of any HTTP API — the status a correct implementation would
return, that no request produced a 500, that a replay was not silently
applied twice. It does not try to verify a scenario's natural-language
invariants ("source balance is still 50.0"); those go to the judge in M1 and
to the wallet domain plugin in M3-T2, which is where domain rules belong.

Nothing here reads `ground_truth.yaml` (ADR 0007).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field

from ecvt.results import ExecutionRecord, RunOutcome, StepResult
from ecvt.schema import Scenario


class Dimension(str, Enum):
    RULE = "rule"
    JUDGE = "judge"
    SIMILARITY = "similarity"
    """Embedding distance. Declared for Mapping v3; not implemented in M1."""

    SEMANTIC = "semantic"
    """BLEU/ROUGE-style overlap. Declared for Mapping v3; not implemented in M1."""

    PERFORMANCE = "performance"
    """Latency and cost. Recorded as metadata in M1, not scored."""


class Verdict(str, Enum):
    PASS = "pass"
    """Behaved as a correct implementation should."""

    FAIL = "fail"
    """Deviated from the expectation — a candidate defect."""

    INCONCLUSIVE = "inconclusive"
    """Rules cannot decide; needs the judge."""

    NOT_RUN = "not_run"
    """No observation to score: the scenario never reached its action."""


class RuleCheck(BaseModel):
    name: str
    verdict: Verdict
    detail: str
    expected: str | None = None
    observed: str | None = None


class DimensionScore(BaseModel):
    dimension: Dimension
    verdict: Verdict
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    checks: list[RuleCheck] = Field(default_factory=list)
    rationale: str | None = None
    model: str | None = Field(default=None, description="Set when a model produced this.")


class ScenarioScore(BaseModel):
    scenario_id: str
    category: str
    verdict: Verdict
    defect_suspected: bool = Field(
        description="True when the observation deviates from correct behaviour."
    )
    summary: str
    dimensions: list[DimensionScore] = Field(default_factory=list)
    latency_ms_max: float | None = None
    latency_ms_mean: float | None = None
    scored_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def dimension(self, dimension: Dimension) -> DimensionScore | None:
        return next((d for d in self.dimensions if d.dimension is dimension), None)


# --- rule engine -----------------------------------------------------------


def _status_class(step: StepResult) -> str:
    if step.status_code is None:
        return "none"
    return f"{step.status_code // 100}xx"


def _check_outcome(record: ExecutionRecord) -> RuleCheck:
    if record.outcome is RunOutcome.COMPLETED:
        return RuleCheck(
            name="execution_outcome",
            verdict=Verdict.PASS,
            detail="setup established state and the action reached the target",
        )
    return RuleCheck(
        name="execution_outcome",
        verdict=Verdict.NOT_RUN,
        detail=record.note or record.outcome.value,
        observed=record.outcome.value,
    )


def _check_status(scenario: Scenario, record: ExecutionRecord) -> RuleCheck:
    expected = scenario.expectation.expected_status
    observed = [a.status_code for a in record.actions if a.status_code is not None]
    if not observed:
        return RuleCheck(
            name="status_expected",
            verdict=Verdict.NOT_RUN,
            detail="no action received an HTTP response",
            expected=str(expected),
        )
    unexpected = sorted({s for s in observed if s not in expected})
    if not unexpected:
        return RuleCheck(
            name="status_expected",
            verdict=Verdict.PASS,
            detail="every action returned a status a correct implementation may return",
            expected=str(expected),
            observed=str(sorted(set(observed))),
        )
    return RuleCheck(
        name="status_expected",
        verdict=Verdict.FAIL,
        detail=f"returned {unexpected}, which a correct implementation should not",
        expected=str(expected),
        observed=str(sorted(set(observed))),
    )


def _check_server_error(record: ExecutionRecord) -> RuleCheck | None:
    """A 5xx is a defect whatever the scenario expected."""
    offenders = [
        s for s in record.all_steps if s.status_code is not None and s.status_code >= 500
    ]
    if not offenders:
        return None
    names = ", ".join(f"{s.name}={s.status_code}" for s in offenders)
    return RuleCheck(
        name="no_server_error",
        verdict=Verdict.FAIL,
        detail=(
            f"the target raised a server error ({names}); a correct implementation "
            "answers with a client error instead of failing"
        ),
        expected="no 5xx",
        observed=names,
    )


def _check_replay_consistency(scenario: Scenario, record: ExecutionRecord) -> RuleCheck | None:
    """For a replay or a race, were the repeated attempts all accepted?

    Domain-agnostic reading: if the same non-GET request is sent N times and
    every copy is accepted, nothing in the target distinguished the repeats.
    Whether that corrupted state is the judge's call (M1) or the domain
    plugin's (M3) — this check only records that no request was rejected.
    """
    attempts = scenario.repeat * scenario.concurrency
    if attempts < 2 or scenario.action.method.value == "GET":
        return None

    accepted = [a for a in record.actions if a.status_code is not None and a.status_code < 300]
    if len(accepted) < 2:
        return RuleCheck(
            name="replay_rejected",
            verdict=Verdict.PASS,
            detail=f"{len(accepted)} of {len(record.actions)} repeated attempts were accepted",
            observed=str([a.status_code for a in record.actions]),
        )
    return RuleCheck(
        name="replay_rejected",
        verdict=Verdict.INCONCLUSIVE,
        detail=(
            f"all {len(accepted)} repeated attempts were accepted; whether that is "
            "correct depends on the domain's idempotency rules"
        ),
        expected="repeats rejected or de-duplicated",
        observed=str([a.status_code for a in record.actions]),
    )


def _check_probes(record: ExecutionRecord) -> RuleCheck | None:
    if not record.probes:
        return None
    broken = [p for p in record.probes if p.is_error or p.http_failed]
    if not broken:
        return RuleCheck(
            name="probes_readable",
            verdict=Verdict.PASS,
            detail=f"all {len(record.probes)} probes returned readable state",
        )
    return RuleCheck(
        name="probes_readable",
        verdict=Verdict.FAIL,
        detail=(
            "state could not be read back: "
            + ", ".join(f"{p.name}={p.error or _status_class(p)}" for p in broken)
        ),
    )


def run_rules(scenario: Scenario, record: ExecutionRecord) -> DimensionScore:
    """Deterministic checks. Never calls a model."""
    checks: list[RuleCheck] = [_check_outcome(record)]

    if record.outcome is RunOutcome.COMPLETED:
        checks.append(_check_status(scenario, record))
        for maybe in (
            _check_server_error(record),
            _check_replay_consistency(scenario, record),
            _check_probes(record),
        ):
            if maybe is not None:
                checks.append(maybe)

    verdicts = [c.verdict for c in checks]
    if Verdict.FAIL in verdicts:
        verdict = Verdict.FAIL
    elif Verdict.NOT_RUN in verdicts:
        verdict = Verdict.NOT_RUN
    elif Verdict.INCONCLUSIVE in verdicts:
        verdict = Verdict.INCONCLUSIVE
    else:
        verdict = Verdict.PASS

    decided = [c for c in checks if c.verdict in (Verdict.PASS, Verdict.FAIL)]
    score = (
        sum(1 for c in decided if c.verdict is Verdict.PASS) / len(decided) if decided else None
    )

    return DimensionScore(
        dimension=Dimension.RULE, verdict=verdict, score=score, checks=checks
    )


def needs_judge(scenario: Scenario, rule_score: DimensionScore) -> bool:
    """Whether a model call is worth its cost for this scenario.

    The judge is skipped when rules already settled it and there is no
    natural-language claim left to weigh — that is the cost lever at scale
    (ADR 0009). It still runs on a rule FAIL, to say what the deviation means.
    """
    if rule_score.verdict is Verdict.NOT_RUN:
        return False
    if rule_score.verdict in (Verdict.FAIL, Verdict.INCONCLUSIVE):
        return True
    return bool(scenario.expectation.invariants)


def _latency(record: ExecutionRecord) -> tuple[float | None, float | None]:
    times = [s.elapsed_ms for s in record.all_steps if s.elapsed_ms]
    if not times:
        return None, None
    return round(max(times), 1), round(mean(times), 1)


def combine(
    scenario: Scenario,
    record: ExecutionRecord,
    dimensions: list[DimensionScore],
) -> ScenarioScore:
    """Fold dimension scores into one verdict for the scenario.

    A FAIL in any dimension carries: one dimension finding a deviation is
    enough to call the scenario a candidate defect, and a dimension that could
    not decide never overturns one that did.
    """
    verdicts = [d.verdict for d in dimensions]
    if Verdict.FAIL in verdicts:
        verdict = Verdict.FAIL
    elif all(v is Verdict.NOT_RUN for v in verdicts) and verdicts:
        verdict = Verdict.NOT_RUN
    elif Verdict.INCONCLUSIVE in verdicts:
        verdict = Verdict.INCONCLUSIVE
    else:
        verdict = Verdict.PASS

    failed = [c for d in dimensions for c in d.checks if c.verdict is Verdict.FAIL]
    judge = next((d for d in dimensions if d.dimension is Dimension.JUDGE), None)

    if verdict is Verdict.FAIL and failed:
        summary = failed[0].detail
    elif verdict is Verdict.FAIL and judge and judge.rationale:
        summary = judge.rationale
    elif verdict is Verdict.NOT_RUN:
        summary = record.note or "the scenario produced no observation"
    elif verdict is Verdict.INCONCLUSIVE:
        summary = "rules could not decide; needs a judgement on the stated invariants"
    else:
        summary = "behaved as a correct implementation should"

    latency_max, latency_mean = _latency(record)
    return ScenarioScore(
        scenario_id=scenario.id,
        category=scenario.category.value,
        verdict=verdict,
        defect_suspected=verdict is Verdict.FAIL,
        summary=summary,
        dimensions=dimensions,
        latency_ms_max=latency_max,
        latency_ms_mean=latency_mean,
    )


def score_with_rules(scenario: Scenario, record: ExecutionRecord) -> ScenarioScore:
    """Rule-only score, for runs with no judge (or no API key)."""
    return combine(scenario, record, [run_rules(scenario, record)])


# --- persistence -----------------------------------------------------------


def write_scores_jsonl(path: str | Path, scores: list[ScenarioScore]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for score in scores:
            fh.write(score.model_dump_json(exclude_none=True) + "\n")
    return path


def read_scores_jsonl(path: str | Path) -> list[ScenarioScore]:
    path = Path(path)
    scores: list[ScenarioScore] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                scores.append(ScenarioScore.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"{path}:{lineno} invalid score: {exc}") from exc
    return scores


def summarize(scores: list[ScenarioScore]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for score in scores:
        counts[score.verdict.value] = counts.get(score.verdict.value, 0) + 1
    return {
        "scored": len(scores),
        "verdicts": counts,
        "defects_suspected": sum(1 for s in scores if s.defect_suspected),
        "judged": sum(1 for s in scores if s.dimension(Dimension.JUDGE) is not None),
    }
