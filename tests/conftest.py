"""Shared fixtures — the live wallet-api demo target, in-process."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

WALLET_APP_PATH = Path(__file__).resolve().parent.parent / "examples" / "wallet-api" / "app.py"


def pytest_configure(config: pytest.Config) -> None:
    """Stop early when the async plugin is missing.

    `asyncio_mode = "auto"` means every `async def test_` needs
    pytest-asyncio. Without it they do not fail, they error during setup —
    44 of them at once in a Codespace installed with `pip install -e .`,
    which until now declared no dependencies. The wall of tracebacks says
    nothing about the cause, so say it here in one line instead.
    """
    if importlib.util.find_spec("pytest_asyncio") is None:  # pragma: no cover
        raise pytest.UsageError(
            "pytest-asyncio is not installed, so every async test would error "
            "before running. Install the test dependencies: "
            "pip install -r requirements.txt  (or: pip install -e '.[dev]')"
        )


def _load_wallet_module() -> Any:
    """Import examples/wallet-api/app.py, which is not an installed package."""
    spec = importlib.util.spec_from_file_location("ecvt_demo_wallet_api", WALLET_APP_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {WALLET_APP_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wallet_module() -> Any:
    """A fresh wallet-api module with empty in-memory state.

    The demo target keeps accounts in module-level dicts, so state is reset per
    test rather than leaking between scenarios.
    """
    module = _load_wallet_module()
    module.accounts.clear()
    module.transactions.clear()
    module._next_id = 1
    return module


@pytest.fixture
def wallet_app(wallet_module: Any) -> Any:
    return wallet_module.app


@pytest.fixture(autouse=True)
def _keep_generated_docs_out_of_the_repo(tmp_path_factory, monkeypatch):
    """Redirect the committed report docs into tmp for every test.

    `record_trend` and `record_latest` write into `docs/09-evaluation/` by
    design. Twice now a test exercised them and left a generated file in the
    working tree — once because a default argument captured the path at
    import, once because a test redirected one doc and not the other.
    Redirecting both for all tests removes the whole class of mistake; a test
    that wants to check the real paths can assert on the constants.
    """
    scratch = tmp_path_factory.mktemp("generated-docs")
    monkeypatch.setattr("ecvt.reporting.TREND_DOC", scratch / "catch-rate.md")
    monkeypatch.setattr("ecvt.reporting.LATEST_DOC", scratch / "latest-run.md")
    monkeypatch.setattr("ecvt.cli.TREND_DOC", scratch / "catch-rate.md")


@pytest.fixture(autouse=True)
def _eval_server_in_memory(monkeypatch):
    """Score through ecvt-eval in-process for every test (M3-T3).

    a06 always scores over MCP; production starts the server as a stdio
    subprocess. Here `mcp.Client` gets the same server object in memory, so
    every test still crosses the MCP protocol — tool schema, JSON round trip,
    structured result — without ~30 subprocess starts. The stdio path has its
    own test in tests/test_eval_server.py, which restores the original.
    """
    from agents.a06_evaluation import agent as a06
    from ecvt.eval_server import build_server

    server = build_server()
    monkeypatch.setattr(a06, "_stdio_eval_server", a06.eval_server, raising=False)
    monkeypatch.setattr(a06, "eval_server", lambda: server)
