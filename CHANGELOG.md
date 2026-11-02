# Changelog

All notable changes to ECVT are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/).

## [0.3.0] - 2026-11-02
M1 — the Scenario → Execution → Evaluation chain, runnable in one command.

### Added
- `ecvt.schema`: `Scenario` / `ScenarioSet` over the 7 QE categories, with
  `setup` → `action` → `expectation` and read-only `probes`.
- `ecvt.openapi_reader`: `openapi.json` → a compact API digest (endpoints,
  body fields, parameter constraints) for the generator's prompt.
- `a03_scenario_generator`: forced-tool generation with a schema-bound retry
  loop and a per-category quota.
- `ecvt.execution`: async runner — placeholder binding, `save` capture,
  `repeat` × `concurrency`, probes after the action. Nothing raises; a
  received HTTP status is never retried (ADR 0006).
- `a06_evaluation`: rule checks plus an LLM judge, scored per dimension and
  combined, with bounded judge concurrency.
- `ecvt.cli`: `ecvt run --target …` runs the three stages and writes
  `run.json` with tokens, cost and timing.
- `ecvt.reporting`: catch rate against `ground_truth.yaml` with one-to-one
  attribution; trend and latest-run docs under `docs/09-evaluation/`.
- `ecvt.evals` + `scripts/eval_runner.py`: per-agent eval datasets scored by
  schema validity, keyword match, code check or LLM grader, against an 80%
  bar.
- `gates/M1.yaml` and `scripts/gate_check.py`: G1 as runnable checks.
- ADR 0005 (model routing) · 0006 (execution errors as data) · 0007 (demo
  target and ground-truth baseline) · 0008 (plan v7 build order) · 0009
  (subagents on the Messages API, Manager on the Agent SDK).

### Fixed
- Generator returned `scenarios` as a JSON string, costing a retry.
- Grader committed to a verdict before writing its reasoning, producing
  self-contradicting failures; `reason` now precedes the verdict.
- Generator left every scenario at `concurrency: 1, repeat: 1`, which put
  races and replays out of reach; the wire schema now requires both fields
  and the validator rejects a set that exercises neither.

## [0.0.1] - 2026-10-23
### Added
- Initial repository scaffold (Repository Structure — FINAL, Mapping v3).
- Draft `CLAUDE.md` hierarchy (root + per-agent).
- Agent folders prefixed with Claude Project codes (01–09).
