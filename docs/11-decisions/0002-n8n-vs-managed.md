# ADR 0002 — Self-hosted n8n for orchestration (not a managed service)

**Status:** Accepted
**Date:** 2026-06-26
**Context:** ECVT's production architecture needs a workflow trigger layer
(webhooks, scheduling, glue between Claude API and tools). Options ranged
from managed orchestration SaaS to self-hosted.
**Decision:** Use self-hosted n8n via Docker Compose. Claude Projects remain
a development-only workspace, not part of production.
**Consequences:** Zero recurring cost, full data control, portable across
machines. Requires Docker upkeep and local port management.
**Alternatives Considered:**
- Managed workflow SaaS — rejected (cost, less control)
- Pure-code scheduler (cron + FastAPI) — deferred (n8n gives UI + faster iteration)
- Temporal / Airflow — rejected (over-engineered for MVP)
