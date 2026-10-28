# Setup — Gemini

> STUB — max 100-150 lines. Gemini Antigravity install (optional).

## Scope (why this file exists)

Mapping v3 lists Gemini/ChatGPT as a **dev-time, second-opinion sub-tool**.
That is compatible with ADR 0001 (Anthropic-only *agent runtime*) as long as:

- Gemini is used **manually by the developer** (research, second opinions).
- It is **never** called from an ECVT agent, plugin, or n8n workflow.
- No Gemini SDK appears in `requirements.txt` / `pyproject.toml`.

## TODO

- [ ] Gemini Antigravity install (optional)
- [ ] Typical second-opinion uses during research phases
- [ ] Keep within 100-150 lines
- [ ] Remove the STUB banner when this doc is complete.
