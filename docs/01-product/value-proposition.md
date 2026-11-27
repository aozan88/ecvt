# Value Proposition

> v1.0.1 · 2026-11-27 · Every number below is measured and recorded in
> [CHANGELOG](../../CHANGELOG.md); nothing here is a projection.

## Pitch

ECVT reads an OpenAPI spec, writes the edge cases a senior QE would try, runs
them against the live system and tells each audience what it needs: a release
recommendation for the business, a repro per finding for developers. It
validates software that exists; it does not generate software.

## What it does today (v1.0.x)

| Claim | Evidence |
|---|---|
| Finds real defects on a known target | wallet-api, 10 seeded defects: median **7/10** caught over five runs (range 6–8), M1 chain + wallet plugin |
| Works on a system it was not built for | Swagger Petstore v3, no baseline: **4 defects**, each confirmed by hand (500s on spec-valid bodies, an update accepted without credentials) |
| Does not cry wolf when the target is down | zero defects reported against a down target; those scenarios are reported as not run |
| Cheap enough to run on every change | about **$0.01** and 20–40 s per chain run (`claude-haiku-5-5`); the Manager path costs about 7× ($0.077 vs $0.011) |
| Agents meet their own bar | evals a03 86.7% (pooled, 3 runs), a05 100%, a06 100% against an 80% bar |
| Reports per audience, from one run | Business and Dev reports (Markdown + HTML) with no extra model call and no ground truth |
| Domain knowledge stays out of the core | plugins by name (`rest_api`, `wallet`); the wallet plugin adds 7 domain rules without touching an agent |
| Fits existing automation | `ecvt serve`: browser UI, and an n8n webhook that answers with the finished run |

## Differentiators

- **Quality engineering, not an agent framework.** The output is a verdict on
  someone else's system: findings with evidence, a recommendation, a cost.
- **Rules first, judge second.** A deterministic rule engine (the `ecvt-eval`
  MCP server) scores every scenario; the LLM judge adds a second verdict, and a
  FAIL from either carries — the judge cannot pass what a rule failed.
- **Measured, with its variance.** Catch rate is reported as min / median /
  max over several runs (`scripts/n_run.py`), never as one lucky run.
- **Domain-agnostic core.** A new kind of system is a plugin, not a fork.

## Not yet claimed

- **How it compares.** No head-to-head with other tools has been measured. The
  M4 baseline (ROADMAP M4-T6) runs the same target with Claude Code + Opus and
  with Schemathesis; until then this page makes no competitive claim.
- **QA report, regression tracking, acceptance criteria** — M5 and M6.
- **A second real domain** (IKYS) — M6.
