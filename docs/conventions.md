# Conventions

Doc length limits + commit + branch standards. Enforced repo-wide.

## Document Length Limits (anti-perfectionism)

| Doc type | Limit | Reason |
|----------|-------|--------|
| README | 5 pages | Quick entry; rest → `setup/` |
| Product Vision | 6 pages | Strategic, executive-readable |
| Architecture | 10 pages | Detailed but readable in one sitting |
| Agent Spec | 3 pages/agent | Compact; prompts kept separate |
| Workflow | 2 pages | Describes a single flow |
| ADR | 1 page | Decision + context + alternatives |
| Research | 5 pages | External research summary |
| Cheatsheet | 10–15 pages | Reference, broad but shallow |
| Setup files | 100–150 lines | Modular; split if exceeded |

### When a doc exceeds its limit
1. Check for repetition (usually ~60% is redundant).
2. Move detail to a sub-doc + reference it.
3. Convert paragraphs to bullets.
4. Use diagrams/tables to save space.

## Commit Message Convention

```
<type>(<scope>): <subject>
```

- **Types:** feat | fix | docs | refactor | test | chore | perf
- **Scope:** manager | domain | scenario | acceptance | execution | eval |
  regression | report | knowledge | plugin | n8n | mcp | docs

Examples:
```
feat(scenario): add adversarial scenario category
fix(execution): timeout handling for REST plugin
docs(adr): 0003 domain-agnostic plugin decision
refactor(manager): simplify intent classification
```

## Branch Naming

```
main                            # protected, only PR merge
dev                             # integration branch
feature/<agent-or-feature>      # weekly work
fix/<issue-id>                  # bug fixes
docs/<topic>                    # documentation only
research/<topic>                # exploratory, may be deleted
```

## ADR Format (max 1 page)

```
# ADR NNNN — [Title]

**Status:** Accepted | Superseded | Deprecated
**Date:** YYYY-MM-DD
**Context:** [2-3 paragraphs — why this decision was needed]
**Decision:** [1-2 paragraphs — what was decided]
**Consequences:** [pros, cons, trade-offs]
**Alternatives Considered:** [3-5 bullets]
```
