"""Tool Use: client-side tools, multi-tool routing and the agent loop.

Watch the "Claude called" lines: one response can hold several tool_use blocks. You execute
them (client-side), then send every result back in one user message.

Rules this script demonstrates:
- N tool_use blocks need N tool_result blocks, or the API returns 400.
- Results match by tool_use_id, not by position.
- The model decides how many tools it calls, not your code.
- The real loop risk is turn explosion (cost and latency), hence MAX_TURNS instead of while True.
- A failing tool comes back as is_error data, so the loop survives (ADR 0006).

    python examples/ccaf/multi_tool.py
    python examples/ccaf/multi_tool.py --drop-result   # break the N-to-N rule, see the 400
"""

from __future__ import annotations

import os
import sys

import anthropic
from dotenv import load_dotenv

load_dotenv()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
MAX_TURNS = 5
client = anthropic.Anthropic()

tools = [
    {
        "name": "get_weather",
        "description": "Get today's current weather for a city.",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "The city to check"}},
            "required": ["city"],
        },
    },
    {
        "name": "get_forecast",
        "description": "Get the weather forecast for the next few days for a city.",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "The city to check"}},
            "required": ["city"],
        },
    },
]


# Mock tools: a real implementation would call a weather API.
def get_weather(city: str) -> str:
    return f"It's 22°C and sunny in {city} today."


def get_forecast(city: str) -> str:
    return f"{city}: 24°C tomorrow, 19°C and rainy after."


def run_tool(name: str, tool_input: dict) -> str:
    if name == "get_weather":
        return get_weather(tool_input["city"])
    if name == "get_forecast":
        return get_forecast(tool_input["city"])
    raise ValueError(f"Unknown tool: {name}")


def tool_result_for(block) -> dict:
    result = {"type": "tool_result", "tool_use_id": block.id}
    try:
        result["content"] = run_tool(block.name, block.input)
    except (KeyError, ValueError) as exc:
        result["content"] = f"Tool failed: {exc}"
        result["is_error"] = True
    return result


def main(drop_result: bool) -> int:
    messages = [
        {
            "role": "user",
            "content": "What's the weather in Istanbul now, and the forecast for London?",
        }
    ]

    for turn in range(1, MAX_TURNS + 1):
        try:
            response = client.messages.create(
                model=MODEL, max_tokens=16000, messages=messages, tools=tools
            )
        except anthropic.BadRequestError as exc:
            print(f"\nAPI rejected the request (400): {exc.message}")
            return 1

        print(f"[turn {turn}] stop_reason={response.stop_reason}")
        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"  Claude called: {block.name}({block.input})")
                tool_results.append(tool_result_for(block))

        if drop_result:
            print(f"  Dropping 1 of {len(tool_results)} tool_result blocks on purpose")
            tool_results.pop()

        messages.append({"role": "user", "content": tool_results})
    else:
        print(f"\nStopped at MAX_TURNS={MAX_TURNS}; the answer may be incomplete.")

    for block in response.content:
        if block.type == "text":
            print("\nCLAUDE:", block.text)
    return 0


if __name__ == "__main__":
    sys.exit(main(drop_result="--drop-result" in sys.argv))
