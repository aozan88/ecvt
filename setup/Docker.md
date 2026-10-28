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
