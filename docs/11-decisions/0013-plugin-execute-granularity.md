# ADR 0013 — Plugin execution granularity for v1.0

**Status:** Accepted — option A (choice delegated by Ozan, 2026-11-25)
**Date:** 2026-11-25
**Context:** ADR 0003 names three plugin methods: `discover_domain()`,
`execute_scenario()`, `validate_response()`. M3-T1 (#37) implemented them as
`execute(scenarios)` (a set, abstract), `discover_domain()` and
`validate_response()` (optional). Project 05 had proposed a narrower seam,
accepted but not carried into the M3 brief: the plugin only `send(step)`s one
request, while retry, repeat, concurrency, `setup`/`save` and probes stay in
Core; `validate_response` does not exist.

Two facts from the code decide between them for v1.0:

- **The scenario schema is HTTP.** `HttpStep` carries method, path, body,
  query, headers. `send(step)` would be `send(HttpStep)`; a non-HTTP backend
  (ikys, manual, solar) cannot honour it without a schema change, and which
  change is unknown until that backend exists. An interface designed now from
  one implementation would be a guess.
- **The seam already exists.** `ecvt/execution.py` `_send_once` is the single
  request; the ADR 0006 rules live around it in `run_step`. Moving that
  function behind a plugin later is a small refactor.

**Decision:**
- v1.0 keeps `execute(scenarios)` as the one required method. `rest_api`
  delegates to Core's runner, so ADR 0006 holds for it unchanged (measured:
  M1 chain 6 / 7 / 8 over five runs, reference 4 / 6 / 7).
- **`send(step)` when the second real backend arrives.** That plugin's needs
  define the step type and the seam; ADR 0006's rules then move with
  `run_step` into Core's side of it.
- **`validate_response` is removed.** Scoring belongs to a06 only. A plugin
  may contribute domain *knowledge* (rules as data); a06 applies it. M3-T2
  (wallet rules) defines that contribution — `ecvt/scoring.py` already routes
  natural-language invariants there.
- `discover_domain()` stays optional (default `None`); nothing calls it yet.

**Consequences:** No behaviour change in v1.0. Until the second backend, a
plugin implementing `execute` itself could bypass ADR 0006; `rest_api` does
not, and a review of any new plugin checks it. This partially supersedes
ADR 0003's method list; ADR 0003's principle (Core talks to targets only
through a plugin) stands.

**Alternatives Considered:**
- B — `send(step)` now: cleaner layering, but its step type would be HTTP by
  construction; ~1 day plus a re-measurement, for no second caller.
- Keep `validate_response` as optional — rejected: a hook with no caller, and
  it invites scoring outside a06.
