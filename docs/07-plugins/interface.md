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
    def domain_rules(self) -> list[DomainRule]                     # default []
```

`PluginContext`: `base_url`, `timeout_s`, `max_parallel`, and `options:
dict[str, str]` for plugin-specific settings, so the interface does not grow a
field per target system.

| Method | Required | Contract |
|---|---|---|
| `execute` | yes | One `ExecutionRecord` per scenario. Raises for nothing the target does: failures are records (ADR 0006). Keeps scenarios that share target state from overlapping. |
| `discover_domain` | no | A DomainModel the plugin knows without a spec. No caller yet. |
| `domain_rules` | no | Rules of the domain for a06 to run (M3-T2). |

## Domain rules (M3-T2)

`DomainRule(id, description, check)`: `check(scenario, record)` returns a
`RuleCheck` (pass / fail / inconclusive) when the rule applies, `None` when it
does not. It reads only the scenario and its execution record — never ground
truth (ADR 0007), never the network.

The plugin knows the rule; **a06 decides**. `run_rules` runs a plugin's rules
on completed executions beside its own checks, folds them the same way and
decides whether the judge runs. A rule that raises is recorded as
`inconclusive`; scoring continues. Both the chain and the Manager's evaluation
stage pass the run's plugin rules to `score_scenarios`.

| Plugin | Rules |
|---|---|
| `rest_api` | none — it knows HTTP, not a domain |
| `wallet` | 7, in `plugins/wallet/rules.py`: no negative balance · amount > 0 · no self-transfer · amounts in whole cents · balances in whole cents · a replay moves money once · balances reconcile |

Each wallet rule passes on a correct wallet, including one that answers a
replay 2xx without moving money twice (tested in `tests/test_scoring.py`).

## Changes from the scaffold (ADR 0013)

- `execute_scenario(one)` became **`execute(set)`**: an implementation may share
  a connection or session across the set and must order scenarios that touch the
  same state — the existing runner already does both with one HTTP client.
- **`send(step)` is deferred** to the second real backend: the step type is
  HTTP today (`HttpStep`), so a narrower seam designed now would be a guess.
- **`validate_response` is removed.** Scoring is a06's alone; a plugin
  contributes `domain_rules`, never scores.
- `discover_domain` is optional with a `None` default.

## Types

`Scenario` (`ecvt/schema.py`), `ExecutionRecord` (`ecvt/results.py`). Scores
stay in Core (`ecvt/scoring.py`, a06); a plugin never scores.
