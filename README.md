# ECVT

AI Quality Engineering Platform · Multi-agent edge-case validation.

ECVT (Edge Case Validation Toolkit) validates existing AI-based or classic
software systems before and after production — generating edge cases,
verifying acceptance criteria, tracking regression, and producing
audience-specific reports. It does **not** generate software (unlike
MetaGPT / CrewAI); it measures software quality.

## Quick Start

1. Choose your OS guide: [Windows](setup/Windows.md) | [Mac](setup/Mac.md) | [WSL](setup/WSL.md)
2. Install Claude tooling: [Claude.md](setup/Claude.md) + [Cursor.md](setup/Cursor.md)
3. Run n8n: [n8n.md](setup/n8n.md) + [Docker.md](setup/Docker.md)
4. Configure environment: [Environment.md](setup/Environment.md)
5. Hello query: `python scripts/hello_claude.py`

Stuck? See [Troubleshooting](setup/Troubleshooting.md).

## Pipeline

```
Manager → Domain Intelligence → Scenario Generator → Execution → Evaluation
          → (v1.1) Regression → (v1.2) Reporting → (v2) Knowledge
```

Domain-agnostic, plugin-based. See [docs/02-architecture](docs/02-architecture/architecture.md).

## Agents (MVP = 5 of 9)

| Code | Agent | Version |
|------|-------|---------|
| 01 | Manager | MVP |
| 02 | Domain Intelligence | MVP |
| 03 | Scenario Generator | MVP |
| 04 | Acceptance | v1.x |
| 05 | Execution | MVP |
| 06 | Evaluation | MVP |
| 07 | Regression | v1.1 |
| 08 | Reporting | v1.2 |
| 09 | Knowledge | v2 |

## Status

Plan is **frozen** (Master Plan v6.2 / Mapping v3). New decisions are recorded
as ADRs under [docs/11-decisions](docs/11-decisions/). No plan revisions.
