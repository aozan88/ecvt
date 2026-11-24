# ADR 0010 — The Manager's `plugin` slot is a string checked at run time

**Status:** Accepted
**Date:** 2026-11-24
**Context:** The agent standard prefers `enum` over free text in tool schemas, because
the model drifts less. The Manager's `record_intent` tool takes a `plugin` slot. An enum
there would list today's plugins (`ikys`, `manual`, `rest_api`, `solar`) inside
`agents/a01_manager/tools.json`, so adding a target system would mean editing the
Manager — against root `CLAUDE.md` rule 5 (domain-agnostic; system-specific logic lives
in `plugins/`).

**Decision:**
- `plugin` stays a plain string in `tools.json`.
- The registry is the directory listing of `plugins/` (`list_plugins()`); the Manager
  shows it to the model as `<available_plugins>` on every request.
- `record_intent` validates the value in code. An unknown plugin is dropped and reported
  as `invalid_slots`, so the run goes to `ask_user` instead of dispatching.

**Consequences:**
- Adding a plugin is a new folder; no prompt, schema or eval change in the Manager.
- The model can still send a wrong name; the cost is one clarifying turn, not a wrong
  run. Eval case `mgr-009` (unknown plugin) covers it.

**Alternatives:** an enum generated from `plugins/` at start-up — rejected: `tools.json`
would differ per checkout, and the committed copy could no longer be checked in CI.
