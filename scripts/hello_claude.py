"""Smoke test: verify Anthropic API key + SDK are configured.

DRAFT stub. Fill in once claude-agent-sdk is installed.
"""

import os


def main() -> None:
    key = os.getenv("ANTHROPIC_API_KEY")
    print("ANTHROPIC_API_KEY set:", bool(key))
    print("TODO: send a hello query via claude-agent-sdk.")


if __name__ == "__main__":
    main()
