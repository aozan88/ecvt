"""Built-in Tools: server-side tools that Anthropic executes.

Compare with multi_tool.py: there is no run_tool and no tool_result here. You declare the tool,
it runs on Anthropic's side, and the result arrives in the same response. The distinction to
remember is who executes the tool, not what it does.

stop_reason "pause_turn" means a server tool hit its iteration limit: resend to continue.

    python examples/ccaf/builtin_tools.py code   # code execution (default)
    python examples/ccaf/builtin_tools.py web    # web search, billed per search; not on Haiku
"""

from __future__ import annotations

import os
import sys

import anthropic
from dotenv import load_dotenv

load_dotenv()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
MAX_CONTINUATIONS = 3
client = anthropic.Anthropic()

DEMOS = {
    "code": (
        {"type": "code_execution_20260521", "name": "code_execution"},
        "Calculate the mean and standard deviation of [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].",
    ),
    "web": (
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 2},
        "What does the Model Context Protocol standardize? Answer in two sentences.",
    ),
}


def print_block(block) -> None:
    if block.type == "text":
        print(block.text)
    elif block.type == "server_tool_use":
        print(f"[server tool] {block.name}: {block.input}")
    elif block.type == "bash_code_execution_tool_result":
        result = block.content
        if result.type == "bash_code_execution_result":
            print(f"[exit {result.return_code}] stdout: {result.stdout}{result.stderr}")
        else:
            print(f"[tool error] {result.error_code}")
    elif block.type == "web_search_tool_result":
        # Server tool errors don't raise: a list is success, a single object is an error.
        if isinstance(block.content, list):
            for hit in block.content:
                print(f"[result] {hit.title} - {hit.url}")
        else:
            print(f"[tool error] {block.content.error_code}")


def main(demo: str) -> None:
    tool, question = DEMOS[demo]
    messages = [{"role": "user", "content": question}]

    for _ in range(MAX_CONTINUATIONS):
        response = client.messages.create(
            model=MODEL, max_tokens=16000, messages=messages, tools=[tool]
        )
        for block in response.content:
            print_block(block)
        print(f"stop_reason={response.stop_reason}")
        if response.stop_reason != "pause_turn":
            return
        messages = [
            {"role": "user", "content": question},
            {"role": "assistant", "content": response.content},
        ]
    print(f"Still paused after {MAX_CONTINUATIONS} continuations.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "code")
