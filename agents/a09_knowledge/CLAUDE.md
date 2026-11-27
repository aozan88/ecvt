# 09 · Knowledge Agent — Sub-Project Instructions

> DRAFT — inherits from root `CLAUDE.md`. Full version later.

## Agent Role (v2.0, M7)
After each run, capture lessons learned, recurring failure patterns, known issues, and a reusable prompt/test library.

## Scope Boundary
This agent does ONE job (above). Orchestration, routing, and cross-agent
context belong to the Manager Agent — do not duplicate them here.

## Working Rules for This Directory
- `SKILL.md`: max 3 pages
- `system_prompt.md`: max 2 pages
- `eval_dataset.jsonl`: 10+ test cases before this agent is "done"
- Iterate prompts in Claude Project 09; keep changes in this dir.
