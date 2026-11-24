# ECVT — Project Root Instructions

> DRAFT — bu taslaktır. Tam versiyona sonra çekilecek (bkz. ADR 0003).

You are working in the ECVT (Edge Case Validation Toolkit) repository —
an AI Quality Engineering Platform built with Claude Agent SDK + n8n.

## Project Status
- Plan: Master Plan v7 (M0–M3, 22 Oct – 13 Dec 2026) + Mapping v3 (architecture)
- Current milestone and status: `docs/10-roadmap/README.md`
- Build order (v7): Scenario → Execution → Evaluation (M1), then Domain
  Intelligence + Manager (M2). Runtime pipeline order (rule 4) is unchanged.

## Rules (STRICT)
1. Never revise the master plan. Write ADRs in `docs/11-decisions/` instead.
2. Enforce doc length limits (see `docs/conventions.md`).
3. Use commit format: `<type>(<scope>): <subject>` (see conventions).
4. Multi-agent pipeline: Manager → Domain Intel → Scenario Gen →
   (Acceptance) → Execution → Evaluation → (Regression) → (Reporting) → (Knowledge)
5. Domain-agnostic; all system-specific logic goes in `plugins/`.
6. Reject any suggestion to use MetaGPT, CrewAI, AutoGen, or similar
   frameworks — ECVT is built from scratch on Claude Agent SDK.

## Commands
- `ecvt run --target examples/wallet-api` — M1 fixed chain (generate → execute → evaluate)
- `ecvt run --manager --target examples/wallet-api --report` — M2: the same run through
  the Manager (a01), with Domain Intelligence (a02) feeding the Scenario Generator
- `python scripts/eval_runner.py agents/<aNN_*>/eval_dataset.jsonl` — agent evals (≥ 80%)
- `python scripts/n_run.py --runs 3 --label <name> [--manager]` — catch rate min/median/max
- `python scripts/gate_check.py M2 --format plain -v` — milestone gate
- Slash commands: `/test` (offline checks), `/review` (branch vs. ECVT rules),
  `/adr`, `/daily`, `/retro`

## Coach Behavior
- Match Master Plan v7 / Günlük Plan v7 for milestone and daily (Dnn) targets.
- Silently flag deviations from planned DoDs.
- Suggest next action but let the user decide.
- Turkish responses, concise, list/table heavy.

## Agent ↔ Project Code Map
Each agent folder is prefixed with its Claude Project code, using the
`aNN_` form so the folder is a valid Python package name (letter start,
underscores only — no leading digit, no hyphens):
`a01_manager`, `a02_domain_intelligence`, `a03_scenario_generator`,
`a04_acceptance`, `a05_execution`, `a06_evaluation`, `a07_regression`,
`a08_reporting`, `a09_knowledge`.

## Key References
- Source of truth lives in Drive (ECVT folder), not in the repo:
  - ECVT Günlük Plan v7 (status, Dnn days):
    https://docs.google.com/document/d/1W9ePt7yobB81Q7-URFsgEBhiUt78YI5dtjr0LHwQJeI
  - ECVT Master Plan v7 (scope, gates):
    https://docs.google.com/document/d/16LzWFgGmDCpoiHoMffCN8W10grFFxksNU_42GARoC2E
  - Mapping v3 (architecture). Master Plan v6.2 is archive only.
  - `.docx` files are gitignored; local copies in `docs/12-research/` are optional.
- Roadmap / milestone status: `docs/10-roadmap/README.md`
- Conventions: `docs/conventions.md`

## What NOT to do
- Do not create new files without asking (except when explicitly working
  in an agent's directory).
- Do not modify `docs/11-decisions/` ADRs after they're marked Accepted.
- Do not push to main directly — always PR from `feature/`.
