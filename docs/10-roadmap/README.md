# Roadmap

> Milestone tracker. Plan source: Master Plan v6.2 (frozen) + ADRs. The M0–M2
> breakdown is worked out in the Claude Project chat; version targets are in
> `docs/01-product/vision.md` §8.

## Current status (2026-10-28)

- **Now:** M0 closing (T2 repo hygiene). **Next:** M1.

## M0 — Setup & repo hygiene

| # | Task | DoD | Status |
|---|------|-----|--------|
| T1 | Agent SDK hello (`scripts/hello_agent.py`) | SDK reply on screen | ✅ |
| T2 | Repo hygiene: untrack `.docx`, scope `setup/Gemini.md`, defer license | `git status` clean after merge | 🔄 PR |
| T3 | Fill `setup/*.md` stubs from `scaffold-setup.sh`, then delete the script | No STUB-only setup docs; no one-off scripts at root | ⏳ proposed |

**T1 result:** `claude-haiku-5-5`, reply "ECVT agent hello OK". Cold run
15,401 cache-creation tokens ($0.00206); warm run 15,401 cache-read
($0.00016), ~13× cheaper.
**Lesson:** ~15K tokens of harness context (tools/skills/plugins) load on
every call. Trim it per ADR 0005 and rely on prompt caching.

**T3 note:** `scaffold-setup.sh` skips non-empty files, so it never replaced the
existing stubs.

## M1 — First pipeline slice on the demo target (DRAFT)

> Derived from ADR 0005–0007 and `examples/wallet-api/`. Confirm against the
> Project chat plan before starting.

| # | Task | DoD | Ref |
|---|------|-----|-----|
| T1 | Run wallet-api locally | `uvicorn` up; happy-path `pytest` green; `openapi.json` snapshot ✅ | ADR 0007 |
| T2 | Manager agent to the 5-artefact standard | "done" checklist in agent standards | `docs/ecvt-agent-standards-v1.md` |
| T3 | Scenario Generator: `openapi.json` → scenarios | Schema-valid output; ≥10 eval cases; no `ground_truth.yaml` leakage | ADR 0007 |
| T4 | Execution agent + REST plugin | One result record per scenario; errors returned as data | ADR 0006, 0003 |
| T5 | Model routing config | Per-agent model via `ECVT_<AGENT>_MODEL`; trimmed harness; cache hits visible | ADR 0005 |
| T6 | End-to-end: Manager → Scenario Gen → Execution on wallet-api | One full run, results saved | — |

## M2 — Measured quality (preview)

- Evaluation agent scores results against `ground_truth.yaml`: **catch rate
  X/10** + false-positive count (ADR 0007).

## Release gates

- **Repo goes public (v1.0):** confirm the license. `LICENSE` is MIT today;
  the repo stays private until then.

## TODO

- [ ] Confirm the M1 task list against the Project chat plan
- [ ] Re-baseline vision §8 version dates (Jul–Aug 2026 have passed) → ADR
- [ ] Map M0–M2 onto the version table v0.5 → v2.0
- [ ] ADR numbering: 0004 is missing (0003 → 0005). Fill it or note the gap
- [ ] Remove the DRAFT marker on M1 when confirmed
