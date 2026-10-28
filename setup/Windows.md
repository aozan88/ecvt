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
