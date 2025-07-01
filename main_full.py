import asyncio
from agent_prompt_full import system_prompt_full
from pydantic_ai import Agent
from pydantic import BaseModel
from dataclasses import dataclass
from typing import Optional, Any
from pydantic_ai.mcp import MCPServerStreamableHTTP, CallToolFunc, ToolResult
from pydantic_ai.tools import RunContext
from dotenv import load_dotenv
import os
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

# Define deps
@dataclass
class S3Config:
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    s3_bucket_name: str

@dataclass
class PostgresDeps:
    user: str
    password: str
    database: str
    host: str
    s3_config: S3Config

# Process tool call function to inject database dependencies
async def process_tool_call(
    ctx: RunContext[PostgresDeps],
    call_tool: CallToolFunc,
    tool_name: str,
    args: dict[str, Any],
) -> ToolResult:
    """Inject database credentials and S3 config into MCP tool calls."""
    # Add database config to the tool arguments
    db_config = {
        "user": ctx.deps.user,
        "password": ctx.deps.password,
        "database": ctx.deps.database,
        "host": ctx.deps.host,
        "port": 5432
    }
    
    # Add S3 config to the tool arguments
    s3_config = {
        "aws_access_key_id": ctx.deps.s3_config.aws_access_key_id,
        "aws_secret_access_key": ctx.deps.s3_config.aws_secret_access_key,
        "aws_region": ctx.deps.s3_config.aws_region,
        "s3_bucket_name": ctx.deps.s3_config.s3_bucket_name,
    }
    
    # Add configs to the tool arguments
    args['db_config'] = db_config
    args['s3_config'] = s3_config
    
    return await call_tool(tool_name, args, {})


full_postgres_server = MCPServerStreamableHTTP(
    'http://127.0.0.1:8000/mcp',
    process_tool_call=process_tool_call
)

# Full metadata agent
agent = Agent(
    "anthropic:claude-3-opus-20240229",
    mcp_servers=[full_postgres_server],
    output_type=AgentFinalOutput,
    instructions=system_prompt_full,
    deps_type=PostgresDeps
)

# Main event loop
async def main():
    # Validate required environment variables
    required_env_vars = ["PGUSER", "PGPASSWORD", "PGDATABASE", "PGHOST", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "S3_BUCKET_NAME"]
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {missing_vars}")

    s3_config = S3Config(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        aws_region=os.getenv("AWS_DEFAULT_REGION", "us-west-2"),
        s3_bucket_name=os.getenv("S3_BUCKET_NAME", ""),
    )

    deps = PostgresDeps(
        user=os.getenv("PGUSER", ""),
        password=os.getenv("PGPASSWORD", ""),
        database=os.getenv("PGDATABASE", ""),
        host=os.getenv("PGHOST", ""),
        s3_config=s3_config,
    )

    print("🚀 Starting Full Metadata AI Agent (type 'exit' to quit)")
    async with agent.run_mcp_servers():
        while True:
            user_input = input("\n📝 Prompt: ")
            if user_input.strip().lower() in {"exit", "quit"}:
                print("👋 Exiting.")
                break

            try:
                result = await agent.run(user_input, deps=deps)
                output = result.output
                print("\n📦 Final Agent Output:\n")
                print(output.model_dump_json(indent=2))
            except Exception as e:
                print("❌ Error:", str(e))

if __name__ == "__main__":
    asyncio.run(main())
