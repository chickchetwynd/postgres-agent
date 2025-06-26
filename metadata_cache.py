from datetime import datetime, timedelta, timezone
from typing import Optional
import json
import asyncpg


class PostgresMetadataCache:
    def __init__(self, db_pool: asyncpg.pool.Pool):
        self.db_pool = db_pool

    async def get(self, cache_key: str, ttl_minutes: int) -> Optional[dict]:
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT data, created_at
                FROM metadata_cache
                WHERE cache_key = $1
            """, cache_key)

            if not row:
                return None

            created_at = row["created_at"]
            if datetime.now(timezone.utc) - created_at > timedelta(minutes=ttl_minutes):
                return None

            return row["data"]

    async def set(self, cache_key: str, data: dict) -> None:
        async with self.db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO metadata_cache (cache_key, data, created_at)
                VALUES ($1, $2::jsonb, NOW())
                ON CONFLICT (cache_key)
                DO UPDATE SET data = EXCLUDED.data, created_at = EXCLUDED.created_at
            """, cache_key, json.dumps(data, default=str))

    async def clear(self, cache_key: str) -> None:
        async with self.db_pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM metadata_cache WHERE cache_key = $1
            """, cache_key)

    async def generate_metadata(self) -> dict:
        async with self.db_pool.acquire() as conn:
            # Step 1: Table names and comments
            table_rows = await conn.fetch("""
                SELECT c.relname AS table_name,
                       pg_catalog.obj_description(c.oid) AS comment
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r' AND n.nspname = 'public'
            """)
            table_names = [r["table_name"] for r in table_rows]

            # Step 2: Foreign keys
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

            fk_map: dict[str, list[dict]] = {}
            for row in fk_rows:
                fk = {
                    "column": row["column_name"],
                    "referenced_table": row["referenced_table"],
                    "referenced_column": row["referenced_column"]
                }
                fk_map.setdefault(row["table_name"], []).append(fk)

            # Step 3: Build metadata per table
            all_metadata: list[dict] = []

            for row in table_rows:
                table = row["table_name"]
                comment = row["comment"]

                # 3.1: Column schema
                schema_rows = await conn.fetch("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = $1
                """, table)
                columns = [{"name": r["column_name"], "type": r["data_type"]} for r in schema_rows]

                # 3.2: Low-cardinality values
                low_cardinality_values = {}
                for col in columns:
                    try:
                        distinct = await conn.fetch(
                            f"SELECT DISTINCT {col['name']} FROM {table} WHERE {col['name']} IS NOT NULL LIMIT 16"
                        )
                        if len(distinct) <= 15:
                            low_cardinality_values[col["name"]] = [str(r[col["name"]]) for r in distinct]
                    except Exception:
                        continue

                # 3.3: Example row
                try:
                    example = await conn.fetchrow(f"SELECT * FROM {table} LIMIT 1")
                    example_row = dict(example) if example else None
                except Exception:
                    example_row = None

                # Final dict
                table_metadata = {
                    "table_name": table,
                    "comment": comment,
                    "columns": columns,
                    "foreign_keys": fk_map.get(table, []),
                    "low_cardinality_values": low_cardinality_values or None,
                    "example_row": example_row
                }
                all_metadata.append(table_metadata)

            return {"tables": all_metadata}
