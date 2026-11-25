"""ECVT plugin interface (M3-T1) — how a scenario reaches a target system.

ECVT Core is domain-agnostic (root CLAUDE.md rule 5): it generates scenarios
from a spec, executes them, and scores the result. Everything that depends on
*what kind* of system is under test lives in a plugin under `plugins/<name>/`.

The registry is the directory listing of `plugins/` (ADR 0010): a folder whose
name does not start with `_` or `.` is a plugin. Its package exposes the class
as `PLUGIN`. A folder without one is registered but not implemented, and loading
it fails with a message saying exactly that — a run must never fall back to
another plugin, which is what happened before this existed: the Manager
accepted `plugin=ikys` and then executed over REST anyway.

What a plugin must do is execute. What it may add is knowledge:

    execute(scenarios)            required  scenarios -> ExecutionRecords
    discover_domain()             optional  a DomainModel the plugin knows
    validate_response(s, record)  optional  plugin-specific rule findings

The optional hooks default to "nothing plugin-specific". Nothing calls them yet;
M3-T2 (wallet rules) is where `validate_response` gets a caller.
"""

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from ecvt.results import ExecutionRecord
    from ecvt.schema import Scenario

PLUGINS_DIR = Path(__file__).resolve().parent.parent


class PluginError(ValueError):
    """A plugin cannot be used: unknown name, or registered without an implementation."""


@dataclass
class PluginContext:
    """What a run tells a plugin about where and how to execute.

    The common settings are typed; anything a single plugin needs (credentials
    by env-var name, a tenant id) goes in `options`, so the interface does not
    grow a field per target system.
    """

    base_url: str
    timeout_s: float = 10.0
    max_parallel: int = 20
    options: dict[str, str] = field(default_factory=dict)


class Plugin(ABC):
    """One kind of target system."""

    name: ClassVar[str]
    version: ClassVar[str]

    def __init__(self, context: PluginContext) -> None:
        self.context = context

    @abstractmethod
    async def execute(self, scenarios: list[Scenario]) -> list[ExecutionRecord]:
        """Run every scenario against the target; one record per scenario.

        A whole set rather than one scenario: an implementation may share a
        connection or a session across it, and must keep scenarios that share
        target state from overlapping. Following ADR 0006, it raises for
        nothing a target does — failures become records.
        """

    async def discover_domain(self) -> dict[str, Any] | None:
        """A DomainModel this plugin knows without reading a spec, or None."""
        return None

    def validate_response(self, scenario: Scenario, record: ExecutionRecord) -> list[str]:
        """Plugin-specific rule violations in one execution, as short findings."""
        return []


def available(plugins_dir: Path = PLUGINS_DIR) -> list[str]:
    """Registered plugin names: the directories under `plugins/` (ADR 0010)."""
    if not plugins_dir.is_dir():
        return []
    return sorted(
        p.name for p in plugins_dir.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
    )


def load(name: str, context: PluginContext, plugins_dir: Path = PLUGINS_DIR) -> Plugin:
    """The plugin called `name`, constructed for this run.

    Raises PluginError rather than substituting another plugin: a run against
    the wrong kind of system produces results that look real and are not.
    """
    registered = available(plugins_dir)
    if name not in registered:
        raise PluginError(f"unknown plugin {name!r}; registered: {', '.join(registered) or 'none'}")
    module = importlib.import_module(f"plugins.{name}")
    cls = getattr(module, "PLUGIN", None)
    if cls is None:
        raise PluginError(
            f"plugin {name!r} is registered (plugins/{name}/) but has no implementation yet"
        )
    if not (isinstance(cls, type) and issubclass(cls, Plugin)):
        raise PluginError(f"plugins.{name}.PLUGIN is not a Plugin subclass")
    return cls(context)
