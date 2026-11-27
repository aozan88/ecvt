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
- a02_domain_intelligence: requirements, glossary, entities, workflows, rules
- a03_scenario_generator: test scenarios (7 categories)
- a04_acceptance (v1.2, M5): parse business AC, gap analysis
- a05_execution: run scenarios via plugin
- a06_evaluation: score responses
- a07_regression (v1.2, M5): compare against historical
- a08_reporting (v1.3, M6): audience-specific reports
- a09_knowledge (v2.0, M7): lessons, patterns, prompt library

## Intent Taxonomy
full_validation · generate_scenarios · domain_analysis · execute_scenarios ·
evaluate_results · run_status · out_of_scope — see `SKILL.md` and `DESIGN.md` §3.
(Replaces the draft UNDERSTAND…FULL_PIPELINE list; mapping in `DESIGN.md` §7.)

## Working Rules for This Directory
- `SKILL.md`: max 3 pages
- `system_prompt.md`: max 2 pages
- Every prompt iteration goes in this dir; do not touch other agents' dirs.
- `eval_dataset.jsonl`: 10+ test cases minimum
- Test each change with a real Claude call before commit

## References (AIPM)
- Cheatsheet §1.1 (4 Pillars) — strategic dispatcher mindset
- Cheatsheet §3.7 (Stakeholder communication) — audience-aware routing
