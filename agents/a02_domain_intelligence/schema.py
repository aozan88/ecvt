"""a02_domain_intelligence — DomainModel wire contract (v1).

The wire model is exactly what the LLM emits through the `emit_domain_model` tool.

Contract rules
--------------
1. No field has a default. "Unknown" is an explicit enum value (`not_specified`,
   `not_declared`) or a required-nullable field (`str | None`, key must be present).
   Lesson from a03: a field that is optional in the schema stays at its default,
   whatever the prompt says.
2. `extra="forbid"` on every model.
3. Self-consistency is enforced here, not in the prompt: every `not_specified` verdict,
   unbounded numeric field and operation-less state transition must be covered by a gap.
   v2 makes the two commonest violations hard to commit (ADR 0021): the identifier is
   a flag on a field, so it cannot name a field that does not exist, and a verdict
   carries its own `gap_question`, which this module files as a gap.
4. Spec-dependent checks run when the caller passes
       context={"spec": <parsed OpenAPI dict>,
                "inventory": {<op_id>: (<METHOD>, <path>), ...}}
   to `DomainModel.model_validate(...)`: full operation coverage, method/path match,
   and every evidence pointer must resolve in the spec.
   Validation errors are written to be sent back to the model verbatim as a
   `tool_result` with `is_error=true` (one retry).

`tools.json` is generated from this module (`build_tools()`); do not edit it by hand.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

SCHEMA_VERSION = "2.0"  # v2 (ADR 0021): is_identifier, gap_question


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- primitives

Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64,
                          description="snake_case identifier, unique within its collection.")]

Ref = Annotated[str, Field(
    pattern=r"^(entity|op|workflow):[a-z][a-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_-]*)?$",
    description="Reference to a model element: 'entity:<id>', 'entity:<id>.<field name>', "
                "'op:<id>' or 'workflow:<id>'.")]

Pointer = Annotated[str, Field(
    pattern=r"^#/",
    description="RFC 6901 JSON Pointer into the OpenAPI document, e.g. "
                "'#/paths/~1parcels/post' or '#/components/schemas/Parcel/properties/weightKg'.")]

Evidence = Annotated[list[Pointer], Field(
    min_length=1, max_length=5,
    description="Where in the spec this is stated, or — for not_specified/inferred — the exact "
                "location where the statement is missing or the reasoning starts.")]

Sentence = Annotated[str, Field(min_length=3, max_length=300)]

Provenance = Literal["declared", "described", "inferred"]
PROVENANCE_DOC = ("declared = machine-readable in the spec (schema keyword, enum, required, "
                  "security, response code); described = stated in prose (description/summary); "
                  "inferred = reasoned from names, structure or HTTP semantics, not stated.")

Confidence = Literal["high", "medium", "low"]

VerdictStatus = Literal["declared", "described", "inferred",
                        "not_specified", "contradictory", "not_applicable"]

ConstraintKind = Literal[
    "required", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "enum", "format", "multipleOf",
    "minItems", "maxItems", "uniqueItems", "readOnly", "writeOnly", "nullable", "default",
]

GapDimension = Literal[
    "bounds", "idempotency", "authorization", "ownership", "state_transition",
    "concurrency", "referential_integrity", "uniqueness", "error_contract",
    "temporal", "precision_units",
]


# --------------------------------------------------------------------------- entities

class Constraint(_Wire):
    kind: ConstraintKind = Field(description="OpenAPI/JSON Schema keyword as written in the spec.")
    value: str = Field(description="Keyword value as written, stringified: '0', 'date', "
                                   "'[created, delivered]', 'true'.")


class EntityField(_Wire):
    name: str = Field(description="Property name exactly as in the spec (case preserved).")
    is_identifier: bool = Field(description="True for the one field that identifies an instance "
                                            "(e.g. 'id'); false for every other field.")
    type: Literal["string", "integer", "number", "boolean", "array", "object", "unknown"] = Field(
        description="JSON Schema type; 'unknown' if the spec gives none.")
    writable: bool = Field(description="True if a client can set it: appears in a request body or "
                                       "parameter and is not readOnly.")
    bounds: Literal["declared", "partial", "not_declared", "not_applicable"] = Field(
        description="declared = value space fully constrained (min+max, enum, maxLength/pattern, "
                    "bounding format); partial = only one side; not_declared = nothing; "
                    "not_applicable = boolean/object.")
    constraints: list[Constraint] = Field(description="Every keyword the spec declares on this field. "
                                                      "Empty list if none.")
    evidence: Evidence

    @model_validator(mode="after")
    def _bounds_need_constraints(self) -> EntityField:
        if self.bounds in ("declared", "partial") and not self.constraints:
            raise ValueError(f"field '{self.name}': bounds='{self.bounds}' but constraints is empty. "
                             "List the declaring keywords or set bounds='not_declared'.")
        return self


class Entity(_Wire):
    id: Id
    name: str = Field(description="Name as the spec uses it (schema name or path noun).")
    description: Sentence = Field(description="One sentence: what this entity is in the spec's terms.")
    fields: list[EntityField] = Field(description="Fields found in the spec. Empty if the entity is "
                                                  "only named by a path; never invent fields.")
    evidence: Evidence

    @model_validator(mode="after")
    def _one_identifier(self) -> Entity:
        names = {f.name for f in self.fields}
        flagged = [f.name for f in self.fields if f.is_identifier]
        if len(flagged) > 1:
            raise ValueError(f"entity '{self.id}': more than one field has is_identifier=true "
                             f"({', '.join(flagged)}); keep it on the one that identifies an instance.")
        if len(names) != len(self.fields):
            raise ValueError(f"entity '{self.id}': duplicate field names.")
        return self

    @property
    def identifier(self) -> str | None:
        """The identifying field's name, or None if the spec exposes none."""
        return next((f.name for f in self.fields if f.is_identifier), None)


# --------------------------------------------------------------------------- operations

class Verdict(_Wire):
    status: VerdictStatus = Field(description="declared/described/inferred as in provenance; "
                                              "not_specified = the spec is silent; contradictory = "
                                              "the spec says two incompatible things; "
                                              "not_applicable = the dimension does not apply.")
    detail: Sentence = Field(description="One sentence stating what the spec says or what is missing.")
    gap_question: Sentence | None = Field(
        description="When status is not_specified or contradictory: the question the spec leaves "
                    "open, phrased as a question (it is filed as a gap for you). null otherwise.")
    evidence: Evidence


class Operation(_Wire):
    id: Id = Field(description="Operation id copied verbatim from <operation_inventory>.")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: str = Field(description="Path template copied verbatim from <operation_inventory>.")
    effect: Literal["read", "create", "update", "delete", "action"] = Field(
        description="What the operation does to domain state.")
    entities: list[Id] = Field(description="Ids of entities this operation reads or changes.")
    idempotency: Verdict = Field(description="Is repeating the call with the same input safe, and "
                                             "does the spec say so? not_applicable for reads.")
    authorization: Verdict = Field(description="Who may call it: security requirements in effect.")
    ownership: Verdict = Field(description="Whose resource it acts on, and whether the spec restricts "
                                           "acting on another party's resource.")
    declared_errors: list[Annotated[str, Field(pattern=r"^([1-5][0-9]{2}|[1-5]XX|default)$")]] = Field(
        description="4XX/5XX/default response codes declared for this operation. Empty if none.")


# --------------------------------------------------------------------------- workflows

class Transition(_Wire):
    from_state: str
    to_state: str
    operation: Id | None = Field(description="Operation that drives the transition, or null if no "
                                             "operation in the spec does (time- or system-driven).")
    provenance: Provenance = Field(description=PROVENANCE_DOC)
    evidence: Evidence


class Workflow(_Wire):
    id: Id
    name: str
    entity: Id = Field(description="Primary entity the workflow moves through its lifecycle.")
    state_field: str | None = Field(description="Field holding the lifecycle state, or null if none.")
    states: list[str] = Field(description="Values of state_field, as in the spec. Empty if "
                                          "state_field is null.")
    steps: list[Id] = Field(min_length=2, description="Operation ids in happy-path order.")
    transitions: list[Transition] = Field(description="State transitions. Empty if state_field is null.")
    provenance: Provenance = Field(description=PROVENANCE_DOC)
    evidence: Evidence

    @model_validator(mode="after")
    def _states_consistent(self) -> Workflow:
        if self.state_field is None and (self.states or self.transitions):
            raise ValueError(f"workflow '{self.id}': states/transitions given but state_field is null.")
        if self.state_field is not None and not self.states:
            raise ValueError(f"workflow '{self.id}': state_field set but states is empty.")
        for t in self.transitions:
            for s in (t.from_state, t.to_state):
                if s not in self.states:
                    raise ValueError(f"workflow '{self.id}': transition state '{s}' not in states.")
        return self


# --------------------------------------------------------------------------- rules, gaps, glossary

class Rule(_Wire):
    id: Id
    kind: Literal["field_constraint", "invariant", "precondition", "uniqueness",
                  "referential", "quota", "temporal", "authorization"]
    statement: Sentence = Field(description="One testable sentence the system should obey.")
    applies_to: list[Ref] = Field(min_length=1)
    provenance: Provenance = Field(description=PROVENANCE_DOC)
    confidence: Confidence = Field(description="declared rules are high; inferred rules are never high.")
    evidence: Evidence

    @model_validator(mode="after")
    def _confidence_matches_provenance(self) -> Rule:
        if self.provenance == "declared" and self.confidence != "high":
            raise ValueError(f"rule '{self.id}': provenance='declared' requires confidence='high'.")
        if self.provenance == "inferred" and self.confidence == "high":
            raise ValueError(f"rule '{self.id}': provenance='inferred' cannot have confidence='high'.")
        return self


class Gap(_Wire):
    id: Id
    kind: Literal["unspecified", "contradictory"]
    dimension: GapDimension
    refs: list[Ref] = Field(min_length=1, description="All elements sharing this same open question.")
    question: Sentence = Field(description="The question the spec does not answer. A question, never a "
                                           "claim that the system is defective.")
    priority: Literal["high", "medium", "low"] = Field(
        description="high = changes the outcome of a write or crosses a party boundary; "
                    "medium = bounds, error contracts, temporal; low = read-only or naming ambiguity.")
    evidence: Evidence


class GlossaryTerm(_Wire):
    term: str
    definition: Sentence
    aliases: list[str] = Field(description="Other names the spec uses for the same concept. Empty if none.")
    maps_to: list[Ref] = Field(description="Model elements this term names. Empty if none.")
    provenance: Provenance = Field(description=PROVENANCE_DOC)
    evidence: Evidence


# --------------------------------------------------------------------------- root

class DomainModel(_Wire):
    """Domain model of the target system, extracted from its OpenAPI document only."""

    entities: list[Entity]
    operations: list[Operation] = Field(min_length=1, description="Exactly one per inventory row.")
    workflows: list[Workflow]
    rules: list[Rule] = Field(max_length=60)
    gaps: list[Gap] = Field(max_length=60)
    glossary: list[GlossaryTerm] = Field(max_length=40)

    @model_validator(mode="after")
    def _cross_checks(self, info: ValidationInfo) -> DomainModel:
        errors: list[str] = []

        # uniqueness
        for name, items in (("entities", self.entities), ("operations", self.operations),
                            ("workflows", self.workflows), ("rules", self.rules), ("gaps", self.gaps)):
            ids = [i.id for i in items]
            dup = {i for i in ids if ids.count(i) > 1}
            if dup:
                errors.append(f"{name}: duplicate ids {sorted(dup)}.")

        ents = {e.id: e for e in self.entities}
        ops = {o.id: o for o in self.operations}
        wfs = {w.id for w in self.workflows}

        def ref_ok(ref: str) -> bool:
            kind, _, rest = ref.partition(":")
            if kind == "op":
                return rest in ops
            if kind == "workflow":
                return rest in wfs
            eid, _, field = rest.partition(".")
            return eid in ents and (not field or field in {f.name for f in ents[eid].fields})

        all_refs = [(f"rule '{r.id}'", x) for r in self.rules for x in r.applies_to]
        all_refs += [(f"gap '{g.id}'", x) for g in self.gaps for x in g.refs]
        all_refs += [(f"glossary '{t.term}'", x) for t in self.glossary for x in t.maps_to]
        for owner, ref in all_refs:
            if not ref_ok(ref):
                errors.append(f"{owner}: reference '{ref}' does not resolve.")
        for o in self.operations:
            for e in o.entities:
                if e not in ents:
                    errors.append(f"op '{o.id}': unknown entity '{e}'.")
        for w in self.workflows:
            if w.entity not in ents:
                errors.append(f"workflow '{w.id}': unknown entity '{w.entity}'.")
            elif w.state_field is not None and w.state_field not in {f.name for f in ents[w.entity].fields}:
                errors.append(f"workflow '{w.id}': state_field '{w.state_field}' is not a field of '{w.entity}'.")
            for s in w.steps:
                if s not in ops:
                    errors.append(f"workflow '{w.id}': unknown step '{s}'.")
            for t in w.transitions:
                if t.operation is not None and t.operation not in ops:
                    errors.append(f"workflow '{w.id}': unknown transition operation '{t.operation}'.")

        # gap obligations: silence must surface as a question
        def covered(dims: set[str], ref: str, contradictory: bool = False) -> bool:
            return any(g.dimension in dims and ref in g.refs and (not contradictory or g.kind == "contradictory")
                       for g in self.gaps)

        # A verdict's own gap_question becomes a gap (v2). Identical questions on the
        # same dimension are filed once, with every operation that asked them.
        filed: dict[tuple[str, str, str], list[tuple[str, list[str]]]] = {}
        for o in self.operations:
            checks = [("authorization", o.authorization), ("ownership", o.ownership)]
            if o.effect != "read":
                checks.append(("idempotency", o.idempotency))
            for dim, v in checks:
                if v.status not in ("not_specified", "contradictory"):
                    continue
                kind = "contradictory" if v.status == "contradictory" else "unspecified"
                if covered({dim}, f"op:{o.id}", kind == "contradictory"):
                    continue
                if v.gap_question:
                    filed.setdefault((dim, kind, v.gap_question), []).append((o.id, v.evidence))
                else:
                    errors.append(f"op:{o.id} {dim} is {v.status}: set {dim}.gap_question to the question "
                                  "the spec leaves open, or correct the verdict.")
        taken = {g.id for g in self.gaps}
        for (dim, kind, question), askers in filed.items():
            gid, n = f"{askers[0][0]}_{dim}"[:60], 2
            while gid in taken:
                gid, n = f"{askers[0][0][:56]}_{dim[:1]}{n}", n + 1
            taken.add(gid)
            self.gaps.append(Gap(
                id=gid, kind=kind, dimension=dim, refs=[f"op:{oid}" for oid, _ in askers],
                question=question, priority="high",  # a write's outcome or a party boundary
                evidence=sorted({p for _, ev in askers for p in ev})[:5],
            ))
        for e in self.entities:
            for f in e.fields:
                if f.type in ("integer", "number") and f.bounds in ("not_declared", "partial"):
                    ref = f"entity:{e.id}.{f.name}"
                    if not covered({"bounds"}, ref):
                        errors.append(f"{ref} is numeric with bounds='{f.bounds}' but no gap with "
                                      f"dimension='bounds' references '{ref}'.")
        for w in self.workflows:
            for t in w.transitions:
                if t.operation is None and not covered({"state_transition", "temporal"}, f"workflow:{w.id}"):
                    errors.append(f"workflow:{w.id} transition {t.from_state}->{t.to_state} has no operation "
                                  f"but no state_transition/temporal gap references 'workflow:{w.id}'.")

        # spec-dependent checks
        ctx: dict[str, Any] = info.context or {}
        inventory: dict[str, tuple[str, str]] | None = ctx.get("inventory")
        if inventory is not None:
            missing, extra = set(inventory) - set(ops), set(ops) - set(inventory)
            if missing:
                errors.append(f"operations missing from inventory: {sorted(missing)}.")
            if extra:
                errors.append(f"operations not in inventory (use inventory ids verbatim): {sorted(extra)}.")
            for oid in set(ops) & set(inventory):
                m, p = inventory[oid]
                if (ops[oid].method, ops[oid].path) != (m.upper(), p):
                    errors.append(f"op '{oid}': method/path must be {m.upper()} {p}.")
        spec = ctx.get("spec")

        if spec is not None:
            for ptr in sorted(set(_all_pointers(self))):
                if not _resolves(spec, ptr):
                    errors.append(f"evidence pointer '{ptr}' does not resolve in the spec.")

        if errors:
            raise ValueError("DomainModel rejected:\n- " + "\n- ".join(errors))
        return self


class DeclineTask(_Wire):
    """Structured refusal returned to the Manager."""

    reason: Literal["out_of_scope", "input_not_openapi"]
    requested: Sentence = Field(description="What the task asked for, in one sentence.")
    detail: Sentence = Field(description="Why a02 does not do it. Name no other agent; the Manager routes.")


# --------------------------------------------------------------------------- helpers

def _all_pointers(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            value = getattr(obj, name)
            if name == "evidence":
                out.extend(value)
            else:
                out.extend(_all_pointers(value))
    elif isinstance(obj, list):
        for x in obj:
            out.extend(_all_pointers(x))
    return out


def _resolves(doc: Any, pointer: str) -> bool:
    node = doc
    for raw in pointer[2:].split("/") if pointer != "#/" else []:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            return False
    return True


def assert_wire_contract(model: type[BaseModel] = DomainModel, _seen: set | None = None) -> None:
    """Fails if any field in the wire model has a default (a03 lesson). Use in CI."""
    seen = _seen if _seen is not None else set()
    if model in seen:
        return
    seen.add(model)
    assert model.model_config.get("extra") == "forbid", f"{model.__name__}: extra must be 'forbid'"
    for name, f in model.model_fields.items():
        assert f.is_required(), f"{model.__name__}.{name} has a default; wire fields must be required"
        for sub in _nested_models(f.annotation):
            assert_wire_contract(sub, seen)


def _nested_models(tp: Any) -> list[type[BaseModel]]:
    from typing import get_args
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return [tp]
    return [m for a in get_args(tp) for m in _nested_models(a)]


def _strip_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(x) for x in node]
    return node


def build_tools() -> list[dict]:
    return [
        {
            "name": "emit_domain_model",
            "description": (
                "Emits the complete DomainModel (entities, operations, workflows, rules, gaps, glossary) "
                "extracted from the OpenAPI document. Use whenever the task is to model the target "
                "system's domain; call it once with the full model, and again in full if validation "
                "errors are returned."),
            "input_schema": _strip_titles(DomainModel.model_json_schema()),
        },
        {
            "name": "decline_task",
            "description": (
                "Returns a structured refusal to the Manager. Use instead of emit_domain_model only when "
                "the task asks for something other than a domain model (test scenarios, defect verdicts, "
                "prioritised bug lists, code) or the input is not an OpenAPI document."),
            "input_schema": _strip_titles(DeclineTask.model_json_schema()),
        },
    ]


if __name__ == "__main__":
    assert_wire_contract()
    assert_wire_contract(DeclineTask)
    print(json.dumps(build_tools(), indent=2))
