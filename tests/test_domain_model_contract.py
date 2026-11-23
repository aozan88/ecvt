"""DomainModel wire contract — a02_domain_intelligence/schema.py.

Hand-written by Ozan (course practice: Accessing Claude with the API › Structured data).
No LLM and no network: only the Pydantic contract the model's tool call must satisfy.

What each test proves (one sentence from the lesson):
  1. A correct payload is accepted as is.
  2. `extra="forbid"`: a key the schema does not know is rejected, not silently dropped.
  3. No defaults: a required key that is missing is rejected, never filled in.

Remove the `pytest.skip(...)` line of a test once its body is written.
"""

from __future__ import annotations

import pytest

# TODO(1): import DomainModel from agents.a02_domain_intelligence.schema
# TODO(1): import ValidationError from pydantic (tests 2 and 3 expect it)


def valid_payload() -> dict:
    """A DomainModel payload that passes validation.

    TODO(0): return a known-good payload. Two options, pick one:
      a) Parse the parcel few-shot from agents/a02_domain_intelligence/system_prompt.md:
         the JSON after the line "emit_domain_model:" and before "</output>"
         (first <example> block only).
      b) Write a minimal dict by hand: one entity, one operation, and
         empty workflows / rules / gaps / glossary.
    Hint: without `context=` the spec-bound checks (coverage, evidence pointers)
    do not run, so the payload needs no OpenAPI file.
    Question to answer for yourself: why does option (b) still need a gap
    if the operation's idempotency is "not_specified"? (schema.py, contract rule 3)
    """
    raise NotImplementedError


def test_valid_payload_is_accepted():
    pytest.skip("TODO: write me")
    # TODO: DomainModel.model_validate(valid_payload()) does not raise
    # TODO: assert on one field of the result, e.g. the first operation id


def test_unknown_key_is_rejected():
    pytest.skip("TODO: write me")
    # TODO: copy valid_payload(), add an unknown top-level key (e.g. "notes")
    # TODO: pytest.raises(ValidationError) around model_validate
    # TODO: assert the error type is "extra_forbidden" (exc.value.errors()[0]["type"])
    # Bonus: repeat one level down — an unknown key inside operations[0]


def test_missing_required_key_is_rejected():
    pytest.skip("TODO: write me")
    # TODO: copy valid_payload(), delete a required key (e.g. "glossary")
    # TODO: pytest.raises(ValidationError); assert the error type is "missing"
    # Bonus: delete a nested required-nullable key (one typed `str | None`) and
    #        show that the key must be present even though its value may be null
