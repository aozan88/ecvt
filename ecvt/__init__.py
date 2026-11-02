"""ECVT core — shared schema, spec reading and LLM plumbing for the agents.

Agent-specific prompts and eval datasets live under `agents/aNN_*/`; anything
two agents both need lives here.
"""

from __future__ import annotations

__version__ = "0.1.0"

from ecvt.schema import (
    ALL_CATEGORIES,
    Expectation,
    HttpMethod,
    HttpStep,
    Scenario,
    ScenarioCategory,
    ScenarioSet,
    Severity,
    read_scenarios_jsonl,
    write_scenarios_jsonl,
)

__all__ = [
    "ALL_CATEGORIES",
    "Expectation",
    "HttpMethod",
    "HttpStep",
    "Scenario",
    "ScenarioCategory",
    "ScenarioSet",
    "Severity",
    "__version__",
    "read_scenarios_jsonl",
    "write_scenarios_jsonl",
]
