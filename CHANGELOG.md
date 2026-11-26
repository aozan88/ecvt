# Changelog

All notable changes to ECVT are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Browser UI on `ecvt serve` (v1.1).** `GET /` serves one page
  (`ecvt/ui.html`, HTML + CSS + vanilla JS, no build step): pick target and
  plugin, judge on/off, Manager as an option (chain stays the default), Run,
  live step indicator, the Business recommendation when it ends, Business/Dev
  report in a sandboxed viewer, run history. Light/dark, no horizontal scroll
  at 390 px. With a token configured the page asks for it once, keeps it in
  localStorage and sends it as `X-ECVT-Token`, never in a URL.
- **New endpoints.** `GET /targets` (directories under `examples/` with
  `openapi.json`, plus plugins that load — stubs are left out), `GET /runs`
  (newest first, from each run's `job.json`), `GET /runs/<id>` (status, step,
  summary), `GET /runs/<id>/report-business.html|report-dev.html`. Run ids are
  12 hex characters; nothing else in a run directory is served (no report.md,
  so no catch rate in the UI — ADR 0007). `/health` adds `auth`.
- **`ecvt run --status-file PATH`**: the current step as JSON, rewritten
  atomically at each step, chain and Manager alike (ADR 0016).

### Changed
- **`POST /runs` is asynchronous**: 202 + `run_id`, the run continues in the
  background. `POST /runs?wait=true` keeps the 1.0 answer; the n8n workflow
  now calls it — re-import `workflows/n8n/ecvt-run.json` after upgrading.
- ADR 0016 — async runs and the status file.

## [1.0.0] - 2026-11-25
M3 — plugins, the ecvt-eval MCP server, Dev + Business reports, the n8n
trigger and the landing page. ECVT runs against any OpenAPI target through a
plugin, scores through its own MCP server, and answers a webhook with a release
recommendation.

Gate G3: 13 of 14 checks pass before the tag, the fourteenth being the tag
itself. Evals: a03 86.7% pooled over 3 runs (26/30), a05 100% (10/10), a06 100%
over 3 runs (45/45); pytest 340; both plugins, ecvt-eval over stdio, the n8n
webhook and the landing page verified live. The demo video is deferred
(ADR 0015). Catch rate on the demo target, M1 chain with the wallet
plugin: median 7/10 over five runs (range 6–8). First run against a real
third-party API (Swagger Petstore v3) found four defects, each confirmed by hand.

### Added
- **Plugin layer (M3-T1, #37).** `plugins/_base/plugin.py`: `Plugin` ABC
  (`execute(scenarios)` required, `discover_domain()` optional), registry =
  directories under `plugins/`, `load(name)` failing on an unknown or
  unimplemented plugin. `rest_api` wraps the M1 runner unchanged. `--plugin`
  was accepted and ignored before; it now selects execution on the chain and in
  the Manager, resolved before any model call. ADR 0013: `execute` stays at set
  granularity for v1.0, `send(step)` waits for a second backend,
  `validate_response` removed.
- **Wallet domain plugin (M3-T2).** `Plugin.domain_rules()` hands a06 rules it
  runs beside its own checks; the plugin knows the rule, a06 decides the
  verdict. `plugins/wallet` executes as `rest_api` and adds 7 rules: no
  negative balance, amount > 0, no self-transfer, amounts and balances in whole
  cents, a replayed transfer moves money once, balances reconcile. Each passes
  on a correct wallet, including an idempotent one; none reads ground truth.
  `ecvt run --plugin wallet`, the Manager's evaluation stage and
  `python -m agents.a06_evaluation.agent --plugin` apply them; `n_run` gains
  `--plugin`.
- **ecvt-eval MCP server (M3-T3, ADR 0014).** `python -m ecvt.eval_server`
  (stdio) exposes `score_response(scenario, execution, plugin)` — the generic
  rule checks plus the plugin's domain rules — and `list_rules`. a06 now gets
  every rule dimension from it, one session per run; the judge stays in a06.
  No in-process fallback: a server that cannot start fails the scoring
  (`EvalServerError`). Over 10 recorded runs, 282 scorings via stdio match the
  in-process rules exactly.
- **Landing page (M3-T6).** `landing/index.html` replaces the stub: what ECVT
  does, the measured numbers (median 7/10 over five runs, 20–40 s and ~$0.01
  per run, zero defects reported against a down target), how the five agents
  divide the work, what ships in v1.0, and a quick start. `landing/sample/`
  holds the Business and Dev reports of a real recorded run, linked from the
  page. `.github/workflows/pages.yml` publishes `landing/` to GitHub Pages on
  push to `main`. The demo video is deferred (ADR 0015); G3-12 checks the page
  and both sample reports are live.
- **n8n trigger (M3-T5).** `ecvt serve` (standard library, no new
  dependency): `POST /runs` runs `ecvt run … --report` in a subprocess and
  answers with status, run directory, the Business recommendation, verdicts,
  catch rate and cost; `GET /health`. A token is required beyond loopback, one
  run at a time (409), the target must be a directory under `examples/` with a
  spec, unknown body fields are refused, and the target's base URL is the
  server's setting. `workflows/n8n/ecvt-run.json`: Webhook → HTTP Request →
  Respond to Webhook, passing ECVT's status and body through. Verified with
  n8n 2.49.6 both by `docker run` (G3-11, now a real check) and by the
  `docker compose` steps in `setup/n8n.md`. `docker-compose.yml`'s n8n
  service drops the `N8N_BASIC_AUTH_*` settings n8n 1.0 removed and gains
  what the workflow needs.
- **Dev + Business reports (M3-T4).** `ecvt report <run_dir>`, and
  `ecvt run --report`, write `report-business` and `report-dev` as Markdown and
  HTML from one document model, with no model call and no ground truth, so they
  work for any target. Business: a release recommendation by worst severity,
  findings in plain words, what was tested by area, what was not settled, cost.
  Dev: failing checks with expected/observed, the judge's verdict and
  rationale, a request/response repro per finding, inconclusive and not-run
  scenarios with their reason. Where the judge passed a scenario a rule failed,
  the rule's finding is shown, not the judge's account. `run.json` now records
  the plugin, and a reused scenario set is copied into the run directory. G3-10
  is a real check now.
- **`gates/M3.yaml` (G3).** 14 checks for the Master Plan's six G3 items:
  plugins by name, both plugins run live, ecvt-eval over stdio and a06 with no
  in-process rule path, evals (a03 and a06 pooled over 3 runs), pytest, catch
  rate, docs without STUB, CHANGELOG 1.0.0, tag `v1.0.0` on the remote. The
  report, n8n and landing items are placeholders that fail until M3-T4..T6
  replace them. `gate_check.py --only <ids>` runs a subset.
- Architecture docs follow the plugin layer: `architecture.md`, `pipeline.md`,
  agent specs for a05 and a06 (were stubs), `docs/07-plugins/`. `pipeline.md`
  listed ground truth as an input of evaluation; it is not (ADR 0007).

### Changed
- **a03 no longer receives a02's DomainModel** (ADR 0012, option B). Handed
  to a03 it showed no catch-rate benefit and once taught it a wire field name
  the target does not use. a02 still runs in `full_validation` and its
  DomainModel stays an artifact of the run. `ECVT_A03_DOMAIN_MODEL=on` restores
  M2-T2 for a larger measurement; `run.json` records which arm a run was in as
  `a03_domain_model`.
- `eval_runner --runs N` judges a dataset on its pooled rate over N runs and
  lists each case as stable pass, flaky or stable fail; G2-4 runs a02 three
  times. Gate checks now run under `bash -e -o pipefail` — before, G1-4 and
  G2-4 passed with the eval runner exiting 1 below the bar.
- a03's few-shot no longer uses the target it is measured on. The example was
  wallet-api's own three endpoints carrying a scenario titled "Transfer above
  available balance" — GT-03 in the demo target's ground truth, so the prompt
  handed the generator one of the ten defects it is scored on finding (root
  `CLAUDE.md` rule 5). It is now a library-lending surface with three
  scenarios, one per branch of the category union. The domain-specific wording
  that travelled with it ("exact-balance", "Money, counters and quantities",
  the sum-of-balances example of a checkable invariant) is generalised, in
  `SKILL.md` too, which carried the same leak in its category table and in an
  anti-pattern illustrated with GT-03.
- `save` now has a stated rule: it maps a name you choose to a field in the
  **response body**, and the field name does not come from the path parameter
  that will carry it. The old example demonstrated this by accident and never
  said it, so moving the example broke it silently — see Measured.
- a03 asks for 16000 output tokens rather than `structured_call`'s 8192. A set
  of 14 scenarios runs past 8192, and over the limit `scenarios` comes back as
  an unterminated JSON string that `coerce_json_strings` cannot parse; the
  validator then reports "Input should be a valid list", which names nothing.
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
- G3-12 failed intermittently: `curl | grep -q` under `pipefail` fails whenever
  grep matches and exits before curl has written everything (curl exit 23). It
  passed in the release run and failed three times in a row on the merged
  commit against the same live page. The check now fetches, then searches; a
  test rejects `| grep -q` and `| head` in every gate file.
- **Found on a live target (Swagger Petstore v3, run locally, unmodified).**
  Two ECVT faults the demo target never exposed:
  - `probes_readable` failed every 4xx read-back. A rename scenario was marked
    a defect because the old username answered 404 — the correct answer. A 4xx
    probe is now inconclusive (the invariants and judge decide); a 5xx or no
    answer still fails. Re-scoring 10 recorded wallet runs changes no catch rate.
  - A 5xx in setup left scenarios `not_run` and the Business report listed them
    one by one, saying "Releasable" while 7 of 14 scenarios never ran because
    one create endpoint failed. Reports now group not-run scenarios by the
    endpoint and status that blocked them, and a blocking server error counts
    as high in the recommendation.
- **a03 no longer exhausts its attempts on a stuck correction.** Every
  generation that failed all four attempts failed them with one identical
  error: the correction turn shows the model its own rejected object and it
  re-sends it. `structured_call` now restarts from the original request after
  two identical rejections, and a correction for a value sent as unparseable
  JSON text names the syntax error, its position and the text around it — the
  old message, "Input should be a valid list", gave the model nothing to fix.
  The chain's a03 failure message now carries the last validator error, as the
  Manager's did; three M3 failures had lost it.
- A gate check with `! cmd` on any line but its last always passed:
  `bash -e` does not stop on a negated command. Two G3 drafts had it (the STUB
  check and a06's no-local-rules check); a test now rejects the pattern in
  every gate file. G0-5 (final line) and G2-8 (`&&` chain) were not affected.
- `scripts/gate_check.py` tested `requires_env` against `os.environ` without
  reading `.env`. With the key sitting there it skipped five of G2's nine checks
  and still printed a verdict, reporting the milestone as not closable for the
  wrong reason. It now calls `ecvt.llm.load_env` before any check runs.
- Every gate verified its tag with `git tag -l`, which reads only the local
  repository, so it passed for a tag that had never left the machine — a release
  gate reporting a release no one else can see. G0-7, G1-8 and G2-9 now use
  `git ls-remote --tags origin`.

### Measured
- **First run against a real third-party API — Swagger Petstore v3**
  (`swaggerapi/petstore3`, local, no baseline, `rest_api` plugin, ~$0.01 and
  ~40 s per run). Confirmed by hand afterwards: `POST /pet` without the
  optional `id` answers 500 (with `id`, 200); invalid enum and empty fields on
  `POST /pet` answer 500 rather than 4xx; `POST /user` answers 500 for some
  spec-valid bodies; `PUT /user/{username}` accepts an update with no
  credentials although its description says only the logged-in user may.
- **a03 exhaustion, M3.** Spec-only, wallet-api, `claude-haiku-5-5`,
  `scripts/measure_a03.py`, 24 generations per arm unless noted:

  | Arm | Valid | Exhausted | First attempt | Cost |
  |---|---|---|---|---|
  | `main` | 24/24 | 0 | 9/24 | $0.126 |
  | `main`, instrumented probe (n=12) | 10/12 | 2 | — | — |
  | + JSON error named in the correction | 22/24 | 2 | 16/24 | $0.113 |
  | + restart after two identical rejections | **24/24** | **0** | 14/24 | **$0.112** |

  Without the restart, 4 of 60 generations exhausted (with the 3 of 18 live
  chain runs, the same rate); with it, 0 of 24. That alone is not a decisive
  sample. The mechanism was seen directly: one generation failed three
  attempts with the same `Field required` and validated on the fresh fourth,
  where it would previously have been lost. The probe also showed what the
  `json_string` failures are: `stop_reason` was `tool_use` and the text was
  complete — not truncation, but a stray `}` inside JSON the model serialised
  itself, re-sent unchanged on every correction.
- **M3-T3, scoring through ecvt-eval.** Offline: 282 recorded scorings over
  stdio equal the in-process rules. Live, five `--plugin wallet` runs with a06
  scoring via the server: 7 · 6 · 8 · 7 · 6 (min 6 / median 7 / max 8), level
  with M3-T2's 6 / 7 / 8. One more attempt failed in a03 before execution —
  3 of 18 live attempts across M3-T1..T3, against 1 of 24 when a03 was last
  measured; carried as its own task.
- **M3-T2, wallet rules, M1 chain.** Paired — the five M3-T1 runs re-scored
  with the same executions and judge verdicts, only the wallet rules added:
  7 · 6 · 7 · 6 · 8 → 7 · 7 · 8 · 8 · 8 (median 7 → 8), unattributed failures
  unchanged at 1 per run. Live, five new `--plugin wallet` runs: 8 · 7 · 6 · 7
  · 8 (min 6 / median 7 / max 8), level with `rest_api`'s 6 / 7 / 8 — between
  fresh runs the scenario sets differ, which the paired comparison removes.
  Two of 12 live attempts across M3-T1/T2 failed in a03 before execution
  ("no valid output in 4 attempts").

#### The DomainModel handed to a03, or withheld (ADR 0012)
Six Manager runs per arm, wallet-api; `ECVT_A03_DOMAIN_MODEL` withholds a02's
output from a03 while a02 still runs, so the arms differ in that input only.

| | Handed to a03 | Withheld |
|---|---|---|
| Catch rate min / median / max | 1 / 5 / 8 | 3 / 5.5 / 7 |
| Cost / run | $0.071 | $0.072 |
| Executions failing setup | 12 / 86 | 1 / 84 |

No measured benefit, no cost difference. The one difference with a mechanism:
the DomainModel's rules, gaps and workflows name the account's id `account_id`
20–29 times per run, after the path parameter, while the target answers
`{"id": ...}`; in one run of six a03 saved `account_id` and that run caught
1/10. ADR 0012 accepted option B on this evidence.

#### Catch rate — the reference from here on
`scripts/n_run.py --runs 5`, wallet-api, `claude-haiku-5-5`. The figures that
preceded this were taken with a few-shot that named GT-03, so they are kept
only as history:

| | min | median | max | Cost / run |
|---|---|---|---|---|
| Reference (leaking few-shot, `main` before this work) | 5 | 5 | 7 | $0.011 |
| M1 chain | 4 | **6** | 7 | $0.011 |
| Through the Manager | 4 | **4** | 6 | $0.077 |

The leak was real, but there is no evidence it inflated the number: with the
example no longer naming a defect the M1 median reads 6 against 5 before. At
five runs a side that difference is not one — the honest claim is that removing
it did not cost catch rate, and the generator finds GT-03 from the spec rather
than from the prompt. What the change buys is validity, not a corrected figure.

The Manager path reads a median of 4 against the M1 chain's 6, at ten times the
cost per run. Taken with M2-T2 already being inconclusive, that is the material
for ADR 0012 rather than a conclusion here.

#### The fault the catch rate caught
The first run of this measurement put the M1 chain at a median of 1/10 — per
run 6, 1, 0, 1, 2 — and the cause was the new few-shot, not the domain move.
Generated scenarios wrote `"save": {"src_id": "account_id"}` against a target
whose create answers `{"id": ...}`, so the capture failed, setup failed with
it, and 49 of 70 executions never reached their action. The library example
saved `{"member_id": "id"}` under a path parameter also called `{member_id}`,
and the model read that as "the response field is named after the path
parameter"; the old example's names differed, so it taught the opposite by
accident. With the save keys renamed to `borrower` and `item`, capture failures
went from 49 to 0 and the median from 1 to 6. The Manager path showed none of it
in that run (0 of 70). The explanation first given here — that the DomainModel
lists the fields — was wrong: a03 receives only its rules, gaps and workflows,
and in the ADR 0012 measurement below the Manager path did show this failure.

#### Generation reliability, six prompt variants
24 generations per arm, spec-only, wallet-api, `claude-haiku-5-5`,
`scripts/measure_a03.py`:

| Arm | Few-shot | Wording | `max_tokens` | Valid | First attempt | `list_type` | Cost / run |
|---|---|---|---|---|---|---|---|
| A | wallet | original | 8192 | 24/24 | 17/24 | 0/24 | $0.0039 |
| D | wallet | generalised | 8192 | 19/24 | 14/24 | 4/24 | $0.0059 |
| B | library, `edge` only | generalised | 8192 | 16/24 | 5/24 | 5/24 | $0.0084 |
| C | + `stress` | generalised | 8192 | 21/24 | 5/24 | 8/24 | $0.0076 |
| E | + `stress` | generalised | 16000 | 21/24 | 7/24 | 5/24 | $0.0068 |
| F | + `adversarial` | generalised | 16000 | **23/24** | **15/24** | **0/24** | **$0.0045** |

Read in pairs: generalising the wording cost something (A→D), moving the
example's domain cost most (D→B, first-attempt 14/24 to 5/24), `stress`
recovered valid sets but not the first-attempt rate (B→C), the token ceiling
alone did little (C→E), and the third example did the work (E→F, 7/24 to
15/24). F lands within two runs of A while no longer naming a defect, at 15%
more per run than A and 41% less than C.

Three conclusions in this session were drawn at eight runs and reversed on more
data — a spec-only "regression", C "making things worse", and the few-shot
"raising" the catch rate. Eight runs do not separate these variants; the arms
above are twenty-four for that reason.

#### `repeat`/`concurrency` omission under the category union
a03's omission rate, wallet-api on `claude-haiku-5-5`,
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
