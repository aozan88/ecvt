# Agent Spec — Domain Intelligence (a02)

Code: `agents/a02_domain_intelligence/` · Version 1.0 (M2) · 2026-11-24 · ADRs 0005, 0009

## 1. Role

a02 reads a target's OpenAPI document and returns a **DomainModel**: what the system
manages, what it promises, and what its spec leaves unsaid. Its only consumer is the
Scenario Generator (a03), which uses the rules as things to try to break and the gaps as
behaviour nobody specified. a02 writes no scenarios, judges no defects and writes no code;
a task asking for those is declined.

## 2. Inputs and outputs

| Direction | What | Defined in |
|---|---|---|
| In | Task text + OpenAPI document (JSON or YAML) | `build_domain_model()` in `agent.py` |
| Out | `DomainModel` — entities, operations, workflows, rules, gaps, glossary | `schema.py` (Pydantic) |
| Out | or `DeclineTask` — reason, what was asked, why not | `schema.py` |

`tools.json` is generated from `schema.py`, never edited by hand.

## 3. How it works

1. **Code builds the operation inventory.** One row per operation, `id | METHOD | path`.
   The id is `operationId` in snake_case, else `{method}_{path}` (`POST /orders` →
   `post_orders`). The model must use these ids verbatim; it never names operations itself.
2. **The spec is re-serialised** from the parsed document, so file comments never reach
   the model and every JSON Pointer it cites still resolves.
3. **One Messages API call, two tools** (`emit_domain_model`, `decline_task`) with
   `tool_choice: any`: the model must call one of them, never answer in prose.
4. **Validation with the spec as context.** Pydantic checks the shape (`extra="forbid"`,
   no defaults), then the cross-checks: every inventory operation covered once, method and
   path match, every evidence pointer exists in the spec, every `not_specified` verdict and
   unbounded field is covered by a gap. On failure the error text goes back to the model
   once as a correction (`max_attempts=2`).

## 4. Contract rules (what keeps the output honest)

- **No defaults.** "Unknown" is an explicit value (`not_specified`, `not_declared`) or a
  key that must be present with `null`. A field the model skips is an error, not a blank.
- **Silence is a finding.** Each operation carries three verdicts — idempotency,
  authorization, ownership — and a silent spec must produce a gap, not a guess.
- **Provenance on every rule:** `declared` (machine-readable), `described` (prose) or
  `inferred`. An inferred rule can never have `high` confidence.
- **Evidence is checked, not trusted.** A pointer that does not resolve is rejected, which
  catches invented facts automatically.

## 5. Prompt strategy

`system_prompt.md` (XML sections: role, task, constraints, output format, examples) plus
`SKILL.md` (the step-by-step procedure). The Messages API does not load skills, so
SKILL.md is sent as the second system block; the cache breakpoint sits on it, so prompt
and skill are cached together (Haiku has a minimum cacheable length — check
`cache_creation_input_tokens > 0` in the usage log). The few-shot example is from a
domain no eval uses (parcels), so the model cannot copy eval vocabulary; a unit test
proves that example is itself schema-valid. Extended thinking cannot be combined with a
forced tool, so the per-operation verdicts make the model reason inside the schema.

## 6. Eval criteria

`eval_dataset.jsonl`, 12 cases, five specs (wallet, library loans, skeletal orders, a
single GET, an API with no bodies).

| Category | Cases | Scoring |
|---|---|---|
| happy-path | 7 | 5 llm-judge (one checkable property each), 2 schema-valid with spec context |
| edge | 3 — missing input, no request bodies, mixed-scope task | llm-judge / schema-valid |
| negative | 2 — wrong task, wrong domain | schema-valid (`DeclineTask`) |

Success bar: pass rate ≥ 80% (G2). A mixed task ("model it and write scenarios") is
declined whole — the prompt forbids partial compliance. Status: not yet run live.

## 7. Dependencies and limits

- Position: first stage after the Manager; output feeds a03 (`rules`, `gaps`,
  `workflows` only — entities and operations are already in a03's API digest).
- Runtime: Messages API via `ecvt/llm.py` (`structured_call`), model `ECVT_SUBAGENT_MODEL`
  (Haiku, ADR 0005), `max_tokens=16000`.
- Limits: ≤ 60 rules, ≤ 60 gaps, ≤ 40 glossary terms per model.

## 8. Known limitations

- Reads OpenAPI only; prose requirements and acceptance criteria are a04's job (v1.2, M5).
- A very large spec is sent whole; there is no chunking.
- Eval rubrics depend on the inventory id rule; changing the rule means updating them.
