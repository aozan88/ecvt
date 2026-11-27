"""Structured Claude calls with validation retry and token accounting (M1-T1).

ECVT's subagents are narrow transforms: spec in, schema-valid JSON out. They use
the Messages API with a forced tool call, which keeps the request to just the
system prompt plus the task — no agent harness context (ADR 0005). The Manager
(M2) is the component that uses the Agent SDK's subagent orchestration.

Validation failures are fed back to the model as a correction turn rather than
raised, so a near-miss costs one cheap retry instead of the whole run (ADR 0006
in spirit: failures are data).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

DEFAULT_SUBAGENT_MODEL = os.environ.get("ECVT_SUBAGENT_MODEL", "claude-haiku-5-5")
DEFAULT_MANAGER_MODEL = os.environ.get("ECVT_MANAGER_MODEL", "claude-sonnet-5-5")
PRICING_PATH = Path(__file__).resolve().parent.parent / "config" / "pricing.json"


def load_env() -> None:
    """Read `.env` into the environment. Every entry point calls this first.

    `.env` is where the project tells people to keep ANTHROPIC_API_KEY
    (`.env.example`), but only the eval runner read it: `ecvt run` and each
    agent's own CLI died with "Could not resolve authentication method" unless
    the key happened to be exported, including the commands in CLAUDE.md. It
    lives here because every entry point that needs the key already imports
    this module. `load_dotenv` does not override a variable that is already
    set, so the shell still wins.
    """
    from dotenv import load_dotenv

    load_dotenv()


class _MessagesClient(Protocol):
    """The slice of anthropic.Anthropic this module uses (so tests can fake it)."""

    @property
    def messages(self) -> Any: ...


@dataclass
class Tokens:
    """Token counts for one pricing tier."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0

    @property
    def prompt_tokens(self) -> int:
        """Everything billed as prompt — what the length tier is decided on."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens

    def cost_usd(self, prices: dict[str, Any]) -> float:
        return (
            self.input_tokens * float(prices.get("input", 0.0))
            + self.output_tokens * float(prices.get("output", 0.0))
            + self.cache_read_tokens * float(prices.get("cache_read", 0.0))
            + self.cache_write_tokens * float(prices.get("cache_write", 0.0))
        ) / 1_000_000


@dataclass
class Usage:
    """Token counts across calls, split by pricing tier, plus cost.

    Anthropic prices some models in two tiers: a request whose prompt exceeds a
    threshold is billed at higher rates (Haiku 5.5: 5x above 100k). The tier is
    a property of each individual request, so counts are bucketed per call as
    they arrive — summing first and pricing later would bill a run of small
    calls at the long-prompt rate as soon as the totals crossed the line.
    """

    model: str
    standard: Tokens = field(default_factory=Tokens)
    long_prompt: Tokens = field(default_factory=Tokens)

    def add_response(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            self.standard.calls += 1
            return

        call = Tokens(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            calls=1,
        )
        bucket = (
            self.long_prompt
            if call.prompt_tokens > self._long_prompt_threshold()
            else self.standard
        )
        bucket.input_tokens += call.input_tokens
        bucket.output_tokens += call.output_tokens
        bucket.cache_read_tokens += call.cache_read_tokens
        bucket.cache_write_tokens += call.cache_write_tokens
        bucket.calls += 1

    def _long_prompt_threshold(self) -> float:
        prices = _load_pricing().get(self.model) or {}
        threshold = prices.get("long_prompt_threshold")
        if isinstance(threshold, (int, float)) and threshold > 0:
            return float(threshold)
        return float("inf")

    @property
    def calls(self) -> int:
        return self.standard.calls + self.long_prompt.calls

    @property
    def input_tokens(self) -> int:
        return self.standard.input_tokens + self.long_prompt.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.standard.output_tokens + self.long_prompt.output_tokens

    @property
    def cache_read_tokens(self) -> int:
        return self.standard.cache_read_tokens + self.long_prompt.cache_read_tokens

    @property
    def cache_write_tokens(self) -> int:
        return self.standard.cache_write_tokens + self.long_prompt.cache_write_tokens

    @property
    def total_tokens(self) -> int:
        return self.standard.total_tokens + self.long_prompt.total_tokens

    def cost_usd(self) -> float | None:
        """Cost in USD, or None when this model has no price configured.

        Prices live in `config/pricing.json`, in USD per million tokens. A model
        with no entry reports None rather than 0.0 — an unpriced model is
        unknown, not free.
        """
        prices = _load_pricing().get(self.model)
        if not prices:
            return None

        cost = self.standard.cost_usd(prices)
        if self.long_prompt.calls:
            long_prices = prices.get("long_prompt")
            if not isinstance(long_prices, dict) or not long_prices:
                return None  # priced model, but not for the tier actually used
            cost += self.long_prompt.cost_usd(long_prices)
        return round(cost, 6)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd(),
        }
        if self.long_prompt.calls:
            data["long_prompt_calls"] = self.long_prompt.calls
        return data


def _load_pricing() -> dict[str, dict[str, float]]:
    if not PRICING_PATH.exists():
        return {}
    try:
        data = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        return {}
    return {k: v for k, v in models.items() if isinstance(v, dict) and v}


class StructuredCallError(RuntimeError):
    """The model never produced output that satisfied the validator."""

    def __init__(self, message: str, attempts: list[str], usage: Usage) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.usage = usage

    def explain(self, what: str, limit: int = 1200) -> str:
        """A failure message that says what was wrong, not just how often.

        The exception's own message is "<tool> produced no valid output in N
        attempts" and names no rule; the validator errors are in `attempts`.
        Dropping them left a failure with nothing to act on — the M3 chain lost
        the reason for 3 a03 failures in 18 runs that way.
        """
        last = self.attempts[-1] if self.attempts else ""
        if not last:
            return f"{what}: {self}"
        return f"{what}: {self}; last error: {last[:limit]}"


@dataclass
class StructuredResult:
    data: dict[str, Any]
    usage: Usage
    attempts: int
    corrections: list[str] = field(default_factory=list)
    tool_name: str | None = None
    """Which tool the model called — matters when it may choose between several."""


STRICT_UNSUPPORTED = (
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "maxItems", "uniqueItems", "pattern",
)  # fmt: skip
"""JSON Schema keywords a `strict: true` tool may not carry (structured outputs'
JSON Schema limitations). They are not dropped silently: each moves into the
field's description, and the Pydantic validator still enforces it."""


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A Pydantic JSON schema rewritten for a `strict: true` tool (ADR 0019).

    - every object is closed (`additionalProperties: false`) and lists all of
      its properties as required: an optional field is exactly what a strict
      grammar can still leave out, and each one counts toward the API's limit
      of 24 per request;
    - `oneOf` becomes `anyOf` and `discriminator` goes: strict schemas accept
      only `anyOf`, and the branches are told apart by their `const` anyway;
    - `default` goes (the field is required now), and so do the constraints in
      STRICT_UNSUPPORTED and `minItems` above 1, written into the description.

    An object with no listed properties can only ever be `{}` under strict
    sampling, so it is refused here rather than allowed to lose its content.
    """

    def walk(node: Any, where: str) -> Any:
        if isinstance(node, list):
            return [walk(x, where) for x in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        notes: list[str] = []
        for key, value in node.items():
            if key in ("default", "discriminator"):
                continue
            if key in STRICT_UNSUPPORTED or (key == "minItems" and value > 1):
                notes.append(f"{key}: {value}")
                continue
            if key == "properties":
                out[key] = {k: walk(v, f"{where}.{k}") for k, v in value.items()}
            elif key == "$defs":
                out[key] = {k: walk(v, k) for k, v in value.items()}
            else:
                out["anyOf" if key == "oneOf" else key] = walk(value, where)
        if out.get("type") == "object":
            props = out.get("properties")
            if not props:
                raise ValueError(
                    f"{where}: an object without listed properties cannot carry content "
                    "in a strict tool; give it fixed fields or a name/value list"
                )
            out["required"] = list(props)
            out["additionalProperties"] = False
        if notes:
            text = out.get("description", "")
            out["description"] = (text + " " if text else "") + "(" + ", ".join(notes) + ")"
        return out

    return walk(schema, "$")


def strict_counts(schema: dict[str, Any]) -> dict[str, int]:
    """Optional and union-typed parameters in a schema, as the API counts them
    against its strict limits (24 and 16 per request)."""
    counts = {"optional": 0, "union": 0}

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x)
            return
        if not isinstance(node, dict):
            return
        props = node.get("properties")
        if isinstance(props, dict):
            required = set(node.get("required") or [])
            counts["optional"] += sum(1 for k in props if k not in required)
            counts["union"] += sum(
                1
                for v in props.values()
                if isinstance(v, dict)
                and ("anyOf" in v or isinstance(v.get("type"), list)
                     or "anyOf" in (v.get("items") or {}))
            )  # fmt: skip
        for value in node.values():
            walk(value)

    walk(schema)
    return counts


def coerce_json_strings(data: dict[str, Any]) -> dict[str, Any]:
    """Parse top-level values the model sent as JSON text instead of structure.

    With a large tool input, Claude sometimes serializes an array or object into
    a string: `{"scenarios": "[{...}, {...}]"}` rather than `{"scenarios": [...]}`.
    The content is correct, only the encoding is off, so rejecting it costs a
    full retry — in the first wallet-api run that was ~10k wasted output tokens.

    Only top-level string values that parse as a JSON array or object are
    touched, and only when parsing succeeds; anything else is passed through
    untouched so genuine type errors still reach the validator.
    """
    out = dict(data)
    for key, value in data.items():
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text.startswith(("[", "{")):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, (list, dict)):
            out[key] = parsed
    return out


def json_string_problems(data: dict[str, Any]) -> list[str]:
    """Explain top-level values sent as JSON text that does not parse.

    `coerce_json_strings` leaves these alone, so the validator only says "Input
    should be a valid list" — which tells the model nothing it can fix. Measured
    on a03: the model then re-sent the identical broken string on every retry
    (same length, same bad character) and exhausted four attempts, 2 runs in 12.
    Naming the syntax error and where it is gives the retry something to act on.
    """
    problems = []
    for key, value in data.items():
        if not isinstance(value, str) or not value.strip().startswith(("[", "{")):
            continue
        text = value.strip()
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            near = text[max(0, exc.pos - 40) : exc.pos + 20]
            kind = "array" if text.startswith("[") else "object"
            problems.append(
                f"`{key}` arrived as a string of JSON text, and that text is not valid JSON: "
                f"{exc.msg} at character {exc.pos}, near …{near}…. Send `{key}` as a JSON "
                f"{kind} itself, not as a string, and check the brackets around that point."
            )
    return problems


def _tool_use_block(response: Any, tool_names: set[str]) -> Any | None:
    """The first tool_use block for one of `tool_names`, or None.

    The block itself, not just its input: a correction turn has to answer it by
    id, and the block is what carries the model's rejected object back into the
    next request.
    """
    for block in getattr(response, "content", None) or []:
        name = getattr(block, "name", None)
        is_call = getattr(block, "type", None) == "tool_use" and name in tool_names
        if is_call and isinstance(getattr(block, "input", None), dict):
            return block
    return None


def _correction_instruction(label: str) -> str:
    return (
        f"Call {label} again. Fix only what the error names and keep everything "
        "else exactly as you had it. Return the complete object, not a patch."
    )


def _correction_messages(
    user: str, response: Any, block: Any, failure: str, label: str
) -> list[dict[str, Any]]:
    """The conversation for one more attempt, with the rejected attempt still in it.

    The rejected tool_use stays in the transcript and the error comes back as its
    `tool_result`, so "keep everything else" is an instruction the model can
    follow — it can see its own object. Replacing the attempt with a bare user
    message made every retry a fresh generation instead: a02 on wallet-api failed
    attempt 1 on an entity identifier and attempt 2 on an unrelated ownership gap,
    5 of 12 eval cases passing (2026-11-24). The errors moved rather than
    converged, because there was nothing to converge on.
    """
    head: list[dict[str, Any]] = [{"role": "user", "content": user}]
    instruction = _correction_instruction(label)
    if block is None or getattr(block, "id", None) is None:
        # Nothing to answer: the model sent no tool call (or a fake client sent
        # no id). Say it in a plain turn rather than forge a tool_result.
        return head + [
            {
                "role": "user",
                "content": (
                    f"Your previous output was rejected by schema validation:\n\n"
                    f"{failure}\n\n{instruction}"
                ),
            }
        ]
    return head + [
        {"role": "assistant", "content": list(getattr(response, "content", None) or [])},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "is_error": True,
                    "content": (
                        f"Your previous output was rejected by schema validation:\n\n"
                        f"{failure}\n\n{instruction}"
                    ),
                }
            ],
        },
    ]


def _stuck(failures: list[str]) -> bool:
    """The last two attempts were rejected for the same reason."""
    if len(failures) < 2:
        return False
    last, previous = (f.split(": ", 1)[-1] for f in failures[-2:][::-1])  # drop "attempt N: "
    return last == previous


FORCED_TOOL_UNSUPPORTED = (
    "claude-opus-5-5", "claude-sonnet-5-5",
    "claude-fable-5-1", "claude-fable-5-2", "claude-mythos-5-1", "claude-mythos-5-2",
)  # fmt: skip
"""Models that answer a forced `tool_choice` (`tool` / `any`) with a 400. On these
the call uses `auto` and relies on the prompt plus `strict: true`; a reply with no
tool call is a failed attempt like any other, so the retry loop covers it."""


def tool_choice_for(model: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
    if model.startswith(FORCED_TOOL_UNSUPPORTED):
        return {"type": "auto"}
    if len(tools) == 1:
        return {"type": "tool", "name": tools[0]["name"]}
    return {"type": "any"}


def structured_call(
    *,
    system: str | list[str],
    user: str,
    tool: dict[str, Any] | list[dict[str, Any]],
    validate: Any,
    client: _MessagesClient | None = None,
    model: str = DEFAULT_SUBAGENT_MODEL,
    max_tokens: int = 8192,
    max_attempts: int = 3,
    cache_system: bool = True,
    effort: str | None = None,
) -> StructuredResult:
    """Call Claude, force a tool call, and return input that `validate` accepted.

    One tool: it is forced by name. Several tools: the model must call one of
    them (`tool_choice: any`) and `validate` is a dict of tool name -> validator;
    `StructuredResult.tool_name` says which one it chose.

    `system` may be a list of texts (e.g. system prompt, then a skill); each
    becomes its own block and the cache breakpoint goes on the last one, so the
    whole stable prefix is cached.

    A validator should raise on invalid input; its error text is sent back to
    the model as a correction. Raises StructuredCallError if every attempt
    fails — the partial Usage is attached so the caller can still log spend.
    """
    if client is None:
        import anthropic  # imported here so tests need no API key

        client = anthropic.Anthropic()

    texts = [system] if isinstance(system, str) else list(system)
    system_blocks: list[dict[str, Any]] = [{"type": "text", "text": t} for t in texts]
    if cache_system:
        system_blocks[-1]["cache_control"] = {"type": "ephemeral"}

    tools = [tool] if isinstance(tool, dict) else list(tool)
    names = {t["name"] for t in tools}
    tool_choice = tool_choice_for(model, tools)
    label = " or ".join(t["name"] for t in tools)
    extra: dict[str, Any] = {"output_config": {"effort": effort}} if effort else {}
    validators = validate if isinstance(validate, dict) else dict.fromkeys(names, validate)

    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    usage = Usage(model=model)
    failures: list[str] = []

    for attempt in range(1, max_attempts + 1):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
            **extra,
        )
        usage.add_response(response)

        block = _tool_use_block(response, names)
        if block is None:
            failures.append(f"attempt {attempt}: no {label} tool call in response")
        else:
            called = block.name
            data = coerce_json_strings(block.input)
            try:
                validators[called](data)
            except Exception as exc:  # noqa: BLE001 — any validator error becomes feedback
                problems = json_string_problems(block.input)
                failures.append(
                    f"attempt {attempt}: {exc}" + "".join(f"\n{p}" for p in problems)
                )
            else:
                return StructuredResult(
                    data=data,
                    usage=usage,
                    attempts=attempt,
                    corrections=failures,
                    tool_name=called,
                )

        if attempt == max_attempts:
            break
        if _stuck(failures):
            # The same rejection twice in a row: the correction turn is not
            # working, because the model keeps re-sending the object it can see.
            # Measured on a03 — every run that exhausted four attempts failed all
            # four with one identical error, while a fresh generation validates
            # first time in about two of three. So start over instead.
            messages = [{"role": "user", "content": user}]
        else:
            messages = _correction_messages(user, response, block, failures[-1], label)

    raise StructuredCallError(
        f"{label} produced no valid output in {max_attempts} attempts",
        attempts=failures,
        usage=usage,
    )
