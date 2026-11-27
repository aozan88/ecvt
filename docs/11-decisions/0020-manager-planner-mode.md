# ADR 0020 — The Manager labels the request; code runs the pipeline

**Status:** Proposed (M4-T3, 2026-11-27)
**Date:** 2026-11-27
**Context:** ADR 0009 put the Manager on the Agent SDK: Sonnet classifies the
request, delegates each stage to an adapter subagent, decides retries and writes
the summary. Every step after the first is already fixed by code — `INTENT_PLAN`
picks the stages, `precondition()` blocks any other order, `MAX_ATTEMPTS` and the
`retryable` flag decide retries — so the model was paid to repeat rules it could
only break. M3 measured the cost: $0.077 a run against the chain's $0.011, and a
catch-rate median of 4 against 6 (five runs a side). ROADMAP principle 1: an agent
only where a decision is needed.

**Decision:**
- **Planner mode is the default** (`ecvt run --manager`, the a01 eval, n8n's
  `manager: true`). One strict `plan_run` call returns intent, slots, rationale
  and, for runs that cannot proceed, the clarifying question or redirect in the
  request's language. `apply_intent` derives the plan; code runs the stages in
  order, retries a retryable failure once, writes `manager_output` (checked
  against `tools.json`) and a templated summary (en / tr).
- **The Agent SDK loop stays**, unchanged in behaviour, behind
  `--manager-mode agent` / `ECVT_MANAGER_MODE=agent`. Both modes share the state
  file, the stage bookkeeping (`begin_stage` / `finish_stage`) and the runners.
- The planner's model is `ECVT_MANAGER_MODEL`, default **Haiku**: it met a01's bar
  as Sonnet did (below). `structured_call` uses `auto` `tool_choice` on models that
  reject a forced one (Sonnet 5.5, Opus 5.5).
- a01's rubrics read the stage records both modes write, not Agent delegations.

**Consequences:** measured 2026-11-27 (wallet-api, wallet plugin, 10 runs a side):
the planner completed 10/10 with catch rate 6 / 7 / 9 against the chain's 7 / 7 / 8,
at $0.021 a run (Manager share $0.004 on Sonnet; the rest is a02 + the chain) against
the agent loop's $0.077 in M3. a01 eval, 3 runs: 100% on Sonnet and on Haiku. The
Manager's own spend falls to one short call; no adapter subagents, no turn limit.
The model can no longer skip, reorder or over-retry a stage — there is nothing left
for it to decide there. Lost: a
summary written freely in any language (templates cover en and tr), and the
Agent SDK leaves the default runtime path; ECVT still ships it as a mode.

**Alternatives Considered:**
- Keep the loop and tighten its prompt — the rules it breaks are already code.
- Remove the loop — rejected by Ozan: it stays as a mode and a comparison arm.
- Planner writes the summary too (a second call) — a decision-free text step.
