# Setup — MCP Servers

> Skeleton (≤150 lines).

## Ready-made servers (npm)
```bash
# examples — TODO confirm exact package names/versions
npx -y @modelcontextprotocol/server-brave-search
npx -y @modelcontextprotocol/server-github
npx -y @modelcontextprotocol/server-slack
```
| Server | Used by |
|---|---|
| brave-search | Domain Intelligence, Scenario Generator |
| github | Execution, Knowledge |
| slack | Manager, Reporting |

## Config
<!-- TODO: where server configs live; keys from .env (BRAVE_API_KEY, GITHUB_PAT, SLACK_BOT_TOKEN) -->

## Custom MCP (ecvt-eval)
`python -m ecvt.eval_server` (stdio). Tools, setup and the Claude Code
command: `mcp-servers/README.md`; decision: ADR 0014.

## Next
→ `Environment.md`
