# ADR 0018 — a05 Execution is deterministic code; its prompt and tool files are N/A

**Status:** Proposed (v1.0.1, 2026-11-27)
**Date:** 2026-11-27
**Context:** The agent standard (`docs/ecvt-agent-standards-v1.md`) gives every
agent five files: `SKILL.md`, `system_prompt.md`, `tools.json`, `agent.py`,
`eval_dataset.jsonl`. a05's prompt and tool files were written in M1-T4 as
skeletons for the day the Manager would dispatch Execution as a model-backed
subagent. That day did not come: ADR 0009 made the subagents plain functions,
Execution makes no model call, and since M3-T1 it runs as `plugin.execute()`
(ADR 0013). Nothing reads `system_prompt.md` or `tools.json` — no code path,
no test, no gate — yet both still describe a model that would return
`ExecutionRecord`s, with TODOs and a placeholder schema. A reader has no way to
tell they are dead.

Running HTTP needs no reasoning: what a scenario sends is fixed by the
scenario, and what came back is recorded verbatim. A model in this step could
only add variance, cost and a path for target output to reach a prompt.

**Decision:**
- a05 is deterministic code: the Plugin ABC's `execute(scenarios)` plus the
  Core runner `ecvt/execution.py`. No model call, now or as a goal.
- `system_prompt.md` is reduced to one line saying it is not applicable;
  `tools.json` holds a single N/A note instead of a schema. Both stay so the
  five-file layout reads the same in every agent folder.
- `SKILL.md` describes the real contract — `execute()`, outcomes, the ADR 0006
  retry rule, what a plugin must not do — without DRAFT or TODO markers.
- `agent.py` drops its unused `load_system_prompt()`.

**Consequences:** the folder says what the agent is. An agent that later needs
a model (for example to repair a scenario) starts with a new ADR, not by
filling these files. The standard's five-file rule now has a stated exception:
an agent without a model call keeps the files as N/A notes.

**Alternatives Considered:**
- Delete the two files — rejected: breaks the uniform layout readers and
  `/review` rely on, for no gain.
- Keep them as skeletons — rejected: dead text that contradicts ADR 0009.
- Make a05 model-backed to fit the standard — rejected: cost and variance for
  no decision to make.
