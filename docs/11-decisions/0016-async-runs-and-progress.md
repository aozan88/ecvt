# ADR 0016 — Async runs on `ecvt serve`, progress from a status file

**Status:** Proposed (v1.1 UI, 2026-11-26)
**Date:** 2026-11-26
**Context:** v1.1 puts a browser UI on `ecvt serve` (ADR 0015 ties the demo
video to it). In 1.0, `POST /runs` held the request until `ecvt run` finished —
fine for n8n's webhook, but a page cannot show progress through one long
request. The UI needs a run id at once, a way to ask where the run is, and the
n8n path (G3-11) must keep answering with the finished run.

Two ways to know the step: parse the `[n/3]` lines `ecvt run` prints, or have
it write a small status file. The printed lines are for people: the Manager
path prints none, and any rewording breaks a parser silently.

**Decision:**
- `POST /runs` answers **202** `{run_id, status: queued}`; the run goes on in a
  background thread. `POST /runs?wait=true` keeps the 1.0 answer (200/502 with
  the summary, now plus `run_id`); the n8n workflow calls it. One run at a time
  still holds across both (409).
- `ecvt run --status-file PATH` rewrites `{steps, current, detail, updated_at}`
  atomically at each step (chain: generate → execute → evaluate → report;
  Manager: intent → domain_intel → scenario_gen → execution → evaluation →
  report). `current: null` means done; a failed run leaves it on the step that
  failed. The server only reads it.
- The server keeps `job.json` per run (`artifacts/triggered/<run_id>/`): request,
  status, timings, result. `GET /runs` lists these, so history outlives a
  restart; a run left `running` by a dead server reads as `failed (interrupted)`.
- What the UI reads carries no catch rate and no log (ADR 0007); only
  `report-business.html` and `report-dev.html` are served from a run directory.

**Consequences:** the file is a contract with a test, not a format scraped
from human output; both paths report steps the same way. Cost: one more CLI
flag, and n8n workflows imported before 1.1 must be re-imported (bare `/runs`
now returns 202). No queue: a second run is refused, not scheduled.

**Alternatives Considered:**
- Parse stdout `[n/3]` lines — rejected: fragile, needs a streamed pipe, and
  the Manager prints none.
- Keep `POST /runs` synchronous, add `POST /runs/async` — rejected: a run is
  minutes long through the Manager; waiting is the case to ask for, not the default.
- Server-sent events / WebSocket — rejected: polling once a second is enough
  for a 30 s run and needs nothing beyond the standard library.
