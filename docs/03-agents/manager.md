# Agent Spec — Manager (a01)

Code: `agents/a01_manager/` · Version 1.1 (M4-T3) · 2026-11-27 · ADRs 0005, 0006, 0009, 0010, 0020

## 1. Role

The Manager is ECVT's only entry point. The CLI and n8n hand it a raw request; it turns
that request into a run and reports back. No caller talks to a stage directly.

Two modes (ADR 0020), one state file and one set of stage runners:

| | **planner** (default) | **agent** (`--manager-mode agent`) |
|---|---|---|
| Model decides | intent, slots; the clarifying question or redirect when a run cannot proceed | the same, plus each delegation, retry or stop, and the summary |
| Model calls | one strict `plan_run` (`planner.py`, `planner_prompt.md`) | an Agent SDK loop + one adapter subagent per stage (`agent.py`) |
| Code decides | plan, stage order, retries, `manager_output`, a templated summary (en / tr) | plan, preconditions, state; checks the model's `manager_output` |

Sequencing is a workflow (fixed by code); classifying a free-text request is the part
that needs a model. The planner leaves the model only that part.

## 2. Inputs and outputs

| Direction | What | Defined in |
|---|---|---|
| In | Raw request + source (`cli` \| `n8n`) and the plugin list | `render_request()` in `agent.py` |
| Out | Pipeline state, one file per run: `runs/<run_id>/state.json` | `pipeline_state.schema.json` |
| Out | Final answer (status, intent, stages run, summary) | `tools.json` › `manager_output` |
| Per stage | `artifact_ref` (a file path) + a one-line summary | `runners.py` |

Stage artifacts stay on disk. The Manager's context holds only their paths and summaries,
so a large scenario set never costs Manager tokens, and every run can be replayed from
its files.

## 3. Routing: intents and plans

| Intent | Plan (stages) | Required slots |
|---|---|---|
| `full_validation` | domain_intel → scenario_gen → execution → evaluation | target_system, plugin |
| `generate_scenarios` | domain_intel → scenario_gen | target_system, plugin |
| `domain_analysis` | domain_intel | target_system, plugin |
| `execute_scenarios` | execution → evaluation | scenario_set_ref, plugin |
| `evaluate_results` | evaluation | execution_ref |
| `run_status` | — (reads a stored run) | run_ref |
| `out_of_scope` | — (rejects with a redirect) | — |

`out_of_scope` has three reasons: `wrong_domain`, `stage_not_available` (Acceptance,
Regression, Reporting, Knowledge are not built yet) and `framework_request`. When a
required slot is missing, the plugin is unknown (ADR 0010) or confidence is below 0.6,
the run goes to `ask_user` with no plan: the Manager asks one clarifying question.

Routing classifies the *request*. It is a different job from the Scenario Generator
labelling its *output* with the seven scenario categories.

## 4. Order guarantees

In planner mode the stages are a loop in code (`planner._run_plan`) over
`precondition()`; the model is never asked about order. The agent loop has three layers:

1. **Prompt** — run stages one at a time, in plan order, at most one retry each.
   Stops the common case; a model can still drift, so it is not the only guard.
2. **Stage-tool preconditions** — `precondition()` runs inside every `run_<stage>` tool
   and returns a reason as data: `stage_not_in_plan`, `upstream_not_succeeded:<stage>`,
   `already_running`, `already_succeeded`, `max_attempts_reached`, `not_retryable`.
   Example: under `generate_scenarios`, a call to `run_execution` answers
   `{status: blocked, reason: stage_not_in_plan}`. Returning data instead of raising lets
   the model read the reason as a `tool_result` and change course (ADR 0006 in spirit).
3. **PreToolUse hook** — each `run_<stage>` tool may be called only by its own subagent;
   the Manager's main thread and any other subagent are denied.

`manager_output` is checked too: `status=completed` is refused unless every plan stage
succeeded. Layer 2 is covered by `tests/test_manager_routing.py` (routing, plan guard,
chaining, retry limit, no retry); layers 1 and 3 are covered by the eval cases.

## 5. Prompt strategy

`planner_prompt.md` (planner) holds the taxonomy, the tie-breakers and the slot rules,
with two examples tested against the strict `plan_run` schema. `system_prompt.md`
(agent loop) is short and structured with XML tags (`<role>`, `<task>`,
`<constraints>`, `<output_format>`, `<examples>`): the request arrives as `<run_id>`, `<available_plugins>`, `<request>`, and
two worked examples show the call order. `SKILL.md` holds the intent definitions and
slot rules. Rules are stated directly ("never invent slot values; copy the plugin exactly
from `<available_plugins>`").

Each `AgentDefinition` is a thin adapter (ADR 0009): a five-line prompt and exactly one
allowed tool. It forwards the `run_id` and returns the tool's JSON verbatim; the real work
runs in the tool, which calls the stage through `runners.py`. Cost: one Haiku call per
stage, about four per full run, on top of the Manager's own Sonnet turns (ADR 0005). To
be re-checked once run logs show the real numbers.

## 6. Eval criteria

`eval_dataset.jsonl`, 12 cases, run with stub stages so that only the Manager's
decisions are measured (`ecvt/evals.py` › `adapt_manager`).

| Category | Cases | Scoring |
|---|---|---|
| happy-path | 7 | 5 keyword-match (intent name), 1 schema-valid (`manager_output`), 1 llm-judge (call order) |
| edge | 3 — missing input, unknown plugin, stage failure | llm-judge |
| negative | 2 — wrong domain, stage not available | llm-judge |

Optional `setup` per case: `seed_runs` (a stored run for `run_status`) and `stub_runners`
(fault injection, e.g. `scenario_gen: fail_retryable`).
Success bar: pass rate ≥ 80% (G2), and one live `full_validation` run where all four
subagents run in order. Status: not yet run against a live model.

## 7. Dependencies and limits

- Position: first in the pipeline (root `CLAUDE.md` rule 4); dispatches a02 → a03 → a05 → a06.
- Runtime: planner — Messages API, one strict call (ADR 0020); agent loop — Claude Agent
  SDK (`claude-agent-sdk==0.2.187`, delegation tool `Agent`, ADR 0009). Subagents:
  Messages API.
- Models: planner `ECVT_MANAGER_MODEL`; agent loop `sonnet` with `haiku` adapters;
  subagents `ECVT_SUBAGENT_MODEL` (Haiku).
- `MAX_ATTEMPTS = 2` — first try plus one retry; a third call is blocked.
- `MAX_MANAGER_TURNS = 24` — hard stop; `finalize_guard` closes an unfinished run as failed.
- Execution target: `ECVT_BASE_URL` (default `http://localhost:8000`).

## 8. Known limitations

- `plugin` is a free string checked against `plugins/` at run time (ADR 0010).
- Execution and evaluation load the `plugin` slot (M3-T1, M3-T2): execution runs through
  `plugin.execute`, evaluation adds the plugin's domain rules. An unusable plugin is a
  non-retryable stage failure.
- Agent loop only: the adapter layer adds ~4 Haiku calls per run.
- Planner summaries are templates in English and Turkish; other languages get English
  (the clarifying question and redirect are the model's, in the request's language).
- No resume: a failed run is re-run from the start, not continued.
