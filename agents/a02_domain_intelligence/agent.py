"""Domain Intelligence (a02) — OpenAPI spec in, schema-valid DomainModel out (M2-T1).

Pipeline position: first stage after the Manager. Its only consumer is the Scenario
Generator, which turns the model into test scenarios.

Division of labour
- Code: reads the spec, builds the operation inventory (ids are decided here, never by
  the model), validates the output against the spec.
- Model: fills the DomainModel, or declines a task that is not domain modelling.

Messages API details (ADR 0009, Project 02 notes)
- Two tools, `tool_choice: any`: the model must call emit_domain_model or decline_task.
  Extended thinking cannot be combined with a forced tool, so the schema itself makes
  the model reason per operation (three verdicts each).
- SKILL.md is not loaded by the Messages API; it is sent as the second system block and
  the cache breakpoint sits on it, so system prompt + skill are cached together.
- The spec is re-serialised from the parsed document: comments in the file never reach
  the model, and every JSON Pointer it cites still resolves.

CLI:
    python -m agents.a02_domain_intelligence.agent --spec examples/wallet-api/openapi.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agents.a02_domain_intelligence.schema import DeclineTask, DomainModel, build_tools
from ecvt.llm import (
    DEFAULT_SUBAGENT_MODEL,
    StructuredCallError,
    StructuredResult,
    load_env,
    structured_call,
)

AGENT_DIR = Path(__file__).resolve().parent
DEFAULT_TASK = "Build the domain model for this target."
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

Inventory = dict[str, tuple[str, str]]  # op id -> (METHOD, path)


def load_system_prompt() -> str:
    return (AGENT_DIR / "system_prompt.md").read_text(encoding="utf-8")


def load_skill() -> str:
    """SKILL.md without its YAML front matter (that part is for Claude Code, not the model)."""
    text = (AGENT_DIR / "SKILL.md").read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    return text.strip()


def load_spec(path: str | Path) -> dict[str, Any]:
    """Parse an OpenAPI document, JSON or YAML (JSON is valid YAML)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "paths" not in data:
        raise ValueError(f"{path} is not an OpenAPI document (no `paths`)")
    return data


def _snake(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    if not text or not text[0].isalpha():
        text = f"op_{text}"
    return text[:64]


def build_inventory(spec: dict[str, Any]) -> Inventory:
    """One row per operation, in spec order.

    Id rule (the eval rubrics depend on it): `operationId` in snake_case, else
    `{method}_{path}` in snake_case — POST /orders -> post_orders,
    GET /orders/{orderId} -> get_orders_order_id. A clash gets a numeric suffix.
    """
    inventory: Inventory = {}
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            base = _snake(op.get("operationId") or f"{method}_{path}")
            oid, n = base, 2
            while oid in inventory:
                oid, n = f"{base[:60]}_{n}", n + 1
            inventory[oid] = (method.upper(), path)
    return inventory


def render_spec(spec: dict[str, Any]) -> str:
    return yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=120).strip()


def build_user_prompt(task: str, inventory: Inventory, spec: dict[str, Any]) -> str:
    rows = "\n".join(f"{oid} | {m} | {p}" for oid, (m, p) in inventory.items())
    return (
        f"<task>{task}</task>\n"
        f"<operation_inventory>\n{rows}\n</operation_inventory>\n"
        f"<spec>\n{render_spec(spec)}\n</spec>"
    )


@dataclass
class DomainResult:
    """What a02 returned: exactly one of `model` / `declined` is set."""

    model: DomainModel | None
    declined: DeclineTask | None
    inventory: Inventory
    call: StructuredResult

    def payload(self) -> dict[str, Any]:
        obj = self.model if self.model is not None else self.declined
        return obj.model_dump(mode="json") if obj is not None else {}


def build_domain_model(
    *,
    spec_path: str | Path,
    task: str = DEFAULT_TASK,
    client: Any | None = None,
    model: str = DEFAULT_SUBAGENT_MODEL,
    max_attempts: int = 3,
) -> DomainResult:
    """Run a02 on one spec.

    max_attempts=3, not 2: DomainModel validates in two layers and Pydantic
    reports them in sequence, not together. A field error (an entity whose
    identifier is not among its fields) stops validation before the model-level
    validator runs, so the cross-field rules (a `not_specified` verdict needs a
    matching gap) only become visible once the field error is fixed. Two
    attempts could surface the second layer but never answer it — measured on
    wallet-api, where a02 failed attempt 1 on the identifier and attempt 2 on an
    ownership gap (2026-11-24).
    """
    spec = load_spec(spec_path)
    inventory = build_inventory(spec)
    context = {"spec": spec, "inventory": inventory}

    def check_model(data: dict[str, Any]) -> None:
        DomainModel.model_validate(data, context=context)

    result = structured_call(
        system=[load_system_prompt(), load_skill()],
        user=build_user_prompt(task, inventory, spec),
        tool=build_tools(),
        validate={"emit_domain_model": check_model, "decline_task": DeclineTask.model_validate},
        client=client,
        model=model,
        max_tokens=16000,
        max_attempts=max_attempts,
    )
    if result.tool_name == "decline_task":
        return DomainResult(None, DeclineTask.model_validate(result.data), inventory, result)
    dm = DomainModel.model_validate(result.data, context=context)
    return DomainResult(dm, None, inventory, result)


def summarize(dm: DomainModel) -> dict[str, int]:
    return {
        "entities": len(dm.entities),
        "operations": len(dm.operations),
        "workflows": len(dm.workflows),
        "rules": len(dm.rules),
        "gaps": len(dm.gaps),
        "glossary": len(dm.glossary),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an ECVT DomainModel from an OpenAPI spec.")
    parser.add_argument("--spec", default="examples/wallet-api/openapi.json")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--out", default="artifacts/domain_model.json")
    parser.add_argument("--model", default=DEFAULT_SUBAGENT_MODEL)
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the inventory and prompt; no API call."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env()
    args = _parse_args(argv)
    if args.dry_run:
        spec = load_spec(args.spec)
        print(build_user_prompt(args.task, build_inventory(spec), spec))
        return 0
    try:
        res = build_domain_model(spec_path=args.spec, task=args.task, model=args.model)
    except StructuredCallError as exc:
        print(f"Domain modelling failed: {exc}", file=sys.stderr)
        for line in exc.attempts:
            print(f"  - {line}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res.payload(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if res.declined:
        print(f"declined ({res.declined.reason}): {res.declined.detail}")
    else:
        print(", ".join(f"{k}={v}" for k, v in summarize(res.model).items()) + f" -> {out}")
    print(f"attempts: {res.call.attempts}")
    print(f"usage: {json.dumps(res.call.usage.as_dict())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
