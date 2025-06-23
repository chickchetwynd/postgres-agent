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





@app.tool()
async def get_tables() -> RichTableList:
    """
    Return all table names along with comments and foreign key relationships.
    """
    db = await get_pool()
    async with db.acquire() as conn:
        # Step 1: Get table names and comments
        table_rows = await conn.fetch("""
            SELECT c.relname AS table_name,
                   pg_catalog.obj_description(c.oid) AS comment
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r'
              AND n.nspname = 'public'
        """)

        # Step 2: Get all foreign key relationships
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

        # Step 3: Map foreign keys by table
        fk_map: dict[str, list[ForeignKeyInfo]] = {}
        for row in fk_rows:
            fk = ForeignKeyInfo(
                column=row["column_name"],
                referenced_table=row["referenced_table"],
                referenced_column=row["referenced_column"]
            )
            fk_map.setdefault(row["table_name"], []).append(fk)

        # Step 4: Build final response
        tables = [
            TableMetadata(
                table_name=row["table_name"],
                comment=row["comment"],
                foreign_keys=fk_map.get(row["table_name"], [])
            )
            for row in table_rows
        ]

        return RichTableList(tables=tables)
