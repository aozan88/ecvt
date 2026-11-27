"""DomainModel wire contract — agents/a02_domain_intelligence/schema.py.

No LLM and no network: only the Pydantic contract the model's tool call must satisfy.

  1. A correct payload is accepted as is.
  2. `extra="forbid"`: a key the schema does not know is rejected, not silently dropped.
  3. No defaults: a required key that is missing is rejected, never filled in.
     A required-nullable key (`str | None`) must be present, even when its value is null.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.a02_domain_intelligence.schema import DomainModel

PROMPT = Path(__file__).parents[1] / "agents/a02_domain_intelligence/system_prompt.md"


def valid_payload() -> dict:
    """The parcel few-shot from the system prompt — so this file also proves the
    example the model learns from is itself schema-valid."""
    text = PROMPT.read_text(encoding="utf-8")
    start = text.index("emit_domain_model:") + len("emit_domain_model:")
    end = text.index("</output>", start)
    return json.loads(text[start:end])


def test_valid_payload_is_accepted():
    model = DomainModel.model_validate(valid_payload())
    assert model.operations[0].id == "create_parcel"


def test_unknown_key_is_rejected():
    payload = copy.deepcopy(valid_payload())
    payload["notes"] = "the model added this on its own"
    with pytest.raises(ValidationError) as exc:
        DomainModel.model_validate(payload)
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


def test_unknown_nested_key_is_rejected():
    payload = copy.deepcopy(valid_payload())
    payload["operations"][0]["priority"] = "high"
    with pytest.raises(ValidationError) as exc:
        DomainModel.model_validate(payload)
    err = exc.value.errors()[0]
    assert err["type"] == "extra_forbidden"
    assert err["loc"] == ("operations", 0, "priority")


def test_missing_required_key_is_rejected():
    payload = copy.deepcopy(valid_payload())
    del payload["glossary"]
    with pytest.raises(ValidationError) as exc:
        DomainModel.model_validate(payload)
    assert exc.value.errors()[0]["type"] == "missing"


def test_required_nullable_key_must_be_present():
    payload = copy.deepcopy(valid_payload())
    verdict = payload["operations"][0]["ownership"]  # not_applicable: no question to ask

    verdict["gap_question"] = None  # null is a valid answer ...
    DomainModel.model_validate(payload)

    del verdict["gap_question"]  # ... but leaving the key out is not
    with pytest.raises(ValidationError) as exc:
        DomainModel.model_validate(payload)
    assert exc.value.errors()[0]["type"] == "missing"


# --- v2 (M4-T2, ADR 0021) -----------------------------------------------------------


def test_identifier_is_a_flag_and_at_most_one_field_carries_it():
    payload = copy.deepcopy(valid_payload())
    assert DomainModel.model_validate(payload).entities[0].identifier == "id"
    for f in payload["entities"][0]["fields"]:
        f["is_identifier"] = False
    assert DomainModel.model_validate(payload).entities[0].identifier is None
    for f in payload["entities"][0]["fields"][:2]:
        f["is_identifier"] = True
    with pytest.raises(ValidationError, match="more than one field has is_identifier"):
        DomainModel.model_validate(payload)


def test_a_verdicts_question_is_filed_as_a_gap_once_per_question():
    dm = DomainModel.model_validate(valid_payload())
    filed = {g.dimension: g for g in dm.gaps if g.dimension in ("authorization", "idempotency")}
    # Both operations asked the same authorization question: one gap, both refs.
    assert filed["authorization"].refs == ["op:create_parcel", "op:deliver_parcel"]
    assert filed["idempotency"].refs == ["op:create_parcel"]
    assert {g.priority for g in filed.values()} == {"high"}
    # Validating the dumped model again files nothing twice.
    again = DomainModel.model_validate(dm.model_dump(mode="json"))
    assert len(again.gaps) == len(dm.gaps)


def test_an_open_verdict_without_a_question_is_rejected_with_where_to_put_it():
    payload = copy.deepcopy(valid_payload())
    payload["operations"][0]["idempotency"]["gap_question"] = None
    with pytest.raises(ValidationError, match=r"set idempotency.gap_question"):
        DomainModel.model_validate(payload)


def test_an_explicit_gap_still_covers_a_verdict():
    payload = copy.deepcopy(valid_payload())
    payload["operations"][0]["idempotency"]["gap_question"] = None
    payload["gaps"].append({
        "id": "create_repeat", "kind": "unspecified", "dimension": "idempotency",
        "refs": ["op:create_parcel"], "question": "Does a repeated create make two parcels?",
        "priority": "high", "evidence": ["#/paths/~1parcels/post"],
    })  # fmt: skip
    dm = DomainModel.model_validate(payload)
    assert [g.id for g in dm.gaps if g.dimension == "idempotency"] == ["create_repeat"]


def test_parameters_must_be_declared_in_the_spec():
    import yaml

    from agents.a02_domain_intelligence.agent import build_inventory

    text = PROMPT.read_text(encoding="utf-8")
    examples = text[text.index("<examples>") :]
    spec = yaml.safe_load(examples[examples.index("<spec>") + 6 : examples.index("</spec>")])
    context = {"spec": spec, "inventory": build_inventory(spec)}
    payload = copy.deepcopy(valid_payload())
    DomainModel.model_validate(payload, context=context)

    invented = {**payload["operations"][1]["parameters"][0], "name": "force", "location": "query"}
    payload["operations"][1]["parameters"].append(invented)
    with pytest.raises(ValidationError, match="parameter 'force' \\(query\\) is not declared"):
        DomainModel.model_validate(payload, context=context)
