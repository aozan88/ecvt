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
