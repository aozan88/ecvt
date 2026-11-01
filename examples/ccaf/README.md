# CCA-F course examples

Runnable versions of the Claude Platform 101 / Building with the Claude API lessons. These are
learning scripts, separate from the agents and from the `wallet-api` demo target.

The exam asks which feature solves which failure, so each row names the failure.

| Topic | Script | Failure it answers | Watch for |
|---|---|---|---|
| API Stateless | `stateless_api.py` | Claude "forgets"; context grows | `input_tokens` rising every turn |
| Tool Use | `multi_tool.py` | 400 on missing results; runaway loops | Several `tool_use` blocks in one response; `--drop-result` |
| Built-in Tools | `builtin_tools.py` | Who runs the tool? | No `run_tool`; `pause_turn` |
| Skills | `skills_demo.py` | Skill or tool? | No `tool_use` for the skill itself; files in `claude_outputs/` |
| MCP | `mcp_demo.py` | Reusing tools hosted elsewhere | `mcp_tool_use` blocks; allowlisted toolset |
| Context Management | `context_management.py` | Context bloat and repeated cost | Cache `written`/`read`; `applied_edits` |

## Run

```bash
pip install -r requirements.txt
python examples/ccaf/multi_tool.py
```

- **API key:** `ANTHROPIC_API_KEY` from `.env` locally, or a Codespaces secret. Every run spends API credit.
- **Model:** `claude-opus-5-5` unless `ANTHROPIC_MODEL` is set. Set it to `claude-haiku-5-5` for cheap runs; `builtin_tools.py web` needs Sonnet or Opus.
- **Scope:** nothing here is imported by the agents or the pipeline.
