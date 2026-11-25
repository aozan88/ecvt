# ADR 0011 — a03's wire schema is a union discriminated by category

**Status:** Accepted
**Date:** 2026-11-24
**Context:** The generator's wire model asked for `repeat` and `concurrency` on every
scenario, with `Scenario`'s defaults dropped so the model could not skip the decision.
That was a response to a real failure: the generator had returned a whole set at 1,
including a scenario titled "Many parallel transfers racing", so a set could claim to
probe a race without sending one.

It bought the form rather than the meaning, and the form was expensive. At the default
quota it asked for 28 numbers, 24 of which could only sensibly be 1. Measured over 8
generations against wallet-api on `claude-haiku-5-5`: from the spec alone 8 of 8 sets
validated but 4 spent a second attempt on `Field required`; with a02's DomainModel in
the prompt 6 of 8 validated and **none** on the first attempt, at 1.9x the cost. The
slip carried no signal about scenario quality.

Restoring the defaults was tried and reverted (PR #30): with a default available the
model fills 1 everywhere, the set-level check rejects the set, and it does not correct
when told — 3 of 6 generations exhausted four attempts. The required fields *prevent*
the omission; the set-level check only *detects* it.

**Decision:**
- `ScenarioCore` holds a scenario minus the two execution modes. `Scenario` is the core
  plus `repeat`/`concurrency` defaulting to 1 — reading a file and writing one by hand
  are unchanged.
- The wire model is a union over `category`:
  - `StressScenarioDraft` — `category: stress`; `concurrency` required at >= 2, `repeat`
    required at >= 1. A race is what this category probes, so the schema guarantees one.
  - `AdversarialScenarioDraft` — `category: adversarial`; `repeat` and `concurrency`
    both required at >= 1. The category is broader than replay (ordering tricks,
    double-submit), so the number is asked for but not forced above 1.
  - `PlainScenarioDraft` — the other five categories; neither field appears in the
    schema. Code writes 1 and 1 on the way to `Scenario`.
- A plain scenario that carries the fields anyway is tolerated at 1 and refused above it,
  with the correction naming the category rather than the field: a model describing a
  race under `happy` should change `category`, not the number.
- `missing_execution_modes()` stays as the set-level net. `stress` now guarantees the
  race; the replay still depends on the model choosing `repeat` > 1 on an adversarial
  scenario, and that check is what catches a set which does not.
- `category` is the discriminator. `oneOf` + `discriminator` was verified against the
  Messages API (`count_tokens` with the tool attached) before committing to it.

**Consequences:**
- The model states 8 numbers at the default quota instead of 28.
- The input schema grows: each branch embeds the core, so the tool definition costs more
  input tokens per call (measured on the two-branch form: 2554 -> 3143, +23%). Output
  tokens are the dearer side, so the trade is expected to pay; the Measured table in
  CHANGELOG records whether it did.
- A new category has to be placed in a branch. The union is the place that decides
  whether a category sends its action more than once.
- If the model cannot work the union reliably, the fallback is two flat lists
  (`scenarios` and `load_scenarios`) with their own category enums, which keeps the
  two-shape idea without `oneOf`. Not taken: measure the union first.
