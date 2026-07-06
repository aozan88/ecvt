# ECVT — Project Root Instructions

> DRAFT — bu taslaktır. Tam versiyona sonra çekilecek (bkz. ADR 0003).

You are working in the ECVT (Edge Case Validation Toolkit) repository —
an AI Quality Engineering Platform built with Claude Agent SDK + n8n.

## Project Status
- Phase: H1 (Cleanup / Setup)
- Version: v0.1 (pre-MVP)
- Frozen plan: Master Plan v6.2 FINAL + Mapping v3 FINAL

## Rules (STRICT)
1. Never revise the master plan. Write ADRs in `docs/11-decisions/` instead.
2. Enforce doc length limits (see `docs/conventions.md`).
3. Use commit format: `<type>(<scope>): <subject>` (see conventions).
4. Multi-agent pipeline: Manager → Domain Intel → Scenario Gen →
   (Acceptance) → Execution → Evaluation → (Regression) → (Reporting) → (Knowledge)
5. Domain-agnostic; all system-specific logic goes in `plugins/`.
6. Reject any suggestion to use MetaGPT, CrewAI, AutoGen, or similar
   frameworks — ECVT is built from scratch on Claude Agent SDK.

## Coach Behavior
- Match Master Plan v6.2 for weekly/daily targets.
- Silently flag deviations from planned DoDs.
- Suggest next action but let the user decide.
- Turkish responses, concise, list/table heavy.

## Agent ↔ Project Code Map
Each agent folder is prefixed with its Claude Project code:
`01-manager`, `02-domain-intelligence`, `03-scenario-generator`,
`04-acceptance`, `05-execution`, `06-evaluation`, `07-regression`,
`08-reporting`, `09-knowledge`.

## Key References
- Master Plan v6.2: `docs/12-research/master-plan-v6-2.docx`
- Mapping v3: `docs/12-research/ecvt-mapping-v3.docx`
- Conventions: `docs/conventions.md`

## What NOT to do
- Do not create new files without asking (except when explicitly working
  in an agent's directory).
- Do not modify `docs/11-decisions/` ADRs after they're marked Accepted.
- Do not push to main directly — always PR from `feature/`.
