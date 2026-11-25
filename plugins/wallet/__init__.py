"""ECVT wallet plugin — a money-moving wallet API (M3-T2).

Executes exactly as `rest_api` does (a wallet is reached over HTTP) and adds
what a generic REST plugin cannot know: the wallet domain's rules, which a06
runs beside its own checks. Rules: `plugins/wallet/rules.py`.
"""

from __future__ import annotations

from plugins._base.plugin import DomainRule
from plugins.rest_api import RestApiPlugin
from plugins.wallet.rules import WALLET_RULES


class WalletPlugin(RestApiPlugin):
    name = "wallet"
    version = "1.0.0"

    def domain_rules(self) -> list[DomainRule]:
        return list(WALLET_RULES)


PLUGIN = WalletPlugin
