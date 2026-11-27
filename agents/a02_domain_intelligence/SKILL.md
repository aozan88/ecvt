---
name: domain-intelligence
description: Extracts a DomainModel (entities, operations with idempotency/authorization/ownership verdicts, workflows, rules, gaps, glossary) from a target system's OpenAPI document, reasoning only from the spec. Trigger when the Manager asks to model, map or understand a target system's domain before scenario generation, or when a new or changed OpenAPI spec arrives.
---

# Domain Intelligence Skill

## When to use
- "Build the domain model for examples/<target>."
- "The spec changed; refresh the domain model."
Do not use: writing scenarios or test cases, judging whether the system is defective, ranking bugs, writing code → `decline_task` (the Manager routes).

## Inputs required
- `<operation_inventory>` and `<spec>` (always supplied by the caller). Never ask for more; model what is there.
- `<task>`. If it asks for anything besides a domain model → `decline_task`, even if a spec is attached.

## Steps

**1. Classify every operation (`effect`).** Take the method first, then let the path or prose override:
- GET/HEAD → `read`. PUT/PATCH → `update`. DELETE → `delete`.
- POST to a collection (`/things`) → `create`. POST whose last segment is a verb or follows an id (`/things/{id}/cancel`) → `action`.
- If a description contradicts the method (a POST that only searches), follow the description and say so in the verdict details.

**2. Build entities.**
- One entity per domain schema used in a request or response body. Fold request variants (`NewThing`, `ThingInput`) into the entity whose properties they are a subset of.
- No entity for error envelopes, pagination wrappers or link objects.
- A resource that only appears as a path noun with an id parameter becomes an entity whose `fields` contain only that parameter. If the spec has no field at all, `fields` is empty. Never invent fields.
- `is_identifier`: `true` on the one field that identifies an instance (usually `id`), `false` on all others; all `false` if the spec exposes none.
- `writable` = appears in a request body or parameter and is not `readOnly`.
- Copy every declared keyword into `constraints`, as written. Then set `bounds`:
  - `declared`: both ends constrained (min and max, `enum`, `maxLength` or `pattern`, a bounding `format` such as `date`/`uuid`/`email`, `maxItems`)
  - `partial`: one end only (e.g. `minimum` without `maximum`)
  - `not_declared`: nothing; `not_applicable`: boolean or object

**3. Give every operation three verdicts.** Decide each one in this order; stop at the first match.
- *Idempotency* — reads: `not_applicable`.
  1. Idempotency-key header/parameter, or a declared response for repeats (e.g. 409 "already done") → `declared`.
  2. Prose says what repeats do → `described`.
  3. PUT/DELETE with no statement → `inferred` (HTTP semantics). Also add an `error_contract` gap if the result of the second call is undefined.
  4. POST/PATCH with no statement → `not_specified`.
- *Authorization* — operation-level `security` → `declared`; global `security` that is not overridden → `declared`; `security: []` → `declared` (explicitly public); no security anywhere → `not_specified`.
- *Ownership* — applies when the resource links to a party (a field or path segment pointing to whatever entity the spec uses for callers: user, customer, tenant…).
  - No such link → `not_applicable`.
  - Link present and the spec restricts cross-party access (scopes, "only the owner", tenant path) → `declared`/`described`.
  - Link present and nothing said → `not_specified`.
- Every `not_specified`/`contradictory` verdict (except read idempotency) carries its `gap_question`: the question the spec leaves open. It is filed as a gap for you; identical questions on the same dimension are filed once with every operation. `gap_question` is `null` on every other verdict.
- `parameters`: every parameter the spec declares for the operation, at path-item or operation level, with `location` (`in`), `type`, `required`, `bounds` and `constraints` on the same scale as entity fields. A path template alone declares nothing; `[]` if none.
- `declared_errors`: list every declared 4XX/5XX/default code; `[]` if none.

**4. Find workflows.** Record one only if it spans 2 or more operations.
- *Lifecycle*: an enum field that is `readOnly` or named like a state/status. Map each value change to the operation whose name or prose implies it. If no operation drives a transition (time- or system-driven), set `operation: null`; this obliges a `state_transition` or `temporal` gap.
- *Chains*: operation B consumes an id that operation A produces (A creates a guest, B takes `guestId`). Order them in `steps`.

**5. Write rules — only what a field cannot hold.**
- If one schema keyword on one field fully expresses the statement, it stays in `fields.constraints` and does not become a rule.
- Rules are cross-field, cross-entity, behavioral or prose statements: quotas ("at most 5 active"), preconditions, referential links, uniqueness, temporal conditions.
- Confidence: `declared` → `high`. `described` → `high` if unambiguous, otherwise `medium`. `inferred` → `medium` if structure strongly implies it, otherwise `low`.

**6. Raise gaps.** Walk the dimensions for each operation and entity. Write one question per gap, and group all refs that share the same question.
- `bounds`: numeric fields with `partial`/`not_declared` (mandatory); strings only when writable and the value space matters (codes, quantities written as strings).
- `idempotency`, `authorization`, `ownership`: not here — the verdict's `gap_question` (step 3) files them.
- `state_transition`: transitions with no operation; operations whose allowed source states are not stated.
- `concurrency`: two or more write operations that change the same instance or a shared counter.
- `referential_integrity`: a write takes another entity's id and declares no error for a missing target.
- `uniqueness`, `temporal`, `precision_units`, `error_contract`: when the spec is silent on them for a writable field or a write operation.
- `kind: contradictory` when the spec says two incompatible things (e.g. prose vs keyword). Put both pointers in `evidence`.
- Priority: `high` if it can change the outcome of a write or crosses a party boundary; `medium` for bounds, errors and temporal; `low` for read-only or naming issues.

**7. Glossary.** Add entity names, state values with domain meaning, and terms defined in prose. Use the spec's own words; set `provenance: inferred` where you define a term from structure alone.

**8. Self-check before calling the tool.**
- [ ] One operation per inventory row; ids, methods and paths copied verbatim
- [ ] Every `not_specified`/`contradictory` verdict has a `gap_question`; every unbounded numeric field and null-operation transition is covered by a gap
- [ ] Every pointer points at an existing node (escape `/` as `~1` in path keys)
- [ ] No term, entity or rule that the spec does not mention
- [ ] No gap phrased as a defect; no `inferred` item at `high`

## Templates
Gap questions (fill in the spec's own nouns):
- bounds — "What range of `<entity>.<field>` is accepted? The spec declares <nothing | only minimum X>."
- idempotency — "What happens when `<op>` is sent twice with the same input?"
- ownership — "May a caller `<verb>` a `<entity>` that belongs to another `<party>`?"
- state_transition — "Which operation or event moves `<entity>` from `<a>` to `<b>`?" / "Is `<op>` allowed when `<entity>` is `<state>`?"
- concurrency — "What happens when two `<op>` calls change the same `<entity>` at once?"
- referential_integrity — "What does `<op>` return when `<field>` refers to a `<entity>` that does not exist?"
- temporal — "Must `<field>` be in the future? Relative to which clock?"
- contradictory — "Prose says `<X>` but the schema declares `<Y>`; which one holds?"

## Anti-patterns
- **Filling silence.** Marking a POST `inferred` idempotent because "APIs usually are". → `not_specified` + gap.
- **Defect claims.** "Parcels accept any weight." → "What is the largest `weightKg` accepted? Only a minimum is declared."
- **Rule mirroring.** Turning every `maxLength` into a rule. → It stays in `constraints`.
- **Domain import.** Adding rules or terms from a domain the spec does not mention (a "cancellation fee" rule in a parcel API that never mentions fees). Any knowledge from outside the spec is `inferred`, ≤ medium, and must still attach to a field or operation that exists.
- **Gap flood.** One gap per unconstrained string id. → Group shared questions; raise string bounds only when the value space matters.
- **Partial compliance.** Building the model and also sketching scenarios because the task asked for both. → `decline_task`.
