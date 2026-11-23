# Agent Spec — Manager (a01)

> STUB — max 3 pages (~120 lines). Code: `agents/a01_manager/`. Written by hand, in my own
> words: this is the interview answer to "walk me through your Manager agent".
> Remove this banner and every `<!-- TODO -->` block when done.

<!-- HOW TO FILL
     - Each section has: TODO (what to write) + EXAMPLE (one sample sentence in the right
       register). The example shows the shape, not the answer: rewrite it in your words.
     - Facts come from: agents/a01_manager/agent.py, DESIGN.md, tools.json,
       pipeline_state.schema.json, eval_dataset.jsonl, and ADRs 0005 / 0006 / 0009.
     - Link, do not paste: no full schemas or prompts here (3-page limit).
     - Test yourself: read each section aloud in under a minute. -->

## 1. Role

<!-- TODO (≤ 8 lines):
     - Single entry point: who calls the Manager, and who never talks to a stage directly.
     - The four things the model decides vs. what code decides. Why split it that way?
     - One sentence: is the Manager a workflow or an agent? (Agents and workflows)
     EXAMPLE: "The Manager is ECVT's only entry point: the CLI and n8n hand it a raw
     request, and it turns that request into a run. It decides what the user wants;
     code decides how that is carried out." -->

## 2. Inputs and outputs

<!-- TODO: a 3-column table, then one sentence on why the Manager sees only paths.
     | Direction | What | Defined in |
     - In: raw request + source (cli | n8n)
     - Out: runs/<run_id>/state.json
     - Per stage: artifact_ref + short summary (never the artifact itself)
     EXAMPLE row:
     | Out | Pipeline state, one file per run | `pipeline_state.schema.json` |
     EXAMPLE sentence: "Stage artifacts stay on disk; the Manager's context holds only
     their paths and a one-line summary, so a large scenario set never costs Manager tokens." -->

## 3. Routing: intents and plans

<!-- TODO: table of the 7 intents: intent | plan (stages) | required slots
     (source: INTENT_PLAN and REQUIRED_SLOTS in agent.py), then 2–3 lines on:
     - the three reasons out_of_scope covers
     - what happens below MIN_CONFIDENCE or when a required slot is missing
     EXAMPLE row:
     | domain_analysis | domain_intel | target_system, plugin |
     EXAMPLE sentence: "Routing classifies the request, not the output: choosing the
     plan is a different job from the Scenario Generator labelling its scenarios." -->

## 4. Order guarantees (three layers)

<!-- TODO (the heart of the doc): for each layer: what it stops + one concrete example.
     1. Prompt — sequential, one stage at a time, at most one retry.
     2. Stage tool preconditions — precondition() returns a reason as data ("blocked"),
        it does not raise. List the reasons and say why data beats an exception here.
     3. PreToolUse hook — only the matching subagent may call run_<stage>.
     Close with: which test in tests/test_manager_routing.py proves which layer,
     and which layer has no automated test yet.
     EXAMPLE (layer 2): "If the model calls run_execution while the plan is
     generate_scenarios, the tool answers {status: blocked, reason: stage_not_in_plan};
     the model reads that as a tool_result and must change course." -->

## 5. Prompt strategy

<!-- TODO (≤ 10 lines): link system_prompt.md and SKILL.md, then:
     - Which prompt-engineering techniques are used and one place each shows up
       (clear and direct, XML tags, examples).
     - Why the AgentDefinitions are thin adapters (ADR 0009), and their cost
       (~1 Haiku call per stage, ~4 per run; ADR 0005).
     EXAMPLE: "Each AgentDefinition has a five-line prompt and one allowed tool: it
     forwards the run_id and returns the tool's JSON verbatim. The real work happens in
     the tool, which calls the subagent through the Messages API." -->

## 6. Eval criteria

<!-- TODO: link eval_dataset.jsonl, then a small table: category | cases | scoring mode.
     - Success bar: pass rate ≥ 80% (G2) and, on a live run, four subagents in order.
     - Current result + date. Until the live run: write "not yet run against a model".
     The file today: 7 happy-path (5 keyword-match, 1 schema-valid, 1 llm-judge),
     3 edge (missing input, unknown plugin, stage failure), 2 negative (wrong domain,
     stage not available) — edge and negative are all llm-judge.
     EXAMPLE: "Happy-path routing is mostly code-graded: the intent name must appear
     in the output. Edge and negative cases need judgement, so a model grades them
     against a one-sentence rubric." -->

## 7. Dependencies, pipeline position, version

<!-- TODO (bullets):
     - Position: first in the pipeline (CLAUDE.md rule 4); calls a02 -> a03 -> a05 -> a06.
     - Runtime: Claude Agent SDK for the Manager, Messages API for subagents (ADR 0009).
       Pin the SDK version: the subagent tool is "Agent" in 0.2.172, "Task" before.
     - Models: Manager sonnet, adapters haiku (ADR 0005).
     - Limits: MAX_MANAGER_TURNS, MAX_ATTEMPTS — give the values and what each stops.
     - Spec version + date.
     EXAMPLE: "- MAX_ATTEMPTS = 2: first try plus one retry; a third call is blocked
     with max_attempts_reached." -->

## 8. Known limitations

<!-- TODO (≤ 5 bullets): what does not work yet, or was deferred on purpose.
     EXAMPLE: "- `plugin` is a free string checked against plugins/ at run time, not an
     enum, so the Manager stays domain-agnostic (one-line ADR pending)." -->
