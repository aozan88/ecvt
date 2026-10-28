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
