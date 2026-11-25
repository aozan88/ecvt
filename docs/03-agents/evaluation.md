# Agent Spec — Evaluation (a06)

Code: `agents/a06_evaluation/`, `ecvt/scoring.py` · Version 1.1 (M3-T2) · 2026-11-25
· ADRs 0005, 0007, 0009, 0013

## 1. Role

Turns each execution record into a verdict — `pass`, `fail` (candidate
defect), `inconclusive`, `not_run` — with the reason. Rules first (free,
exact), the LLM judge only where rules cannot settle it. Evaluation is the only
place ECVT scores; plugins contribute rules, never scores (ADR 0013).

## 2. Pipeline position

Last stage: execution → **evaluation**. Entry points: the chain (`ecvt run`),
the Manager stage `run_evaluation`, and `python -m agents.a06_evaluation.agent
[--plugin <name>] [--no-judge]` for re-scoring a saved run.

## 3. Inputs / outputs

| | Contract |
|---|---|
| In | scenarios (for each `expectation`) + execution records, paired by id; the run plugin's `domain_rules` |
| Out | one `ScenarioScore` per scenario: verdict, `defect_suspected`, summary, a `rule` dimension (every check) and, when judged, a `judge` dimension |
| Artifact | `scores.jsonl` (chain) · `runs/<run_id>/evaluation.json` (Manager) |

`ground_truth.yaml` is not an input: catch rate is computed afterwards by
reporting (ADR 0007).

## 4. Rules

Generic, true of any HTTP API (`ecvt/scoring.py`): execution outcome · status
in `expected_status` · no 5xx · repeats rejected or not (inconclusive when all
accepted) · probes readable.

Domain rules come from the plugin (`Plugin.domain_rules()`), run on completed
executions beside the generic checks and fold the same way. A rule that raises
is recorded `inconclusive`. `rest_api` has none; `wallet` has 7:

| Rule | Fails when |
|---|---|
| `no_negative_balance` | a balance read after the action is < 0 |
| `transfer_amount_positive` | a transfer of 0 or less is accepted |
| `no_self_transfer` | a transfer to the same account is accepted |
| `amount_in_minor_units` | a sub-cent amount is accepted |
| `balance_in_minor_units` | a reported balance has more than 2 decimals |
| `replay_applied_once` | a replayed transfer (`repeat` ≥ 2) moves money more than once |
| `balances_reconcile` | the two accounts' total changes, or the source moved by no whole number of accepted transfers |

Each passes on a correct wallet, including one that answers a replay 2xx
without moving money twice.

## 5. Folding and the judge

Any `fail` → `fail`; else any `not_run` → `not_run`; else any `inconclusive`
→ `inconclusive`. The judge (Haiku, forced tool use, one verdict per
invariant) runs on `fail`, `inconclusive`, or `pass` with natural-language
invariants; at most 6 in parallel. The same folding applies across
dimensions: a judge `fail` makes the scenario `fail`; a judge `inconclusive`
over a rule `pass` makes it `inconclusive`, never `fail`.

## 6. Eval

`agents/a06_evaluation/eval_dataset.jsonl`, 15 cases (5 rule, 5 judge, 5
hybrid), 100% (M1-T6). Wallet rules: `tests/test_scoring.py`.
