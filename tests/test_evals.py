"""Eval runner tests (M1-T2b).

The free scorers and the runner's failure handling are covered offline; the
llm-judge scorer is exercised with a faked client.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ecvt.evals import (
    MIN_CASES,
    PASS_THRESHOLD,
    AdapterContext,
    EvalCase,
    EvalReport,
    Invocation,
    adapter_for,
    load_dataset,
    render_report,
    run_dataset,
    score_keyword_match,
    score_llm_judge,
    score_schema_valid,
)
from ecvt.llm import Usage
from tests.test_scenario_generator import FakeClient, FakeText, FakeToolUse

DATASETS = {
    "a03_scenario_generator": Path("agents/a03_scenario_generator/eval_dataset.jsonl"),
    "a05_execution": Path("agents/a05_execution/eval_dataset.jsonl"),
    "a06_evaluation": Path("agents/a06_evaluation/eval_dataset.jsonl"),
}


def case(**kw: Any) -> EvalCase:
    data: dict[str, Any] = {
        "id": "c-1",
        "input": {},
        "category": "happy-path",
        "scoring": "schema-valid",
    }
    data.update(kw)
    return EvalCase(**data)


def grade(satisfies: bool, reason: str = "because") -> FakeToolUse:
    return FakeToolUse(name="grade", input={"satisfies_rubric": satisfies, "reason": reason})


# --- the committed datasets ------------------------------------------------


@pytest.mark.parametrize("agent", sorted(DATASETS))
def test_committed_datasets_are_runnable(agent: str):
    """Every case's input must be an object an adapter can execute.

    The first version of these datasets described situations in prose
    ("overdraft transfer, expected_status [400,422]"), which reads well and
    cannot be run — so the pass rate could never be measured.
    """
    cases, _ = load_dataset(DATASETS[agent])
    assert len(cases) >= MIN_CASES
    for item in cases:
        assert isinstance(item.input, dict) and item.input, f"{item.id} has no runnable input"


@pytest.mark.parametrize("agent", sorted(DATASETS))
def test_every_case_carries_what_its_scorer_needs(agent: str):
    cases, _ = load_dataset(DATASETS[agent])
    for item in cases:
        if item.scoring == "schema-valid":
            assert item.expected_schema, f"{item.id} needs expected_schema"
        elif item.scoring == "keyword-match":
            assert item.expected_keywords, f"{item.id} needs expected_keywords"
        else:
            assert item.rubric, f"{item.id} needs a rubric"


@pytest.mark.parametrize("agent", sorted(DATASETS))
def test_each_dataset_has_an_adapter(agent: str):
    found, adapter = adapter_for(DATASETS[agent])
    assert found == agent
    assert adapter is not None, f"{agent} has no adapter, so its cases cannot run"


def test_execution_and_evaluation_cases_carry_real_scenarios():
    """Those adapters validate `input.scenario` as a Scenario, so it must be one."""
    from ecvt.schema import Scenario

    for agent in ("a05_execution", "a06_evaluation"):
        for item in load_dataset(DATASETS[agent])[0]:
            Scenario.model_validate(item.input["scenario"])


# --- dataset validation ----------------------------------------------------


def test_prose_input_is_rejected(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "x",
                "input": "overdraft transfer, expected_status [400,422]",
                "category": "happy-path",
                "scoring": "llm-judge",
                "rubric": "r",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be an object the adapter can run"):
        load_dataset(path)


def test_note_lines_are_not_cases(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text('{"_todo": "more cases pending"}\n', encoding="utf-8")
    cases, notes = load_dataset(path)
    assert cases == []
    assert "more cases pending" in notes[0]


def test_unknown_scoring_mode_rejected(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_text(
        json.dumps({"id": "x", "input": {}, "category": "c", "scoring": "vibes"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown scoring"):
        load_dataset(path)


def test_duplicate_ids_rejected(tmp_path):
    path = tmp_path / "d.jsonl"
    row = json.dumps({"id": "x", "input": {}, "category": "c", "scoring": "llm-judge"})
    path.write_text(row + "\n" + row + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate case ids"):
        load_dataset(path)


# --- schema-valid ----------------------------------------------------------


def test_schema_valid_passes_and_fails():
    from ecvt.results import StepResult

    good = Invocation(text="", obj={"name": "a", "endpoint": "GET /x"})
    assert score_schema_valid(
        case(expected_schema="ecvt.results.StepResult"), good
    ).passed

    bad = Invocation(text="", obj={"endpoint": 5})
    result = score_schema_valid(case(expected_schema="ecvt.results.StepResult"), bad)
    assert not result.passed
    assert "does not match" in result.detail
    assert StepResult  # imported to pin the dotted path this test relies on


def test_schema_valid_without_structured_output():
    result = score_schema_valid(
        case(expected_schema="ecvt.results.StepResult"),
        Invocation(text="declined", error="no spec supplied"),
    )
    assert not result.passed
    assert "no spec supplied" in result.detail


def test_schema_valid_with_unimportable_path():
    result = score_schema_valid(
        case(expected_schema="ecvt.nope.Missing"), Invocation(text="", obj={})
    )
    assert not result.passed
    assert "cannot import" in result.detail


# --- keyword-match ---------------------------------------------------------


def test_keyword_match_is_case_insensitive():
    inv = Invocation(text='{"error_type": "CONNECT"}')
    assert score_keyword_match(case(scoring="keyword-match", expected_keywords=["connect"]), inv).passed


def test_keyword_match_names_what_is_missing():
    inv = Invocation(text='{"status": 500}')
    result = score_keyword_match(
        case(scoring="keyword-match", expected_keywords=["500", "attempts"]), inv
    )
    assert not result.passed
    assert "attempts" in result.detail and "500" not in result.detail


# --- llm-judge -------------------------------------------------------------


def test_judge_pass_and_fail():
    usage = Usage(model="m")
    item = case(scoring="llm-judge", rubric="must not contain a verdict")

    ok = score_llm_judge(item, Invocation(text="status 200"), client=FakeClient([grade(True)]), usage=usage)
    assert ok.passed and ok.detail == "because"

    no = score_llm_judge(item, Invocation(text="this is wrong"), client=FakeClient([grade(False, "it judged")]))
    assert not no.passed and "it judged" in no.detail
    assert usage.calls == 1


def test_judge_sees_the_rubric_and_the_output():
    fake = FakeClient([grade(True)])
    score_llm_judge(
        case(scoring="llm-judge", rubric="records the 500 once"),
        Invocation(text='{"status": 500}'),
        client=fake,
    )
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "records the 500 once" in sent
    assert '"status": 500' in sent


def test_judge_is_told_when_the_agent_declined():
    """A refusal can be the correct output, so the grader must see it as one."""
    fake = FakeClient([grade(True)])
    score_llm_judge(
        case(scoring="llm-judge", rubric="declines without inventing an API"),
        Invocation(text="no spec", error="no spec supplied"),
        client=fake,
    )
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "declined" in sent


def test_judge_failure_is_a_fail_not_a_crash():
    result = score_llm_judge(
        case(scoring="llm-judge", rubric="r"),
        Invocation(text="x"),
        client=FakeClient([FakeText(text="I think so")] * 3),
    )
    assert not result.passed
    assert "no verdict" in result.detail


def test_judge_sends_a_large_output_whole():
    """Within budget the grader sees all of it — no silent truncation."""
    fake = FakeClient([grade(True)])
    score_llm_judge(
        case(scoring="llm-judge", rubric="r"),
        Invocation(text="x" * 20000),
        client=fake,
        max_chars=200_000,
    )
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert sent.count("x") == 20000
    assert "truncated" not in sent


# --- report semantics ------------------------------------------------------


def test_skipped_cases_keep_the_bar_unmet():
    """An unmeasured case is not a pass, so it must not round up to one."""
    from ecvt.evals import CaseResult

    passing = [CaseResult(f"c{i}", "schema-valid", "happy-path", True, "ok") for i in range(10)]
    clean = EvalReport(agent="a", results=passing, usage=Usage(model="m"))
    assert clean.pass_rate == 1.0
    assert clean.meets_bar

    with_skip = EvalReport(
        agent="a", results=passing, usage=Usage(model="m"), skipped=[("c99", "no key")]
    )
    assert with_skip.pass_rate == 1.0
    assert not with_skip.meets_bar, "100% of what ran is not 100% of the dataset"


def test_bar_is_the_documented_threshold():
    from ecvt.evals import CaseResult

    results = [
        CaseResult(f"c{i}", "schema-valid", "happy-path", i < 8, "x") for i in range(10)
    ]
    report = EvalReport(agent="a", results=results, usage=Usage(model="m"))
    assert report.pass_rate == pytest.approx(PASS_THRESHOLD)
    assert report.meets_bar


def test_render_report_shows_failures_and_skips():
    from ecvt.evals import CaseResult

    report = EvalReport(
        agent="a05_execution",
        results=[
            CaseResult("c1", "keyword-match", "happy-path", False, "missing: connect"),
            CaseResult("c2", "schema-valid", "happy-path", True, "ok"),
        ],
        usage=Usage(model="m"),
        skipped=[("c3", "llm-judge skipped")],
    )
    text = render_report(report)
    assert "BELOW BAR" in text
    assert "FAIL  c1" in text
    assert "SKIP  c3" in text
    assert "c2" not in text, "passing cases are quiet unless --verbose"
    assert "pass  c2" in render_report(report, verbose=True)


# --- runner robustness -----------------------------------------------------


def test_scoring_failure_does_not_stop_the_dataset(tmp_path, monkeypatch):
    """A missing API key once took down cases that needed no model at all."""
    path = tmp_path / "a05_execution" / "eval_dataset.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "free",
                        "input": {"scenario": _minimal_scenario()},
                        "category": "happy-path",
                        "scoring": "keyword-match",
                        "expected_keywords": ["scenario_id"],
                    }
                ),
                json.dumps(
                    {
                        "id": "costly",
                        "input": {"scenario": _minimal_scenario()},
                        "category": "happy-path",
                        "scoring": "llm-judge",
                        "rubric": "r",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def explode(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("no API key")

    monkeypatch.setattr("ecvt.evals.score_llm_judge", explode)
    report = run_dataset(path, ctx=AdapterContext(base_url="http://127.0.0.1:9"))

    assert [r.case_id for r in report.results] == ["free"]
    assert report.skipped == [("costly", "scoring failed: RuntimeError: no API key")]


def test_skip_judge_leaves_the_costly_cases_out(tmp_path):
    path = DATASETS["a05_execution"]
    report = run_dataset(
        path, ctx=AdapterContext(base_url="http://127.0.0.1:9"), skip_judge=True
    )
    assert all(r.scoring != "llm-judge" for r in report.results)
    assert any("--skip-judge" in reason for _, reason in report.skipped)


def test_unknown_agent_directory_is_skipped_not_crashed(tmp_path):
    path = tmp_path / "a99_unknown" / "eval_dataset.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"id": "x", "input": {}, "category": "c", "scoring": "llm-judge", "rubric": "r"})
        + "\n",
        encoding="utf-8",
    )
    report = run_dataset(path)
    assert report.results == []
    assert "no adapter" in report.skipped[0][1]
    assert not report.meets_bar


def _minimal_scenario() -> dict[str, Any]:
    return {
        "id": "s",
        "title": "t",
        "category": "happy",
        "rationale": "r",
        "setup": [],
        "action": {"name": "a", "method": "GET", "path": "/accounts/1/balance"},
        "expectation": {"expected_status": [200], "expected_behavior": "b"},
    }


# --- harness honesty -------------------------------------------------------


def test_oversized_output_is_unmeasurable_not_a_failure():
    """A truncated output grades the harness, not the agent.

    The first live run capped a 19k-character ScenarioSet at 6k; the grader
    correctly said it could not verify anything, and that came out as seven
    FAILs against an agent that had done nothing wrong.
    """
    from ecvt.evals import Unmeasurable

    with pytest.raises(Unmeasurable, match="over the"):
        score_llm_judge(
            case(scoring="llm-judge", rubric="r"),
            Invocation(text="x" * 5000),
            client=FakeClient([grade(True)]),
            max_chars=1000,
        )


def test_unmeasurable_case_is_skipped_by_the_runner(tmp_path, monkeypatch):
    from ecvt.evals import Unmeasurable

    path = tmp_path / "a05_execution" / "eval_dataset.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "id": "big",
                "input": {"scenario": _minimal_scenario()},
                "category": "happy-path",
                "scoring": "llm-judge",
                "rubric": "r",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def too_big(*_a: Any, **_k: Any) -> None:
        raise Unmeasurable("output is 19000 chars, over the 6000 the grader is given")

    monkeypatch.setattr("ecvt.evals.score_llm_judge", too_big)
    report = run_dataset(path, ctx=AdapterContext(base_url="http://127.0.0.1:9"))

    assert report.results == [], "an unmeasurable case must not count as a failure"
    assert "unmeasurable" in report.skipped[0][1]
    assert not report.meets_bar


def test_generator_output_fits_the_grader_budget():
    """A full ScenarioSet must fit, or every a03 judge case is unmeasurable."""
    import inspect

    from ecvt.evals import score_llm_judge as judge

    budget = inspect.signature(judge).parameters["max_chars"].default
    # 14 scenarios measured at ~19k characters; leave room for growth.
    assert budget >= 100_000, f"grader budget {budget} is too small for a ScenarioSet"


@pytest.mark.parametrize("agent", sorted(DATASETS))
def test_rubrics_assert_something_about_the_output(agent: str):
    """A rubric must be a claim about the output, not a narration of ECVT.

    Rubrics that explained the semantics ("Rules alone are inconclusive; the
    judge resolves it...") made the grader evaluate the explanation: three
    cases returned false while the reason said the condition held.
    """
    for item in load_dataset(DATASETS[agent])[0]:
        if item.scoring != "llm-judge":
            continue
        assert item.rubric
        assert "rubric" not in item.rubric.lower(), (
            f"{item.id} refers to itself, which flips the grader's polarity"
        )
        assert len(item.rubric) <= 400, f"{item.id} rubric is an essay, not an assertion"


# --- code-based checks -----------------------------------------------------


def check_case(**kw: Any) -> EvalCase:
    data: dict[str, Any] = {
        "id": "c",
        "input": {"spec": "examples/wallet-api/openapi.json"},
        "category": "happy-path",
        "scoring": "python-check",
    }
    data.update(kw)
    return EvalCase(**data)


def scenario_payload(*scenarios: dict[str, Any]) -> Invocation:
    return Invocation(text="", obj={"scenarios": list(scenarios)})


def gen_scenario(
    sid: str = "sc-1",
    *,
    setup: list[dict[str, Any]] | None = None,
    action: dict[str, Any] | None = None,
    probes: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": sid,
        "setup": setup
        if setup is not None
        else [
            {
                "name": "mk",
                "method": "POST",
                "path": "/accounts",
                "body": {"owner": "a"},
                "save": {"src": "id"},
            }
        ],
        "action": action
        or {
            "name": "t",
            "method": "POST",
            "path": "/transfers",
            "body": {"from_account": "{src}", "to_account": "{src}", "amount": 1.0},
        },
        "expectation": {"probes": probes or []},
    }
    data.update(extra)
    return data


def test_placeholder_check_accepts_a_bound_set():
    from ecvt.eval_checks import placeholders_are_bound

    ok, detail = placeholders_are_bound(check_case(), scenario_payload(gen_scenario()))
    assert ok and "bound" in detail


def test_placeholder_check_names_the_unbound_one():
    from ecvt.eval_checks import placeholders_are_bound

    bad = gen_scenario(
        setup=[],
        action={"name": "t", "method": "POST", "path": "/transfers", "body": {"x": "{nope}"}},
    )
    ok, detail = placeholders_are_bound(check_case(), scenario_payload(bad))
    assert not ok
    assert "{nope}" in detail


def test_placeholder_check_rejects_a_step_using_its_own_save():
    """A step cannot reference what it is about to produce."""
    from ecvt.eval_checks import placeholders_are_bound

    bad = gen_scenario(
        setup=[
            {
                "name": "mk",
                "method": "POST",
                "path": "/accounts",
                "body": {"owner": "{src}"},
                "save": {"src": "id"},
            }
        ]
    )
    ok, _ = placeholders_are_bound(check_case(), scenario_payload(bad))
    assert not ok


def test_placeholder_check_lets_probes_use_action_saves():
    from ecvt.eval_checks import placeholders_are_bound

    good = gen_scenario(
        setup=[],
        action={
            "name": "t",
            "method": "POST",
            "path": "/accounts",
            "body": {"owner": "a"},
            "save": {"made": "id"},
        },
        probes=[{"name": "r", "method": "GET", "path": "/accounts/{made}/balance"}],
    )
    ok, _ = placeholders_are_bound(check_case(), scenario_payload(good))
    assert ok


def test_spec_check_allows_a_concrete_id_where_the_spec_has_a_parameter():
    """A 404 scenario must put a real value where the spec has {account_id}."""
    from ecvt.eval_checks import only_spec_endpoints

    good = gen_scenario(
        setup=[],
        action={"name": "t", "method": "GET", "path": "/accounts/nope/balance"},
    )
    ok, _ = only_spec_endpoints(check_case(), scenario_payload(good))
    assert ok, "a literal id is on-spec, not an invented endpoint"


def test_spec_check_catches_an_invented_endpoint():
    from ecvt.eval_checks import only_spec_endpoints

    for bad_action in (
        {"name": "t", "method": "DELETE", "path": "/accounts/{src}"},
        {"name": "t", "method": "POST", "path": "/accounts/{src}/freeze"},
    ):
        ok, detail = only_spec_endpoints(
            check_case(), scenario_payload(gen_scenario(action=bad_action))
        )
        assert not ok
        assert "off-spec" in detail


def test_concurrency_check_needs_both_a_race_and_a_replay():
    from ecvt.eval_checks import exercises_concurrency_and_repeat

    flat = scenario_payload(gen_scenario("sc-1"), gen_scenario("sc-2"))
    ok, detail = exercises_concurrency_and_repeat(check_case(), flat)
    assert not ok
    assert "concurrency > 1" in detail and "repeat > 1" in detail

    race_only = scenario_payload(gen_scenario("sc-1", concurrency=5))
    ok, detail = exercises_concurrency_and_repeat(check_case(), race_only)
    assert not ok
    assert "repeat > 1" in detail

    both = scenario_payload(gen_scenario("sc-1", concurrency=5), gen_scenario("sc-2", repeat=3))
    ok, _ = exercises_concurrency_and_repeat(check_case(), both)
    assert ok


def test_checks_report_an_empty_output_rather_than_crashing():
    from ecvt.eval_checks import (
        exercises_concurrency_and_repeat,
        only_spec_endpoints,
        placeholders_are_bound,
    )

    empty = Invocation(text="declined", error="no spec supplied")
    for fn in (placeholders_are_bound, only_spec_endpoints, exercises_concurrency_and_repeat):
        ok, detail = fn(check_case(), empty)
        assert not ok
        assert "no scenarios" in detail


def test_python_check_scorer_wires_the_dotted_path():
    from ecvt.evals import score_python_check

    result = score_python_check(
        check_case(check="ecvt.eval_checks.placeholders_are_bound"),
        scenario_payload(gen_scenario()),
    )
    assert result.passed

    broken = score_python_check(check_case(check="ecvt.eval_checks.nope"), scenario_payload())
    assert not broken.passed
    assert "cannot import" in broken.detail


def test_generator_asks_for_concurrency_and_repeat():
    """The one real finding of the live run: every scenario came back flat."""
    from agents.a03_scenario_generator.agent import build_user_prompt, load_system_prompt
    from ecvt.openapi_reader import read_openapi

    system = load_system_prompt()
    assert "concurrency" in system and "repeat" in system
    user = build_user_prompt(read_openapi("examples/wallet-api/openapi.json"), "t")
    assert "concurrency" in user and "repeat" in user


def test_mechanical_claims_are_not_left_to_the_judge():
    """These three are decidable by parsing, so they must not cost a call."""
    cases = {c.id: c for c in load_dataset(DATASETS["a03_scenario_generator"])[0]}
    for case_id in ("sg-002", "sg-003", "sg-005"):
        assert cases[case_id].scoring == "python-check", f"{case_id} should be code-checked"
        assert cases[case_id].check


def test_judge_reason_is_kept_long_enough_to_diagnose():
    """A judge reason at 300 characters was cut mid-sentence, so the artifact
    never said which rubric clause failed and diagnosing a case meant running
    the eval again. The stored detail is long; the terminal line is not."""
    from ecvt.evals import DETAIL_CHARS, DISPLAY_DETAIL_CHARS

    assert DETAIL_CHARS >= 1000
    assert DISPLAY_DETAIL_CHARS < DETAIL_CHARS


def test_schema_failure_detail_names_the_rule(tmp_path):
    """The validation error itself, not just "does not match <schema>"."""
    from ecvt.evals import DETAIL_CHARS, EvalCase, Invocation, score_schema_valid

    case = EvalCase(
        id="x",
        input={},
        category="happy-path",
        scoring="schema-valid",
        expected_schema="ecvt.schema.Scenario",
    )
    res = score_schema_valid(case, Invocation(text="", obj={"id": "sc-001"}))
    assert res.passed is False
    assert "Field required" in res.detail  # the rule, not only the schema name
    assert len(res.detail) <= DETAIL_CHARS
