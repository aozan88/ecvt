"""Shared fixtures — the live wallet-api demo target, in-process."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

WALLET_APP_PATH = Path(__file__).resolve().parent.parent / "examples" / "wallet-api" / "app.py"


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
