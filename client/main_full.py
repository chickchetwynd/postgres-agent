import asyncio
from planner_prompt import planner_prompt
from evaluator_prompt import evaluator_prompt
from pydantic_ai import Agent
from pydantic import BaseModel, Field
from dataclasses import dataclass
from typing import Optional, Any, List, Annotated
from pydantic_ai.mcp import MCPServerStreamableHTTP, CallToolFunc, ToolResult
from pydantic_ai.tools import RunContext
from dotenv import load_dotenv
import os
import logfire
import argparse
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import uvicorn

# Add module-level variables for MCP contexts
planner_mcp_context = None
evaluator_mcp_context = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global planner_mcp_context, evaluator_mcp_context
    # Startup
    planner_mcp_context = planner_agent.run_mcp_servers()
    evaluator_mcp_context = evaluator_agent.run_mcp_servers()
    
    await planner_mcp_context.__aenter__()
    await evaluator_mcp_context.__aenter__()
    print("✅ MCP servers started")
    
    yield
    
    # Shutdown
    if planner_mcp_context:
        await planner_mcp_context.__aexit__(None, None, None)
    if evaluator_mcp_context:
        await evaluator_mcp_context.__aexit__(None, None, None)
    print("🛑 MCP servers stopped")

app = FastAPI(lifespan=lifespan)
load_dotenv()

logfire.configure(environment=os.getenv("LOGFIRE_ENVIRONMENT", "local"), scrubbing=False)
logfire.instrument_pydantic_ai()

# Agent output schema
class PlannerOutput(BaseModel):
    sql: str = Field(description="The generated SQL SELECT query")
    reasoning: str = Field(description="Step-by-step reasoning explaining how the query maps to the user's request")
    prompt_ambiguities: Optional[List[str]] = Field(
        default=None,
        description="A list of strings, where each string is one ambiguity in the user's request, or null if no ambiguities")
    assumptions: Optional[List[str]] = Field(
        default=None,
        description="A list of strings, where each string is one assumption (e.g., ['Q2 means April-June', 'performance means conversion_rate']), or null if no assumptions"
    )
    success: bool = Field(description="Whether query generation was successful")
    error: Optional[str] = Field(default=None, description="Error message if generation failed")

class EvaluatorOutput(BaseModel):
    success: bool = Field(description="Whether the query was executed successfully")
    confidence: float = Field(description="Confidence score (0.0 to 1.0) based on accuracy, clarity, and alignment with user request")
    confidence_reasoning: str = Field(description="Detailed explanation of confidence assessment including evaluation of assumptions and potential issues")
    sql: str = Field(description="The SQL query from the planner phase")
    reasoning: str = Field(description="The reasoning from the planner phase")
    assumptions: List[str] = Field(description="All assumptions (from planner + any additional ones discovered during evaluation)")
    s3_url: Optional[str] = Field(default=None, description="S3 URL returned by the run_query_save_results tool. Null if `success` is false.")
    row_count: Optional[int] = Field(default=None, description="Number of rows returned by the query")
    query_time_ms: Optional[int] = Field(default=None, description="Query execution time in milliseconds")
    error: Optional[str] = Field(default=None, description="Error message if execution failed or query was rejected")

class AgentRequest(BaseModel):
    prompt:str

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
    
    # Add configs to the tool arguments
    args['db_config'] = db_config
    
    # Only inject S3 config for tools that need it
    if tool_name == "run_query_save_results":
        s3_config = {
            "aws_access_key_id": ctx.deps.s3_config.aws_access_key_id,
            "aws_secret_access_key": ctx.deps.s3_config.aws_secret_access_key,
            "aws_region": ctx.deps.s3_config.aws_region,
            "s3_bucket_name": ctx.deps.s3_config.s3_bucket_name,
        }
        args['s3_config'] = s3_config
    
    return await call_tool(tool_name, args, {})


# MCP server config
server_url = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")
print(f"Connecting to server at: {server_url}")

full_postgres_server = MCPServerStreamableHTTP(
    server_url,
    process_tool_call=process_tool_call
)


# Replace the single agent with two agents
planner_agent = Agent(
    "anthropic:claude-3-5-sonnet-20241022",
    mcp_servers=[full_postgres_server],
    output_type=PlannerOutput,
    instructions=planner_prompt,
    deps_type=PostgresDeps
)

evaluator_agent = Agent(
    "anthropic:claude-3-5-sonnet-20241022",
    mcp_servers=[full_postgres_server],
    output_type=EvaluatorOutput,
    instructions=evaluator_prompt,
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

    print("Starting Two-Phase AI Agent (type 'exit' to quit)")
    async with planner_agent.run_mcp_servers(), evaluator_agent.run_mcp_servers():
        while True:
            user_input = input("\nPrompt: ")
            if user_input.strip().lower() in {"exit", "quit"}:
                print("Exiting.")
                break

            try:
                # Phase 1: Planner Agent
                print("\n[Phase 1] Planner agent running...")
                planner_result = await planner_agent.run(user_input, deps=deps)
                planner_output = planner_result.output
                print("Planner output:")
                print(planner_output.model_dump_json(indent=2))

                if not planner_output.success:
                    print(f"Planner failed: {planner_output.error}")
                    continue

                # Phase 2: Evaluator Agent
                print("\n[Phase 2] Evaluator agent running...")
                evaluator_result = await evaluator_agent.run(
                    "Please evaluate the planner's output in the message history and decide whether to execute.",
                    message_history=planner_result.new_messages(),
                    deps=deps
                )
                evaluator_output = evaluator_result.output
                print("Final agent output:")
                print(evaluator_output.model_dump_json(indent=2))

            except Exception as e:
                print("Error:", str(e))


@app.post(
    "/agent",
    summary="Run the postgres agent",
    description="Run this agent by providing the postgres credentials in the headers and the prompt in the body. Will return the final agent output.",
    )
async def agent(
    req: AgentRequest,
    pg_user: Annotated[str, Header()],
    pg_database: Annotated[str, Header()],
    pg_host: Annotated[str, Header()],
    pg_password: Annotated[str, Header()]
    ):
    """
    Runs the AI agent to translate a natural language prompt into a SQL query and execute it on the specified Postgres database.

    - **pg_user**: Postgres user (header: `pg-user`)
    - **pg_database**: Postgres database name (header: `pg-database`)
    - **pg_host**: Postgres host (header: `pg-host`)
    - **pg_password**: Postgres password (header: `pg-password`)
    """
    try:

        # build deps
        s3_config = S3Config(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            aws_region=os.getenv("AWS_DEFAULT_REGION", "us-west-2"),
            s3_bucket_name=os.getenv("S3_BUCKET_NAME", ""),
        )

        deps = PostgresDeps(
            user=pg_user,
            password=pg_password,
            database=pg_database,
            host=pg_host,
            s3_config=s3_config,
        )

        # Phase 1: planner
        planner_result = await planner_agent.run(req.prompt, deps=deps)
        if not planner_result.output.success:
            return {"planner_output": planner_result.output.model_dump(), "evaluator_output": None}

        # Phase 2: evaluator
        evaluator_result = await evaluator_agent.run(
            "Please evaluate the planner's output in the message history and decide whether to execute.",
            message_history=planner_result.new_messages(),
            deps=deps
        )

        return JSONResponse(content={
#            "planner_output": planner_result.output.model_dump(), # don't send planner output in final response for now.
            "evaluator_output": evaluator_result.output.model_dump()
        })


    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in CLI mode instead of starting the FastAPI server"
    )
    args = parser.parse_args()

    if args.cli:
        asyncio.run(main())
    else:
        uvicorn.run("main_full:app", host="0.0.0.0", port=10000, reload=True)

