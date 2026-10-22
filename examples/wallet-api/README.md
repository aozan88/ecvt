# Wallet API — ECVT demo target (deliberately buggy)

A tiny FastAPI fintech wallet. It exists **to be validated by ECVT**, not to be correct.

> ⚠️ **DELIBERATE BUGS — DO NOT FIX.** Documented in `ground_truth.yaml`; they are
> the measurement baseline for ECVT's catch rate (M2).

## Run
    pip install -r ../../requirements.txt
    uvicorn app:app --reload --port 8000
    # OpenAPI spec: http://localhost:8000/openapi.json  (feeds M1 Scenario Generator)

## Endpoints
POST /accounts · GET /accounts/{id}/balance · POST /transfers · GET /accounts/{id}/transactions

## Tests
`pytest` runs the **happy path only** — buggy paths are left for ECVT to discover.
