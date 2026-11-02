"""Execution result models — what the runner records for Evaluation (M1-T3).

Per ADR 0006 a failure is an observation, not a crash: every step produces a
`StepResult`, whether it returned 200, returned 500, or never reached the
target at all. Evaluation reads these and scores them against the scenario's
expectation; nothing here interprets correctness.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class StepOutcome(str, Enum):
    OK = "ok"
    """The target returned an HTTP response — any status, including 5xx."""

    ERROR = "error"
    """No response: transport failure, or the request could not be built."""


class ErrorType(str, Enum):
    TIMEOUT = "timeout"
    CONNECT = "connect"
    TRANSPORT = "transport"
    PLACEHOLDER = "placeholder"
    """A {name} in the request was never bound by an earlier `save`."""

    CAPTURE = "capture"
    """A `save` path was absent from the response."""


class StepResult(BaseModel):
    """One HTTP call's outcome."""

    name: str
    endpoint: str
    url: str | None = None
    request_body: dict[str, Any] | None = None
    status_code: int | None = None
    response_json: Any | None = None
    response_text: str | None = Field(
        default=None, description="Body as text when it was not valid JSON."
    )
    elapsed_ms: float = 0.0
    attempts: int = 1
    outcome: StepOutcome = StepOutcome.OK
    error: str | None = None
    error_type: ErrorType | None = None

    @property
    def is_error(self) -> bool:
        return self.outcome is StepOutcome.ERROR

    @property
    def http_failed(self) -> bool:
        """True for a 4xx/5xx response. Not an error — a finding."""
        return self.status_code is not None and self.status_code >= 400

    def as_tool_result(self, tool_use_id: str = "") -> dict[str, Any]:
        """Render as a `tool_result` block for an LLM loop (ADR 0006).

        Errors come back as content with `is_error`, never as a raised
        exception, so the model can decide what to do about them.
        """
        payload: dict[str, Any] = {"step": self.name, "endpoint": self.endpoint}
        if self.is_error:
            payload["error"] = self.error
            payload["error_type"] = self.error_type.value if self.error_type else None
        else:
            payload["status_code"] = self.status_code
            payload["body"] = (
                self.response_json if self.response_json is not None else self.response_text
            )
        payload["elapsed_ms"] = round(self.elapsed_ms, 1)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "is_error": self.is_error,
            "content": json.dumps(payload, ensure_ascii=False),
        }


class RunOutcome(str, Enum):
    COMPLETED = "completed"
    """Setup established state and the action reached the target."""

    SETUP_FAILED = "setup_failed"
    """Preconditions could not be built, so the action was not attempted."""

    ACTION_ERROR = "action_error"
    """Setup succeeded but no action attempt reached the target."""


class ExecutionRecord(BaseModel):
    """Everything observed while running one scenario."""

    scenario_id: str
    category: str
    base_url: str
    outcome: RunOutcome
    setup: list[StepResult] = Field(default_factory=list)
    actions: list[StepResult] = Field(default_factory=list)
    probes: list[StepResult] = Field(default_factory=list)
    bindings: dict[str, str] = Field(default_factory=dict)
    note: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    elapsed_ms: float = 0.0

    @property
    def action_statuses(self) -> list[int | None]:
        return [a.status_code for a in self.actions]

    @property
    def all_steps(self) -> list[StepResult]:
        return [*self.setup, *self.actions, *self.probes]


def write_executions_jsonl(path: str | Path, records: list[ExecutionRecord]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(record.model_dump_json(exclude_none=True) + "\n")
    return path


def read_executions_jsonl(path: str | Path) -> list[ExecutionRecord]:
    path = Path(path)
    records: list[ExecutionRecord] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(ExecutionRecord.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"{path}:{lineno} invalid execution record: {exc}") from exc
    return records
