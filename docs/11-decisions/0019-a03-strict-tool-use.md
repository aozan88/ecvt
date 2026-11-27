# ADR 0019 — a03 calls its tool with `strict: true`; free-form fields travel as text and lists

**Status:** Proposed (M4-T1, 2026-11-27)
**Date:** 2026-11-27
**Context:** a03's forced tool call is validated by Pydantic after the fact. When
Haiku leaves out a required field the whole set is rejected and regenerated:
`Field required` appeared in 8 of 24 generations after the M3 fixes, and an
omission repeated twice in a row is why exhaustion needed a restart path (#45).
ADR 0011 cut the numbers asked for from 28 to 8 but could not remove the
omission itself. `strict: true` on a tool constrains sampling to the schema, so a
required field cannot be left out (Haiku 5.5 supports it with a forced
`tool_choice`).

Strict schemas must close every object (`additionalProperties: false`). A
request `body` and the `query` / `headers` / `save` maps have keys no schema can
list; under strict sampling they could only ever be `{}`, which would silently
send every request without a body. They also reject `oneOf`, `discriminator`,
numeric bounds and `maxItems`, and cap a request at 24 optional and 16
union-typed parameters.

**Decision:**
- The tool is strict. Its schema is generated from the Pydantic models and
  rewritten by `ecvt.llm.strict_schema`: every property required, objects
  closed, `oneOf` → `anyOf`, unsupported bounds moved into the description and
  still enforced by Pydantic. Result: 0 optional and 1 union parameter.
- On the wire, a step's `body` is JSON text of an object (`""` for none) and
  `query`, `headers`, `save` are `[{name, value}]` lists. `ecvt.schema.from_wire`
  turns them back into `HttpStep`'s shape before validation; body text that is
  not JSON, or a name given twice, is a correction like any other. Nothing after
  a03 sees the wire form. The few-shot is written in it and tested against it.

**Consequences:** `Field required` and `scenarios` sent as a string can no longer
occur. Measured over 8 generations per arm (2026-11-27): first attempt 4/8 → 8/8,
`Field required` 2/8 → 0/8, cost −33%; catch rate 7 / 8 / 8 over five runs. A new failure becomes possible — JSON text inside `body` that does not
parse — and is counted separately (`scripts/measure_a03.py`, class `body_json`).
The category union and its set-level checks (ADR 0011) are unchanged. A model
that rejects forced `tool_choice` (Opus 5.5, Sonnet 5.5) still needs `auto`;
that is M4-T5's to add.

**Alternatives Considered:**
- `output_config.format` (JSON outputs) instead of a strict tool — same schema
  limits, and the tool path, its corrections and the Manager stay as they are.
- Strict with the schema as is — rejected: bodies would arrive empty.
- Body as name/value pairs with typed values — rejected: nested bodies and
  union-typed values grow the grammar; JSON text covers every body.
- Keep validating after the fact — the 8-in-24 omission is the measured cost.
