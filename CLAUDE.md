# ECVT — Project Root Instructions

> DRAFT — bu taslaktır. Tam versiyona sonra çekilecek (bkz. ADR 0003).

You are working in the ECVT (Edge Case Validation Toolkit) repository —
an AI Quality Engineering Platform built with Claude Agent SDK + n8n.

## Project Status
- Plan: Project 00 (Claude Docs, repodan okunmaz). Durum: `ROADMAP.md`.
- Architecture reference: Mapping v3. Etap 1 export: `docs/10-roadmap/README.md`.
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
- Match `ROADMAP.md` for milestone targets and their gates.
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
- Plan: Project 00 (Claude Docs, repodan okunmaz). Mapping v3 is the
  architecture reference; Master Plan v6.2 is archive only.
- `.docx` files are gitignored; local copies in `docs/12-research/` are optional.
- Roadmap / milestone status: `ROADMAP.md` — each PR ticks its box and adds its link
- Conventions: `docs/conventions.md`

## What NOT to do
- Do not create new files without asking (except when explicitly working
  in an agent's directory).
- Do not modify `docs/11-decisions/` ADRs after they're marked Accepted.
- Do not push to main directly — always PR from `feature/`.
