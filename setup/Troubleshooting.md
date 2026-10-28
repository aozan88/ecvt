# Setup — Troubleshooting

> Skeleton (≤150 lines). Grow this from real errors you hit.

## Known issues
- **`claude-agent-sdk` import error** — check Python ≥3.10; reinstall in the active venv.
- **n8n container can't reach host API** — on WSL/Docker use `host.docker.internal`, not `localhost`.
- **API key leak** — rotate in Console; `git filter-repo` to purge history.
- **`no matching manifest for windows/amd64` on `docker compose up`** — Docker
  Desktop is in Windows-containers mode. Switch to Linux containers (tray menu,
  or `& "C:\Program Files\Docker\Docker\DockerCli.exe" -SwitchLinuxEngine`);
  check with `docker info --format '{{.OSType}}'` → `linux`.
- **`UnicodeEncodeError: 'charmap'` / `cp1254` when printing** (Windows) — the
  console code page can't encode `→` etc. Set `PYTHONUTF8=1` or write to a
  UTF-8 file.
- **`gh` not found in a new shell right after install** — PATH is only picked
  up by new sessions; reopen the terminal or call
  `"C:\Program Files\GitHub CLI\gh.exe"` directly.
- **`gh pr create --body "..."` fails with `unknown argument` (PowerShell 5.1)**
  — embedded double quotes get split. Use `--body-file <file>`.

<!-- TODO: append issues as you encounter them, with the fix that worked -->
