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
    entity = payload["entities"][0]

    entity["identifier"] = None  # null is a valid answer ...
    DomainModel.model_validate(payload)

    del entity["identifier"]  # ... but leaving the key out is not
    with pytest.raises(ValidationError) as exc:
        DomainModel.model_validate(payload)
    assert exc.value.errors()[0]["type"] == "missing"
