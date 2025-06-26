import os
import json
import csv
from uuid import uuid4
from typing import Optional, List, Dict
import sqlparse
from sqlparse.tokens import DDL, DML
from sqlparse.sql import Statement
import asyncpg
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError
from mcp.server.fastmcp import FastMCP
from metadata_cache import PostgresMetadataCache

# Load environment
load_dotenv()

app = FastMCP(request_timeout=600)

# Postgres config
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

metadata_cache: PostgresMetadataCache | None = None

async def get_metadata_cache() -> PostgresMetadataCache:
    global metadata_cache
    if metadata_cache is None:
        db = await get_pool()
        metadata_cache = PostgresMetadataCache(db)
    return metadata_cache

# ---------- Pydantic Models ----------
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
    error: Optional[str] = None


# tools

@app.tool()
async def get_db_metadata_test() -> DatabaseMetadata:
    """
    Returns full database metadata including:
    - table names and descriptions
    - columns and data types
    - foreign keys
    - low-cardinality distinct values
    - 1 example row per table
    """

    cache = await get_metadata_cache()
    cache_key = "full_metadata_cache"
    ttl_minutes = 60

    # Try cache first
    cached = await cache.get(cache_key=cache_key, ttl_minutes=ttl_minutes)
    if cached:
        return cached

    # Cache is missing or expired — regenerate
    await cache.clear(cache_key)
    new_metadata = await cache.generate_metadata()
    await cache.set(cache_key=cache_key, data=new_metadata)

    return new_metadata




#@app.tool()
#async def get_db_metadata() -> DatabaseMetadata:
#    """
#    Returns full database metadata including:
#    - table names and descriptions
#    - columns and data types
#    - foreign keys
#    - low-cardinality distinct values
#    - 1 example row per table
#    """
#    # Try Redis cache first
#    cached = await redis_client.get("full_metadata_cache")
#    if cached:
#        try:
#            data = json.loads(cached)
#            return DatabaseMetadata.model_validate(data)
#        except (json.JSONDecodeError, ValidationError):
#            pass  # fallback to live query
#
#    db = await get_pool()
#    async with db.acquire() as conn:
#        # Step 1: Table names and comments
#        table_rows = await conn.fetch("""
#            SELECT c.relname AS table_name,
#                   pg_catalog.obj_description(c.oid) AS comment
#            FROM pg_catalog.pg_class c
#            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
#            WHERE c.relkind = 'r' AND n.nspname = 'public'
#        """)
#
#        table_names = [r["table_name"] for r in table_rows]
#
#        # Step 2: Foreign keys (build once for all tables)
#        fk_rows = await conn.fetch("""
#            SELECT
#                tc.table_name,
#                kcu.column_name,
#                ccu.table_name AS referenced_table,
#                ccu.column_name AS referenced_column
#            FROM information_schema.table_constraints AS tc
#            JOIN information_schema.key_column_usage AS kcu
#              ON tc.constraint_name = kcu.constraint_name
#              AND tc.table_schema = kcu.table_schema
#            JOIN information_schema.constraint_column_usage AS ccu
#              ON ccu.constraint_name = tc.constraint_name
#              AND ccu.table_schema = tc.table_schema
#            WHERE tc.constraint_type = 'FOREIGN KEY'
#        """)
#
#        fk_map: dict[str, list[ForeignKeyInfo]] = {}
#        for row in fk_rows:
#            fk = ForeignKeyInfo(
#                column=row["column_name"],
#                referenced_table=row["referenced_table"],
#                referenced_column=row["referenced_column"]
#            )
#            fk_map.setdefault(row["table_name"], []).append(fk)
#
#        # Step 3: Build metadata per table
#        full_metadata: list[TableFullMetadata] = []
#
#        for row in table_rows:
#            table = row["table_name"]
#            comment = row["comment"]
#
#            # 3.1: Column schema
#            schema_rows = await conn.fetch("""
#                SELECT column_name, data_type
#                FROM information_schema.columns
#                WHERE table_name = $1
#            """, table)
#            columns = [ColumnInfo(name=r["column_name"], type=r["data_type"]) for r in schema_rows]
#
#            # 3.2: Low-cardinality distinct values (threshold: ≤15 distinct)
#            low_cardinality_values: dict[str, list[str]] = {}
#            for col in columns:
#                try:
#                    distinct = await conn.fetch(
#                        f"SELECT DISTINCT {col.name} FROM {table} WHERE {col.name} IS NOT NULL LIMIT 16"
#                    )
#                    if len(distinct) <= 15:
#                        low_cardinality_values[col.name] = [str(r[col.name]) for r in distinct]
#                except Exception:
#                    continue  # skip if error
#
#            # 3.3: Example row
#            try:
#                example = await conn.fetchrow(f"SELECT * FROM {table} LIMIT 1")
#                example_row = dict(example) if example else None
#            except Exception:
#                example_row = None
#
#            # Final table entry
#            table_metadata = TableFullMetadata(
#                table_name=table,
#                comment=comment,
#                columns=columns,
#                foreign_keys=fk_map.get(table, []),
#                low_cardinality_values=low_cardinality_values or None,
#                example_row=example_row
#            )
#            full_metadata.append(table_metadata)
#
#        result = DatabaseMetadata(tables=full_metadata)
#
#        # Cache it in Redis (TTL = 1 hour)
#        await redis_client.set(
#            "full_metadata_cache",
#            json.dumps(result.model_dump(), default=str),
#            ex=3600
#        )
#
#        return result


@app.tool()
async def run_query_save_results(query: str) -> SaveQueryResultsResponse:
    """
    Validates the SQL (SELECT-only), runs it, and saves results to a local CSV file.
    """

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
    db = await get_pool()
    async with db.acquire() as conn:
        try:
            records = await conn.fetch(query)
            if not records:
                return SaveQueryResultsResponse(success=False, error="Query returned no results.")

            os.makedirs("results", exist_ok=True)
            filename = f"query_result_{uuid4().hex[:8]}.csv"
            filepath = os.path.join("results", filename)

            with open(filepath, mode="w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(records[0].keys())  # CSV header
                for row in records:
                    writer.writerow(list(row.values()))

            return SaveQueryResultsResponse(success=True, file_path=filepath)

        except Exception as e:
            return SaveQueryResultsResponse(success=False, error=f"Execution failed: {str(e)}")



# ---------- Main ----------
if __name__ == "__main__":
    app.run(transport="stdio")
