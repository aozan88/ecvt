"""Reporting Agent — ECVT (v1.2).

DRAFT scaffold. Iterate in Project 08.
"""

from __future__ import annotations

from pathlib import Path

_AGENT_DIR = Path(__file__).parent


def load_system_prompt() -> str:
    return (_AGENT_DIR / "system_prompt.md").read_text(encoding="utf-8")


async def run(payload: dict) -> dict:
    raise NotImplementedError("Reporting Agent — implement per roadmap (v1.2).")
