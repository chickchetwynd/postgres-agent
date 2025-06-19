import asyncio
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio
from dotenv import load_dotenv


load_dotenv()


postgres_server = MCPServerStdio(
    "python",
    args=["postgres_server.py"]
)

agent = Agent("anthropic:claude-3-opus-20240229", mcp_servers=[postgres_server])

async def main():
    print("Starting AI Agent (type 'exit' to quit)")
    async with agent.run_mcp_servers():
        while True:
            user_input = input("\n Prompt: ")
            if user_input.strip().lower() in {"exit", "quit"}:
                print("Exiting.")
                break

            try:
                result = await agent.run(user_input)

                if result.tool_calls:
                    last_tool = result.tool_calls[-1]
                    if last_tool.name == "run_query_route":
                        print("📊 Large query result (routed directly):")
                        print(last_tool.output)
                        continue

                print("Response:", result.output)
            except Exception as e:
                print("Error:", str(e))

if __name__ == "__main__":
    asyncio.run(main())