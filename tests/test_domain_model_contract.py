"""DomainModel wire contract — agents/a02_domain_intelligence/schema.py.

Hand-written by Ozan (course practice: Accessing Claude with the API › Structured data).
No LLM and no network: only the Pydantic contract the model's tool call must satisfy.

What each test proves (one sentence from the lesson):
  1. A correct payload is accepted as is.
  2. `extra="forbid"`: a key the schema does not know is rejected, not silently dropped.
  3. No defaults: a required key that is missing is rejected, never filled in.

How to work through this file
  - Uncomment the imports below as soon as a test uses them (ruff fails on unused imports).
  - Write `valid_payload()` first; all three tests use it.
  - In each test, delete the `pytest.skip(...)` line, then write the body.
  - Run only this file:  pytest -q tests/test_domain_model_contract.py
  - Done when: 3 passed, 0 skipped, `ruff check .` clean.
"""

from __future__ import annotations

import copy  # noqa: F401  (used by tests 2 and 3: copy.deepcopy(valid_payload()))

import pytest

# Uncomment when first used:
from pydantic import ValidationError
from agents.a02_domain_intelligence.schema import DomainModel


def valid_payload() -> dict:
    """Return a DomainModel payload that passes validation.

    Pick ONE option, then delete the other and the `raise` line.

    Option A — reuse the parcel few-shot from the system prompt (least typing):
        The JSON sits in agents/a02_domain_intelligence/system_prompt.md, in the FIRST
        <example> block, after the line "emit_domain_model:" and before "</output>".

        Example of the pattern (adapt it, do not copy blindly):
            from pathlib import Path
            import json
            text = Path("agents/a02_domain_intelligence/system_prompt.md").read_text("utf-8")
            start = text.index("emit_domain_model:") + len("emit_domain_model:")
            end = text.index("</output>", start)
            return json.loads(text[start:end])

        Bonus: this also proves the few-shot in the prompt is itself schema-valid.

    Option B — write the smallest dict by hand (best for learning the contract):
        Top-level keys: entities, operations, workflows, rules, gaps, glossary.
        Start with:   {"entities": [], "operations": [ {...one operation...} ],
                       "workflows": [], "rules": [], "gaps": [], "glossary": []}
        Then run test 1 and read each ValidationError: it tells you the next missing key.
        Look at the parcel operation "create_parcel" in system_prompt.md for the shape.
        Question to answer for yourself: if idempotency is "not_specified", why does
        validation demand a gap? (schema.py docstring, contract rule 3)

    Note: without `context=...` the spec-bound checks (coverage, evidence pointers)
    do not run, so the payload needs no OpenAPI file.
    """

    text = Path("agents/a02_domain_intelligence/system_prompt.md").read_text(encoding="utf-8")
    start = text.index("emit_domain_model:") + len("emit_domain_model:")
    end = text.index("</output>", start)

    return json.loads(text[start:end].strip())
    # raise NotImplementedError("TODO: pick option A or B")


def test_valid_payload_is_accepted():
    # pytest.skip("TODO: write me")
    # Pattern:
    model = DomainModel.model_validate(valid_payload())
    assert model.operations[0].id == "create_parcel"
    # One assert on a real field is enough; the point is "no exception".


def test_unknown_key_is_rejected():
    # pytest.skip("TODO: write me")
    # Pattern:
    payload = copy.deepcopy(valid_payload())
    payload["notes"] = "the model added this on its own"
    with pytest.raises(ValidationError) as exc:
        DomainModel.model_validate(payload)
    assert exc.value.errors()[0]["type"] == "extra_forbidden"
    #
    # Bonus (one level down): add an unknown key inside payload["operations"][0]
    # and assert the error's "loc" points there, e.g. ("operations", 0, "<your key>").


def test_missing_required_key_is_rejected():
    # pytest.skip("TODO: write me")
    # Pattern:
    payload = copy.deepcopy(valid_payload())
    del payload["glossary"]
    with pytest.raises(ValidationError) as exc:
        DomainModel.model_validate(payload)
    assert exc.value.errors()[0]["type"] == "missing"
    #
    # Bonus: delete a nested key typed `str | None` (required-nullable) and show that
    # the key must be present even though its value may be null — then set it to None
    # instead and show validation passes. That pair IS contract rule 1.
