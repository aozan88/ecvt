# ADR 0007 — Demo target domain + ground-truth-driven eval baseline

**Status:** Accepted
**Date:** 2026-10-23
**Context:** ECVT needs a target system to validate during M1/M2. Real
systems (Solar, IKYS) have unknown bug inventories, so "ECVT found N issues"
cannot be distinguished from noise or from missed bugs. A credible demo and a
measurable quality bar need a target whose defects are known in advance.
**Decision:**
- Demo target = `examples/wallet-api/`: a small FastAPI fintech wallet with
  10 deliberately injected bugs, spread across QE categories (numeric,
  negative-validation, boundary, authz, idempotency, race, error-handling,
  edge, validation-stress).
- `ground_truth.yaml` is the eval baseline. ECVT's headline metric is
  **catch rate = bugs detected / 10**, plus false-positive count.
- The app is never fixed; `pytest` covers the happy path only.
- `openapi.json` snapshot is the Scenario Generator's canonical input.
- Ground truth is for *measurement only*: it is never fed to Scenario
  Generator or Execution prompts (no leakage).
**Consequences:** Every pipeline change gets a comparable number, which also
seeds the future Regression agent. Fintech is a domain recruiters and
reviewers instantly understand. Risk: overfitting prompts to one target —
mitigated by the second domain plugin (Solar/IKYS) in a later milestone.
**Alternatives Considered:**
- Use Solar/IKYS directly — rejected (no ground truth, private data)
- Public buggy apps (e.g. OWASP Juice Shop) — rejected (web/UI-heavy, too large)
- Synthetic unit fixtures only — rejected (no end-to-end HTTP realism)
