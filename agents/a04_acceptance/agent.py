"""Acceptance Agent — ECVT (v1.x).

DRAFT scaffold. Iterate in Project 04.
"""

from __future__ import annotations

from pathlib import Path

_AGENT_DIR = Path(__file__).parent


def load_system_prompt() -> str:
    return (_AGENT_DIR / "system_prompt.md").read_text(encoding="utf-8")


async def run(payload: dict) -> dict:
    raise NotImplementedError("Acceptance Agent — implement per roadmap (v1.x).")
