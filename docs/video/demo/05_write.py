from paradoxdb import WriteRecord, open_table, write_table

source = open_table("demo_tables/ASSETS.DB")
records = [
    WriteRecord(r.raw_bytes, {5: None if r["Note"].is_null else r["Note"].value.encode(source.codec)})
    for r in source.records()
]
result = write_table("demo_tables/ASSETS.DB", "out/ASSETS.DB", records)
print(f"{result.record_count} records in {result.block_count} blocks")
print(f"db  {result.db_path}\nmb  {result.mb_path}\npx  {result.px_path}")
print(f"header now declares {open_table(result.db_path).declared_record_count}")
