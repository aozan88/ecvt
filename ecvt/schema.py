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
from typing import Annotated, Any, Literal

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


class ScenarioCore(BaseModel):
    """A scenario minus how many times its action is sent.

    Split out so the wire format can ask for `repeat` and `concurrency` only in
    the categories where they mean something (see `ScenarioDraft`). Reading a
    scenario from a file or writing one by hand goes through `Scenario`, which
    defaults both to 1 — that behaviour is unchanged.
    """

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

    @model_validator(mode="after")
    def _unique_step_names(self) -> ScenarioCore:
        names = [s.name for s in self.setup] + [self.action.name]
        dupes = [n for n, c in Counter(names).items() if c > 1]
        if dupes:
            raise ValueError(f"duplicate step names in {self.id}: {dupes}")
        return self

    @model_validator(mode="after")
    def _probes_are_reads(self) -> ScenarioCore:
        bad = [p.name for p in self.expectation.probes if p.method is not HttpMethod.GET]
        if bad:
            raise ValueError(f"probes must be GET in {self.id}: {bad}")
        return self


class Scenario(ScenarioCore):
    """A scenario as the rest of ECVT uses it: both execution modes always present.

    `repeat` and `concurrency` default to 1, which is what a hand-written
    scenario or one read back from `scenarios.jsonl` wants.
    """

    repeat: int = Field(
        default=1, ge=1, le=50, description="Send the action N times (idempotency / replay)."
    )
    concurrency: int = Field(
        default=1, ge=1, le=50, description="Send the action N times in parallel (race)."
    )


LOAD_CATEGORIES: tuple[ScenarioCategory, ...] = (
    ScenarioCategory.STRESS,
    ScenarioCategory.ADVERSARIAL,
)
"""The categories whose point is sending the action more than once.

The prompt already maps them that way: a race is what `stress` probes, a replay
is what `adversarial` probes. Everywhere else 1 is the only sensible value.
"""

PLAIN_CATEGORIES: tuple[ScenarioCategory, ...] = tuple(
    c for c in ALL_CATEGORIES if c not in LOAD_CATEGORIES
)


class StressScenarioDraft(ScenarioCore):
    """`stress`: a race is what the category probes, so `concurrency` must be above 1.

    A `stress` scenario left at `concurrency: 1` is a single request with a
    label — the generator has produced exactly that, including one titled "Many
    parallel transfers racing". Here the schema does not merely ask for the
    number, it rules out the value that makes the category a lie.
    """

    category: Literal[ScenarioCategory.STRESS]
    repeat: int = Field(
        ge=1, le=50, description="Send the action N times (idempotency / replay). 1 = once."
    )
    concurrency: int = Field(
        ge=2,
        le=50,
        description="How many copies of the action run at once. Must be 2 or more: "
        "one sequential request cannot expose a race.",
    )

    def to_scenario(self) -> Scenario:
        return Scenario.model_validate(self.model_dump())


class AdversarialScenarioDraft(ScenarioCore):
    """`adversarial`: both numbers are asked for, neither is forced above 1.

    Replay and double-submit need `repeat`, but the category is wider than that
    — ordering tricks and malformed sequences probe abuse without sending the
    action twice. Forcing `repeat >= 2` here would push every adversarial
    scenario into one shape. The set-level check
    (`ScenarioSetDraft.missing_execution_modes`) is what catches a writing set
    in which nothing replays.
    """

    category: Literal[ScenarioCategory.ADVERSARIAL]
    repeat: int = Field(
        ge=1,
        le=50,
        description="Send the action N times in sequence. Above 1 for replay or "
        "double-submit; 1 for abuse that does not need a duplicate.",
    )
    concurrency: int = Field(
        ge=1, le=50, description="Send the action N times in parallel (race). 1 = no race."
    )

    def to_scenario(self) -> Scenario:
        return Scenario.model_validate(self.model_dump())


class PlainScenarioDraft(ScenarioCore):
    """The other five categories: neither number appears in the schema at all.

    Requiring both on every scenario asked the model for 28 numbers at the
    default quota, 24 of which could only sensibly be 1, and it omitted some
    often enough to cost an attempt — with a02's DomainModel in the prompt, no
    generation validated on its first try. Asking only where the number is the
    point cuts that to 8 (measured in CHANGELOG › Measured).

    Code supplies 1 and 1 on the way to `Scenario`.
    """

    category: Literal[
        ScenarioCategory.HAPPY,
        ScenarioCategory.EDGE,
        ScenarioCategory.SECURITY,
        ScenarioCategory.NEGATIVE,
        ScenarioCategory.PERSONA,
    ]

    @model_validator(mode="before")
    @classmethod
    def _execution_modes_belong_to_a_load_category(cls, data: Any) -> Any:
        """Tolerate `repeat`/`concurrency` at 1; refuse them above it.

        The fields are not in this branch's schema, so a model that sends them
        anyway is working from memory. At 1 it is saying what the code would
        have written, which is no reason to fail a whole set. Above 1 it is
        describing a race or a replay under a category that does not run one,
        and the right correction is the category, not the number — rejecting it
        as an unknown field would not say that.
        """
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for name in ("repeat", "concurrency"):
            if name not in out:
                continue
            value = out.pop(name)
            if value in (1, None):
                continue
            categories = " or ".join(c.value for c in LOAD_CATEGORIES)
            raise ValueError(
                f"{name}={value!r} on a {out.get('category', '?')} scenario: only "
                f"{categories} send the action more than once. If this probes a race "
                f"or a replay, change `category` to {categories}; otherwise drop "
                f"`{name}` — it is 1 everywhere else and the code fills it in."
            )
        return out

    @property
    def repeat(self) -> int:
        return 1

    @property
    def concurrency(self) -> int:
        return 1

    def to_scenario(self) -> Scenario:
        return Scenario.model_validate(self.model_dump())


ScenarioDraft = Annotated[
    StressScenarioDraft | AdversarialScenarioDraft | PlainScenarioDraft,
    Field(discriminator="category"),
]
"""The per-scenario wire format, split by category (ADR 0011).

Three branches rather than one model with two optional fields: the generator is
asked for the numbers where they are the probe, asked with a floor where the
category *is* the probe (`stress`), and not asked at all where they are noise.
`category` is the discriminator, so a wrong-branch payload is reported against
the branch its own category names.
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
