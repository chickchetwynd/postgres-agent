from pydantic import BaseModel
from typing import Optional, List
import asyncpg
import os
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
import sqlparse
from sqlparse.sql import Statement
from sqlparse.tokens import DDL, DML
import csv
from uuid import uuid4

load_dotenv()
app = FastMCP()

DB_CONFIG = {
    "user": os.getenv("PGUSER"),
    "password": os.getenv("PGPASSWORD"),
    "database": os.getenv("PGDATABASE"),
    "host": os.getenv("PGHOST"),
}
pool: Optional[asyncpg.Pool] = None

async def get_pool() -> asyncpg.Pool:
    global pool
    if not pool:
        pool = await asyncpg.create_pool(**DB_CONFIG)
        print("✅ Connected to DB.")
    return pool

# Pydantic models
class QueryResult(BaseModel):
    data: Optional[List[dict]] = None
    error: Optional[str] = None

class TableList(BaseModel):
    tables: List[str]

class TableSchema(BaseModel):
    columns: List[dict]

class SQLValidationResult(BaseModel):
    valid: bool
    reason: Optional[str] = None

class QueryMeta(BaseModel):
    row_count: int
    execution_time_ms: float
    error: Optional[str] = None

class SaveQueryResultsResponse(BaseModel):
    success: bool
    file_path: Optional[str] = None
    error: Optional[str] = None

class ForeignKeyInfo(BaseModel):
    column: str
    referenced_table: str
    referenced_column: str

class TableMetadata(BaseModel):
    table_name: str
    comment: Optional[str] = None
    foreign_keys: List[ForeignKeyInfo] = []

class RichTableList(BaseModel):
    tables: List[TableMetadata]

class DistinctValuesResponse(BaseModel):
    values: list[str]
    error: str | None = None


# Tools
@app.tool()
async def get_tables() -> RichTableList:
    """
     Returns a list of all tables in the public schema, including:
    - Table name
    - Optional table description (from Postgres comments)
    - Any foreign key relationships (i.e., which columns link to other tables)
    Use this tool at the beginning of a run to explore available tables, understand their purpose, and identify potential join paths across the schema. This metadata helps determine which tables are relevant to the user’s request and how they relate to one another.
    """
    db = await get_pool()
    async with db.acquire() as conn:
# Get table names and comments
        table_rows = await conn.fetch("""
            SELECT c.relname AS table_name,
                   pg_catalog.obj_description(c.oid) AS comment
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r'
              AND n.nspname = 'public'
        """)

# Get all foreign key relationships
        fk_rows = await conn.fetch("""
            SELECT
                tc.table_name,
                kcu.column_name,
                ccu.table_name AS referenced_table,
                ccu.column_name AS referenced_column
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
              AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
        """)

# Map foreign keys by table
        fk_map: dict[str, list[ForeignKeyInfo]] = {}
        for row in fk_rows:
            fk = ForeignKeyInfo(
                column=row["column_name"],
                referenced_table=row["referenced_table"],
                referenced_column=row["referenced_column"]
            )
            fk_map.setdefault(row["table_name"], []).append(fk)

# Build final response
        tables = [
            TableMetadata(
                table_name=row["table_name"],
                comment=row["comment"],
                foreign_keys=fk_map.get(row["table_name"], [])
            )
            for row in table_rows
        ]

        return RichTableList(tables=tables)

@app.tool()
async def get_table_schema(table: str) -> TableSchema:
    """
    Returns the schema of a specific table, including:
    - Column names
    - Data types
    """
    db = await get_pool()
    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = $1
        """, table)
        return TableSchema(columns=[dict(r) for r in rows])

@app.tool()
async def validate_sql(query: str) -> SQLValidationResult:
    """
    Validate that the SQL is safe (e.g., SELECT-only) using sqlparse.
    """
    try:
        # Parse the SQL query into a list of statements
        parsed = sqlparse.parse(query.strip())
        
        if not parsed:
            return SQLValidationResult(valid=False, reason="Empty or invalid SQL query.")

        for statement in parsed:
            # Check if the statement is a SELECT query
            if not statement.get_type() == "SELECT":
                # Specifically check for modifying statements
                if statement.get_type() in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"]:
                    return SQLValidationResult(valid=False, reason=f"{statement.get_type()} queries are not permitted.")
                return SQLValidationResult(valid=False, reason="Only SELECT queries are allowed.")
            
            # Additional check for tokens that might indicate modifying operations
            for token in statement.flatten():
                if token.ttype in (DDL, DML) and token.value.upper() in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"]:
                    return SQLValidationResult(valid=False, reason=f"{token.value.upper()} queries are not permitted.")

        return SQLValidationResult(valid=True)
    except Exception as e:
        return SQLValidationResult(valid=False, reason=f"Invalid SQL query: {str(e)}")

@app.tool()
async def test_sql(query: str) -> QueryResult:
    """Run the SQL with a LIMIT 1 to check for validity."""
    # Validate the query again using validate_sql
    validation = await validate_sql(query)
    if not validation.valid:
        return QueryResult(error=validation.reason)
    
    # Proceed with query execution
    db = await get_pool()
    async with db.acquire() as conn:
        try:
            limited_query = f"SELECT * FROM ({query.rstrip(';')}) AS subquery LIMIT 1"
            records = await conn.fetch(limited_query)
            return QueryResult(data=[dict(r) for r in records])
        except Exception as e:
            return QueryResult(error=f"Query execution failed: {str(e)}")


@app.tool()
async def get_distinct_values(table: str, column: str) -> DistinctValuesResponse:
    """
    Return the distinct values for a given column in a specific table.

    Use this tool when you need to:
    - Understand possible values in a categorical column
    - Disambiguate user input by inspecting how values are spelled or capitalized
    - Prepare for GROUP BY operations or filters based on specific values

    Limits to 100 values to avoid overwhelming the prompt. Returns an error if the column or table is invalid.
    """
    db = await get_pool()
    async with db.acquire() as conn:
        try:
            query = f"""
                SELECT DISTINCT {column}
                FROM {table}
                WHERE {column} IS NOT NULL
                LIMIT 100
            """
            rows = await conn.fetch(query)
            return DistinctValuesResponse(values=[str(r[column]) for r in rows])
        except Exception as e:
            return DistinctValuesResponse(values=[], error=str(e))

# deprecated tool
#@app.tool()
#async def query_meta_data(query: str) -> QueryMeta:
#    """
#    Run a query and return its row count and execution time (in ms).
#    """
#    import time
#    # Validate the query using validate_sql
#    validation = await validate_sql(query)
#    if not validation.valid:
#        return QueryMeta(row_count=0, execution_time_ms=0, error=validation.reason)
#    
#    # Proceed with query execution
#    db = await get_pool()
#    async with db.acquire() as conn:
#        try:
#            # Wrap query in a COUNT to get row count efficiently
#            count_query = f"SELECT COUNT(*) FROM ({query.rstrip(';')}) AS subquery"
#            start = time.time()
#            count_result = await conn.fetchval(count_query)
#            elapsed = (time.time() - start) * 1000
#            return QueryMeta(row_count=count_result, execution_time_ms=elapsed)
#        except Exception as e:
#           return QueryMeta(row_count=0, execution_time_ms=0, error=f"Query execution failed: {str(e)}")


@app.tool()
async def save_query_results(query: str) -> SaveQueryResultsResponse:
    """
    Execute a validated SQL SELECT query and save the results to a local CSV file.

    Use this tool **only after** a query has been:
    1. Validated using validate_sql(query)
    2. Tested with test_sql(query) to ensure correctness and usefulness
    """

    # Validate SQL before running
    validation = await validate_sql(query)
    if not validation.valid:
        return SaveQueryResultsResponse(success=False, error=validation.reason)

    db = await get_pool()
    async with db.acquire() as conn:
        try:
            records = await conn.fetch(query)
            if not records:
                return SaveQueryResultsResponse(success=False, error="Query returned no results.")

            # Prepare file
            os.makedirs("results", exist_ok=True)
            filename = f"query_result_{uuid4().hex[:8]}.csv"
            filepath = os.path.join("results", filename)

            # Write CSV
            with open(filepath, mode="w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(records[0].keys())  # CSV header
                for row in records:
                    writer.writerow(list(row.values()))

            return SaveQueryResultsResponse(success=True, file_path=filepath)

        except Exception as e:
            return SaveQueryResultsResponse(success=False, error=f"Execution failed: {str(e)}")

# Main
if __name__ == "__main__":
    app.run(transport="stdio")