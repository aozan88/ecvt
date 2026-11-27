# ADR 0021 — DomainModel holds its cross-field rules by structure, not strict sampling

**Status:** Proposed (M4-T2, 2026-11-27)
**Date:** 2026-11-27
**Context:** a02 validated on wallet-api in 8 of 8 generations but on the first
attempt in 1 of 8, at ~2.5 attempts and $0.0085 a generation. The corrections were
cross-field rules named by reference: an entity's `identifier` naming a field it
does not have (4 of 8), and a `not_specified` verdict whose gap was missing from
the separate `gaps` list (6 of 8). Strict tool use, which fixed a03 (ADR 0019), was
tried first and does not fit: the DomainModel compiles to a grammar the API refuses
("compiled grammar is too large") with or without its regex patterns; only halves of
it compile. Strict would also have prevented neither rule — only a stray extra field.

**Decision:** move the two rules into the structure, as ADR 0011 did for a03's
`repeat` / `concurrency` (DomainModel v2):
- `EntityField.is_identifier` replaces `Entity.identifier`: the identifier is a mark
  on the field itself, at most one per entity, so it cannot name a missing field.
- `Verdict.gap_question` (required, nullable): an open verdict (`not_specified`,
  `contradictory`) carries its own question; the schema files it under `gaps`, one
  gap per distinct question, high priority. An explicit gap still covers a verdict.
- M4-T2 in the same contract: `Operation.parameters` records declared parameters
  (location, type, required, bounds, constraints); an undeclared one is rejected.
- Readers adapt: few-shot, SKILL, a02 rubrics, di-010. Consumers keep reading
  `gaps`, which now also holds the filed questions; `Entity.identifier` stays as a
  derived property.

**Consequences:** measured on wallet-api, 8 generations each: the structure alone
took the first attempt from 1/8 to 6/8 and cost to $0.0039; with parameters the
larger output read 4/8 at $0.0046, its new failures being `entities` sent as broken
JSON text (2 of 8). `extra="forbid"` stays: a stray top-level key is still a
correction. Two halves of the model would make strict possible later.

**Alternatives Considered:**
- Strict tool use on the whole model — rejected: the API will not compile it.
- Strict split into two calls — doubles calls; prevents only the extra-field error.
- Drop `extra="forbid"` at the root — rejected: the contract test holds it.
