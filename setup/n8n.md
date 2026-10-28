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
