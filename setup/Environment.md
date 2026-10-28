# Setup — Environment & Secrets

> Skeleton (≤150 lines).

## .env (never commit — it's in .gitignore)
Copy `.env.example` → `.env` and fill:
```
ANTHROPIC_API_KEY=sk-ant-...
N8N_URL=http://localhost:5678
N8N_API_KEY=
BRAVE_API_KEY=
GITHUB_PAT=ghp_...
SLACK_BOT_TOKEN=xoxb-...
```

## Secret hygiene
- Local: `.env` only. CI: GitHub Secrets.
- Rotate `ANTHROPIC_API_KEY` from the Console if exposed.
- Consider a `detect-secrets` pre-commit hook.

## Multi-PC sync
<!-- TODO: how you keep work-PC vs home-PC in sync (Codespaces vs local) -->

## Next
→ `Troubleshooting.md`
