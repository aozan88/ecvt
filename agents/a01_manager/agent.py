"""Manager Agent — ECVT validation pipeline orchestrator.

DRAFT scaffold. ClaudeSDKClient + AgentDefinition + FastAPI wrapper.
Iterate in Project 01; finalize H3 Mon (13 Jul).
"""

from __future__ import annotations

from pathlib import Path

_AGENT_DIR = Path(__file__).parent


def load_system_prompt() -> str:
    return (_AGENT_DIR / "system_prompt.md").read_text(encoding="utf-8")


async def run(user_input: str) -> dict:
    """Classify intent, select pipeline, dispatch subagents, return summary.

    TODO(H3): wire ClaudeSDKClient + AgentDefinition + subagent spawn.
    """
    raise NotImplementedError("Manager agent loop — implement in H3.")


if __name__ == "__main__":
    print("Manager Agent scaffold — not yet implemented.")
