---
name: execution
description: Runs ECVT test scenarios against a live target system through a plugin and records every response verbatim — status, body, timing — without judging correctness. Trigger when scenarios exist and need to be run against a target, when a run must be repeated after a target change, or when a scenario failed to run and the cause must be identified.
---

# Execution Skill

Deterministic code, no model call (ADR 0009, 0018). The contract is the Plugin
ABC's `execute(scenarios)` (ADR 0013); `rest_api` and `wallet` implement it with
the Core runner `ecvt/execution.py`.

## When to use

- "Run these scenarios against the wallet API"
- "Re-run the suite, the target was redeployed"
- "Why did sc-007 not produce a result?"

Do not use for: generating scenarios (03), scoring results (06), deciding what
to run (01).

## Inputs required

- A ScenarioSet (`scenarios.jsonl`, `ecvt/schema.py`).
- A plugin by name (`--plugin`, default `rest_api`) and a `PluginContext`:
  `base_url`, `timeout_s`, `max_parallel`, plugin `options`.

## Interface

```
plugin = load(name, PluginContext(base_url=...))      # PluginError if unknown or a stub
records = await plugin.execute(scenarios)              # one ExecutionRecord per scenario
```

`execute` takes the whole set, so a plugin can share one client or session and
must keep scenarios that share target state from overlapping (`rest_api` runs
them one after another). It raises for nothing the target does.

## Steps

1. Load the plugin. Unknown or unimplemented → stop before anything is spent;
   never fall back to another plugin.
2. Per scenario, run `setup` in order; each step must answer 2xx and bind its
   `save` captures into `{name}` placeholders.
   - A setup step fails or a placeholder cannot be bound → `setup_failed`; the
     action is not attempted.
3. Fire the `action`: `repeat` rounds in series, `concurrency` copies in
   parallel per round (capped by `max_parallel`).
   - No attempt reaches the target → `action_error`.
   - Any HTTP status, 500 included → `completed`; the status is the finding.
4. Run the `probes` (read-only state after the action).
5. Record every `StepResult` as received: status, body, timing, attempts, error.

## Errors (ADR 0006)

- Transport faults (timeout, connect refused) retry up to 2× with backoff.
- A received status is never retried, whatever it is.
- The target being down produces records, not an exception; Evaluation reports
  them as not run, never as defects.

## Output

`executions.jsonl` (chain) or `runs/<run_id>/execution.json` (Manager): one
`ExecutionRecord` per scenario (`ecvt/results.py`) with `outcome`, every step and
the resolved `bindings`.

## Anti-patterns

- **Judging.** Marking a scenario "failed" because the target returned 500.
  The record says 500; Evaluation decides what that means.
- **Retrying a response.** Replaying a non-idempotent write hides the
  idempotency bugs the adversarial scenarios exist to find.
- **Repairing scenarios.** Editing a scenario so it runs turns a Generator
  defect into a silent pass.
- **Scoring in a plugin.** A plugin may hand a06 `domain_rules()`; the verdict
  stays in a06 (ADR 0013).
