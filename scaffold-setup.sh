#!/usr/bin/env bash
# ECVT setup/ scaffolder — H2 (7 Jul 2026)
# Run from repo root inside Codespaces:  bash scaffold-setup.sh
# Creates skeleton setup docs. NEVER overwrites a file that already has content.

set -euo pipefail
mkdir -p setup

created=(); skipped=()

# write <path> <<'EOF' ... EOF   — but only if file is missing or empty
write() {
  local f="$1"
  if [[ -s "$f" ]]; then
    skipped+=("$f")
    cat >/dev/null   # consume heredoc
  else
    cat > "$f"
    created+=("$f")
  fi
}

write setup/Windows.md <<'EOF'
# Setup — Windows

> Skeleton (≤150 lines). Fill TODOs, then delete this note.

## Prerequisites
- Windows 10/11, PowerShell 7+
- **Recommended:** enable WSL2 and follow `WSL.md` for the Linux-side toolchain.

## Install (PowerShell, via winget or scoop)
```powershell
winget install --id Git.Git -e
winget install --id GitHub.cli -e
winget install --id Python.Python.3.12 -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Docker.DockerDesktop -e
```

## Windows-specific notes
- Docker Desktop must use the **WSL2 backend** (Settings → General).
- Line endings: set `git config --global core.autocrlf input`.

## Common issues
<!-- TODO: PATH not updated after install; Docker WSL integration off; long-path errors -->

## Next
→ `Claude.md` · `Docker.md` · `n8n.md` · `Environment.md`
EOF

write setup/Mac.md <<'EOF'
# Setup — macOS

> Skeleton (≤150 lines).

## Prerequisites
- macOS 13+; note **Apple Silicon (ARM)** vs **Intel** where it matters.
- Xcode Command Line Tools: `xcode-select --install`

## Install (Homebrew)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install git gh python@3.12 node
brew install --cask docker
```

## Apple Silicon notes
<!-- TODO: rosetta for x86 images; platform flags for docker; python arch mismatches -->

## Next
→ `Claude.md` · `Docker.md` · `n8n.md` · `Environment.md`
EOF

write setup/WSL.md <<'EOF'
# Setup — WSL2 (Windows Subsystem for Linux)

> Skeleton (≤150 lines).

## Install WSL2 + Ubuntu
```powershell
wsl --install -d Ubuntu
```

## Inside Ubuntu
```bash
sudo apt update && sudo apt install -y git python3 python3-pip
# gh CLI, Node via nodesource — TODO pin versions
```

## Windows ↔ WSL bridge
- Repo lives on the Linux filesystem (`~/dev/...`), NOT `/mnt/c`, for speed.
- Docker Desktop → Settings → Resources → WSL Integration → enable your distro.

## Common issues
<!-- TODO: clock drift; localhost vs WSL IP for n8n; file watchers -->

## Next
→ `Docker.md` · `n8n.md`
EOF

write setup/Claude.md <<'EOF'
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

## Links
- Agent SDK docs: https://code.claude.com/docs/en/agent-sdk/overview
- Console: https://console.anthropic.com

## Next
→ `Cursor.md` · `MCP.md`
EOF

write setup/Cursor.md <<'EOF'
# Setup — Cursor / VS Code

> Skeleton (≤150 lines).

## Option A — Cursor
- Download: https://cursor.com  (Pro optional)
- Sign in; set default model preferences.

## Option B — VS Code + Claude Code
- Install VS Code, then the Claude Code extension.
- Authenticate with your Claude Pro account.

## Recommended settings
<!-- TODO: format-on-save, Ruff/ESLint integration, .editorconfig -->

## Next
→ `Claude.md`
EOF

write setup/Gemini.md <<'EOF'
# Setup — Gemini (OPTIONAL, second-opinion only)

> ⚠️ **DECISION NEEDED — conflicts with ADR 0001 (Anthropic-only stack).**
> Mapping v3 lists Gemini/ChatGPT as *dev-time second-opinion* tools, but the
> core architecture is Anthropic-only. Before filling this in, choose ONE:
>   (a) Keep — but scope strictly to *manual, dev-time second-opinion queries*
>       (never in the agent pipeline). Add a short note to ADR 0001, or a new ADR.
>   (b) Delete this file and drop Gemini from the toolchain entirely.
> Leaving this unresolved is the contradiction flagged in the repo audit.

## If kept — scope
<!-- TODO: state explicitly: no Gemini in production agents, no gemini SDK in deps -->

## Install
<!-- TODO -->
EOF

write setup/n8n.md <<'EOF'
# Setup — n8n (self-hosted via Docker)

> Skeleton (≤150 lines). Requires `Docker.md` done first.

## Run with docker compose
Add to repo-root `docker-compose.yml` (or use the existing one):
```yaml
services:
  n8n:
    image: docker.n8n.io/n8nio/n8n
    ports: ["5678:5678"]
    environment:
      - N8N_HOST=localhost
      - N8N_PORT=5678
    volumes:
      - n8n_data:/home/node/.n8n
volumes:
  n8n_data:
```
```bash
docker compose up -d n8n
```

## First run
- Open http://localhost:5678 → create the admin user.
- Set `N8N_URL=http://localhost:5678` in `.env`.

## Webhook smoke test
<!-- TODO: create a webhook node; curl it; confirm 200 -->

## Common issues
<!-- TODO: port 5678 in use; container can't reach host API; WSL localhost -->

## Next
→ `Environment.md`
EOF

write setup/Docker.md <<'EOF'
# Setup — Docker Desktop

> Skeleton (≤150 lines). Install BEFORE n8n.

## Install
- Windows: `winget install Docker.DockerDesktop` (WSL2 backend).
- macOS: `brew install --cask docker`.
- Verify: `docker --version && docker compose version`.

## Basic commands
```bash
docker compose up -d        # start services
docker compose ps           # status
docker compose logs -f n8n  # follow logs
docker compose down         # stop
```

## Volumes
<!-- TODO: where n8n_data lives; backup/restore note -->

## Next
→ `n8n.md`
EOF

write setup/MCP.md <<'EOF'
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
<!-- TODO: scaffold under mcp-servers/ecvt-eval/; score_response(input, output, criteria) -> 0-1 -->

## Next
→ `Environment.md`
EOF

write setup/Environment.md <<'EOF'
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
EOF

write setup/Troubleshooting.md <<'EOF'
# Setup — Troubleshooting

> Skeleton (≤150 lines). Grow this from real errors you hit.

## Known issues
- **`claude-agent-sdk` import error** — check Python ≥3.10; reinstall in the active venv.
- **n8n container can't reach host API** — on WSL/Docker use `host.docker.internal`, not `localhost`.
- **API key leak** — rotate in Console; `git filter-repo` to purge history.

<!-- TODO: append issues as you encounter them, with the fix that worked -->
EOF

echo
echo "== Created =="; printf '  %s\n' "${created[@]:-<none>}"
echo "== Skipped (already had content) =="; printf '  %s\n' "${skipped[@]:-<none>}"
echo
echo "Review, then:"
echo "  git add setup/"
echo "  git commit -m 'docs(setup): scaffold modular setup skeletons'"
