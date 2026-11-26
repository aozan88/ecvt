# Setup — n8n (self-hosted via Docker)

> Requires `Docker.md` first. Decision: ADR 0002. Workflow spec: `docs/04-workflows/`.

n8n is the trigger layer: a webhook in n8n starts an ECVT run and answers with
its result. n8n does not run ECVT itself; it calls `ecvt serve` on the host.

```
curl → n8n webhook /webhook/ecvt-run → POST http://host.docker.internal:8090/runs?wait=true
     → ecvt serve → ecvt run --plugin … --report → JSON result back through n8n
```

## 1. Start the ECVT trigger on the host

```bash
export ECVT_TRIGGER_TOKEN=$(python -c "import secrets; print(secrets.token_hex(16))")
python -m ecvt.cli serve --host 0.0.0.0 --port 8090 --base-url http://localhost:8000
```

- A token is required on any host but loopback: every request starts a paid run.
- `--base-url` is where the target runs. It is the server's setting; a request
  cannot change it.
- The target (e.g. wallet-api) must be up: `uvicorn app:app --app-dir examples/wallet-api --port 8000`.
- The same server is the browser UI: open http://localhost:8090/ (it asks for the token once).

## 2. Start n8n and load the workflow

```bash
docker compose up -d n8n           # passes ECVT_TRIGGER_TOKEN from your shell / .env
docker compose exec n8n n8n import:workflow --input=/workflows/ecvt-run.json
docker compose exec n8n n8n publish:workflow --id=ecvtRunWebhook01
```

Upgrading from 1.0: re-import the workflow. The 1.1 export calls `/runs?wait=true`;
the old one calls bare `/runs`, which now answers 202 at once (ADR 0016).

A running n8n registers the published webhook within a minute; until then the
webhook answers 404 "not registered".

The webhook needs no login. To see or edit the workflow, open http://localhost:5678
and create the owner account on first visit (n8n 2.x).

## 3. Webhook smoke test

```bash
curl -s -X POST localhost:5678/webhook/ecvt-run \
  -H 'Content-Type: application/json' \
  -d '{"target": "examples/wallet-api", "plugin": "wallet", "judge": false}'
```

The call waits for the run (about 40 s for the chain) and returns 200 with
`status`, `run_dir`, `recommendation`, `verdicts`, `catch_rate` (when the target
has a baseline), `cost_usd` and the report file names.

Body fields: `target` (required, a directory under `examples/` with
`openapi.json`), `plugin` (default `rest_api`), `manager` (default false — the
Manager path costs ~10× a chain run), `judge` (default true). Anything else is
refused with 400.

| Status | Meaning |
|---|---|
| 200 | run completed |
| 400 | bad body — the message names the field |
| 401 | missing or wrong token (n8n's `ECVT_TRIGGER_TOKEN` ≠ the server's) |
| 409 | a run is already in progress — one at a time |
| 502 | the run failed; `log_tail` has its last lines |

The same check runs in the gate: `python scripts/gate_check.py M3 --only G3-11`.

## Common issues

- **n8n exits with "address '::' is not available"** — no IPv6 in the
  container host: set `N8N_LISTEN_ADDRESS=0.0.0.0`.
- **401 through n8n** — the token was not passed into the container; check
  `docker compose exec n8n printenv ECVT_TRIGGER_TOKEN`.
- **`ECONNREFUSED host.docker.internal:8090`** — `ecvt serve` is not running, or
  it listens on 127.0.0.1 (use `--host 0.0.0.0` with a token).
- **Errors fetching `api.n8n.io`** in the log — n8n's own templates/telemetry;
  harmless on a network that blocks it.
- **Port 5678 in use** — set `N8N_PORT` in `.env`.

## Next
→ `Environment.md`
