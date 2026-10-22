import anyio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
    async for message in query(
        prompt="Reply with exactly: ECVT agent hello OK",
        options=ClaudeAgentOptions(model="claude-haiku-5-5"),
    ):
        print(message)

anyio.run(main)
