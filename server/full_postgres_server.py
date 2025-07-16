import os
import json
import csv
import time
from datetime import datetime
from uuid import uuid4
from typing import Optional, List, Dict
import sqlparse
from sqlparse.tokens import DDL, DML
import asyncpg
from pydantic import BaseModel, Field
from fastmcp import FastMCP
from metadata_cache import PostgresMetadataCache
import aioboto3



app = FastMCP()

# Pydantic Models
class ForeignKeyInfo(BaseModel):
    column: str
    referenced_table: str
    referenced_column: str

class ColumnInfo(BaseModel):
    name: str
    type: str

class TableFullMetadata(BaseModel):
    table_name: str
    comment: Optional[str] = None
    columns: List[ColumnInfo]
    foreign_keys: List[ForeignKeyInfo] = []
    low_cardinality_values: Optional[Dict[str, List[str]]] = None
    example_row: Optional[dict] = None

class DatabaseMetadata(BaseModel):
    tables: List[TableFullMetadata]

class SaveQueryResultsResponse(BaseModel):
    success: bool = Field(description="Whether the query execution and S3 upload was successful")
    s3_url: Optional[str] = Field(default=None, description="Presigned URL to download the CSV file from S3 (expires in 1 hour)")
    row_count: Optional[int] = Field(default=None, description="Number of rows returned by the query")
    query_time_ms: Optional[int] = Field(default=None, description="Total time for query execution and S3 upload in milliseconds")
    error: Optional[str] = Field(default=None, description="Error message if the operation failed")

# Add DatabaseConfig model for MCP tools
class DatabaseConfig(BaseModel):
    user: str
    password: str
    database: str
    host: str
    port: int = 5432

# Add S3Config model for MCP tools
class S3Config(BaseModel):
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    s3_bucket_name: str

# tools
@app.tool(
    exclude_args=["db_config"]
)
async def get_db_metadata(db_config: Optional[DatabaseConfig] = None) -> DatabaseMetadata:
    """
    Returns full database metadata including:
    - table names and descriptions
    - columns and data types
    - foreign keys
    - low-cardinality distinct values
    - 1 example row per table
    """
    assert db_config is not None, "db_config is required but was not provided"

    # Use target DB for introspection
    conn = await asyncpg.connect(
        user=db_config.user,
        password=db_config.password,
        database=db_config.database,
        host=db_config.host,
        port=db_config.port
    )

    # Use central cache DB for caching
    cache_key = f"{db_config.host}:{db_config.database}"
    cache = PostgresMetadataCache(conn, cache_key)
    ttl_minutes = 60

    try:
        # Try cache first
        cached = await cache.get(ttl_minutes=ttl_minutes)
        if cached:
            return DatabaseMetadata(**cached)

        # Cache is missing or expired — regenerate
        await cache.clear()
        new_metadata = await cache.generate_metadata()
        await cache.set(new_metadata)

        return DatabaseMetadata(**new_metadata)
    finally:
        await conn.close()

@app.tool(
    exclude_args=["db_config", "s3_config"]
)
async def run_query_save_results(query: str, db_config: Optional[DatabaseConfig] = None, s3_config: Optional[S3Config] = None) -> SaveQueryResultsResponse:
    """
    Validates the SQL (SELECT-only), runs it, and saves results to S3 as a CSV file.
    """
    # db_config and s3_config will be injected by process_tool_call, not provided by LLM

    assert db_config is not None, "db_config is required but was not provided"
    assert s3_config is not None, "s3_config is required but was not provided"

    # --- Step 1: Validate SQL ---
    try:
        parsed = sqlparse.parse(query.strip())

        if not parsed:
            return SaveQueryResultsResponse(success=False, error="Empty or invalid SQL query.")

        for statement in parsed:
            if not statement.get_type() == "SELECT":
                return SaveQueryResultsResponse(success=False, error=f"{statement.get_type()} queries are not permitted.")
            for token in statement.flatten():
                if token.ttype in (DDL, DML) and token.value.upper() in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"]:
                    return SaveQueryResultsResponse(success=False, error=f"{token.value.upper()} queries are not permitted.")
    except Exception as e:
        return SaveQueryResultsResponse(success=False, error=f"SQL validation error: {str(e)}")

    # --- Step 2: Execute and Save ---
    start = time.time()
    try:
        conn = await asyncpg.connect(
            user=db_config.user,
            password=db_config.password,
            database=db_config.database,
            host=db_config.host,
            port=db_config.port
        )
        
        records = await conn.fetch(query)
        end = time.time()
        if not records:
            return SaveQueryResultsResponse(success=False, error="Query returned no results.")

        row_count = len(records)
        
        # Generate S3 key with date-based folder structure
        today = datetime.now().strftime("%Y-%m-%d")
        filename = f"query_result_{uuid4().hex[:8]}.csv"
        s3_key = f"postgres_agent_query_results/{today}/{filename}"
        
        # Create CSV content in memory
        csv_content = []
        csv_content.append(list(records[0].keys()))  # CSV header
        for row in records:
            csv_content.append(list(row.values()))
        
        # Convert to CSV string
        csv_string = ""
        for row in csv_content:
            csv_string += ",".join(f'"{str(cell).replace('"', '""')}"' for cell in row) + "\n"
        
        # Upload to S3
        session = aioboto3.Session(
            aws_access_key_id=s3_config.aws_access_key_id,
            aws_secret_access_key=s3_config.aws_secret_access_key,
            region_name=s3_config.aws_region
        )
        async with session.client('s3') as s3_client:  # type: ignore
            await s3_client.put_object(
                Bucket=s3_config.s3_bucket_name,
                Key=s3_key,
                Body=csv_string.encode('utf-8'),
                ContentType='text/csv'
            )
        
        # Generate presigned URL (expires in 1 hour)
        presigned_url = await s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': s3_config.s3_bucket_name, 'Key': s3_key},
            ExpiresIn=3600  # 1 hour
        )

        # Clean the URL to prevent HTML encoding issues
        clean_url = presigned_url.replace('&amp;', '&')

        return SaveQueryResultsResponse(success=True, s3_url=clean_url, row_count=row_count, query_time_ms=int((end - start) * 1000))

    except Exception as e:
        return SaveQueryResultsResponse(success=False, error=f"Execution failed: {str(e)}")
    finally:
        await conn.close()

# ---------- Main ----------
if __name__ == "__main__":
    app.run()
    