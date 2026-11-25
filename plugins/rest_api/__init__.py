"""ECVT rest_api plugin — any HTTP/JSON API described by an OpenAPI spec (M3-T1).

Domain-agnostic: it knows HTTP, not what the API is for. Execution is
`ecvt.execution`, wrapped rather than moved — that module is already a plain
HTTP runner with no knowledge of any target, and the error rules it carries
(ADR 0006: nothing raises, a received status is never retried) are exactly the
contract a plugin owes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plugins._base.plugin import Plugin

if TYPE_CHECKING:
    from ecvt.results import ExecutionRecord
    from ecvt.schema import Scenario


class RestApiPlugin(Plugin):
    name = "rest_api"
    version = "1.0.0"

    async def execute(self, scenarios: list[Scenario]) -> list[ExecutionRecord]:
        from ecvt.execution import RunnerConfig, run_scenarios

        config = RunnerConfig(
            base_url=self.context.base_url,
            timeout_s=self.context.timeout_s,
            max_parallel=self.context.max_parallel,
        )
        return await run_scenarios(scenarios, config)


PLUGIN = RestApiPlugin
