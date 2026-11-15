from paradoxdb import open_table

table = open_table("demo_tables/ASSETS.DB")
print(f"{table.header.table_name}: Paradox {table.header.version_label}, code page {table.header.codec}")
print(f"fields: {[f.name for f in table.fields]}")
for record in list(table.records())[:2]:
    print(f"-- block {record.block_no}, position {record.position}")
    for name, cell in record.cells.items():
        print(f"   {name:<7} {cell.state.value:<8} {repr(cell.value)[:30]:<30} raw={cell.raw_bytes[:6].hex()}")
