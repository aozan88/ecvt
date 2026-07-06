# ADR 0001 — Build on Claude Agent SDK (not a Dev-Team framework)

**Status:** Accepted
**Date:** 2026-06-26
**Context:** ECVT needs a multi-agent orchestration core. Off-the-shelf
AI Dev-Team frameworks (MetaGPT, CrewAI, AutoGen, OpenDevin, OpenHands)
generate code and position ECVT in a crowded, wrong market category. They
are also weak evidence for the CCA-F (Claude) certification track.
**Decision:** Build the Manager + subagents from scratch on the Claude Agent
SDK (Python). Manager loop, subagent spawn, and context passing are
hand-written.
**Consequences:** More initial effort than adopting a framework, but full
control, correct market positioning (Quality Engineering, not Dev Team), and
direct CCA-F practice. Vendor-aligned with Anthropic ecosystem.
**Alternatives Considered:**
- CrewAI / AutoGen — rejected (wrong category, weak CCA-F signal)
- LangGraph / LangChain — rejected (outside Anthropic ecosystem)
- MetaGPT / OpenDevin — rejected (code generators, not validators)
