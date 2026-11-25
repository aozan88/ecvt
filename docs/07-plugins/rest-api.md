# REST API Plugin

Any HTTP/JSON API described by an OpenAPI spec. Code: `plugins/rest_api/`
(M3-T1). The default plugin for `ecvt run` and the Manager.

## What it does

`RestApiPlugin.execute` wraps `ecvt.execution.run_scenarios` with a
`RunnerConfig` built from the `PluginContext`. The runner was moved behind the
interface, not rewritten, so the M1/M2 behaviour is unchanged:

- One shared `httpx.AsyncClient` per set; scenarios run sequentially.
- Per scenario: `setup` in order (its `save` captures bind path parameters),
  then the `action` `repeat` rounds in series, `concurrency` copies in parallel
  per round (capped by `max_parallel`), then read-only `probes`.
- Transport errors are retried (`max_transport_retries`, backoff); a received
  HTTP status is **never** retried (ADR 0006). Everything ends as a record.

## Configuration

| Setting | Source |
|---|---|
| Base URL | `--base-url` (chain) · `ECVT_BASE_URL` (Manager) · default `http://localhost:8000` |
| Timeout | `--timeout` (chain); 10 s otherwise |
| Spec | `<target>/openapi.json`, read by Core for scenario generation |

Domain-agnostic: it knows HTTP, not what the API is for. Nothing in it names
the wallet.

## Not yet

- **Auth** (keys/tokens via env): not implemented. The intended place is
  `PluginContext.options` naming an env var, never a value in the repo.
- `discover_domain` uses the interface default (`None`).
