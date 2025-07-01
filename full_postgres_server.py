import os
import json
import csv
import time
from uuid import uuid4
from typing import Optional, List, Dict
import sqlparse
from sqlparse.tokens import DDL, DML
import asyncpg
from pydantic import BaseModel
from fastmcp import FastMCP
from metadata_cache import PostgresMetadataCache



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
    success: bool
    file_path: Optional[str] = None
    row_count: Optional[int] = None
    query_time_ms: Optional[int] = None
    error: Optional[str] = None

# Add DatabaseConfig model for MCP tools
class DatabaseConfig(BaseModel):
    user: str
    password: str
    database: str
    host: str
    port: int = 5432

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

    try:
        conn = await asyncpg.connect(
            user=db_config.user,
            password=db_config.password,
            database=db_config.database,
            host=db_config.host,
            port=db_config.port
        )

        cache = PostgresMetadataCache(conn)
        cache_key = "full_metadata_cache"
        ttl_minutes = 60

        # Try cache first
        cached = await cache.get(cache_key=cache_key, ttl_minutes=ttl_minutes)
        if cached:
            return DatabaseMetadata(**cached)

        # Cache is missing or expired — regenerate
        await cache.clear(cache_key)
        new_metadata = await cache.generate_metadata()
        await cache.set(cache_key=cache_key, data=new_metadata)

        return DatabaseMetadata(**new_metadata)
    finally:
        await conn.close()

@app.tool(
    exclude_args=["db_config"]
)
async def run_query_save_results(query: str, db_config: Optional[DatabaseConfig] = None) -> SaveQueryResultsResponse:
    """
    Validates the SQL (SELECT-only), runs it, and saves results to a local CSV file.
    """
    # db_config will be injected by process_tool_call, not provided by LLM

    assert db_config is not None, "db_config is required but was not provided"

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
        os.makedirs("results", exist_ok=True)
        filename = f"query_result_{uuid4().hex[:8]}.csv"
        filepath = os.path.join("results", filename)

        with open(filepath, mode="w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(records[0].keys())  # CSV header
            for row in records:
                writer.writerow(list(row.values()))

        return SaveQueryResultsResponse(success=True, file_path=filepath, row_count=row_count, query_time_ms=int((end - start) * 1000))

    except Exception as e:
        return SaveQueryResultsResponse(success=False, error=f"Execution failed: {str(e)}")
    finally:
        await conn.close()

# ---------- Main ----------
if __name__ == "__main__":
    app.run()
    