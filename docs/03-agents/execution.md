# Agent Spec — Execution (a05)

Code: `plugins/<name>/` → `ecvt/execution.py` · `agents/a05_execution/` · Version 1.1 (M3-T1)
· 2026-11-25 · ADRs 0006, 0009, 0010, 0013, 0018

## 1. Role

Runs every scenario of a ScenarioSet against the live target and records what
happened, verbatim. It does not judge: a 500 is a finding for Evaluation, not
an execution error. No model call (ADR 0009) — running HTTP needs no reasoning.

## 2. Pipeline position

Third stage: scenario_gen → **execution** → evaluation. Reached three ways:

| Entry point | Path |
|---|---|
| `ecvt run [--plugin <name>]` | `load(plugin).execute(scenarios)` |
| Manager stage `run_execution` | the intent's `plugin` slot (default `rest_api`) → `plugin.execute` |
| `python -m agents.a05_execution.agent` | developer tool: Core runner directly, with `--retries`/`--only` knobs |

## 3. Inputs / outputs

| | Contract |
|---|---|
| In | `list[Scenario]` (`ecvt/schema.py`): `setup` steps with `save` captures, one `action`, `repeat`, `concurrency`, read-only `probes` |
| Out | one `ExecutionRecord` per scenario (`ecvt/results.py`): `outcome` (`completed` / `setup_failed` / `action_error`), every `StepResult` (status, body, timing, attempts, error), resolved `bindings` |
| Artifact | `executions.jsonl` (chain) · `runs/<run_id>/execution.json` (Manager) |

## 4. How a scenario runs (`rest_api`)

1. `setup` in order; each must answer 2xx, its `save` captures bind `{name}`
   placeholders. A failed setup ends the scenario as `setup_failed`.
2. `action`: `repeat` rounds in series, `concurrency` copies in parallel per
   round (capped by `max_parallel`).
3. `probes`: read-only state after the action.

Scenarios run one after another so they never share target state mid-flight;
one HTTP client serves the whole set.

## 5. Errors (ADR 0006)

Nothing the target does raises. Transport faults (timeout, connect) retry up to
2× with backoff; a received status is never retried. An unusable plugin is
refused before any model call (CLI) or as a non-retryable `StageError`
(Manager) — never a fallback to another plugin.

## 6. Eval

`agents/a05_execution/eval_dataset.jsonl`, 10 cases, 100% (M1-T4). Plugin
loading and the `rest_api` wrapper: `tests/test_execution.py`. Catch rate
unchanged by the move behind the plugin: 6 / 7 / 8 over five runs (#37).
