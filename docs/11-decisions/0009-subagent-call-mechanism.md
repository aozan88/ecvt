# ADR 0009 — Subagents call the Messages API; the Manager uses the Agent SDK

**Status:** Accepted
**Date:** 2026-11-02
**Context:** ADR 0001 chose the Claude Agent SDK as ECVT's foundation but did
not say how each agent reaches the model. The SDK's `query()` starts the Claude
Code harness: the model reads files, runs commands and decides its own next
step. M1's subagents need none of that — Scenario Generator is spec in, JSON
out; Evaluation is response in, score out. M0-T1 measured ~15k tokens of harness
context on every `query()` call, and the harness returns prose that must be
parsed rather than schema-bound structure.
**Decision:**
- **Subagents** (Scenario Generator, Execution, Evaluation) call the Messages
  API with a forced `tool_choice`, so output is schema-bound and testable with a
  stub client. Shared plumbing lives in `ecvt/llm.py`.
- **Manager** (M2-T3) uses the Agent SDK: choosing a subagent, sequencing a
  pipeline and reacting to failure is genuine agent work.
- **Migration trigger:** move a subagent to the Agent SDK when it needs real
  autonomy — reading the target's own source, or choosing its next tool call.
  Interface boundary: callers use `generate_*`/`run_*` functions, so swapping
  the mechanism does not touch the chain or the CLI.
**Consequences:**
- Cost scales by component, not uniformly: Generator cost is flat in scenario
  count (one call per spec, chunked per endpoint group when specs grow);
  Execution makes **zero** model calls; Evaluation is the only 1:1 cost, so
  deterministic rules run first and the LLM judge only where rules are
  inconclusive. That ordering is the lever at 1000+ scenarios, not the SDK choice.
- Rate limits apply per organization per minute. The chain therefore needs
  bounded concurrency and backoff (M1-T8); for large non-interactive runs the
  Batch API is the route, not more parallelism.
- Deployment stays light: the Messages API needs only an API key, while the
  Agent SDK also expects the Claude Code CLI present at runtime — a real
  constraint once ECVT is installed by someone else. To be confirmed before M2.
- Risk: two call mechanisms to maintain. Contained by keeping all model access
  behind `ecvt/llm.py` and the Manager's own module.
**Alternatives Considered:**
- Agent SDK everywhere — rejected for now (~15k context per subagent call, no
  schema guarantee, untestable without the harness); revisit per trigger above
- Messages API everywhere, hand-rolled orchestration — rejected (re-implements
  what the SDK gives the Manager, and weakens CCA-F D1 evidence)
- Structured output via prose + JSON parsing — rejected (forced tools are stricter)
