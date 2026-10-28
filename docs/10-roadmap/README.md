# Roadmap

> Export of **ECVT Master Plan v7** + **ECVT Günlük Plan v7** (Drive, ECVT
> folder). Those two docs are the source of truth; this copy is refreshed at
> the Sunday retro. On conflict, Günlük Plan (status) > Master Plan (scope,
> gates) > ADRs > Mapping v3.

IDs: `Dnn` day (D00 = 22 Oct 2026) · `Mx-Tn` milestone task · `Gx` gate ·
`ADR nnnn` decision in `docs/11-decisions/`. A milestone closes only when its
gate list is complete and `python scripts/gate_check.py Mx` prints PASS.

## Status (export 2026-10-28 · D06)

- **M0 open.** T1–T3 done; T4 (ADR 0008), T5 (gate check), T6 (tag) open.
- **Slip:** G0 was due D01 (23 Oct). Plan rule 6: a gate slipping more than
  3 days is re-dated once, at the Sunday retro (D10 · 1 Nov).

## Milestone map

| Milestone | Scope | Days | Tag |
|---|---|---|---|
| M0 | Setup + demo target | D00–D01 (22–23 Oct) | v0.0.1 |
| M1 | Scenario → Execution → Evaluation chain + CLI | D02–D15 (24 Oct – 6 Nov) | v0.3.0 |
| M2 | Domain Intelligence + Manager, 5-agent MVP | D16–D29 (7–20 Nov) | v0.5.0 |
| Exam week | CCA-F (26 Nov); ECVT bugfix only | D30–D36 | — |
| M3 | Plugins, MCP eval, report, n8n, landing | D37–D52 (28 Nov – 13 Dec) | v1.0.0 |

## M0 — Setup + demo target

| ID | Task | Status · evidence |
|---|---|---|
| M0-T1 | Agent SDK call + token/cost note | ✅ `claude-haiku-5-5`; cold $0.00206 / warm $0.00016 (~13×) |
| M0-T2 | Repo debt: `.docx`, node cruft, Gemini ↔ ADR 0001, LICENSE ↔ README | ✅ PR #3 (`78c7601`) |
| M0-T3 | wallet-api check: 10 bugs, ground truth, README, happy-path pytest | ✅ pytest 1 passed; GT 10/10 with all fields |
| M0-T4 | ADR 0005 · 0006 · 0007 · 0008 (plan v7 order) | 🔄 0005–0007 in PR #2 (`4fe566d`); **0008 open** |
| M0-T5 | `scripts/gate_check.py` + `gates/M0.yaml` | ⏳ |
| M0-T6 | `CHANGELOG.md` + tag v0.0.1 | ⏳ |

**T1 lesson:** ~15K tokens of harness context load on every call. Trim it
(ADR 0005) and rely on prompt caching.

### Gate G0

- [x] `hello_agent.py` runs and prints a reply (plan says `learn/`; the file is `scripts/hello_agent.py`)
- [x] `uvicorn app:app --app-dir examples/wallet-api` up; `/openapi.json` 200 (matches the committed snapshot)
- [x] `pytest examples/wallet-api` green (happy path)
- [x] `ground_truth.yaml` has 10 records with id, category, endpoint, expected, actual
- [x] `git status` clean; no tracked `.docx` or `node_modules`
- [ ] ADR 0005–0008 in `docs/11-decisions/`, each ≤1 page
- [ ] `gate_check.py M0` PASS + tag v0.0.1

## M1 — Scenario → Execution → Evaluation chain

| ID | Task | Days |
|---|---|---|
| M1-T1 | Scenario Generator code: OpenAPI → Pydantic `Scenario`/`ScenarioSet`, 7 categories | D02–D03 |
| M1-T2 | Scenario Generator 5 files + `eval_dataset.jsonl` (10 cases) | D04–D06 |
| M1-T3 | Execution runner: httpx, timeout + retry, error → `tool_result`, `executions.jsonl` | D07–D09 |
| M1-T4 | Execution 5 files + 10 cases | D09 |
| M1-T5 | Evaluation: rule engine (status, schema, balance invariants) + LLM-as-judge + score schema | D10–D11 |
| M1-T6 | Evaluation 5 files + 15 cases (5 rule, 5 judge, 5 hybrid) | D12 |
| M1-T7 | Chain + CLI: `ecvt run --target examples/wallet-api` | D13 |
| M1-T8 | pytest, structured log, token/cost log, first X/10 | D14 |
| M1-T9 | Gate G1 + CHANGELOG + tag v0.3.0 | D15 |

### Gate G1

- [ ] `ecvt run --target examples/wallet-api` finishes with one command (exit 0)
- [ ] `scenarios.jsonl` covers all 7 categories; one execution and one score per scenario
- [ ] Each of the three agents' eval datasets passes ≥ 80%
- [ ] pytest green (unit + integration)
- [ ] Report states bugs caught vs ground truth (X/10); measured, no target
- [ ] Tokens and cost logged per run
- [ ] `gate_check.py M1` PASS + tag v0.3.0

## M2 / M3 (summary)

- **M2:** Domain Intelligence (DomainModel) → feeds Scenario Generator;
  Manager with subagents, intent taxonomy, pipeline state; README v1 +
  Mermaid; demo video. Gate G2, v0.5.0.
- **M3:** Plugin ABC + REST and wallet plugins; `ecvt-eval` MCP server;
  Dev + Business report; n8n webhook; landing + video. Gate G3, v1.0.0.

Full task lists and gates: Master Plan v7.

## Release gate notes

- Before the repo goes public: confirm the license (`LICENSE` is MIT today;
  repo stays private until v1.0).
