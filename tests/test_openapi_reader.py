"""OpenAPI digest tests (M1-T1 DoD: 'OpenAPI parse testi geçiyor')."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecvt.openapi_reader import parse_openapi, read_openapi

WALLET_SPEC = Path("examples/wallet-api/openapi.json")
FIXTURES = Path("tests/fixtures")


def test_wallet_spec_digest():
    digest = read_openapi(WALLET_SPEC)

    assert digest.title == "Wallet API (buggy demo)"
    assert digest.endpoint_keys() == [
        "POST /accounts",
        "GET /accounts/{account_id}/balance",
        "GET /accounts/{account_id}/transactions",
        "POST /transfers",
    ]


def test_wallet_request_bodies_are_dereferenced():
    """Body fields come from components/schemas via $ref, not left as a ref blob."""
    digest = read_openapi(WALLET_SPEC)
    by_key = {e.key: e for e in digest.endpoints}

    accounts = by_key["POST /accounts"]
    fields = {f.name: f for f in accounts.body_fields}
    assert set(fields) == {"owner", "initial_balance"}
    assert fields["owner"].required is True
    assert fields["initial_balance"].required is False
    assert fields["initial_balance"].default == 0.0

    transfers = by_key["POST /transfers"]
    assert {f.name for f in transfers.body_fields} == {"from_account", "to_account", "amount"}
    assert all(f.required for f in transfers.body_fields)


def test_path_params_captured():
    digest = read_openapi(WALLET_SPEC)
    balance = next(e for e in digest.endpoints if e.path.endswith("/balance"))
    assert [(p.name, p.location, p.required) for p in balance.params] == [
        ("account_id", "path", True)
    ]


def test_render_is_compact_and_mentions_every_endpoint():
    digest = read_openapi(WALLET_SPEC)
    rendered = digest.render()
    raw = WALLET_SPEC.read_text(encoding="utf-8")

    for key in digest.endpoint_keys():
        assert key in rendered
    assert "Wallet API (buggy demo)" in rendered
    # The whole point of the digest is to be far smaller than the spec.
    assert len(rendered) < len(raw) / 2


def test_status_codes_listed():
    digest = read_openapi(WALLET_SPEC)
    accounts = next(e for e in digest.endpoints if e.key == "POST /accounts")
    assert "200" in accounts.status_codes


def test_single_get_fixture():
    digest = read_openapi(FIXTURES / "openapi_single_get.json")
    assert digest.endpoint_keys() == ["GET /status/{node_id}"]
    endpoint = digest.endpoints[0]
    assert endpoint.body_fields == []
    locations = {p.name: p.location for p in endpoint.params}
    assert locations == {"node_id": "path", "verbose": "query"}


def test_no_bodies_fixture_resolves_param_ref():
    digest = read_openapi(FIXTURES / "openapi_no_bodies.json")
    archive = next(e for e in digest.endpoints if e.key == "POST /items/{item_id}/archive")
    item_id = next(p for p in archive.params if p.name == "item_id")
    assert item_id.type_ == "string"

    listing = next(e for e in digest.endpoints if e.key == "GET /items")
    limit = next(p for p in listing.params if p.name == "limit")
    assert limit.type_ == "integer"
    assert "minimum=1" in listing.render()
    assert "maximum=100" in listing.render()


def test_spec_without_endpoints_is_an_error(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"openapi": "3.1.0", "info": {}, "paths": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="no usable endpoints"):
        read_openapi(path)


def test_non_object_spec_is_an_error(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        read_openapi(path)


def test_unresolvable_ref_does_not_crash():
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Dangling", "version": "1"},
        "paths": {
            "/x": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Gone"}}
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    digest = parse_openapi(spec)
    assert digest.endpoint_keys() == ["POST /x"]
    assert digest.endpoints[0].body_fields == []


def test_non_http_keys_in_path_item_ignored():
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Mixed", "version": "1"},
        "paths": {
            "/y": {
                "summary": "not an operation",
                "parameters": [],
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        },
    }
    assert parse_openapi(spec).endpoint_keys() == ["GET /y"]


def test_anyof_and_enum_type_names():
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Types", "version": "1"},
        "paths": {
            "/z": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["kind"],
                                    "properties": {
                                        "kind": {"enum": ["a", "b"]},
                                        "note": {
                                            "anyOf": [{"type": "string"}, {"type": "null"}]
                                        },
                                        "tags": {"type": "array", "items": {"type": "string"}},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    fields = {f.name: f.type_ for f in parse_openapi(spec).endpoints[0].body_fields}
    assert fields["kind"].startswith("enum[")
    assert fields["note"] == "string | null"
    assert fields["tags"] == "array<string>"
