# ADR 0003 — Domain-agnostic plugin architecture

**Status:** Accepted
**Date:** 2026-06-26
**Context:** ECVT's market position depends on not being tied to a single
domain. Every target system (Solar, IKYS, arbitrary REST) has different
entities, execution backends, and validation rules.
**Decision:** ECVT Core (agents + pipeline + eval engine) talks to every
target system through a standard `Plugin` interface. Each backend
(REST, OpenAPI, Solar, IKYS, Manual) is a separate plugin implementing
`discover_domain()`, `execute_scenario()`, and `validate_response()`.
**Consequences:** New domains onboard by writing a plugin, not by touching
core. Slightly more upfront abstraction. First plugin: domain-agnostic REST
(H4); first concrete domain plugin (H5): Solar or IKYS.
**Alternatives Considered:**
- Hard-code Solar domain first — rejected (kills reusable positioning)
- Config-file-only adapters — rejected (insufficient for execution logic)
- Per-domain forks — rejected (unmaintainable)
