"""ECVT Plugin base interface (ABC).

DRAFT scaffold. Every execution backend implements this interface so
ECVT Core talks to all target systems the same way. See docs/07-plugins.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Plugin(ABC):
    name: str
    version: str

    @abstractmethod
    async def discover_domain(self) -> Any:
        """Return entities, relationships, workflows, rules (DomainModel)."""

    @abstractmethod
    async def execute_scenario(self, scenario: Any) -> Any:
        """Run one scenario against the target system; return raw Response."""

    @abstractmethod
    async def validate_response(self, expected: Any, actual: Any) -> Any:
        """Return a Score comparing expected vs actual."""
