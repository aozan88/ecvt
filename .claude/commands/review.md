# /review — Review the current branch against ECVT rules

Diff the branch against `main` and check, in this order:

1. **Root rules** (`CLAUDE.md`): commit format, domain-agnostic (no system-specific logic
   outside `plugins/`), no new frameworks, no edits to Accepted ADRs.
2. **Agent standard** (`docs/ecvt-agent-standards-v1.md`): five files per agent, page
   limits, tools.json generated from the schema, eval dataset ≥ 10 cases.
3. **Contracts**: stage runner output `{artifact, summary, metrics, usage}`; errors as
   `StageError` with the right `retryable`; no HTTP status ever retried (ADR 0006).
4. **Tests**: every behaviour change has an offline test; no test needs an API key.
5. **Docs**: limits in `docs/conventions.md`; agent specs in `docs/03-agents/` match the code;
   `docs/02-architecture/architecture.md` and `pipeline.md` match the code (stages,
   inputs/outputs, entry points); a plugin change updates `docs/07-plugins/`. A PR that
   changes the architecture, the pipeline, an agent's input/output, the stage order or an
   entry point updates these in the same PR, and its description carries a **Docs** line
   (which docs changed, or "no architecture change").

Output: a table — finding · file:line · severity (blocker / fix / nit). No rewrites.
