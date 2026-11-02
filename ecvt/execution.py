"""Execution runner — runs scenarios against a live target over HTTP (M1-T3).

Makes no model calls: this is plain httpx. For each scenario it runs `setup` in
order, captures ids into bindings, fires the `action` (`repeat` times in series,
`concurrency` copies in parallel each time), then runs the read-only `probes`.

Two rules shape the error handling (ADR 0006):

1. Nothing raises. Transport failures and unbound placeholders become
   `StepResult`s with `outcome=error`, so one broken scenario never stops the
   chain and Evaluation can still score what happened.
2. **An HTTP response is never retried** — not even a 500. A received status is
   the observation ECVT exists to make, and retrying a non-idempotent write
   would quietly mask the very idempotency bugs the adversarial scenarios probe.
   Only failures that produced no response at all are retried.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ecvt.results import (
    ErrorType,
    ExecutionRecord,
    RunOutcome,
    StepOutcome,
    StepResult,
)
from ecvt.schema import HttpMethod, HttpStep, Scenario

PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
DEFAULT_BASE_URL = "http://localhost:8000"


@dataclass
class RunnerConfig:
    base_url: str = DEFAULT_BASE_URL
    timeout_s: float = 10.0
    max_transport_retries: int = 2
    """Retries for failures that produced no HTTP response. Never for a status."""

    backoff_s: float = 0.25
    max_parallel: int = 20
    """Ceiling on in-flight requests, so a stress scenario cannot exhaust sockets."""


class _PlaceholderError(ValueError):
    """An unbound {name} — surfaced as a StepResult, never raised to callers."""


def _resolve_text(text: str, bindings: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in bindings:
            raise _PlaceholderError(
                f"{{{key}}} is not bound; no earlier step saved it "
                f"(bound: {sorted(bindings) or 'nothing'})"
            )
        return str(bindings[key])

    return PLACEHOLDER.sub(replace, text)


def _resolve_value(value: Any, bindings: dict[str, str]) -> Any:
    if isinstance(value, str):
        resolved = _resolve_text(value, bindings)
        # A placeholder standing alone keeps the bound value's own type.
        match = PLACEHOLDER.fullmatch(value)
        if match:
            return bindings[match.group(1)]
        return resolved
    if isinstance(value, dict):
        return {k: _resolve_value(v, bindings) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, bindings) for v in value]
    return value


def _extract(payload: Any, dotted: str) -> Any:
    """Read a dotted path out of a JSON response; raises KeyError when absent."""
    current = payload
    for part in dotted.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
                continue
            except (ValueError, IndexError) as exc:
                raise KeyError(dotted) from exc
        if not isinstance(current, dict) or part not in current:
            raise KeyError(dotted)
        current = current[part]
    return current


async def _send_once(
    client: httpx.AsyncClient, step: HttpStep, path: str, body: Any, query: Any
) -> httpx.Response:
    kwargs: dict[str, Any] = {}
    if query:
        kwargs["params"] = query
    if step.headers:
        kwargs["headers"] = step.headers
    if body is not None and step.method is not HttpMethod.GET:
        kwargs["json"] = body
    return await client.request(step.method.value, path, **kwargs)


async def run_step(
    client: httpx.AsyncClient,
    step: HttpStep,
    bindings: dict[str, str],
    config: RunnerConfig,
) -> StepResult:
    """Send one step. Always returns a StepResult; never raises."""
    base = StepResult(name=step.name, endpoint=step.endpoint, outcome=StepOutcome.OK)

    try:
        path = _resolve_text(step.path, bindings)
        body = _resolve_value(step.body, bindings) if step.body is not None else None
        query = _resolve_value(step.query, bindings) if step.query else None
    except _PlaceholderError as exc:
        return base.model_copy(
            update={
                "outcome": StepOutcome.ERROR,
                "error": str(exc),
                "error_type": ErrorType.PLACEHOLDER,
            }
        )

    started = time.perf_counter()
    last_error: Exception | None = None
    used_attempts = 0

    for attempt in range(1, config.max_transport_retries + 2):
        used_attempts = attempt
        try:
            response = await _send_once(client, step, path, body, query)
        except httpx.TimeoutException as exc:
            last_error, error_type = exc, ErrorType.TIMEOUT
        except httpx.ConnectError as exc:
            last_error, error_type = exc, ErrorType.CONNECT
        except httpx.TransportError as exc:
            last_error, error_type = exc, ErrorType.TRANSPORT
        except Exception as exc:  # noqa: BLE001 — nothing may escape (ADR 0006)
            # Anything else the client stack throws (a broken transport, an
            # app exception surfacing in-process) is still only this step's
            # problem, so it is recorded and the chain continues.
            last_error, error_type = exc, ErrorType.TRANSPORT
            break
        else:
            # A response arrived. Whatever its status, this is the observation.
            elapsed = (time.perf_counter() - started) * 1000
            try:
                parsed: Any = response.json()
                text = None
            except ValueError:
                parsed, text = None, response.text[:4000]
            return base.model_copy(
                update={
                    "url": str(response.request.url),
                    "request_body": body if isinstance(body, dict) else None,
                    "status_code": response.status_code,
                    "response_json": parsed,
                    "response_text": text,
                    "elapsed_ms": elapsed,
                    "attempts": attempt,
                }
            )

        if attempt <= config.max_transport_retries:
            await asyncio.sleep(config.backoff_s * attempt)

    return base.model_copy(
        update={
            "request_body": body if isinstance(body, dict) else None,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "attempts": used_attempts,
            "outcome": StepOutcome.ERROR,
            "error": f"{type(last_error).__name__}: {last_error}",
            "error_type": error_type,
        }
    )


def _apply_saves(step: HttpStep, result: StepResult, bindings: dict[str, str]) -> str | None:
    """Bind this step's `save` captures. Returns an error message on failure."""
    if not step.save:
        return None
    for key, path in step.save.items():
        try:
            bindings[key] = _extract(result.response_json, path)
        except KeyError:
            return f"step '{step.name}' has no '{path}' in its response to save as {{{key}}}"
    return None


async def run_scenario(
    scenario: Scenario,
    client: httpx.AsyncClient,
    config: RunnerConfig,
) -> ExecutionRecord:
    """Run one scenario end to end. Always returns a record; never raises."""
    started = time.perf_counter()
    bindings: dict[str, str] = {}
    record = ExecutionRecord(
        scenario_id=scenario.id,
        category=scenario.category.value,
        base_url=config.base_url,
        outcome=RunOutcome.COMPLETED,
    )

    # --- setup: must succeed, or there is nothing meaningful to test
    for step in scenario.setup:
        result = await run_step(client, step, bindings, config)
        record.setup.append(result)

        if result.is_error:
            record.outcome = RunOutcome.SETUP_FAILED
            record.note = f"setup step '{step.name}' never reached the target: {result.error}"
            break
        if result.http_failed:
            record.outcome = RunOutcome.SETUP_FAILED
            record.note = (
                f"setup step '{step.name}' returned {result.status_code}; "
                "preconditions were not established"
            )
            break
        capture_error = _apply_saves(step, result, bindings)
        if capture_error:
            record.outcome = RunOutcome.SETUP_FAILED
            record.note = capture_error
            result.outcome = StepOutcome.ERROR
            result.error = capture_error
            result.error_type = ErrorType.CAPTURE
            break

    record.bindings = dict(bindings)

    if record.outcome is RunOutcome.SETUP_FAILED:
        record.elapsed_ms = (time.perf_counter() - started) * 1000
        return record

    # --- action: `repeat` rounds, `concurrency` copies in parallel per round
    semaphore = asyncio.Semaphore(max(1, config.max_parallel))

    async def fire() -> StepResult:
        async with semaphore:
            return await run_step(client, scenario.action, bindings, config)

    for _ in range(scenario.repeat):
        if scenario.concurrency > 1:
            record.actions.extend(await asyncio.gather(*(fire() for _ in range(scenario.concurrency))))
        else:
            record.actions.append(await fire())

    if record.actions and all(a.is_error for a in record.actions):
        record.outcome = RunOutcome.ACTION_ERROR
        record.note = record.actions[0].error

    # Captures from the action: only the first attempt, since parallel copies
    # would otherwise race to bind the same name.
    if record.actions and not record.actions[0].is_error:
        _apply_saves(scenario.action, record.actions[0], bindings)
        record.bindings = dict(bindings)

    # --- probes: run even after an action error; post-failure state is evidence
    for step in scenario.expectation.probes:
        record.probes.append(await run_step(client, step, bindings, config))

    record.elapsed_ms = (time.perf_counter() - started) * 1000
    return record


async def run_scenarios(
    scenarios: list[Scenario],
    config: RunnerConfig,
    client: httpx.AsyncClient | None = None,
) -> list[ExecutionRecord]:
    """Run scenarios one after another against `config.base_url`.

    Scenarios run sequentially on purpose: they share target state, and two
    races overlapping would make either one unreadable. Parallelism inside a
    scenario comes from its own `concurrency`.
    """
    if client is not None:
        return [await run_scenario(s, client, config) for s in scenarios]

    async with httpx.AsyncClient(
        base_url=config.base_url, timeout=config.timeout_s
    ) as owned:
        return [await run_scenario(s, owned, config) for s in scenarios]
