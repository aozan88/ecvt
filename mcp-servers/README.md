# MCP Servers

Custom MCP servers ECVT ships. Ready-made third-party servers: `setup/MCP.md`.

## ecvt-eval (M3-T3, ADR 0014)

ECVT's rule scoring as an MCP server. Evaluation (a06) scores every execution
through it; any MCP client can call it too.

| | |
|---|---|
| Code | `ecvt/eval_server.py` (importable, so tests use it in memory) |
| Run | `python -m ecvt.eval_server` — stdio, from the repository root |
| Tools | `score_response(scenario, execution, plugin="rest_api")` → rule verdict, 0–1 score, every check · `list_rules(plugin)` → the checks that run |
| Needs | nothing: no API key, no network, no ground truth |

The LLM judge is not in the server; it stays in a06 (ADR 0014).

Claude Code, from the repository root:

```bash
claude mcp add ecvt-eval -- python -m ecvt.eval_server
```

## ecvt-plugin-loader

Not built. Plugin discovery is a directory listing in `plugins/_base/plugin.py`
(ADR 0010), which Execution, Evaluation and the Manager import directly.
