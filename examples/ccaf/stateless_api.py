"""API Stateless: the server keeps no conversation, the client resends history every turn.

Watch: the first demo forgets the city, the second remembers it, and input_tokens grow on
every turn. That growth is where context bloat, pruning, caching and compaction come from.

    python examples/ccaf/stateless_api.py
"""

from __future__ import annotations

import os

import anthropic
from dotenv import load_dotenv

load_dotenv()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
client = anthropic.Anthropic()


def ask(messages: list) -> anthropic.types.Message:
    return client.messages.create(model=MODEL, max_tokens=16000, messages=messages)


def text_of(response: anthropic.types.Message) -> str:
    return "".join(block.text for block in response.content if block.type == "text").strip()


def without_history() -> None:
    print("--- Two independent calls (no history) ---")
    ask([{"role": "user", "content": "My favourite city is Izmir. Just say OK."}])
    response = ask([{"role": "user", "content": "What is my favourite city?"}])
    print("CLAUDE:", text_of(response))


def with_history() -> None:
    print("\n--- Same questions, history resent every turn ---")
    messages = []
    for question in (
        "My favourite city is Izmir. Just say OK.",
        "What is my favourite city?",
        "Name one famous dish from there.",
    ):
        messages.append({"role": "user", "content": question})
        response = ask(messages)
        # Append response.content, not just the text: thinking and tool blocks must survive.
        messages.append({"role": "assistant", "content": response.content})
        print(f"[input_tokens={response.usage.input_tokens}] CLAUDE: {text_of(response)}")


if __name__ == "__main__":
    without_history()
    with_history()
