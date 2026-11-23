# Agent Spec — Manager (a01)

> STUB — max 3 pages (~120 lines). Code: `agents/a01_manager/`. Written by hand, in my own
> words: this is the interview answer to "walk me through your Manager agent".
> Remove this banner and every TODO line when done.

## 1. Role

<!-- TODO (≤ 8 lines): What the Manager does and, just as important, what it does not do.
     - Single entry point: CLI / n8n -> Manager -> stages. Nobody talks to a stage directly.
     - The four decisions the model makes (intent, slots, retry-or-stop, summary)
       vs. what code decides (plan, order, preconditions, state). Why split it that way?
     - One sentence: workflow or agent? (Agents and workflows › Workflows vs agents) -->

## 2. Inputs and outputs

<!-- TODO: a small table, then links — do not paste the schemas here.
     | Direction | What | Where it is defined |
     - Input: raw request + source (cli | n8n)
     - Output: runs/<run_id>/state.json  -> agents/a01_manager/pipeline_state.schema.json
     - Per stage: artifact_ref (a file path) + short summary — never the artifact itself.
       Why does the Manager only see the path? (context size; Introduction to subagents) -->

## 3. Routing: intents and plans

<!-- TODO: the 7 intents as a table: intent | plan (stages) | required slots.
     Source: INTENT_PLAN and REQUIRED_SLOTS in agent.py.
     - How out_of_scope splits into its three reasons.
     - What happens when confidence < MIN_CONFIDENCE or a slot is missing. -->

## 4. Order guarantees (three layers)

<!-- TODO (the heart of the doc): for each layer, one line "what it stops" + one example.
     1. Prompt — sequential, one at a time, at most one retry.
     2. Stage tool preconditions — precondition() reasons: stage_not_in_plan,
        upstream_not_succeeded:<stage>, already_running, already_succeeded,
        max_attempts_reached, not_retryable. Returned as data ("blocked"), not raised. Why?
     3. PreToolUse hook — only the matching subagent may call run_<stage>.
     Point to tests/test_manager_routing.py: which test proves which layer? -->

## 5. Prompt strategy

<!-- TODO (≤ 10 lines): link agents/a01_manager/system_prompt.md and SKILL.md.
     - Which prompt-engineering techniques it uses (clear/direct, XML structure, examples)
       and one place each shows up.
     - Why AgentDefinitions are "thin adapters" (ADR 0009) and what they cost:
       ~1 Haiku call per stage, ~4 per run (ADR 0005). Open question: measure, then decide. -->

## 6. Eval criteria

<!-- TODO: link agents/a01_manager/eval_dataset.jsonl (12 cases).
     - Categories covered and the scoring mode of each (code-based vs model-based).
     - Success bar: pass rate ≥ 80% (G2), plus "4 subagents run in order" on a live run.
     - Current result: not yet run against a live model — fill in after the run, with date. -->

## 7. Dependencies, pipeline position, version

<!-- TODO:
     - Position: first in the pipeline (CLAUDE.md rule 4); calls a02 -> a03 -> a05 -> a06.
     - Runtime: Claude Agent SDK (pin the version — the subagent tool is "Agent" in 0.2.172,
       "Task" before) + Messages API for subagents. Models: Manager sonnet, adapters haiku.
     - Limits: MAX_MANAGER_TURNS, MAX_ATTEMPTS.
     - ADRs: 0005, 0006, 0009. Spec version + date. -->

## 8. Known limitations

<!-- TODO (≤ 5 bullets): what does not work yet or was deferred
     (e.g. plugin is a free string checked against plugins/, not an enum). -->
