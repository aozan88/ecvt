# ADR 0005 — Model routing: Sonnet for Manager, Haiku for subagents

**Status:** Accepted
**Date:** 2026-10-23
**Context:** ECVT runs one Manager plus up to 8 subagents per pipeline run.
Using the strongest model everywhere multiplies cost and latency; using the
cheapest everywhere hurts intent classification and multi-step routing.
Subagent tasks (scenario generation, execution routing, evaluation) are
narrow, schema-bound, and validated downstream. The Agent SDK harness also
adds its own system context and tool definitions to every call, which inflates
input tokens for single-purpose subagents.
**Decision:**
- Manager → `claude-sonnet-5-5` (routing, multi-step planning, synthesis).
- Subagents → `claude-haiku-5-5` by default; per-agent override via env
  (`ECVT_<AGENT>_MODEL`) when evals show Haiku is below the ≥75% bar.
- Trim harness context for subagents: explicit minimal `tools=[...]`
  (often `[]`), no `setting_sources`, `max_turns` capped.
- Enable prompt caching on stable prefixes (system prompt + SKILL + schema).
**Consequences:** Lower cost per run and faster subagents; schema validation
and retry (ADR 0006 spirit) absorb Haiku's occasional format slips. Model IDs
live in config, so upgrades are a one-line change. Risk: a subagent may need
Sonnet for quality — decided per agent by eval score, not by default.
**Alternatives Considered:**
- Sonnet everywhere — rejected (≈3–5× cost, no measured quality need yet)
- Haiku everywhere — rejected (Manager routing is the hardest reasoning step)
- Opus for Manager — rejected for MVP (cost; revisit if routing evals fail)
