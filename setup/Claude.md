# Setup — Claude tooling

> Skeleton (≤150 lines).

## Accounts
- Claude Pro/Max (claude.ai) — Projects + Claude Code session quota.
- Anthropic Console (console.anthropic.com) — API key + usage/billing.

## API key
1. Console → API Keys → Create key.
2. Add **$5** starter credit.
3. Store in `.env` as `ANTHROPIC_API_KEY` (see `Environment.md`). Never commit it.

## Claude Code CLI
```bash
npm install -g @anthropic-ai/claude-code
claude --version
```
- Reads repo context from `CLAUDE.md` and `.claude/`.

## Claude Agent SDK (Python)
```bash
pip install claude-agent-sdk   # Python 3.10+
```

## Verify
```bash
python scripts/hello_claude.py   # API key + anthropic SDK
python scripts/hello_agent.py    # Agent SDK round trip
```

## Links
- Agent SDK docs: https://code.claude.com/docs/en/agent-sdk/overview
- Console: https://console.anthropic.com

## Next
→ `Cursor.md` · `MCP.md`
