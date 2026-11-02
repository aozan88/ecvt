# ADR 0008 — Plan v7 build order: chain first, Manager last

**Status:** Accepted
**Date:** 2026-10-29
**Context:** Master Plan v6.2 built agents in runtime order (Manager → Domain
Intelligence → Scenario → Execution → Evaluation) on a week-based calendar
(H1–H6) that has since slipped. Building the Manager first meant writing an
orchestrator before any agent existed to orchestrate, and produced no
measurable output until late. v6.2 also had no concrete target system with
known defects, so there was no number to show progress.
**Decision:**
- Calendar moves to Master Plan v7: milestones M0–M3 (22 Oct – 13 Dec 2026),
  days `Dnn`, tasks `Mx-Tn`, gates `Gx` checked by `scripts/gate_check.py`.
- **Build order:** M1 = Scenario Generator → Execution → Evaluation chain +
  CLI against `examples/wallet-api` (ADR 0007). M2 = Domain Intelligence +
  Manager (5-agent MVP). M3 = plugins, MCP eval server, report, n8n, landing.
- **Runtime pipeline order is unchanged** (root `CLAUDE.md` rule 4). Only the
  order in which agents are built changes.
- A milestone's code merges to `main` only after its gate passes; the next
  milestone may start on a `feature/` branch.
**Consequences:** M1 already yields a measured catch rate (X/10), so every
later agent is judged by its effect on that number. The Manager is designed
around real subagent interfaces, not guesses. Trade-off: until M2, the CLI
calls the agents directly with a fixed chain (no intent routing), and M2-T2
must retrofit Domain Intelligence output into Scenario Generator input.
**Alternatives Considered:**
- Keep v6.2 order (Manager first) — rejected (no measurable output for weeks)
- Build all agents in parallel — rejected (single developer, interfaces unknown)
- Skip Manager entirely (scripted chain only) — rejected (Manager is core to
  the product and to CCA-F D1 evidence)
