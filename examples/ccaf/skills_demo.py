"""Skills: instructions Claude loads and follows, not a tool it calls.

Tool = execution (tool_use goes out, your code runs, tool_result comes back).
Skill = context (Claude reads it and applies it). The description is always visible and the full
body loads only when needed (progressive disclosure), so a skill costs context tokens.

This uses Anthropic's pre-built xlsx skill on the Messages API, which runs inside the code
execution container. Generated files are saved to claude_outputs/ (gitignored).

ECVT does not use this upload path: agents/*/SKILL.md files load from the filesystem through
the Agent SDK. Learn the mechanism here; there is no need to upload ECVT skills.

    python examples/ccaf/skills_demo.py
"""

from __future__ import annotations

import os

import anthropic
from dotenv import load_dotenv

load_dotenv()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
MAX_CONTINUATIONS = 3
OUTPUT_DIR = "claude_outputs"
SKILLS = [{"type": "anthropic", "skill_id": "xlsx", "version": "latest"}]
PROMPT = "Create an .xlsx with three sample monthly expenses and a total row that uses a formula."
client = anthropic.Anthropic()


def run() -> anthropic.types.beta.BetaMessage:
    container = {"skills": SKILLS}
    messages = [{"role": "user", "content": PROMPT}]

    for _ in range(MAX_CONTINUATIONS):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["code-execution-2025-08-25"],
            container=container,
            tools=[{"type": "code_execution_20260521", "name": "code_execution"}],
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            break
        container = {"id": response.container.id, "skills": SKILLS}
        messages = [
            {"role": "user", "content": PROMPT},
            {"role": "assistant", "content": response.content},
        ]
    return response


def save_files(response: anthropic.types.beta.BetaMessage) -> None:
    for block in response.content:
        if block.type != "bash_code_execution_tool_result":
            continue
        result = block.content
        if result.type != "bash_code_execution_result":
            continue
        for file_ref in result.content or []:
            metadata = client.files.retrieve_metadata(file_ref.file_id)
            # basename() so a model-chosen filename can't escape OUTPUT_DIR.
            name = os.path.basename(metadata.filename)
            if not name or name in (".", ".."):
                print(f"Skipping invalid filename: {metadata.filename}")
                continue
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            path = os.path.join(OUTPUT_DIR, name)
            client.files.download(file_ref.file_id).write_to_file(path)
            print(f"Saved: {path}")


if __name__ == "__main__":
    final = run()
    print(f"stop_reason={final.stop_reason}")
    for content_block in final.content:
        if content_block.type == "text":
            print(content_block.text)
    save_files(final)
