# Agent Spec — Scenario Generator (a03)

Code: `agents/a03_scenario_generator/` · Version 1.1 (M3) · 2026-11-25 · ADRs 0005, 0007,
0009, 0011, 0012

## 1. Role

a03 reads a target's OpenAPI spec and writes a **ScenarioSet**: test scenarios across
seven QE categories, each one runnable exactly as written against that API. It decides
*what to try*; it does not run anything (a05) or judge results (a06). It never sees
`ground_truth.yaml` — a catch rate is only meaningful if the defects were not handed to
the agent looking for them (ADR 0007).

## 2. Inputs and outputs

| Direction | What | Defined in |
|---|---|---|
| In | OpenAPI spec → `ApiDigest` (paths, params, body fields, constraints) | `ecvt/openapi_reader.py` |
| In | a02's DomainModel (`rules`, `gaps`, `workflows`) — **off by default** (ADR 0012) | `build_user_prompt()` |
| Out | `ScenarioSet` of `Scenario`s: `setup` steps with `save` captures, one `action`, `expectation` (status, behaviour, invariants, read-only `probes`), `repeat`, `concurrency` | `ecvt/schema.py` |
| Artifact | `scenarios.jsonl` (chain) · `runs/<run_id>/scenario_gen.json` (Manager) | |

Categories, `DEFAULT_PER_CATEGORY = 2` each (14 per set): happy · edge · stress ·
security · negative · adversarial · persona.

## 3. How it works

1. **Code distils the spec** into an API surface, so the prompt carries the contract,
   not the whole document.
2. **One Messages API call with a forced tool** (`scenario_generator_output`), its input
   schema generated from `ScenarioSetDraft`; `tools.json` is the committed copy, kept in
   sync by a test. `max_tokens = 16000`: a 14-scenario set runs past 8192, and a cut-off
   tool input surfaces as an unhelpful "Input should be a valid list".
3. **Validation in the retry loop** (`structured_call`, up to 4 attempts): Pydantic, then
   set-level checks — every category present, and on an API that writes, at least one
   scenario with `concurrency` > 1 and one with `repeat` > 1. A rejection goes back as
   the rejected `tool_use`'s `tool_result`, so the model corrects its own set.
4. **Draft → Scenario.** Code writes `repeat`/`concurrency` = 1 where the wire branch
   has none, and attaches target, spec title and model.

## 4. Contract rules

- **Category union on the wire** (ADR 0011). `stress` must state `concurrency` ≥ 2;
  `adversarial` states both numbers; the other five categories carry neither, and one
  that sends them above 1 is told to change `category`, not the number. 8 numbers per
  default set instead of 28.
- **Runnable as written.** Only paths and fields from the surface; any state a scenario
  depends on is created in its own `setup`; values move between steps by `save` and
  `{placeholder}`. A `save` key is a name of the scenario's choosing mapped to a field of
  the *response body* — never derived from the path parameter it will fill.
- **Expectation = a correct system**, never the target's observed behaviour. Invariants
  are checkable against `probes`.

## 5. Prompt strategy

`system_prompt.md` (role, task, constraints, output format, one example) plus
`SKILL.md` (procedure, templates, anti-patterns). The few-shot is a **library-lending**
API (`/members`, `/titles`, `/loans`) with three scenarios — edge, stress, adversarial,
one per union branch — so nothing in the prompt names wallet-api or any of its known
defects. Its `save` keys (`borrower`, `item`) differ from the path parameters on
purpose: when they matched, the model learned "the field is named after the path
parameter" and 49 of 70 executions failed setup.

## 6. Eval criteria

`eval_dataset.jsonl`, 10 cases.

| Category | Cases | Scoring |
|---|---|---|
| happy-path | 6 | 1 schema-valid, 3 python-check, 2 llm-judge |
| edge | 3 — minimal spec, no request bodies, missing input | llm-judge |
| negative | 1 — wrong domain | llm-judge |

Bar ≥ 80%, **pooled over 3 runs** (`eval_runner --runs 3`, G3-6): a single run is one
draw — the same code has read 90% and 70%.

Generation reliability, 24 generations per arm (`scripts/measure_a03.py`, final prompt):
23/24 valid sets, 15/24 on the first attempt, $0.0045 per run. Catch rate on the M1
chain, five runs: min 6 / median 7 / max 8 (M3-T1..T3).

## 7. Dependencies and limits

- Position: after domain_intel in the Manager plan, first stage of the M1 chain; output
  feeds a05.
- Runtime: Messages API via `ecvt/llm.py`, model `ECVT_SUBAGENT_MODEL` (Haiku, ADR 0005).
- Cost: the union's three branches each embed the scenario core, so the tool schema is
  ~3.9k input tokens on every call.

## 8. Known limitations

- **Exhaustion came from stuck corrections.** In M3, 4 of 60 generations without a
  fix failed all four attempts with one identical error each time (`field_required`,
  or `scenarios` sent as broken JSON text). Since then the correction names a JSON
  syntax error and where it is, and two identical rejections in a row restart from
  the original request: 24/24 valid, 0 exhausted (CHANGELOG, Measured). The
  omission itself remains — `Field required` in 8 of 24 generations.
- With the DomainModel on, the account id is named `account_id` throughout a02's output
  while the target answers `{"id": ...}`; a03 once saved the wrong field (ADR 0012).
- `system_prompt.md` is 171 lines against a "2 pages" rule and a test bound of 180 —
  the conventions decision is open.
- Reads OpenAPI only; no prose requirements or acceptance criteria (a04, v1.2, M5).
