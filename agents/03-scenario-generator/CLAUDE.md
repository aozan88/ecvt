# 03 · Scenario Generator — Sub-Project Instructions

> DRAFT — inherits from root `CLAUDE.md`. Full version later.

## Agent Role (MVP)
Produce test scenarios across 7 categories: happy path, edge/boundary, stress, security, negative, adversarial, persona-based.

## Scope Boundary
This agent does ONE job (above). Orchestration, routing, and cross-agent
context belong to the Manager Agent — do not duplicate them here.

## Working Rules for This Directory
- `SKILL.md`: max 3 pages
- `system_prompt.md`: max 2 pages
- `eval_dataset.jsonl`: 10+ test cases before this agent is "done"
- Iterate prompts in Claude Project 03; keep changes in this dir.
