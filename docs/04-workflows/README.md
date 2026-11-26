# Workflows

n8n workflows that drive ECVT (ADR 0002). Exports live in `workflows/n8n/`;
setup and the smoke test in `setup/n8n.md`.

| Workflow | Trigger | File |
|---|---|---|
| ECVT — webhook to ecvt run | `POST /webhook/ecvt-run` | `workflows/n8n/ecvt-run.json` |

## ECVT — webhook to ecvt run (M3-T5)

**Purpose.** Start one ECVT run from anything that can send an HTTP request —
a CI job, a chat command, a schedule — and answer with its result.

**Flow.**

```
Webhook (POST /webhook/ecvt-run, responds from the last node)
  → HTTP Request  POST {ECVT_TRIGGER_URL}/runs?wait=true   header X-ECVT-Token, body passed through
  → Respond to Webhook   ECVT's status code and JSON body, unchanged
```

**Input** (JSON body): `target` (required, a directory under `examples/` holding
`openapi.json`), `plugin` (default `rest_api`), `manager` (default false),
`judge` (default true). The body is passed to ECVT as is; ECVT validates it and
refuses unknown fields, so n8n holds no rules of its own.

**Synchronous on purpose.** Since v1.1 a bare `POST /runs` answers 202 and runs
in the background (the browser UI polls it, ADR 0016). The workflow asks for
`?wait=true`, which keeps the M3-T5 behaviour: the webhook replies with the
finished run. A workflow imported before v1.1 calls bare `/runs` and would get
the 202 — re-import `workflows/n8n/ecvt-run.json`.

**Output.** 200 with `status`, `run_id`, `run_dir`, `recommendation` (the Business
report's headline), `verdicts`, `defects_suspected`, `catch_rate` when the
target has a baseline, `cost_usd`, `elapsed_s`, `reports`. Errors keep ECVT's
status: 400 bad body, 401 token, 409 busy, 502 run failed.

**Why ECVT is called over HTTP, not run inside n8n.** n8n 2.x disables the
Execute Command node by default, and the n8n container has no Python or ECVT.
`ecvt serve` (`ecvt/trigger_server.py`) runs `ecvt run … --report` in a
subprocess on the host, one run at a time, with the target's base URL fixed in
its own configuration.

**Configuration (n8n container env).** `ECVT_TRIGGER_URL` (default
`http://host.docker.internal:8090`), `ECVT_TRIGGER_TOKEN` (must equal the
server's), `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` so the workflow can read them.

**Timing.** The webhook waits for the run: ~20–40 s for a chain run, minutes
through the Manager. The HTTP node's timeout is 30 min, matching the server's.

**Verified.** `gates/M3.yaml` G3-11 starts wallet-api, `ecvt serve` and the n8n
image, imports and publishes this workflow, and curls the webhook.

## Limits

- One run at a time per `ecvt serve`, UI and n8n together; a second request gets 409, not a queue.
- Runs started through n8n appear in the UI's run list too (`artifacts/triggered/<run_id>/`).
- A Manager run started this way records `source: cli` in `state.json`: the
  trigger reaches the Manager through the CLI.
