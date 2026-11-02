---
name: scenario-generator
description: Turns a target system's OpenAPI surface into runnable edge-case test scenarios across seven QE categories (happy, edge, stress, security, negative, adversarial, persona). Trigger when the user has an API spec and needs test scenarios generated, when a ScenarioSet must be regenerated after the spec or DomainModel changed, or when scenario coverage across categories needs checking.
---

# Scenario Generator Skill

How ECVT turns an API surface into probes worth running. This is procedure, not
background reading.

## When to use

- "Generate scenarios for this API spec"
- "We added an endpoint — regenerate the scenario set"
- "Do our scenarios actually cover the security and race cases?"

Do not use for: running scenarios (Execution, 05), scoring results
(Evaluation, 06), deciding which agent runs when (Manager, 01), or building a
domain model from docs (Domain Intelligence, 02).

## Inputs required

- **OpenAPI spec** of the target (`openapi.json`). Without it, stop and ask —
  never generate scenarios against a guessed API.
- **Target identifier** (e.g. `examples/wallet-api`), for the output metadata.
- *Optional:* a DomainModel from Domain Intelligence (M2-T2). When present, its
  rules and workflows take priority over what can be inferred from the spec.

Never accept a defect list as input. Scenarios generated from known bugs cannot
measure whether ECVT finds bugs (ADR 0007).

## Steps

1. **Read the surface, not the file.** Run the spec through
   `ecvt.openapi_reader.read_openapi()`. If it raises "no usable endpoints", the
   spec is wrong or empty — report that instead of inventing endpoints.
2. **Mine each endpoint for silence.** For every field, ask what the spec does
   *not* say: no minimum → try negative and zero; no maximum → try absurd; no
   owner or auth → try acting on someone else's resource; no stated idempotency
   on a write → replay it. The gaps are the scenarios.
3. **Classify by what breaks, not by vocabulary.** A scenario is `security` if
   it crosses an ownership boundary, `adversarial` if it abuses protocol
   (replay, double-submit, ordering), `stress` if it needs volume or
   concurrency, `edge` if it sits on a boundary value. If two categories fit,
   pick the one whose failure would be reported first and move on — do not file
   the same probe twice.
4. **Make it runnable.** Every scenario carries its own `setup`. Capture ids
   with `save`, reference them as `{placeholder}`. A scenario that assumes
   pre-existing state is a broken scenario.
5. **Write the expectation as correctness.** State what a *good* system does,
   with concrete status codes and a behaviour sentence a reviewer can check.
   Add `probes` (GET only) for anything an `invariant` claims.
6. **Self-check before returning.** All seven categories present? Every
   `{placeholder}` bound by an earlier `save`? Every path in the spec? Every
   invariant backed by a probe? Schema-valid against `ScenarioSetDraft`?

## Templates

Minimum viable scenario shape:

```json
{
  "id": "sc-0NN",
  "title": "<what is probed>",
  "category": "<one of the seven>",
  "severity": "low|medium|high|critical",
  "rationale": "<the failure mode this would expose>",
  "setup": [{"name": "<step>", "method": "POST", "path": "/...", "body": {}, "save": {"x": "id"}}],
  "action": {"name": "<step>", "method": "POST", "path": "/...", "body": {"ref": "{x}"}},
  "expectation": {
    "expected_status": [400, 422],
    "expected_behavior": "<what a correct system does>",
    "invariants": ["<state claim>"],
    "probes": [{"name": "read_x", "method": "GET", "path": "/.../{x}"}]
  }
}
```

Category starters for a transactional API:

| Category | Probe that usually pays off |
|---|---|
| happy | the intended flow end to end, as a baseline |
| edge | exact-balance, zero, empty string, self-reference (from == to) |
| stress | `concurrency: 10` on the same record |
| security | act on a resource created by a different owner |
| negative | wrong type, missing required field, negative amount |
| adversarial | `repeat: 2` on a non-idempotent write |
| persona | a plausible user sequence whose middle step is invalid |

## Anti-patterns

- **Describing the bug instead of the expectation.** "Balance goes negative" is
  an observation; Execution makes observations. Write "the transfer is rejected
  and balances are unchanged".
- **Unbound placeholders.** `{src_id}` with no `save` that produces it — the
  scenario cannot run. Trace every placeholder to its step.
- **Invented fields.** An `Idempotency-Key` header that the spec never mentions
  tests ECVT's imagination, not the target.
- **Category padding.** Seven copies of the same request with different labels.
  One defect idea per scenario; if a category has nothing real, say so in the
  `rationale` of the one scenario you do write.
- **Unverifiable invariants.** A claim about state with no probe reading it back
  is a sentence, not a check.
