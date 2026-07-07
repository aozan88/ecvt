"""Smoke test: verify Anthropic API key + SDK are wired up.

Loads .env, then sends one minimal message to the Claude API and prints
the reply. Run after adding ANTHROPIC_API_KEY to .env:

    .venv\\Scripts\\python.exe scripts\\hello_claude.py   (Windows)
    .venv/bin/python scripts/hello_claude.py               (Mac/WSL)
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()  # reads .env from repo root
    key = os.getenv("ANTHROPIC_API_KEY", "")
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    if not key or key.startswith("sk-ant-xxxxx"):
        print("ANTHROPIC_API_KEY not set (still placeholder).")
        print("-> Add your real key to .env, then re-run this script.")
        return 1

    import anthropic

    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model,
        max_tokens=64,
        messages=[{"role": "user", "content": "Reply with exactly: ECVT hello OK"}],
    )
    text = "".join(block.text for block in msg.content if block.type == "text")
    print(f"Model: {model}")
    print(f"Claude says: {text.strip()}")
    print("Smoke test PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
