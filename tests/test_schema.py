"""Scenario schema tests (M1-T1)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ecvt.schema import (
    ALL_CATEGORIES,
    Scenario,
    ScenarioCategory,
    ScenarioSet,
    ScenarioSetDraft,
    read_scenarios_jsonl,
    write_scenarios_jsonl,
)


def make_scenario(sid: str = "sc-001", category: str = "edge", **overrides) -> dict:
    data = {
        "id": sid,
        "title": "Transfer above available balance",
        "category": category,
        "severity": "critical",
        "repeat": 1,
        "concurrency": 1,
        "rationale": "No stated minimum balance, so the source may be allowed to go negative.",
        "setup": [
            {
                "name": "create_source",
                "method": "POST",
                "path": "/accounts",
                "body": {"owner": "alice", "initial_balance": 50.0},
                "save": {"src_id": "id"},
            }
        ],
        "action": {
            "name": "overdraw",
            "method": "POST",
            "path": "/transfers",
            "body": {"from_account": "{src_id}", "to_account": "{src_id}", "amount": 500.0},
        },
        "expectation": {
            "expected_status": [400, 422],
            "expected_behavior": "Rejected; balances unchanged.",
            "invariants": ["source balance is still 50.0"],
            "probes": [
                {"name": "read_source", "method": "GET", "path": "/accounts/{src_id}/balance"}
            ],
        },
    }
    data.update(overrides)
    return data


def test_valid_scenario_round_trips():
    scenario = Scenario.model_validate(make_scenario())
    assert scenario.category is ScenarioCategory.EDGE
    assert scenario.action.endpoint == "POST /transfers"
    assert scenario.repeat == 1 and scenario.concurrency == 1
    assert Scenario.model_validate_json(scenario.model_dump_json()) == scenario


def test_path_must_start_with_slash():
    bad = make_scenario()
    bad["action"]["path"] = "transfers"
    with pytest.raises(ValidationError, match="must start with"):
        Scenario.model_validate(bad)


def test_duplicate_step_names_rejected():
    bad = make_scenario()
    bad["action"]["name"] = "create_source"
    with pytest.raises(ValidationError, match="duplicate step names"):
        Scenario.model_validate(bad)


def test_probes_must_be_get():
    bad = make_scenario()
    bad["expectation"]["probes"][0]["method"] = "POST"
    with pytest.raises(ValidationError, match="probes must be GET"):
        Scenario.model_validate(bad)


def test_implausible_status_rejected():
    bad = make_scenario()
    bad["expectation"]["expected_status"] = [999]
    with pytest.raises(ValidationError, match="not HTTP status codes"):
        Scenario.model_validate(bad)


def test_unknown_category_rejected():
    with pytest.raises(ValidationError):
        Scenario.model_validate(make_scenario(category="chaos"))


def test_concurrency_and_repeat_bounds():
    with pytest.raises(ValidationError):
        Scenario.model_validate(make_scenario(concurrency=0))
    with pytest.raises(ValidationError):
        Scenario.model_validate(make_scenario(repeat=999))


def test_draft_forbids_extra_fields():
    with pytest.raises(ValidationError):
        ScenarioSetDraft.model_validate({"scenarios": [make_scenario()], "notes": "hi"})


def test_draft_rejects_duplicate_ids():
    payload = {"scenarios": [make_scenario("sc-001"), make_scenario("sc-001", "happy")]}
    with pytest.raises(ValidationError, match="duplicate scenario ids"):
        ScenarioSetDraft.model_validate(payload)


def test_draft_requires_at_least_one_scenario():
    with pytest.raises(ValidationError):
        ScenarioSetDraft.model_validate({"scenarios": []})


def test_missing_categories_reported():
    draft = ScenarioSetDraft.model_validate({"scenarios": [make_scenario(category="happy")]})
    assert draft.missing_categories() == [
        c.value for c in ALL_CATEGORIES if c is not ScenarioCategory.HAPPY
    ]


def full_set() -> ScenarioSet:
    scenarios = [
        make_scenario(f"sc-{i:03d}", c.value) for i, c in enumerate(ALL_CATEGORIES, start=1)
    ]
    return ScenarioSet.model_validate(
        {
            "target": "examples/wallet-api",
            "spec_title": "Wallet API (buggy demo)",
            "scenarios": scenarios,
            "model": "claude-haiku-5-5",
        }
    )


def test_coverage_helpers():
    scenario_set = full_set()
    assert scenario_set.covers_all_categories()
    assert scenario_set.missing_categories() == []
    assert set(scenario_set.category_counts()) == {c.value for c in ALL_CATEGORIES}
    assert sum(scenario_set.category_counts().values()) == 7


def test_jsonl_round_trip_and_meta(tmp_path):
    scenario_set = full_set()
    out = write_scenarios_jsonl(tmp_path / "scenarios.jsonl", scenario_set)

    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 7

    reread = read_scenarios_jsonl(out)
    assert [s.id for s in reread] == [s.id for s in scenario_set.scenarios]
    assert reread == scenario_set.scenarios

    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert meta["scenario_count"] == 7
    assert meta["target"] == "examples/wallet-api"
    assert meta["model"] == "claude-haiku-5-5"
    assert "scenarios" not in meta
    assert all(count == 1 for count in meta["category_counts"].values())


def test_jsonl_read_reports_bad_line(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text(json.dumps(make_scenario()) + "\n{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"broken\.jsonl:2"):
        read_scenarios_jsonl(path)


def test_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "padded.jsonl"
    path.write_text("\n" + json.dumps(make_scenario()) + "\n\n", encoding="utf-8")
    assert len(read_scenarios_jsonl(path)) == 1
