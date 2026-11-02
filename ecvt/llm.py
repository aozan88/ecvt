"""Structured Claude calls with validation retry and token accounting (M1-T1).

ECVT's subagents are narrow transforms: spec in, schema-valid JSON out. They use
the Messages API with a forced tool call, which keeps the request to just the
system prompt plus the task — no agent harness context (ADR 0005). The Manager
(M2) is the component that uses the Agent SDK's subagent orchestration.

Validation failures are fed back to the model as a correction turn rather than
raised, so a near-miss costs one cheap retry instead of the whole run (ADR 0006
in spirit: failures are data).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

DEFAULT_SUBAGENT_MODEL = os.environ.get("ECVT_SUBAGENT_MODEL", "claude-haiku-5-5")
DEFAULT_MANAGER_MODEL = os.environ.get("ECVT_MANAGER_MODEL", "claude-sonnet-5-5")
PRICING_PATH = Path(__file__).resolve().parent.parent / "config" / "pricing.json"


class _MessagesClient(Protocol):
    """The slice of anthropic.Anthropic this module uses (so tests can fake it)."""

    @property
    def messages(self) -> Any: ...


@dataclass
class Usage:
    """Token counts for one or more calls, plus cost when pricing is known."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0

    def add_response(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        self.calls += 1
        if usage is None:
            return
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def cost_usd(self) -> float | None:
        """Cost in USD, or None when this model has no price configured.

        Prices live in `config/pricing.json` (USD per million tokens) and ship
        empty on purpose: a guessed price in a cost log is worse than a blank.
        """
        prices = _load_pricing().get(self.model)
        if not prices:
            return None
        per_million = (
            self.input_tokens * prices.get("input", 0.0)
            + self.output_tokens * prices.get("output", 0.0)
            + self.cache_read_tokens * prices.get("cache_read", 0.0)
            + self.cache_write_tokens * prices.get("cache_write", 0.0)
        )
        return round(per_million / 1_000_000, 6)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd(),
        }


def _load_pricing() -> dict[str, dict[str, float]]:
    if not PRICING_PATH.exists():
        return {}
    try:
        data = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        return {}
    return {k: v for k, v in models.items() if isinstance(v, dict) and v}


class StructuredCallError(RuntimeError):
    """The model never produced output that satisfied the validator."""

    def __init__(self, message: str, attempts: list[str], usage: Usage) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.usage = usage


@dataclass
class StructuredResult:
    data: dict[str, Any]
    usage: Usage
    attempts: int
    corrections: list[str] = field(default_factory=list)


def coerce_json_strings(data: dict[str, Any]) -> dict[str, Any]:
    """Parse top-level values the model sent as JSON text instead of structure.

    With a large tool input, Claude sometimes serializes an array or object into
    a string: `{"scenarios": "[{...}, {...}]"}` rather than `{"scenarios": [...]}`.
    The content is correct, only the encoding is off, so rejecting it costs a
    full retry — in the first wallet-api run that was ~10k wasted output tokens.

    Only top-level string values that parse as a JSON array or object are
    touched, and only when parsing succeeds; anything else is passed through
    untouched so genuine type errors still reach the validator.
    """
    out = dict(data)
    for key, value in data.items():
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text.startswith(("[", "{")):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, (list, dict)):
            out[key] = parsed
    return out


def _tool_input(response: Any, tool_name: str) -> dict[str, Any] | None:
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
            data = getattr(block, "input", None)
            if isinstance(data, dict):
                return data
    return None


def structured_call(
    *,
    system: str,
    user: str,
    tool: dict[str, Any],
    validate: Any,
    client: _MessagesClient | None = None,
    model: str = DEFAULT_SUBAGENT_MODEL,
    max_tokens: int = 8192,
    max_attempts: int = 3,
    cache_system: bool = True,
) -> StructuredResult:
    """Call Claude, force `tool`, and return input that `validate` accepted.

    `validate(data)` should raise on invalid input; its error text is sent back
    to the model as a correction. Raises StructuredCallError if every attempt
    fails — the partial Usage is attached so the caller can still log spend.
    """
    if client is None:
        import anthropic  # imported here so tests need no API key

        client = anthropic.Anthropic()

    system_blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
    if cache_system:
        system_blocks[0]["cache_control"] = {"type": "ephemeral"}

    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    usage = Usage(model=model)
    failures: list[str] = []

    for attempt in range(1, max_attempts + 1):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=messages,
        )
        usage.add_response(response)

        data = _tool_input(response, tool["name"])
        if data is None:
            failures.append(f"attempt {attempt}: no {tool['name']} tool call in response")
        else:
            data = coerce_json_strings(data)
            try:
                validate(data)
            except Exception as exc:  # noqa: BLE001 — any validator error becomes feedback
                failures.append(f"attempt {attempt}: {exc}")
            else:
                return StructuredResult(
                    data=data, usage=usage, attempts=attempt, corrections=failures
                )

        if attempt == max_attempts:
            break
        messages = [
            {"role": "user", "content": user},
            {
                "role": "user",
                "content": (
                    "Your previous output was rejected by schema validation:\n\n"
                    f"{failures[-1]}\n\n"
                    f"Call {tool['name']} again. Fix only what the error names and keep "
                    "everything else. Return the complete object, not a patch."
                ),
            },
        ]

    raise StructuredCallError(
        f"{tool['name']} produced no valid output in {max_attempts} attempts",
        attempts=failures,
        usage=usage,
    )
