# ECVT

AI Quality Engineering Platform · Multi-agent edge-case validation.

ECVT (Edge Case Validation Toolkit) validates existing AI-based or classic
software systems before and after production — generating edge cases,
verifying acceptance criteria, tracking regression, and producing
audience-specific reports. It does **not** generate software (unlike
MetaGPT / CrewAI); it measures software quality.

## Quick Start

1. Choose your OS guide: [Windows](setup/Windows.md) | [Mac](setup/Mac.md) | [WSL](setup/WSL.md)
2. Install Claude tooling: [Claude.md](setup/Claude.md) + [Cursor.md](setup/Cursor.md)
3. Run n8n: [n8n.md](setup/n8n.md) + [Docker.md](setup/Docker.md)
4. Configure environment: [Environment.md](setup/Environment.md)
5. Hello query: `python scripts/hello_claude.py`

Stuck? See [Troubleshooting](setup/Troubleshooting.md).

## Run it

```bash
pip install -r requirements.txt                      # Python 3.11+
uvicorn app:app --app-dir examples/wallet-api --port 8000 &   # demo target, 10 known bugs
ecvt run --manager --target examples/wallet-api --report
```

The Manager classifies the request, plans the stages, and runs Domain Intelligence →
Scenario Generator → Execution → Evaluation. Output lands in `artifacts/runs/<run_id>/`:
`state.json`, one JSON artifact per stage, `scenarios/executions/scores.jsonl`, `run.json`
(tokens, cost, Manager turns) and `report.md` with the catch rate against
`ground_truth.yaml`. `ecvt run` without `--manager` is the M1 fixed chain
(the default: ~$0.01 and 20–40 s per run).

In a browser: `ecvt serve`, then open http://localhost:8090/ — pick a target and
plugin, start a run, watch its steps, open the Business and Dev reports, browse
past runs. n8n calls the same server ([setup/n8n.md](setup/n8n.md)).

Evals: `python scripts/eval_runner.py agents/<aNN_*>/eval_dataset.jsonl` (bar: 80%).

## Pipeline

```
Manager → Domain Intelligence → Scenario Generator → Execution → Evaluation
          → (v1.2) Acceptance · Regression → (v1.3) Reporting → (v2.0) Knowledge
```

Domain-agnostic, plugin-based. See [docs/02-architecture](docs/02-architecture/architecture.md).

## Agents (MVP = 5 of 9)

| Code | Agent | Version |
|------|-------|---------|
| 01 | Manager — [spec](docs/03-agents/manager.md) | MVP (M2) |
| 02 | Domain Intelligence — [spec](docs/03-agents/domain-intelligence.md) | MVP (M2) |
| 03 | Scenario Generator | MVP (M1) |
| 04 | Acceptance | v1.2 (M5) |
| 05 | Execution | MVP (M1) |
| 06 | Evaluation | MVP (M1) |
| 07 | Regression | v1.2 (M5) |
| 08 | Reporting | v1.3 (M6) |
| 09 | Knowledge | v2.0 (M7) |

## Status

**v1.0.x — M0–M3 closed.** 5 of 9 agents run (01, 02, 03, 05, 06) behind a plugin
layer (`rest_api`, `wallet`), the `ecvt-eval` MCP server scores rules, every run
writes Dev + Business reports, and `ecvt serve` gives a browser UI and the n8n
trigger. On the demo target the chain with the wallet plugin catches a median of
7 of 10 known defects over five runs (range 6–8). Details: [CHANGELOG](CHANGELOG.md).

What comes next, milestone by milestone: [ROADMAP.md](ROADMAP.md). Decisions are
ADRs under [docs/11-decisions](docs/11-decisions/); the plan is not revised.

## Limitations

- One demo target with known defects (`examples/wallet-api`). The only run against a
  third-party API so far is a local Swagger Petstore (four defects, confirmed by hand).
- A run's scenario set varies between runs; compare catch rates with `scripts/n_run.py`,
  not single runs.
- No resume: a failed run is re-run from the start.
- No demo video yet (deferred, ADR 0015).
- Acceptance (04), Regression (07), Reporting (08) and Knowledge (09) do not exist yet.
