To manually delete the data in redis, run:

```bash
docker exec -it redis redis-cli
DEL tables_metadata_cache
```