"""MCP: tools hosted on a remote server, reached through the Messages API connector.

Anthropic connects to the server, so there is no run_tool in your code. Two halves are required
together: mcp_servers (where) and an mcp_toolset entry (which tools). The allowlist below enables
only one tool: expose the minimum, because every exposed tool definition costs context tokens.

The default server is DeepWiki, a public third-party service with no auth, so the question text
is sent to it. Never put private data in this prompt. MCP_SERVER_URL overrides the server; if you
do, update ALLOWED_TOOL too.

    python examples/ccaf/mcp_demo.py
"""

from __future__ import annotations

import os

import anthropic
from dotenv import load_dotenv

load_dotenv()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
SERVER_URL = os.getenv("MCP_SERVER_URL", "https://mcp.deepwiki.com/mcp")
SERVER_NAME = "deepwiki"
ALLOWED_TOOL = "ask_wiki_question"
QUESTION = "Using the wiki of modelcontextprotocol/python-sdk, which transports does it support?"
client = anthropic.Anthropic()


def main() -> None:
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["mcp-client-2025-11-20"],
        mcp_servers=[{"type": "url", "url": SERVER_URL, "name": SERVER_NAME}],
        tools=[
            {
                "type": "mcp_toolset",
                "mcp_server_name": SERVER_NAME,
                "default_config": {"enabled": False},
                "configs": {ALLOWED_TOOL: {"enabled": True}},
            }
        ],
        messages=[{"role": "user", "content": QUESTION}],
    )

    for block in response.content:
        if block.type == "mcp_tool_use":
            print(f"[mcp call] {block.server_name}.{block.name}({block.input})")
        elif block.type == "mcp_tool_result":
            print(f"[mcp result] is_error={block.is_error}")
        elif block.type == "text":
            print(block.text)
    print(f"stop_reason={response.stop_reason}")


if __name__ == "__main__":
    main()
