"""ecvt-eval — ECVT's rule scoring as an MCP server (M3-T3, ADR 0014).

Evaluation (a06) scores every execution through this server; any other MCP
client (Claude Code, Claude Desktop, an n8n node) can call the same tools.

    python -m ecvt.eval_server            # stdio

Tools:
    score_response(scenario, execution, plugin="rest_api") -> rule DimensionScore
    list_rules(plugin="rest_api")                         -> the checks that may run

Deterministic and keyless: the generic checks of `ecvt/scoring.py` plus the
named plugin's `domain_rules`, with no model call. The LLM judge stays in a06,
where all model access goes through `ecvt/llm.py` (ADR 0009). Ground truth is
never an input (ADR 0007).
"""

from __future__ import annotations

from functools import cache
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from ecvt.results import ExecutionRecord
from ecvt.schema import Scenario
from ecvt.scoring import DimensionScore, run_rules
from plugins._base.plugin import DomainRule, PluginContext, PluginError
from plugins._base.plugin import load as load_plugin

SERVER_NAME = "ecvt-eval"
VERSION = "1.0.0"

GENERIC_CHECKS = {
    "execution_outcome": "Setup established state and the action reached the target.",
    "status_expected": "Every action returned a status in the scenario's expected_status.",
    "no_server_error": "No step received a 5xx.",
    "replay_rejected": "A repeated non-GET was rejected or de-duplicated (inconclusive if all accepted).",
    "probes_readable": "State could be read back after the action.",
}


class RuleInfo(BaseModel):
    id: str
    description: str
    source: str  # "generic" or the plugin name


class RuleList(BaseModel):
    plugin: str
    rules: list[RuleInfo]


@cache
def _domain_rules(plugin: str) -> tuple[DomainRule, ...]:
    try:
        return tuple(load_plugin(plugin, PluginContext(base_url="")).domain_rules())
    except PluginError as exc:
        raise ToolError(str(exc)) from exc


def score_response(
    scenario: dict[str, Any], execution: dict[str, Any], plugin: str = "rest_api"
) -> DimensionScore:
    """Rule-score one execution against its scenario.

    `scenario` is an ECVT Scenario and `execution` its ExecutionRecord, both as
    JSON. Returns the rule dimension: a verdict (pass / fail / inconclusive /
    not_run), a 0–1 score over the decided checks, and every check with its
    detail. `plugin` adds that plugin's domain rules to the generic checks.
    """
    try:
        parsed_scenario = Scenario.model_validate(scenario)
        record = ExecutionRecord.model_validate(execution)
    except ValidationError as exc:
        raise ToolError(f"invalid input: {exc.error_count()} errors; {exc.errors()[:3]}") from exc
    if record.scenario_id != parsed_scenario.id:
        raise ToolError(
            f"execution is for {record.scenario_id!r}, scenario is {parsed_scenario.id!r}"
        )
    return run_rules(parsed_scenario, record, _domain_rules(plugin))


def list_rules(plugin: str = "rest_api") -> RuleList:
    """The checks score_response may run for `plugin`: generic ones, then the plugin's."""
    rules = [RuleInfo(id=k, description=v, source="generic") for k, v in GENERIC_CHECKS.items()]
    rules += [
        RuleInfo(id=r.id, description=r.description, source=plugin) for r in _domain_rules(plugin)
    ]
    return RuleList(plugin=plugin, rules=rules)


def build_server() -> MCPServer:
    server = MCPServer(
        SERVER_NAME,
        version=VERSION,
        instructions=(
            "Deterministic rule scoring for ECVT executions. Call score_response once per "
            "scenario with the scenario and its execution record; list_rules shows what runs."
        ),
    )
    server.tool()(score_response)
    server.tool()(list_rules)
    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
