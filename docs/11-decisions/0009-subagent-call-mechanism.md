# ADR 0009 — Subagents call the Messages API; the Manager uses the Agent SDK

**Status:** Accepted
**Date:** 2026-11-02 (revised after reviewing `docs/` and Mapping v3)
**Context:** ADR 0001 chose the Claude Agent SDK as ECVT's foundation but did
not say how each agent reaches the model. Mapping v3's production table is
more specific — "Agent SDK: AgentDefinition, Manager loop, subagent spawn" —
and its 5-file standard calls `agent.py` a `ClaudeSDKClient`. This ADR departs
from that, which the Master Plan v7 source hierarchy permits: ADRs rank above
Mapping v3, and Mapping v3 is frozen reference, not a constraint.

The reason is what the M1 subagents turned out to be. Scenario Generator is
spec in, JSON out; Execution makes no model call at all; Evaluation's rule
engine is deterministic. None needs to read files, run commands or pick its
own next step. Meanwhile M0-T1 measured ~15k tokens of harness context per
`query()` call, and the harness returns prose that must be parsed instead of
schema-bound structure.
**Decision:**
- **Subagents** call the Messages API with a forced `tool_choice`; shared
  plumbing lives in `ecvt/llm.py`.
- **Manager** (M2-T3) uses the Agent SDK — intent classification, pipeline
  sequencing and reacting to failure is genuine agent work, and it is where
  Mapping v3's subagent-spawn design and the CCA-F D1 evidence land.
- **The two compose rather than compete.** Each subagent's entry point is a
  plain function (`generate_scenario_set`, `run_scenarios`, `score_*`), so the
  Manager's `AgentDefinition`s expose those functions as tools. Nothing has to
  be rewritten to put an agent behind the SDK later; the mechanism changes, the
  logic does not.
- n8n stays the trigger layer per ADR 0002: webhooks and scheduling, not
  intent routing. It does not overlap the Manager.
**Consequences:**
- Cost scales per component, not uniformly: Generator is flat in scenario
  count, Execution is zero model calls, and Evaluation is the only 1:1 cost —
  so deterministic rules run first and the judge only where rules are
  inconclusive. That ordering is the lever at 1000+ scenarios.
- Rate limits are per organization per minute, so the chain needs bounded
  concurrency and backoff (M1-T8); bulk runs belong on the Batch API.
- Deployment stays light, which matters because ECVT ships self-hosted
  (vision §7, ADR 0002): the Messages API needs only an API key, while the
  Agent SDK also expects the Claude Code CLI at runtime. Confirm before M2.
- Keeping scoring a pure function also serves M3-T3, where `ecvt-eval` moves
  it behind an MCP server.
- Risk: two call mechanisms. Contained by routing all model access through
  `ecvt/llm.py` and the Manager's own module.
**Alternatives Considered:**
- Agent SDK everywhere (Mapping v3 as written) — rejected: ~15k context per
  subagent call, no schema guarantee, untestable without the harness
- Messages API everywhere — rejected: re-implements what the SDK gives the
  Manager, and weakens CCA-F D1 evidence
- Prose output parsed as JSON — rejected: forced tools are stricter
