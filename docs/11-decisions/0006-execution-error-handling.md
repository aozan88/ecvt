# ADR 0006 — Execution errors are returned as data, never raised

**Status:** Accepted
**Date:** 2026-10-23
**Context:** The Execution Agent calls target systems that are expected to
misbehave (timeouts, 5xx, connection resets, malformed bodies). In an agent
loop, an uncaught exception kills the loop and Claude never sees what went
wrong — the run ends with no result and no chance to decide. For a QE
platform, a failure of the target is a *finding*, not a crash.
**Decision:**
- Every tool/plugin call is wrapped in `try/except`.
- Failures are returned as structured content — inside an LLM loop as a
  `tool_result` with `is_error: true`; in plain Python as an
  `ExecutionResult(error=..., error_type=...)` record.
- Error records keep: scenario id, error type, message, elapsed ms, attempt.
- Transient network errors get bounded retry (max 2, backoff); HTTP status
  codes (incl. 500) are never retried — they are the observation.
- Only programmer errors (bad config, invalid scenario schema) raise.
**Consequences:** Pipelines always complete and produce a result per
scenario; Evaluation can score crashes (e.g. 500 vs expected 404). Slightly
more code per plugin. Retry must not mask idempotency bugs, hence no retry on
any received HTTP response.
**Alternatives Considered:**
- Let exceptions propagate, catch at Manager — rejected (loses per-scenario context)
- Retry everything — rejected (replays non-idempotent requests, hides bugs)
- Return raw strings — rejected (Evaluation needs typed fields)
