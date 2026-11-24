"""ECVT core scenario schema (M1-T1).

A Scenario is one QE probe against a target system: optional `setup` steps that
build state, one `action` step under test, an `expectation` describing correct
behaviour, and optional `probes` that read state back afterwards.

Execution (M1-T3) runs the steps and records raw responses; Evaluation (M1-T5)
scores them against `expectation`. Neither reads `ground_truth.yaml` — that file
is for measuring catch rate only (ADR 0007).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"


class ScenarioCategory(str, Enum):
    """The 7 QE categories every ScenarioSet must cover (Master Plan v7)."""

    HAPPY = "happy"
    EDGE = "edge"
    STRESS = "stress"
    SECURITY = "security"
    NEGATIVE = "negative"
    ADVERSARIAL = "adversarial"
    PERSONA = "persona"


ALL_CATEGORIES: tuple[ScenarioCategory, ...] = tuple(ScenarioCategory)


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HttpStep(BaseModel):
    """One HTTP call.

    `path` and string values inside `body` / `query` may contain `{name}`
    placeholders, resolved by Execution from earlier steps' `save` captures.
    """

    name: str = Field(description="Short snake_case label, unique within the scenario.")
    method: HttpMethod
    path: str = Field(description="Path beginning with '/', e.g. /accounts/{src_id}/balance")
    body: dict[str, Any] | None = Field(default=None, description="JSON request body.")
    query: dict[str, str] | None = Field(default=None, description="Query string parameters.")
    headers: dict[str, str] | None = Field(default=None, description="Extra request headers.")
    save: dict[str, str] | None = Field(
        default=None,
        description=(
            "Capture values from this step's JSON response for later steps: "
            '{"src_id": "id"} reads the response field `id` and binds it to {src_id}. '
            "Dotted paths index nested objects, e.g. 'data.account.id'."
        ),
    )

    @field_validator("path")
    @classmethod
    def _path_starts_with_slash(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("path must start with '/'")
        return v

    @property
    def endpoint(self) -> str:
        return f"{self.method.value} {self.path}"


class Expectation(BaseModel):
    """What a correct system would do — never what the buggy target does."""

    expected_status: list[int] = Field(
        min_length=1,
        description="HTTP status codes a correct implementation may return for the action.",
    )
    expected_behavior: str = Field(
        description=(
            "One or two sentences on correct behaviour, in terms a reviewer can check "
            "against the recorded response. Used by the LLM judge."
        )
    )
    invariants: list[str] = Field(
        default_factory=list,
        description=(
            "Checkable statements about state after the action, e.g. "
            "'sum of both balances is unchanged'. Verified against `probes` output."
        ),
    )
    probes: list[HttpStep] = Field(
        default_factory=list,
        description="Read-only calls run after the action to observe resulting state.",
    )

    @field_validator("expected_status")
    @classmethod
    def _plausible_status(cls, v: list[int]) -> list[int]:
        bad = [s for s in v if not 100 <= s <= 599]
        if bad:
            raise ValueError(f"not HTTP status codes: {bad}")
        return v


class Scenario(BaseModel):
    id: str = Field(description="Stable id, lowercase slug, e.g. 'sc-001' or 'sc-overdraft'.")
    title: str = Field(description="One short line naming what is probed.")
    category: ScenarioCategory
    severity: Severity = Severity.MEDIUM
    rationale: str = Field(
        description="Why this is worth testing — the failure mode it would expose."
    )
    setup: list[HttpStep] = Field(
        default_factory=list, description="Steps that build prerequisite state."
    )
    action: HttpStep = Field(description="The single step under test.")
    expectation: Expectation
    repeat: int = Field(
        default=1, ge=1, le=50, description="Send the action N times (idempotency / replay)."
    )
    concurrency: int = Field(
        default=1, ge=1, le=50, description="Send the action N times in parallel (race)."
    )

    @model_validator(mode="after")
    def _unique_step_names(self) -> Scenario:
        names = [s.name for s in self.setup] + [self.action.name]
        dupes = [n for n, c in Counter(names).items() if c > 1]
        if dupes:
            raise ValueError(f"duplicate step names in {self.id}: {dupes}")
        return self

    @model_validator(mode="after")
    def _probes_are_reads(self) -> Scenario:
        bad = [p.name for p in self.expectation.probes if p.method is not HttpMethod.GET]
        if bad:
            raise ValueError(f"probes must be GET in {self.id}: {bad}")
        return self


class ScenarioDraft(Scenario):
    """The per-scenario wire format.

    This used to drop `Scenario`'s defaults so that the model had to state
    `repeat` and `concurrency` on every scenario: a defaulted field is an
    optional field, and the generator had returned a whole set at 1 — including
    a scenario titled "Many parallel transfers racing" — even with the prompt
    spelling the requirement out. The intent was that the decision be taken
    rather than skipped.

    What it actually bought was the form, not the meaning. The guarantee that a
    set reaches a race and a replay comes from the generator's own validator
    (`missing_execution_modes`, asked only of a set that writes), which the
    prompt also states; required fields were a second mechanism for the same
    goal. At the default quota they asked for 28 numbers, 24 of which could only
    sensibly be 1, and half of all generations spent a whole extra attempt on
    "Field required" — a mechanical slip that carries no signal about scenario
    quality. Measured over 8 generations, 4 needed a second attempt for this and
    nothing else.

    So the defaults are back, and a number is stated where the model means
    something other than 1. The set-level check still rejects a set that cannot
    observe a race or a duplicate, and that rejection names what is missing.
    """


class ScenarioSetDraft(BaseModel):
    """The generator's wire format — just the scenarios.

    This is what the model fills in via the forced tool call; run metadata
    (target, model, timestamp) is attached by code afterwards, so the model
    cannot get it wrong. `extra="forbid"` turns silent field drift into a
    validation error the retry can act on.
    """

    model_config = ConfigDict(extra="forbid")

    scenarios: list[ScenarioDraft] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _unique_ids(self) -> ScenarioSetDraft:
        dupes = [i for i, c in Counter(s.id for s in self.scenarios).items() if c > 1]
        if dupes:
            raise ValueError(f"duplicate scenario ids: {dupes}")
        return self

    def missing_categories(self) -> list[str]:
        present = {s.category.value for s in self.scenarios}
        return [c.value for c in ALL_CATEGORIES if c.value not in present]

    def writes(self) -> bool:
        return any(s.action.method is not HttpMethod.GET for s in self.scenarios)

    def missing_execution_modes(self) -> list[str]:
        """Which of `concurrency` / `repeat` the set never uses above 1.

        Asked only of a set that writes: there is nothing to replay on a
        read-only API, so demanding a duplicate there would be noise.
        """
        if not self.writes():
            return []
        missing = []
        if not any(s.concurrency > 1 for s in self.scenarios):
            missing.append("concurrency")
        if not any(s.repeat > 1 for s in self.scenarios):
            missing.append("repeat")
        return missing


class ScenarioSet(BaseModel):
    """The Scenario Generator's output: scenarios covering all 7 categories."""

    schema_version: str = SCHEMA_VERSION
    target: str = Field(description="Target system identifier, e.g. 'examples/wallet-api'.")
    spec_title: str = Field(description="Title taken from the OpenAPI spec.")
    scenarios: list[Scenario] = Field(min_length=1)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model: str | None = Field(default=None, description="Model that produced this set.")

    @model_validator(mode="after")
    def _unique_ids(self) -> ScenarioSet:
        dupes = [i for i, c in Counter(s.id for s in self.scenarios).items() if c > 1]
        if dupes:
            raise ValueError(f"duplicate scenario ids: {dupes}")
        return self

    def category_counts(self) -> dict[str, int]:
        counts = Counter(s.category.value for s in self.scenarios)
        return {c.value: counts.get(c.value, 0) for c in ALL_CATEGORIES}

    def missing_categories(self) -> list[str]:
        return [name for name, n in self.category_counts().items() if n == 0]

    def covers_all_categories(self) -> bool:
        return not self.missing_categories()


def write_scenarios_jsonl(path: str | Path, scenario_set: ScenarioSet) -> Path:
    """Write one scenario per line. Run metadata goes to `<stem>.meta.json`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for scenario in scenario_set.scenarios:
            fh.write(scenario.model_dump_json(exclude_none=True) + "\n")

    meta = scenario_set.model_dump(mode="json", exclude={"scenarios"})
    meta["scenario_count"] = len(scenario_set.scenarios)
    meta["category_counts"] = scenario_set.category_counts()
    path.with_suffix(".meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def read_scenarios_jsonl(path: str | Path) -> list[Scenario]:
    """Read scenarios back, validating each line."""
    path = Path(path)
    scenarios: list[Scenario] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                scenarios.append(Scenario.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"{path}:{lineno} invalid scenario: {exc}") from exc
    return scenarios
