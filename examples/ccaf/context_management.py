"""Context Management: the stateless API means every token you resend costs again.

Pick the lever by the failure it solves:
- count:  how big is this request? Measure before you optimize.
- cache:  a large prefix repeats every call. Any byte change in the prefix invalidates the cache.
- edit:   old tool results bloat the history. Context editing clears them.
- Compaction (not run here, it needs a very large context) summarizes the history instead:
  beta compact-2026-01-12. Append response.content so compaction blocks survive.

    python examples/ccaf/context_management.py count
    python examples/ccaf/context_management.py cache
    python examples/ccaf/context_management.py edit
"""

from __future__ import annotations

import os
import sys

import anthropic
from dotenv import load_dotenv

load_dotenv()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
client = anthropic.Anthropic()

# Large enough to clear every model's minimum cacheable prefix (up to 4096 tokens).
BIG_SYSTEM = "\n".join(
    f"Rule {n}: answer briefly and state which rule number you applied." for n in range(1, 500)
)
QUESTION = [{"role": "user", "content": "Say OK and name one rule number."}]

SEARCH_TOOL = {
    "name": "search_logs",
    "description": "Search application logs for a term.",
    "input_schema": {
        "type": "object",
        "properties": {"term": {"type": "string"}},
        "required": ["term"],
    },
}


def demo_count() -> None:
    messages = []
    for question in ("Name a colour.", "Name a fruit.", "Name a city."):
        messages.append({"role": "user", "content": question})
        tokens = client.messages.count_tokens(model=MODEL, messages=messages).input_tokens
        print(f"{len(messages)} message(s): {tokens} input tokens")
        messages.append({"role": "assistant", "content": "A short answer."})


def print_cache_usage(label: str, usage) -> None:
    print(
        f"{label}: written={usage.cache_creation_input_tokens} "
        f"read={usage.cache_read_input_tokens} uncached={usage.input_tokens}"
    )


def demo_cache() -> None:
    def call(system: str):
        return client.messages.create(
            model=MODEL,
            max_tokens=16000,
            cache_control={"type": "ephemeral"},
            system=system,
            messages=QUESTION,
        ).usage

    print_cache_usage("1st call, cold         ", call(BIG_SYSTEM))
    print_cache_usage("2nd call, same prefix  ", call(BIG_SYSTEM))
    print_cache_usage("3rd call, prefix edited", call("Today is Monday.\n" + BIG_SYSTEM))


def fake_tool_round(n: int) -> list:
    """One finished tool call with a bulky result, as an agent's history accumulates them."""
    tool_id = f"toolu_demo_{n}"
    return [
        {"role": "user", "content": f"Search the logs for error {n}."},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": "search_logs", "input": {"term": str(n)}}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": f"log line for error {n}\n" * 400,
                }
            ],
        },
        {"role": "assistant", "content": f"Found the log lines for error {n}."},
    ]


def demo_edit() -> None:
    messages = [message for n in (1, 2, 3) for message in fake_tool_round(n)]
    messages.append({"role": "user", "content": "How many searches did you run? One sentence."})

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["context-management-2025-06-27"],
        context_management={
            "edits": [
                {
                    "type": "clear_tool_uses_20250919",
                    "trigger": {"type": "input_tokens", "value": 1000},
                    "keep": {"type": "tool_uses", "value": 1},
                }
            ]
        },
        tools=[SEARCH_TOOL],
        messages=messages,
    )

    print(f"input_tokens after editing: {response.usage.input_tokens}")
    print(f"applied_edits: {response.context_management.applied_edits}")
    print("CLAUDE:", "".join(b.text for b in response.content if b.type == "text"))


DEMOS = {"count": demo_count, "cache": demo_cache, "edit": demo_edit}

if __name__ == "__main__":
    DEMOS[sys.argv[1] if len(sys.argv) > 1 else "count"]()
