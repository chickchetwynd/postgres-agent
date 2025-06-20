import asyncio
from agent_prompt import system_prompt
from pydantic_ai import Agent
from pydantic import BaseModel
from typing import Optional
from pydantic_ai.mcp import MCPServerStdio
from dotenv import load_dotenv


load_dotenv()


class AgentFinalOutput(BaseModel):
    sql: str
    reasoning: str
    file_path: Optional[str] = None
    success: bool
    error: Optional[str] = None

postgres_server = MCPServerStdio(
    "python",
    args=["postgres_server.py"]
)

agent = Agent(
    "anthropic:claude-3-opus-20240229",
    mcp_servers=[postgres_server],
    output_type=AgentFinalOutput,
    instructions=system_prompt
)

async def main():
    print("🚀 Starting AI Agent (type 'exit' to quit)")
    async with agent.run_mcp_servers():
        while True:
            user_input = input("\n📝 Prompt: ")
            if user_input.strip().lower() in {"exit", "quit"}:
                print("👋 Exiting.")
                break

            try:
                result = await agent.run(user_input)
                output = result.output
                print("\n📦 Final Agent Output:\n")
                print(output.model_dump_json(indent=2))
            except Exception as e:
                print("❌ Error:", str(e))

if __name__ == "__main__":
    asyncio.run(main())