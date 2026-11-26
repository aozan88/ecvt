# ADR 0014 — What the ecvt-eval MCP server does, and how a06 reaches it

**Status:** Accepted (Ozan, 2026-11-26)
**Date:** 2026-11-25
**Context:** M3-T3 asks for a custom MCP server `ecvt-eval` exposing
`score_response`, with Evaluation (a06) scoring through it (gate G3). a06 has
two halves: deterministic rules (`ecvt/scoring.py` plus a plugin's
`domain_rules`, ADR 0013) and an LLM judge for what rules cannot decide.
ADR 0009 kept scoring a pure function so it could move behind a server.

**Decision:**
- **The server holds the rules, not the judge.** `score_response(scenario,
  execution, plugin)` returns the rule dimension — verdict, score, every
  check — computed by the generic checks and the named plugin's domain rules.
  `list_rules(plugin)` shows what runs. No model call, no API key, no ground
  truth (ADR 0007). The judge stays in a06, where all model access goes
  through `ecvt/llm.py` (ADR 0009), and so does folding rule + judge into the
  scenario verdict.
- **Transport: stdio**, `python -m ecvt.eval_server`, started by a06 as a
  subprocess of the same interpreter in the repository root. One session per
  scoring run, one `score_response` call per scenario.
- **a06 has no in-process path.** `score_scenarios` gets every rule dimension
  from the server; if it cannot start or refuses an input, scoring fails with
  `EvalServerError` (non-retryable stage error in the Manager). A silent local
  fallback would make G3 true only when nothing went wrong.
- **Code lives in `ecvt/eval_server.py`**, importable, rather than as a
  script under `mcp-servers/ecvt-eval/` (the scaffold's path): tests hand
  `mcp.Client` the same server object in memory, so every test crosses the
  MCP protocol without a subprocess; one test covers the real stdio start.

**Consequences:** Measured on 10 recorded runs: 282 scenario × plugin scorings
over stdio, 0 differ from the in-process rules; ~1 s per session start. Any
MCP client (Claude Code, Desktop, an n8n node) can score an execution with the
same rules ECVT uses. The judge is not reachable over MCP; exposing it would
put model spend behind a tool any client can call.

**Alternatives Considered:**
- Judge inside the server too — rejected: the server would need an API key
  and its own model routing, and every MCP client could spend on it.
- In-process fallback when the server is down — rejected: hides the failure
  G3 is meant to check.
- Streamable HTTP transport — not needed for a local subprocess; the server
  supports it if a remote client (n8n, M3-T5) needs one.
