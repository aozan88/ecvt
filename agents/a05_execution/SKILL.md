---
name: execution
description: Runs ECVT test scenarios against a live target system over HTTP and records every response verbatim — status, body, timing — without judging correctness. Trigger when scenarios exist and need to be run against a target, when a previous run must be repeated after a target change, or when a scenario failed to run and the cause must be identified.
---

# Execution Skill

DRAFT skeleton (M1-T4). The runner itself is implemented and tested
(`ecvt/execution.py`); this file becomes the agent-facing procedure in M2.

## When to use

- "Run these scenarios against the wallet API"
- "Re-run the suite, the target was redeployed"
- "Why did sc-007 not produce a result?"

Do not use for: generating scenarios (03), scoring results (06), deciding what
to run (01).

## Inputs required

- `scenarios.jsonl` from the Scenario Generator
- Target base URL

## Steps

1. Read and validate the scenarios. A malformed line stops nothing else.
2. For each scenario: run `setup` in order, bind `save` captures, fire the
   `action` (`repeat` in series, `concurrency` in parallel), run the `probes`.
3. Record every response as-is. On no response, retry up to the limit and then
   record the failure — do not retry a received status.
4. Write `executions.jsonl` and report the outcome mix.

## Anti-patterns

- **Judging.** Marking a scenario "failed" because the target returned 500.
  The record says 500; Evaluation decides what that means.
- **Retrying a response.** Replaying a non-idempotent write hides the
  idempotency bugs the adversarial scenarios exist to find.
- **Repairing scenarios.** Editing a scenario so it runs turns a Generator
  defect into a silent pass.

<!-- TODO before this file leaves draft:
     - decision criteria in Steps (if/else), per the agent standard
     - plugin hand-off once M3-T1 moves the runner behind the Plugin ABC
     - templates section
-->
