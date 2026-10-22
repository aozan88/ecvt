"""
ECVT demo target — a deliberately buggy fintech wallet API.

⚠️ DELIBERATE BUGS — DO NOT FIX. See ground_truth.yaml.
This exists to be validated by ECVT, not to be correct.
"""
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Wallet API (buggy demo)")

accounts: dict[str, dict] = {}       # id -> {"owner": str, "balance": float}
transactions: list[dict] = []
_next_id = 1


class AccountIn(BaseModel):
    owner: str
    initial_balance: float = 0.0     # BUG GT-01: float money, not Decimal


class TransferIn(BaseModel):
    from_account: str
    to_account: str
    amount: float                    # BUG GT-10: no bounds/type guard
    # BUG GT-05: no idempotency key — replays double-charge


@app.post("/accounts")
def create_account(body: AccountIn):
    global _next_id
    acc_id = str(_next_id)
    _next_id += 1
    accounts[acc_id] = {"owner": body.owner, "balance": body.initial_balance}
    return {"id": acc_id, "owner": body.owner, "balance": body.initial_balance}


@app.get("/accounts/{account_id}/balance")
def get_balance(account_id: str):
    acc = accounts[account_id]       # BUG GT-07: KeyError -> 500, not 404
    return {"account": account_id, "balance": acc["balance"]}


@app.post("/transfers")
def transfer(body: TransferIn):
    src = accounts[body.from_account]  # BUG GT-07: 500 on missing account
    dst = accounts[body.to_account]
    # BUG GT-04: no ownership/auth check — move anyone's money
    # BUG GT-02: no amount > 0 check — negative reverses direction
    # BUG GT-03: no overdraft check — balance goes negative
    # BUG GT-08: self-transfer (from == to) not blocked
    # BUG GT-06: read-modify-write, no lock — race under concurrency
    src["balance"] = src["balance"] - body.amount   # BUG GT-09: float residue
    dst["balance"] = dst["balance"] + body.amount
    txn = {"from": body.from_account, "to": body.to_account, "amount": body.amount}
    transactions.append(txn)
    return {"status": "ok", **txn,
            "from_balance": src["balance"], "to_balance": dst["balance"]}


@app.get("/accounts/{account_id}/transactions")
def list_transactions(account_id: str):
    return [t for t in transactions
            if t["from"] == account_id or t["to"] == account_id]
