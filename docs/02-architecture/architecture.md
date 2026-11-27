# Architecture

> v1.5 · 2026-11-26 · Max 10 pages. The M1 chain (Scenario → Execution →
> Evaluation) and the M2 additions — Manager (a01) and Domain Intelligence
> (a02) — are all live on `main` (PRs #25, #26); M3 adds the plugin layer
> (M3-T1 #37, M3-T2 #38) the ecvt-eval MCP server (M3-T3) and the Dev + Business reports (M3-T4) and the n8n trigger (M3-T5); v1.1 adds the browser UI on `ecvt serve` (ADR 0016). Stage contracts, context passing and error
> handling: [`pipeline.md`](pipeline.md).

## 1. System context

```mermaid
flowchart LR
    N[n8n webhook] -->|POST /runs?wait=true| TS[ecvt serve]
    B[Browser UI] -->|POST /runs · poll GET /runs/id| TS
    TS -->|ecvt run| U
    U[CLI] -->|request text| M[Manager a01<br/>agent · Sonnet]
    M -->|plan from intent| DI[Domain Intelligence a02]
    DI --> SG[Scenario Generator a03]
    SG --> EX[Execution a05]
    EX --> EV[Evaluation a06]
    EV <-->|MCP stdio: score_response| ES[ecvt-eval<br/>rule scoring]
    EX -->|plugin.execute| P[Plugin<br/>rest_api · wallet]
    P <-->|HTTP| T[(Target system<br/>e.g. wallet-api)]
    P -.->|domain_rules| ES
    M <-->|state.json, artifacts| R[(runs/run_id/)]
    EV -->|scores| RP[Reports<br/>Dev · Business · catch rate]
    RP --> U
```

ECVT validates an existing system; it never generates product code.
System-specific knowledge lives in `plugins/`, never in an agent (root
`CLAUDE.md` rule 5).

## 2. Components

| Component | Job | Model call | Code |
|---|---|---|---|
| CLI | Entry point. `ecvt run` runs the fixed chain; `--manager` hands the request to the Manager. `--plugin <name>` (default `rest_api`) is resolved on both paths before any model call. `ecvt report <run_dir>` writes the Dev + Business reports for a finished run. `ecvt serve` is the HTTP trigger | none | `ecvt/cli.py` |
| Trigger + UI (`ecvt serve`) | `POST /runs` starts `ecvt run … --report --status-file` in a subprocess: 202 + `run_id`, the run goes on in the background; `?wait=true` answers with the finished run (n8n). `GET /` single-page UI, `GET /runs` history, `GET /runs/<id>` status + step, the two HTML reports, `GET /targets`. Token beyond loopback (all but `/` and `/health`), one run at a time, target base URL fixed server-side, no catch rate in what the UI reads (ADR 0007, 0016) | none | `ecvt/trigger_server.py`, `ecvt/ui.html`, `workflows/n8n/` |
| Manager (a01) | Classify intent, extract slots, decide retry/stop, write the summary | Agent SDK `query()`, Sonnet | `agents/a01_manager/` |
| Domain Intelligence (a02) | OpenAPI spec → DomainModel (entities, workflows, rules, gaps) | Messages API, forced tool use, Haiku | `agents/a02_domain_intelligence/` |
| Scenario Generator (a03) | Spec → ScenarioSet across 7 categories (the DomainModel is not passed in; ADR 0012) | Messages API, forced tool use, Haiku | `agents/a03_scenario_generator/` |
| Execution (a05) | Fire each scenario at the target through the run's plugin, record what happened | **none** | `plugins/<name>/` → `ecvt/execution.py` |
| Evaluation (a06) | Rule dimension from ecvt-eval, then LLM-as-judge on what rules cannot decide; folds both into the verdict | Messages API, Haiku (judge) | `agents/a06_evaluation/` |
| ecvt-eval (MCP server) | `score_response`: generic rule checks plus the plugin's domain rules; `list_rules`. Stdio subprocess of a06, also usable by any MCP client | **none** | `ecvt/eval_server.py`, `ecvt/scoring.py` (ADR 0014) |
| Plugins | How a kind of target is reached (`execute`) and, optionally, its domain rules (`domain_rules`). Never scores. Registry = directories under `plugins/` | none | `plugins/` (ADRs 0003, 0010, 0013) |
| Reports | Dev + Business reports (Markdown + HTML) from one run directory; no ground truth, so they work for any target. The demo's catch rate (`report.md`) is separate and the only reader of ground truth. Code, not the a08 agent (v1.3, M6) | **none** | `ecvt/audience_reports.py`, `ecvt/reporting.py` |
| Eval harness + gates | Agent eval datasets, milestone gates | judge calls only | `scripts/eval_runner.py`, `scripts/gate_check.py` |

Model choice: ADR 0005. Why subagents use the Messages API and only the
Manager uses the Agent SDK: ADR 0009.

## 3. Mechanism map — which pattern, where, why

The course vocabulary (Anthropic, "Agents and workflows") mapped onto ECVT.

| Mechanism | Where in ECVT | Why this choice |
|---|---|---|
| **Workflow vs agent** | Only the Manager is an agent. Every subagent is one model call | Use an agent only where the next step is not known in advance. Here that is intent and retry/stop; everything else is fixed |
| **Chaining** | domain_intel → scenario_gen → execution → evaluation, enforced by `INTENT_PLAN` and `precondition()` in code | Each stage needs the previous stage's output. Order is known up front, so code holds it, not the model |
| **Routing** | Manager `record_intent`: 7 intents, each mapped to a subset of the chain | Requests differ in kind; classification decides which stages run (e.g. `generate_scenarios` runs only domain_intel → scenario_gen) |
| **Parallelization — sectioning** | Evaluation judges scenarios in parallel, at most 6 at a time (`DEFAULT_JUDGE_PARALLEL`) | Each score is independent of the others, so order does not matter. Gain is wall-clock time; the cap of 6 protects rate limits |
| **Parallelization — voting** | Not used yet. Proposed: run each a02 eval case 3× and average | Repeating the same task lowers variance. This is the variant that improves consistency, not sectioning |
| **Evaluator–optimizer** | a02: the Pydantic validator rejects the output and its error goes back to the model verbatim as `tool_result` with `is_error=true`, one retry (M2 design) | A code-based evaluator is cheap and exact; one retry bounds the cost |
| **Environment inspection** | Each `run_<stage>` tool reads `state.json` first; off-plan, out-of-order or exhausted calls return `blocked` | The model's decision is checked against real state before anything runs |
| **Guardrails** | `MAX_MANAGER_TURNS = 24`, `MAX_ATTEMPTS = 2`; `finalize_guard`; a false "completed" is rejected | Stops runaway loops and infinite retry; the model's "done" is verified by code (ADR 0006 spirit) |
| **Hook (PreToolUse)** | `run_<stage>` may be called only by its own subagent; the Manager calling it directly is denied | Authority boundaries are enforced in code, not left to the prompt |
| **Context isolation** | Artifacts are written to `runs/<run_id>/`; the Manager sees only `artifact_ref` + a ≤500-char summary | Keeps the Manager's context small across the whole pipeline (D5) |
| **Structured output** | Pydantic wire models, `extra="forbid"`, no defaults; forced `tool_choice` | A field optional in the schema gets skipped even if the prompt asks for it (a03 lesson) — required means required in the schema |
| **Model routing** | Manager Sonnet; subagents and AgentDefinition adapters Haiku | Hard decisions on the strong model, volume on the cheap one (ADR 0005) |
| **Prompt caching** | Long, repeated system blocks (e.g. SKILL.md) carry `cache_control` | Cuts repeated cost; below the model's minimum length the cache silently does not apply |
| **Plugin seam** | Core reaches a target only through `Plugin.execute(scenarios)`; a domain plugin adds `domain_rules` that a06 runs beside its own checks | Domain-agnostic Core (rule 5). The plugin knows the rule, a06 decides the verdict; `send(step)` waits for a second backend (ADR 0013) |
| **MCP server (custom)** | a06 scores rules through `ecvt-eval` over stdio, one session per run; no in-process fallback | Scoring becomes a tool any MCP client can call with ECVT's own rules; the judge, which spends money, stays inside a06 (ADR 0014) |
| **Concurrency as a test probe** | Execution fires one request N times at once (`scenario.concurrency`) | Not an LLM pattern: it stresses the target to expose race conditions |

## 4. Who decides what

| Layer | Decides | Where |
|---|---|---|
| Model (Manager) | intent, slots, retry or stop, summary | `system_prompt.md` |
| Code | plan, order, preconditions, attempt count, all state writes | `agents/a01_manager/agent.py` |
| AgentDefinition (Haiku) | nothing — thin dispatch adapter required by ADR 0009 | `build_subagents()` |
| Subagent | the domain work of one stage | a02 / a03 / a05 / a06 |

## 5. Data and state

- M1 CLI run: `scenarios.jsonl`, `executions.jsonl`, `scores.jsonl`, `run.json`
  (tokens, cost, timing, plugin) in the output directory. A reused scenario set
  is copied in, so a run directory stands on its own.
- `--report` (or `ecvt report <run_dir>`) adds `report-business.{md,html}` and
  `report-dev.{md,html}`; with a baseline, also `report.md` (catch rate).
- M2 Manager run: `runs/<run_id>/state.json` (schema
  `agents/a01_manager/pipeline_state.schema.json`), one JSON artifact per stage.
  Only code writes state.

## Open items

- [ ] Persistent stores beyond files (Redis / Postgres) — not decided; no ADR yet
- [ ] Live `full_validation` sequence diagram after the M2 merge
