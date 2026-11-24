# Changelog

All notable changes to ECVT are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]
Fixes to the gate itself, found by running G2, and M3's first P0 work. They land
after `v0.5.0` and are not part of it: the tag records what was released, so it
stays where it is.

### Changed
- a03's wire schema is a union discriminated by `category` (ADR 0011).
  `StressScenarioDraft` requires `concurrency` at 2 or more — a race is what the
  category probes, so the schema rules out the value that makes the label a lie.
  `AdversarialScenarioDraft` requires both numbers without forcing either above
  1, because the category is wider than replay. The other five categories carry
  neither field and code supplies 1 and 1. At the default quota the model states
  8 numbers instead of 28. A plain scenario carrying the fields anyway is
  tolerated at 1 and refused above it, with the correction naming the category
  rather than the field. `missing_execution_modes()` stays as the set-level net
  for a writing set in which nothing replays.
- The Manager loads no project settings (`setting_sources=[]`). `"project"`
  pulled the repository's own CLAUDE.md files into its context every turn:
  development-coach rules written for whoever is building ECVT, "Turkish
  responses" among them, which cost tokens and contradict the Manager's own
  prompt.
- `run.json` carries `usage.manager_model_usage` — tokens and cost per model
  inside the Manager's session. `manager_cost_usd` stays and is their total;
  this says which model spent it.
- `setup/Gemini.md` removed: a STUB for an optional install of a tool the stack
  does not use (ADR 0001), linked from nothing.

### Fixed
- `scripts/gate_check.py` tested `requires_env` against `os.environ` without
  reading `.env`. With the key sitting there it skipped five of G2's nine checks
  and still printed a verdict, reporting the milestone as not closable for the
  wrong reason. It now calls `ecvt.llm.load_env` before any check runs.
- Every gate verified its tag with `git tag -l`, which reads only the local
  repository, so it passed for a tag that had never left the machine — a release
  gate reporting a release no one else can see. G0-7, G1-8 and G2-9 now use
  `git ls-remote --tags origin`.

### Measured
a03's `repeat`/`concurrency` omission rate, wallet-api on `claude-haiku-5-5`,
`scripts/measure_a03.py`. "Before" is the single wire model that asked for both
numbers on every scenario; "after" is the category union.

| Arm | | Valid sets | First attempt | `Field required` in | Cost / run |
|---|---|---|---|---|---|
| With a02's DomainModel | before | 6 / 8 | **0 / 8** | 8 / 8 | $0.0092 |
| With a02's DomainModel | after | **8 / 8** | **5 / 8** | 3 / 8 | **$0.0052** |
| Spec only | before | 8 / 8 | 4 / 8 | 4 / 8 | $0.0048 |
| Spec only | after | 15 / 16 | 8 / 16 | 7 / 16 | $0.0051 |

The union is a clear win on the path it was aimed at and neutral on the other.
The mechanism is visible in the cost: three branches each embed the scenario
core, so the tool schema grows from 2554 to 3869 input tokens (+51%), a fixed
charge on every call. The saving is the attempts not spent. The Manager path had
attempts to save and ends 43% cheaper; the spec-only path had few, so the larger
schema shows up as about 6% more per run. The spec-only arm is 16 runs because
the first 8 read 7/8 and 3/8 — a borderline draw that the second 8 (8/8, 5/8)
puts back at the rate before the change.

It reduces the omission rather than removing it: `Field required` still appears
in 3 of 8 Manager-path generations.

a03's eval does not separate the two, and the reason is worth recording. Two
runs on each side, bar 80%:

| | Run 1 | Run 2 | Pooled |
|---|---|---|---|
| Single wire model (`main`) | 90% | 70% | 16 / 20 |
| Category union | 80% | 70% | 15 / 20 |

`main` itself fails the bar in one run of two, and the dominant failure in all
four runs is a generation exhausting its four attempts — the same omission the
union is about. One case over 20 is not a difference at this sample size. The
useful finding is about the gate rather than the change: a single run of a03's
eval is not a reliable 80% check, because its floor moves with the omission rate.

Restoring the defaults was tried as a fix and reverted. Without them the model
fills 1 everywhere, the set-level check rejects the set, and it does not correct
when told: 3 of 6 generations exhausted four attempts on `no_race_or_replay`.
The required fields prevent the omission; the set-level check only detects it.

Dropping the Manager's project settings cut its eval's spend: 24,809 tokens and
$0.0033 before, 14,856 and $0.0019 after, at 12/12 both ways. One run each, and
the turn count moved too, so the proportion is not exact — but the input-token
drop is what removing a static block from context looks like.

## [0.5.0] - 2026-11-24
M2 — Domain Intelligence and the Manager; five agents end to end.

Gate G2: 8 of 9 checks pass, the ninth being this tag. a01 eval 100% (12/12),
a02 eval 83% (10/12), both over the 80% bar. One live `ecvt run --manager`
completes all four stages in order at a catch rate of 6/10.

M2-T2 (does a02's DomainModel raise the catch rate?) is **measured and
inconclusive**: over three runs a side, M1 read 5 / 5 / 7 and M2 read 5 / 7
with one run failing — the bands coincide and M2's higher median is an artefact
of having two completed runs rather than three. The clear difference is cost,
about 7× M1 per run. Separating the two needs 6–10 runs a side.

Known limitation: `ScenarioDraft` requires `repeat` and `concurrency` per
scenario by design, which at the default quota is 28 numbers the model must
state, and Haiku omits some often enough that roughly one generation in three
exhausts a03's four attempts. That makes a Manager run probabilistic — measured
at about 3 in 4 — and is the first thing to address in M3.

### Added
- `a02_domain_intelligence`: OpenAPI → `DomainModel` (entities, operations with
  idempotency / authorization / ownership verdicts, workflows, rules, gaps, glossary)
  or a structured `DeclineTask`. Operation inventory built in code; output validated
  against the spec (coverage, method/path, evidence pointers); up to two corrections,
  because the two validation layers are reported in sequence, not together.
- `a01_manager`: Agent SDK orchestrator — 7 intents, plan derived in code, stage
  preconditions, PreToolUse hook, pipeline state per run; `runners.py` wires a02/a03/a05/a06.
- `ecvt run --manager`: the run through the Manager; artifacts exported to the M1 files.
- a03 takes a02's rules, gaps and workflows as optional input (M2-T2).
- `scripts/n_run.py`: catch rate over N runs (min / median / max).
- Evals: a01 (12, stub stages, fault injection) and a02 (12) datasets and adapters.
- `gates/M2.yaml`; ADR 0010 (plugin slot is a string); `/test` and `/review` commands.

### Changed
- `ecvt.llm.structured_call`: several tools (`tool_choice: any`) and system blocks.
  A rejected attempt now stays in the conversation and the validator error comes
  back as its `tool_result`, so a correction edits the model's own object instead
  of regenerating it from nothing.
- Stage tool calls raise the CLI's 60s MCP ceiling (`MCP_TOOL_TIMEOUT`); a stage
  is a whole subagent run. A call cancelled anyway is recorded as failed and
  retryable rather than left `running`, which used to block the Manager's retry
  with `already_running`.
- A stage failure records the last validator error, not only the attempt count.
- `ecvt run --manager` refuses `--scenarios`, `--no-judge`, `--per-category`,
  `--timeout` and `--max-parallel` instead of accepting and ignoring them.
- Every entry point that needs a key reads `.env` (`ecvt.llm.load_env`); before,
  only the eval runner did, so `ecvt run` needed the key exported by hand.
- Eval results keep enough detail to name the rule or rubric clause that failed.
- `claude-agent-sdk` pinned to 0.2.187; `jsonschema` added.

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
- `pyproject.toml` declared no dependencies, so `pip install -e .` produced
  an environment where every async test errored. Runtime and dev
  dependencies are declared, `requirements.txt` installs them, and
  `conftest.py` names the cause when the async plugin is absent.

## [0.0.1] - 2026-10-23
### Added
- Initial repository scaffold (Repository Structure — FINAL, Mapping v3).
- Draft `CLAUDE.md` hierarchy (root + per-agent).
- Agent folders prefixed with Claude Project codes (01–09).
