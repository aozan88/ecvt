"""Read an OpenAPI 3 spec into a compact digest for the Scenario Generator (M1-T1).

The raw `openapi.json` of even a tiny API is mostly noise for a scenario-writing
prompt. This module keeps what matters — endpoint, parameters, request body
fields and their types/constraints, response codes — and renders it as short
text, so the prompt prefix stays small enough to cache (ADR 0005).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_REF_DEPTH = 8


@dataclass(frozen=True)
class Param:
    name: str
    location: str
    required: bool
    type_: str
    constraints: str = ""

    def render(self) -> str:
        bits = [self.location, self.type_, "required" if self.required else "optional"]
        if self.constraints:
            bits.append(self.constraints)
        return f"{self.name} ({', '.join(bits)})"


@dataclass(frozen=True)
class BodyField:
    name: str
    type_: str
    required: bool
    default: Any = None
    constraints: str = ""

    def render(self) -> str:
        bits = [self.type_, "required" if self.required else "optional"]
        if self.default is not None:
            bits.append(f"default={self.default!r}")
        if self.constraints:
            bits.append(self.constraints)
        return f"{self.name} ({', '.join(bits)})"


@dataclass
class Endpoint:
    method: str
    path: str
    summary: str = ""
    params: list[Param] = field(default_factory=list)
    body_fields: list[BodyField] = field(default_factory=list)
    body_required: bool = False
    status_codes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"

    def render(self) -> str:
        lines = [f"{self.key}" + (f" — {self.summary}" if self.summary else "")]
        if self.params:
            lines.append("  params: " + "; ".join(p.render() for p in self.params))
        if self.body_fields:
            tag = "body" if self.body_required else "body (optional)"
            lines.append(f"  {tag}: " + "; ".join(f.render() for f in self.body_fields))
        if self.status_codes:
            lines.append("  documented responses: " + ", ".join(self.status_codes))
        return "\n".join(lines)


@dataclass
class ApiDigest:
    title: str
    version: str
    endpoints: list[Endpoint]

    def render(self) -> str:
        head = f"API: {self.title} (spec version {self.version})"
        return "\n".join([head, ""] + [e.render() for e in self.endpoints])

    def endpoint_keys(self) -> list[str]:
        return [e.key for e in self.endpoints]


def _resolve(node: Any, spec: dict[str, Any], depth: int = 0) -> Any:
    """Follow local $ref pointers. Unresolvable or too-deep refs are left as-is."""
    if depth > MAX_REF_DEPTH or not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return node
    target: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(target, dict) or part not in target:
            return node
        target = target[part]
    return _resolve(target, spec, depth + 1)


def _type_name(schema: dict[str, Any]) -> str:
    if "anyOf" in schema or "oneOf" in schema:
        options = schema.get("anyOf") or schema.get("oneOf") or []
        names = [_type_name(o) for o in options if isinstance(o, dict)]
        return " | ".join(dict.fromkeys(names)) or "any"
    if "enum" in schema:
        return "enum[" + ", ".join(repr(v) for v in schema["enum"]) + "]"
    t = schema.get("type")
    if t == "array":
        items = schema.get("items")
        inner = _type_name(items) if isinstance(items, dict) else "any"
        return f"array<{inner}>"
    if isinstance(t, list):
        return " | ".join(str(x) for x in t)
    return str(t) if t else "any"


def _constraints(schema: dict[str, Any]) -> str:
    keys = (
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "multipleOf",
    )
    found = [f"{k}={schema[k]}" for k in keys if k in schema]
    return ", ".join(found)


def _body_fields(operation: dict[str, Any], spec: dict[str, Any]) -> tuple[list[BodyField], bool]:
    body = _resolve(operation.get("requestBody") or {}, spec)
    if not isinstance(body, dict) or not body:
        return [], False
    content = body.get("content") or {}
    media = content.get("application/json") or next(iter(content.values()), {})
    schema = _resolve((media or {}).get("schema") or {}, spec)
    if not isinstance(schema, dict):
        return [], bool(body.get("required"))
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields = []
    for name, raw in props.items():
        prop = _resolve(raw, spec)
        if not isinstance(prop, dict):
            prop = {}
        fields.append(
            BodyField(
                name=name,
                type_=_type_name(prop),
                required=name in required,
                default=prop.get("default"),
                constraints=_constraints(prop),
            )
        )
    return fields, bool(body.get("required"))


def _params(operation: dict[str, Any], path_item: dict[str, Any], spec: dict[str, Any]) -> list[Param]:
    raw = list(path_item.get("parameters") or []) + list(operation.get("parameters") or [])
    out: list[Param] = []
    for item in raw:
        param = _resolve(item, spec)
        if not isinstance(param, dict) or "name" not in param:
            continue
        schema = _resolve(param.get("schema") or {}, spec)
        if not isinstance(schema, dict):
            schema = {}
        out.append(
            Param(
                name=str(param["name"]),
                location=str(param.get("in", "query")),
                required=bool(param.get("required")),
                type_=_type_name(schema),
                constraints=_constraints(schema),
            )
        )
    return out


def parse_openapi(spec: dict[str, Any]) -> ApiDigest:
    """Turn a parsed OpenAPI document into an ApiDigest."""
    info = spec.get("info") or {}
    endpoints: list[Endpoint] = []
    methods = {"get", "post", "put", "patch", "delete"}

    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in methods or not isinstance(operation, dict):
                continue
            body_fields, body_required = _body_fields(operation, spec)
            endpoints.append(
                Endpoint(
                    method=method.upper(),
                    path=str(path),
                    summary=str(operation.get("summary") or operation.get("operationId") or ""),
                    params=_params(operation, path_item, spec),
                    body_fields=body_fields,
                    body_required=body_required,
                    status_codes=sorted(str(c) for c in (operation.get("responses") or {})),
                )
            )

    endpoints.sort(key=lambda e: (e.path, e.method))
    return ApiDigest(
        title=str(info.get("title") or "untitled API"),
        version=str(info.get("version") or "unknown"),
        endpoints=endpoints,
    )


def read_openapi(path: str | Path) -> ApiDigest:
    """Load an OpenAPI spec from a local JSON file."""
    raw = Path(path).read_text(encoding="utf-8")
    spec = json.loads(raw)
    if not isinstance(spec, dict):
        # A malformed spec file is bad input data, not a caller type error:
        # callers handle spec problems as ValueError alongside "no endpoints".
        raise ValueError(f"{path}: OpenAPI document must be a JSON object")  # noqa: TRY004
    digest = parse_openapi(spec)
    if not digest.endpoints:
        raise ValueError(f"{path}: no usable endpoints found in spec")
    return digest
