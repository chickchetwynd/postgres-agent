# main_full.py
import asyncio
from agent_prompt_full import system_prompt_full
from pydantic_ai import Agent
from pydantic import BaseModel
from typing import Optional
from pydantic_ai.mcp import MCPServerStreamableHTTP
from dotenv import load_dotenv
import logfire

load_dotenv()

logfire.configure()
logfire.instrument_pydantic_ai()

# Agent output schema
class AgentFinalOutput(BaseModel):
    sql: str
    reasoning: str
    file_path: Optional[str] = None
    success: bool
    row_count: Optional[int] = None
    query_time_ms: Optional[int] = None
    error: Optional[str] = None
    confidence: float

# Link to new full metadata MCP server
full_postgres_server = MCPServerStreamableHTTP('http://127.0.0.1:8000/mcp')

# Full metadata agent
agent = Agent(
    "anthropic:claude-3-opus-20240229",
    mcp_servers=[full_postgres_server],
    output_type=AgentFinalOutput,
    instructions=system_prompt_full
)

# Main event loop
async def main():
    print("🚀 Starting Full Metadata AI Agent (type 'exit' to quit)")
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
