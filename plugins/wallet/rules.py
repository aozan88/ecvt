"""Wallet domain rules (M3-T2) — what any money-moving wallet must uphold.

Each rule reads one scenario's execution record and answers one question.
They encode the domain — balances, transfers, money in minor units — not the
demo target's defect list: nothing here reads `ground_truth.yaml` (ADR 0007),
and every rule passes on a correct wallet, including one that de-duplicates
replays (answers 2xx without moving money twice).

The field names are those of a wallet API with accounts, balances and transfers
(`examples/wallet-api`): accounts are `id` / `balance`, a balance read is
`account` / `balance`, a transfer request is `from_account` / `to_account` /
`amount` and its response carries `from_balance` / `to_balance`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ecvt.scoring import RuleCheck, Verdict
from plugins._base.plugin import DomainRule

if TYPE_CHECKING:
    from ecvt.results import ExecutionRecord, StepResult
    from ecvt.schema import Scenario

TRANSFER = "POST /transfers"
CREATE_ACCOUNT = "POST /accounts"
BALANCE_FIELDS = ("balance", "from_balance", "to_balance")
CENT = 0.005  # half a minor unit: the tolerance for comparing money


# --- reading the record ------------------------------------------------------


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _accepted(step: StepResult) -> bool:
    return step.status_code is not None and 200 <= step.status_code < 300


def _rejected(step: StepResult) -> bool:
    return step.status_code is not None and 400 <= step.status_code < 500


def _body(step: StepResult) -> dict[str, Any]:
    return step.request_body or {}


def _reply(step: StepResult) -> dict[str, Any]:
    return step.response_json if isinstance(step.response_json, dict) else {}


def _transfers(steps: list[StepResult]) -> list[StepResult]:
    return [s for s in steps if s.endpoint == TRANSFER]


def _observed_balances(step: StepResult) -> list[tuple[str, float]]:
    reply = _reply(step)
    return [(f, v) for f in BALANCE_FIELDS if (v := _money(reply.get(f))) is not None]


def _has_sub_cent(value: float) -> bool:
    """More than two decimals as the target wrote it. Exact on purpose: JSON `0.3`
    parses to the double nearest 0.3 and passes; `0.30000000000000004` — the
    residue of float arithmetic on money — does not."""
    return round(value, 2) != value


def _known_balances(steps: list[StepResult]) -> dict[str, float]:
    """Balance per account id, as the given steps left it (later steps win)."""
    known: dict[str, float] = {}
    for step in steps:
        if not _accepted(step):
            continue
        body, reply = _body(step), _reply(step)
        if step.endpoint == CREATE_ACCOUNT and "id" in reply:
            balance = _money(reply.get("balance"))
            if balance is not None:
                known[str(reply["id"])] = balance
        elif step.endpoint == TRANSFER:
            for side, field in (("from_account", "from_balance"), ("to_account", "to_balance")):
                balance = _money(reply.get(field))
                if body.get(side) is not None and balance is not None:
                    known[str(body[side])] = balance
        elif "account" in reply:
            balance = _money(reply.get("balance"))
            if balance is not None:
                known[str(reply["account"])] = balance
    return known


def _check(rule_id: str, ok: bool, detail_ok: str, detail_fail: str, **kw: str) -> RuleCheck:
    return RuleCheck(
        name=rule_id,
        verdict=Verdict.PASS if ok else Verdict.FAIL,
        detail=detail_ok if ok else detail_fail,
        **kw,
    )


# --- the rules ---------------------------------------------------------------


def no_negative_balance(scenario: Scenario, record: ExecutionRecord) -> RuleCheck | None:
    """After the action, no account the run can see holds less than zero."""
    seen = [
        (s.name, f, v) for s in [*record.actions, *record.probes] for f, v in _observed_balances(s)
    ]
    if not seen:
        return None
    negative = [f"{name}.{field}={value}" for name, field, value in seen if value < -CENT]
    return _check(
        "wallet.no_negative_balance",
        not negative,
        f"all {len(seen)} observed balances are ≥ 0",
        "a balance went below zero: " + ", ".join(negative[:3]),
        expected="every balance ≥ 0",
        observed=", ".join(negative[:3]) or None,
    )


def transfer_amount_positive(scenario: Scenario, record: ExecutionRecord) -> RuleCheck | None:
    """A transfer of zero or a negative amount is refused."""
    bad = [
        s
        for s in _transfers(record.actions)
        if (a := _money(_body(s).get("amount"))) is not None and a <= 0
    ]
    if not bad:
        return None
    accepted = [s for s in bad if _accepted(s)]
    amount = _body(bad[0]).get("amount")
    return _check(
        "wallet.transfer_amount_positive",
        not accepted,
        f"a transfer of {amount} was refused",
        f"a transfer of {amount} was accepted ({len(accepted)} of {len(bad)}); "
        "a wallet moves only positive amounts",
        expected="4xx for amount ≤ 0",
        observed=str([s.status_code for s in bad]),
    )


def no_self_transfer(scenario: Scenario, record: ExecutionRecord) -> RuleCheck | None:
    """A transfer from an account to itself is refused."""
    selfs = [
        s
        for s in _transfers(record.actions)
        if _body(s).get("from_account") is not None
        and _body(s).get("from_account") == _body(s).get("to_account")
    ]
    if not selfs:
        return None
    accepted = [s for s in selfs if _accepted(s)]
    return _check(
        "wallet.no_self_transfer",
        not accepted,
        "a transfer to the same account was refused",
        f"a transfer from an account to itself was accepted ({len(accepted)} of {len(selfs)})",
        expected="4xx when from_account == to_account",
        observed=str([s.status_code for s in selfs]),
    )


def amount_in_minor_units(scenario: Scenario, record: ExecutionRecord) -> RuleCheck | None:
    """Money sent in is whole cents: a fraction of a cent is refused."""
    fields = {TRANSFER: "amount", CREATE_ACCOUNT: "initial_balance"}
    offered = [
        (s, v)
        for s in record.actions
        if s.endpoint in fields and (v := _money(_body(s).get(fields[s.endpoint]))) is not None
    ]
    sub_cent = [(s, v) for s, v in offered if _has_sub_cent(v)]
    if not sub_cent:
        return None
    accepted = [v for s, v in sub_cent if _accepted(s)]
    return _check(
        "wallet.amount_in_minor_units",
        not accepted,
        f"a sub-cent amount ({sub_cent[0][1]}) was refused",
        f"a sub-cent amount was accepted: {accepted[:3]}; money is held in whole cents",
        expected="4xx for a fraction of a cent",
        observed=str([s.status_code for s, _ in sub_cent]),
    )


def balance_in_minor_units(scenario: Scenario, record: ExecutionRecord) -> RuleCheck | None:
    """Every balance the target reports is a whole number of cents."""
    seen = [(s.name, f, v) for s in record.all_steps for f, v in _observed_balances(s)]
    if not seen:
        return None
    residue = [f"{name}.{field}={value!r}" for name, field, value in seen if _has_sub_cent(value)]
    return _check(
        "wallet.balance_in_minor_units",
        not residue,
        f"all {len(seen)} reported balances are whole cents",
        "a balance carries a fraction of a cent (floating-point money?): " + ", ".join(residue[:3]),
        expected="balances in whole cents",
        observed=", ".join(residue[:3]) or None,
    )


def replay_applied_once(scenario: Scenario, record: ExecutionRecord) -> RuleCheck | None:
    """A transfer re-sent unchanged moves money once (idempotency).

    Applies to replays — the same transfer sent in `repeat` ≥ 2 rounds. A
    correct wallet may refuse the repeat or answer it with the original result;
    either way the source balance changes once. Copies sent at the same moment
    (`concurrency` alone) are a different question: `balances_reconcile`.
    """
    if scenario.repeat < 2 or scenario.action.endpoint != TRANSFER:
        return None
    accepted = [s for s in _transfers(record.actions) if _accepted(s)]
    after = {_money(_reply(s).get("from_balance")) for s in accepted} - {None}
    if len(accepted) < 2 or not after:
        return None
    return _check(
        "wallet.replay_applied_once",
        len(after) == 1,
        f"{len(accepted)} replays accepted, money moved once",
        f"the same transfer, replayed {len(accepted)} times, moved money "
        f"{len(after)} times (source balance after each: {sorted(after, reverse=True)[:4]})",
        expected="one debit for a replayed transfer",
        observed=f"{len(after)} distinct source balances",
    )


def balances_reconcile(scenario: Scenario, record: ExecutionRecord) -> RuleCheck | None:
    """Money is neither created nor lost, and what moved is what was accepted.

    Compares the balances before the action (from setup) with those read back
    after it (probes): the two accounts' total is unchanged, and the source
    moved by a whole number k of the transfer's amount, 0 ≤ k ≤ accepted
    copies — any k, so a wallet that de-duplicates repeats passes too.
    """
    transfers = _transfers(record.actions)
    if not transfers or scenario.action.endpoint != TRANSFER:
        return None
    body = _body(transfers[0])
    src, dst = str(body.get("from_account")), str(body.get("to_account"))
    amount = _money(body.get("amount"))
    before, after = _known_balances(record.setup), _known_balances(record.probes)
    if src == dst or amount is None or not {src, dst} <= before.keys() & after.keys():
        return None

    total_before, total_after = before[src] + before[dst], after[src] + after[dst]
    if abs(total_before - total_after) > CENT:
        return _check(
            "wallet.balances_reconcile",
            False,
            "",
            f"the two accounts held {total_before} before and {total_after} after: "
            "money was created or lost",
            expected=f"total {total_before}",
            observed=f"total {total_after}",
        )

    accepted = sum(1 for s in transfers if _accepted(s))
    moved = before[src] - after[src]
    if amount == 0:
        ok = abs(moved) <= CENT
    else:
        k = moved / amount
        ok = abs(k - round(k)) * abs(amount) <= CENT and 0 <= round(k) <= accepted
    return _check(
        "wallet.balances_reconcile",
        ok,
        f"balances reconcile with {accepted} accepted transfer(s) of {amount}",
        f"the source moved by {moved}, which no number of the {accepted} accepted "
        f"transfer(s) of {amount} explains (lost or partial update?)",
        expected=f"a multiple k of {amount}, 0 ≤ k ≤ {accepted}",
        observed=str(moved),
    )


WALLET_RULES: list[DomainRule] = [
    DomainRule("wallet.no_negative_balance", "No balance goes below zero.", no_negative_balance),
    DomainRule(
        "wallet.transfer_amount_positive",
        "Transfers of zero or a negative amount are refused.",
        transfer_amount_positive,
    ),
    DomainRule(
        "wallet.no_self_transfer", "A transfer to the same account is refused.", no_self_transfer
    ),
    DomainRule(
        "wallet.amount_in_minor_units",
        "Amounts are whole cents; a fraction of a cent is refused.",
        amount_in_minor_units,
    ),
    DomainRule(
        "wallet.balance_in_minor_units",
        "Reported balances are whole cents.",
        balance_in_minor_units,
    ),
    DomainRule(
        "wallet.replay_applied_once",
        "A transfer re-sent unchanged moves money once.",
        replay_applied_once,
    ),
    DomainRule(
        "wallet.balances_reconcile",
        "Money is neither created nor lost; what moved is what was accepted.",
        balances_reconcile,
    ),
]
