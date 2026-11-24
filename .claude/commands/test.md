# /test — Offline checks before a commit

Run the checks that need no API key and report only what failed.

1. `ruff check .`
2. `python -m pytest -q`
3. `python scripts/eval_runner.py agents/*/eval_dataset.jsonl --check-only`
4. If an agent's `schema.py` or `tools.json` changed, confirm `tools.json` still equals
   the generated copy (`build_tools()` / `build_tool()`).

Report: one line per step (pass / fail + the first error). Do not fix anything unless asked.
