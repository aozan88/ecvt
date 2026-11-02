---
name: evaluation
description: Scores an execution record against its scenario's expectation — deterministic rule checks first, then an LLM judge only where rules cannot settle it. Trigger when executions need scoring, when a run's verdicts must be explained, or when the catch rate against a known-defect baseline is being measured.
---

# Evaluation Skill

How ECVT turns raw responses into verdicts. Procedure, not background.

## When to use

- "Score these execution results"
- "Why was sc-004 marked inconclusive?"
- "Which of these runs found real defects?"

Do not use for: generating scenarios (03), running them (05), choosing what to
run (01), comparing against history (07, v1.1).

## Inputs required

- `scenarios.jsonl` — for each scenario's `expectation`
- `executions.jsonl` — what actually happened

`ground_truth.yaml` is **not** an input. It is read only when computing catch
rate in reporting, never while scoring (ADR 0007). A judge that knows the
answer measures nothing.

## Steps

1. **Pair by id.** A scenario with no execution record is skipped, not
   guessed at.
2. **Run the rules** (`ecvt.scoring.run_rules`). They are free, deterministic,
   domain-agnostic, and they run on everything:
   - `status_expected` — is the status one a correct implementation may return
   - `no_server_error` — a 5xx is a defect even if the scenario expected it
   - `replay_rejected` — were repeated writes all accepted
   - `probes_readable` — can state be read back at all
3. **Decide whether the judge is worth its cost** (`needs_judge`):
   - rules said FAIL or INCONCLUSIVE → judge, to say what it means
   - rules said PASS but invariants remain → judge, to weigh them
   - rules said PASS with nothing left to weigh → **skip**, no model call
   - nothing was observed (`NOT_RUN`) → **skip**, there is nothing to judge
4. **Judge independently.** Hand it the scenario, the expectation and the
   observation — never the rule verdict. An anchored second opinion is an echo.
5. **Combine.** A FAIL in any dimension carries; an INCONCLUSIVE never
   overturns a decided verdict. The summary comes from the failing rule check,
   or the judge's rationale when rules passed.
6. **Separate a broken scenario from a defect.** `NOT_RUN` means the scenario
   never reached its action: `defect_suspected` stays false and the reason
   names the setup step that failed.

## Verdict vocabulary

| Verdict | Means | `defect_suspected` |
|---|---|---|
| `pass` | behaved as a correct implementation should | false |
| `fail` | deviates from the stated correct behaviour | **true** |
| `inconclusive` | the observation does not settle it | false |
| `not_run` | no observation to score | false |

## Anti-patterns

- **Guessing an invariant.** Marking "source balance is still 50.0" as `pass`
  because it seems likely, with no probe that read it. That is `inconclusive`.
- **Trusting the status code.** A 200 is wrong when the request should have
  been rejected; a 422 is right when it should. Read the state.
- **Blaming the target for a broken scenario.** An unbound placeholder is the
  Generator's defect, not the system's.
- **Paying for a judgement on nothing.** A `NOT_RUN` scenario has no
  observation; a model call there buys a guess.
- **Reading ground truth while scoring.** It turns the measurement into a
  tautology.

<!-- TODO (M2/M3): similarity and semantic dimensions (Mapping v3); scoring
     moves behind the ecvt-eval MCP server in M3-T3. -->
