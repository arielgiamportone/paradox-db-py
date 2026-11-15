"""Create a synthetic table: 600 rows, header deliberately declaring 598, one broken BCD."""

import datetime as dt
from decimal import Decimal

from paradoxdb.fields import FieldType
from paradoxdb.synthetic import FieldSpec, TableSpec, encode_row, random_rows, write_synthetic_table

spec = TableSpec(
    fields=(
        FieldSpec("Id", FieldType.LONG),
        FieldSpec("Name", FieldType.ALPHA, 16),
        FieldSpec("Qty", FieldType.SHORT),
        FieldSpec("Day", FieldType.DATE),
        FieldSpec("Amount", FieldType.BCD, 2),
        FieldSpec("Note", FieldType.MEMO, 24),
    ),
    key_fields=1,
    code_page=437,
    block_size_kib=1,
)
rows = random_rows(spec, 600, rng=42, null_ratio=0.05)
rows.sort(key=lambda r: r["Id"])
rows[0].update(Qty=0, Day=None, Amount=Decimal("12.50"), Note="primer registro")
rows[1].update(Qty=7, Day=dt.date(1999, 12, 31), Amount=Decimal("-0.75"))

files = write_synthetic_table("demo_tables", "ASSETS", spec, rows, declared_count=598, with_px_template=True)

# Corrupt the BCD of the third record: nibble 0xF is not a decimal digit.
raw = bytearray(files.db.read_bytes())
header_size = int.from_bytes(raw[2:4], "little")
amount = spec.field("Amount")
third = header_size + 6 + 2 * spec.record_size + amount.offset
raw[third] = 0xC2
raw[third + 16] = 0xFF
files.db.write_bytes(bytes(raw))
print(f"wrote {files.db} (600 records in blocks, header declares 598)")
print(f"wrote {files.mb} and {files.px} (empty index template)")
