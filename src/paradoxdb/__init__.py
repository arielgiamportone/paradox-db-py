"""paradoxdb: pure-Python reader and writer for Paradox tables (.DB / .MB / .PX).

Quick start::

    from paradoxdb import open_table

    table = open_table("ORDERS.DB")
    for record in table.records():
        cell = record["Amount"]
        if cell.is_ok:
            print(cell.value)
        else:
            print(cell.state, cell.reason, cell.raw_bytes.hex())
"""

from __future__ import annotations

from .blocks import Anomaly
from .errors import (
    DuplicateKeyError,
    EncodingError,
    EncryptedTableError,
    FormatError,
    ParadoxError,
    WriteError,
)
from .fields import FieldDef, FieldType
from .header import TableHeader, parse_header
from .index import DataBlock, build_index, read_data_blocks, verify_index
from .memo import MemoFile, MemoPointer
from .profile import TableProfile, export_csv, export_json, profile_table
from .table import Decryptor, ParadoxTable, Record, open_table
from .values import (
    CellValue,
    ValueState,
    decode_value,
    encode_alpha,
    encode_bcd,
    encode_bool,
    encode_date,
    encode_double,
    encode_int,
    encode_null,
    encode_time,
    encode_timestamp,
)
from .writer import WriteRecord, WriteResult, legacy_sort_key, write_table

__version__ = "0.1.0"

__all__ = [
    "Anomaly",
    "CellValue",
    "DataBlock",
    "Decryptor",
    "DuplicateKeyError",
    "EncodingError",
    "EncryptedTableError",
    "FieldDef",
    "FieldType",
    "FormatError",
    "MemoFile",
    "MemoPointer",
    "ParadoxError",
    "ParadoxTable",
    "Record",
    "TableHeader",
    "TableProfile",
    "ValueState",
    "WriteError",
    "WriteRecord",
    "WriteResult",
    "__version__",
    "build_index",
    "decode_value",
    "encode_alpha",
    "encode_bcd",
    "encode_bool",
    "encode_date",
    "encode_double",
    "encode_int",
    "encode_null",
    "encode_time",
    "encode_timestamp",
    "export_csv",
    "export_json",
    "legacy_sort_key",
    "open_table",
    "parse_header",
    "profile_table",
    "read_data_blocks",
    "verify_index",
    "write_table",
]
