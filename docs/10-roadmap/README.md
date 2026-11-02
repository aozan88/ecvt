# Roadmap

> Export of **ECVT Master Plan v7** + **ECVT Günlük Plan v7** (Drive, ECVT
> folder). Those two docs are the source of truth; this copy is refreshed at
> the Sunday retro. On conflict, Günlük Plan (status) > Master Plan (scope,
> gates) > ADRs > Mapping v3.

IDs: `Dnn` day (D00 = 22 Oct 2026) · `Mx-Tn` milestone task · `Gx` gate ·
`ADR nnnn` decision in `docs/11-decisions/`. A milestone closes only when its
gate list is complete and `python scripts/gate_check.py Mx` prints PASS.

## Status (export 2026-11-02 · D11)

- **M0 closed.** G0 PASS, tag `v0.0.1`.
- **M1 nearly closed.** T1–T8 done; T9 open on two G1 items: pytest green
  (G1-5) and tag `v0.3.0` (G1-8). The three agents' evals read a03 90% /
  a05 100% / a06 100% against the 80% bar; catch rate 8/10.
- **Ahead of plan** on M1 (T9 was dated D15 · 6 Nov) because the code was
  written for Ozan rather than by him, and the prompt-iteration days
  (D04–D06, D09, D12) were spent on the eval harness instead.

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
| M0-T4 | ADR 0005 · 0006 · 0007 · 0008 (plan v7 order) | ✅ all four Accepted in `docs/11-decisions/` |
| M0-T5 | `scripts/gate_check.py` + `gates/M0.yaml` | ✅ G0 PASS |
| M0-T6 | `CHANGELOG.md` + tag v0.0.1 | ✅ `v0.0.1` |

**T1 lesson:** ~15K tokens of harness context load on every call. Trim it
(ADR 0005) and rely on prompt caching.

### Gate G0

- [x] `hello_agent.py` runs and prints a reply (plan says `learn/`; the file is `scripts/hello_agent.py`)
- [x] `uvicorn app:app --app-dir examples/wallet-api` up; `/openapi.json` 200 (matches the committed snapshot)
- [x] `pytest examples/wallet-api` green (happy path)
- [x] `ground_truth.yaml` has 10 records with id, category, endpoint, expected, actual
- [x] `git status` clean; no tracked `.docx` or `node_modules`
- [x] ADR 0005–0008 in `docs/11-decisions/`, each ≤1 page
- [x] `gate_check.py M0` PASS + tag v0.0.1

## M1 — Scenario → Execution → Evaluation chain

| ID | Task | Days |
|---|---|---|
| ID | Task | Days | Status · evidence |
|---|---|---|---|
| M1-T1 | Scenario Generator code: OpenAPI → Pydantic `Scenario`/`ScenarioSet`, 7 categories | D02–D03 | ✅ `ecvt/schema.py`, `ecvt/openapi_reader.py` |
| M1-T2 | Scenario Generator 5 files + `eval_dataset.jsonl` (10 cases) | D04–D06 | ✅ a03 eval 90% |
| M1-T3 | Execution runner: httpx, timeout + retry, error → `tool_result`, `executions.jsonl` | D07–D09 | ✅ `ecvt/execution.py` (ADR 0006) |
| M1-T4 | Execution 5 files + 10 cases | D09 | ✅ a05 eval 100% |
| M1-T5 | Evaluation: rule engine (status, schema, balance invariants) + LLM-as-judge + score schema | D10–D11 | ✅ `ecvt/scoring.py`, `a06_evaluation/agent.py` |
| M1-T6 | Evaluation 5 files + 15 cases (5 rule, 5 judge, 5 hybrid) | D12 | ✅ a06 eval 100% |
| M1-T7 | Chain + CLI: `ecvt run --target examples/wallet-api` | D13 | ✅ `ecvt/cli.py` |
| M1-T8 | pytest, structured log, token/cost log, first X/10 | D14 | ✅ `run.json`; catch rate 8/10 |
| M1-T9 | Gate G1 + CHANGELOG + tag v0.3.0 | D15 | 🔄 G1-5, G1-8 open |

### Gate G1

- [x] `ecvt run --target examples/wallet-api` finishes with one command (exit 0)
- [x] `scenarios.jsonl` covers all 7 categories; one execution and one score per scenario
- [x] Each of the three agents' eval datasets passes ≥ 80% — a03 90% · a05 100% · a06 100%
- [ ] pytest green (unit + integration) — **G1-5 open:** 3 failed / 44 errors in the Codespace, 197 green in CI and locally; under diagnosis
- [x] Report states bugs caught vs ground truth (X/10); measured, no target — 8/10
- [x] Tokens and cost logged per run
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
