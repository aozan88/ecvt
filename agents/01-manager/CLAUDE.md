# 01 · Manager Agent — Sub-Project Instructions

> DRAFT — inherits from root `CLAUDE.md`. Full version later.

## Agent Role
You are co-developing the ECVT Manager Agent. Its ONLY responsibilities:
- Orchestration
- Routing (intent classification)
- Context (across subagent calls)
- Memory (long-running pipeline state)

It does NOT perform domain analysis, scenario generation, execution, or
evaluation itself. Those belong to subagents.

## Available Subagents (routing targets)
- 02-domain-intelligence: requirements, glossary, entities, workflows, rules
- 03-scenario-generator: test scenarios (7 categories)
- 04-acceptance (v1.x): parse business AC, gap analysis
- 05-execution: run scenarios via plugin
- 06-evaluation: score responses
- 07-regression (v1.1): compare against historical
- 08-reporting (v1.2): audience-specific reports
- 09-knowledge (v2): lessons, patterns, prompt library

## Intent Taxonomy
UNDERSTAND · GENERATE · VALIDATE_AC · RUN · SCORE · COMPARE · REPORT · FULL_PIPELINE

## Working Rules for This Directory
- `SKILL.md`: max 3 pages
- `system_prompt.md`: max 2 pages
- Every prompt iteration goes in this dir; do not touch other agents' dirs.
- `eval_dataset.jsonl`: 10+ test cases minimum
- Test each change with a real Claude call before commit

## References (AIPM)
- Cheatsheet §1.1 (4 Pillars) — strategic dispatcher mindset
- Cheatsheet §3.7 (Stakeholder communication) — audience-aware routing
