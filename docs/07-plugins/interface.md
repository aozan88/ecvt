# Plugin Interface

How ECVT Core reaches a target system. Code: `plugins/_base/plugin.py` (M3-T1).

Core is domain-agnostic (root `CLAUDE.md` rule 5): it reads a spec, generates
scenarios, executes them and scores the result. What depends on the *kind* of
system under test lives in `plugins/<name>/`.

## Registry and loading

- **Registry = directories under `plugins/`** (ADR 0010). A folder whose name
  does not start with `_` or `.` is a registered plugin. `available()` lists
  them; the Manager's `list_plugins` reads the same function.
- A plugin package exposes its class as `PLUGIN`.
- `load(name, context)` returns the constructed plugin or raises `PluginError`:

| Case | Result |
|---|---|
| `rest_api` | `RestApiPlugin` |
| folder exists, no `PLUGIN` (`ikys`, `manual`, `solar` today) | `PluginError: … registered … but has no implementation yet` |
| no such folder | `PluginError: unknown plugin …; registered: …` |

There is **no fallback**. Before M3-T1 the `--plugin` value was accepted and
ignored, so `plugin=ikys` ran over REST and produced results that looked real.

Both entry points resolve the plugin **before any model call**: `ecvt run`
(fixed chain) and `ecvt run --manager` exit with the error. Inside the Manager,
the execution stage reads the `plugin` slot and turns a `PluginError` into a
non-retryable `StageError`.

## The ABC

```python
class Plugin(ABC):
    name: ClassVar[str]; version: ClassVar[str]
    def __init__(self, context: PluginContext)
    async def execute(self, scenarios) -> list[ExecutionRecord]   # abstract
    async def discover_domain(self) -> dict | None                 # default None
    def validate_response(self, scenario, record) -> list[str]     # default []
```

`PluginContext`: `base_url`, `timeout_s`, `max_parallel`, and `options:
dict[str, str]` for plugin-specific settings, so the interface does not grow a
field per target system.

| Method | Required | Contract |
|---|---|---|
| `execute` | yes | One `ExecutionRecord` per scenario. Raises for nothing the target does: failures are records (ADR 0006). Keeps scenarios that share target state from overlapping. |
| `discover_domain` | no | A DomainModel the plugin knows without a spec. No caller yet. |
| `validate_response` | no | Plugin-specific rule findings for one execution. First caller: M3-T2 (wallet rules). |

## Changes from the scaffold

- `execute_scenario(one)` became **`execute(set)`**: an implementation may share
  a connection or session across the set and must order scenarios that touch the
  same state — the existing runner already does both with one HTTP client.
- `discover_domain` and `validate_response` are **optional** with neutral
  defaults. Nothing calls them in M3-T1; making them abstract would force every
  plugin to write stubs for hooks with no caller.

## Types

`Scenario` (`ecvt/schema.py`), `ExecutionRecord` (`ecvt/results.py`). Scores
stay in Core (`ecvt/scoring.py`); a plugin contributes findings, not scores.
