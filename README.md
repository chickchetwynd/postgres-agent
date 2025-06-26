To manually delete the data in redis, run:

```bash
docker exec -it redis redis-cli
DEL tables_metadata_cache
EXIT
```

to run mcp inspector:

```bash
npx @modelcontextprotocol/inspector uv run full_postgres_server.py
```