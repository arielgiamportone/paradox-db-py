"""Reading records: every block, explicit state per cell, code pages, decryptor protocol."""

from __future__ import annotations

import datetime as dt
import struct
from decimal import Decimal
from pathlib import Path

import pytest

from paradoxdb import ValueState, open_table
from paradoxdb.fields import FieldType
from paradoxdb.header import HeaderOffset
from paradoxdb.synthetic import (
    FieldSpec,
    TableSpec,
    build_table_bytes,
    encode_row,
    random_rows,
    write_synthetic_table,
)


def test_text_identical_across_code_pages(tmp_path: Path) -> None:
    text = "Ñandú nº 1 ºª áéíóú"
    values = {}
    for cp in (437, 1252):
        spec = TableSpec(fields=(FieldSpec("T", FieldType.ALPHA, 30),), code_page=cp)
        files = write_synthetic_table(tmp_path / str(cp), "t", spec, [{"T": text}])
        table = open_table(files.db)
        assert table.header.code_page == cp
        (record,) = table.records()
        values[cp] = record["T"].value
    assert values[437] == values[1252] == text


def test_signs_and_types_decode_exactly(tmp_path: Path, mixed_spec: TableSpec) -> None:
    row = {
        "Id": -7,
        "Label": "Ñu",
        "When": dt.date(1999, 12, 31),
        "Qty": -3,
        "Price": Decimal("-12.5"),
        "Ratio": Decimal("0.25"),
        "Flag": True,
        "Clock": dt.time(23, 59, 59, 999000),
        "Stamp": dt.datetime(2001, 2, 3, 4, 5, 6, 7000),
        "Seq": 42,
        "Amount": Decimal("-99.99"),
        "Raw": b"\x01\x02\x03\x04",
    }
    files = write_synthetic_table(
        tmp_path, "mixed", mixed_spec, [row, {**row, "Id": 1, "Flag": False}]
    )
    table = open_table(files.db)
    first, second = table.records()
    for name, expected in row.items():
        cell = first[name]
        assert cell.state is ValueState.OK, (name, cell)
        assert cell.value == expected, name
        assert type(cell.value) is type(expected), name
    assert second["Flag"].value is False
    assert isinstance(first["Price"].value, Decimal)
    assert isinstance(first["When"].value, dt.date)


def test_null_is_not_zero(tmp_path: Path) -> None:
    spec = TableSpec(
        fields=(
            FieldSpec("S", FieldType.SHORT),
            FieldSpec("D", FieldType.DATE),
            FieldSpec("A", FieldType.ALPHA, 5),
        )
    )
    raw = encode_row(spec, {"S": 0, "D": None, "A": None})
    assert raw[:2] == b"\x80\x00"
    (tmp_path / "z.DB").write_bytes(build_table_bytes(spec, [raw]))
    (record,) = open_table(tmp_path / "z.DB").records()
    assert record["S"].state is ValueState.OK and record["S"].value == 0
    assert record["D"].state is ValueState.NULL and record["D"].value is None
    assert record["A"].state is ValueState.NULL
    assert record["D"].raw_bytes == bytes(4)


def test_unreadable_bcd_is_invalid_and_keeps_raw_bytes(tmp_path: Path) -> None:
    spec = TableSpec(fields=(FieldSpec("B", FieldType.BCD, 2),))
    raw = bytearray(17)
    raw[0] = 0xC2
    raw[16] = 0x0F
    (tmp_path / "b.DB").write_bytes(build_table_bytes(spec, [bytes(raw)]))
    (record,) = open_table(tmp_path / "b.DB").records()
    cell = record["B"]
    assert cell.state is ValueState.INVALID
    assert cell.value is None
    assert cell.raw_bytes == bytes(raw)
    assert "nibble" in (cell.reason or "")


def test_invalid_logical_byte(tmp_path: Path) -> None:
    spec = TableSpec(fields=(FieldSpec("L", FieldType.LOGICAL),))
    (tmp_path / "l.DB").write_bytes(build_table_bytes(spec, [b"\x05"]))
    (record,) = open_table(tmp_path / "l.DB").records()
    assert record["L"].state is ValueState.INVALID


def test_every_block_is_read_even_if_header_declares_fewer(tmp_path: Path) -> None:
    spec = TableSpec(fields=(FieldSpec("N", FieldType.LONG),), block_size_kib=1)
    rows = [{"N": i} for i in range(600)]
    files = write_synthetic_table(tmp_path, "many", spec, rows, declared_count=598)
    table = open_table(files.db)
    values = [r["N"].value for r in table.records()]
    assert values == list(range(600))
    assert table.block_count > 1
    assert table.block_record_count() == 600
    assert table.declared_record_count == 598
    assert table.header_mismatch == 2
    assert len(table) == 600
    assert table.anomalies == []


def test_block_chain_cycle_is_an_anomaly_not_a_crash(tmp_path: Path) -> None:
    spec = TableSpec(fields=(FieldSpec("N", FieldType.LONG),), block_size_kib=1)
    data = bytearray(build_table_bytes(spec, [encode_row(spec, {"N": i}) for i in range(400)]))
    header_size = struct.unpack_from("<H", data, HeaderOffset.HEADER_SIZE)[0]
    # make block 2 point back to block 1
    struct.pack_into("<H", data, header_size + 1024, 1)
    (tmp_path / "loop.DB").write_bytes(bytes(data))
    table = open_table(tmp_path / "loop.DB")
    records = list(table.records())
    assert len(records) == table.block_record_count()
    assert any("loops" in a.message for a in table.anomalies)


def test_block_declaring_too_many_records_counts_zero(tmp_path: Path) -> None:
    spec = TableSpec(fields=(FieldSpec("N", FieldType.LONG),), block_size_kib=1)
    data = bytearray(build_table_bytes(spec, [encode_row(spec, {"N": 1})]))
    header_size = struct.unpack_from("<H", data, HeaderOffset.HEADER_SIZE)[0]
    struct.pack_into("<h", data, header_size + 4, 4 * 300)  # 301 records > capacity 254
    (tmp_path / "big.DB").write_bytes(bytes(data))
    table = open_table(tmp_path / "big.DB")
    assert list(table.records()) == []
    assert table.anomalies and "can hold" in table.anomalies[0].message


def test_record_accessors(tmp_path: Path, mixed_spec: TableSpec) -> None:
    files = write_synthetic_table(tmp_path, "m", mixed_spec, random_rows(mixed_spec, 3, rng=5))
    table = open_table(files.db)
    record = next(iter(table))
    assert record.block_no == 1 and record.position == 0
    assert record.get("label") is record.cells["Label"]
    with pytest.raises(KeyError):
        record.get("nope")
    plain = record.values()
    assert set(plain) == {f.name for f in mixed_spec.fields}


class XorDecryptor:
    """Fake decryptor for the tests: the data blocks were XOR-ed with a byte."""

    def __init__(self, key: int, record_size: int, header_size: int, block_size: int) -> None:
        self.key = key
        self.record_size = record_size
        self.header_size = header_size
        self.block_size = block_size

    def decrypt(self, path: Path, blob_indexes: list[int]) -> tuple[list[bytes], list[dict]]:
        data = path.read_bytes()
        records: list[bytes] = []
        pos = self.header_size
        while pos + self.block_size <= len(data):
            block = bytes(b ^ self.key for b in data[pos : pos + self.block_size])
            _, _, last = struct.unpack_from("<HHh", block, 0)
            count = 0 if last < 0 else last // self.record_size + 1
            for i in range(count):
                start = 6 + i * self.record_size
                records.append(block[start : start + self.record_size])
            pos += self.block_size
        return records, [{} for _ in records]


def test_encrypted_table_needs_a_decryptor(tmp_path: Path) -> None:
    from paradoxdb.errors import EncryptedTableError

    spec = TableSpec(fields=(FieldSpec("T", FieldType.ALPHA, 10),), encryption_key=0xDEADBEEF)
    plain = build_table_bytes(spec, [encode_row(spec, {"T": "secreto"})])
    header_size = struct.unpack_from("<H", plain, HeaderOffset.HEADER_SIZE)[0]
    scrambled = plain[:header_size] + bytes(b ^ 0x5A for b in plain[header_size:])
    (tmp_path / "enc.DB").write_bytes(scrambled)
    with pytest.raises(EncryptedTableError):
        open_table(tmp_path / "enc.DB")
    table = open_table(
        tmp_path / "enc.DB",
        decryptor=XorDecryptor(0x5A, spec.record_size, header_size, spec.block_size),
    )
    (record,) = table.records()
    assert record["T"].value == "secreto"
    assert record.block_no == 0 and record.position == 0
    assert table.block_record_count() == 1


def test_decryptor_blob_content_is_used(tmp_path: Path, memo_spec: TableSpec) -> None:
    spec = TableSpec(fields=memo_spec.fields, encryption_key=1)
    plain = build_table_bytes(spec, [encode_row(spec, {"Id": 1, "Note": "x"})])
    (tmp_path / "e.DB").write_bytes(plain)

    class PassThrough:
        def decrypt(self, path: Path, blob_indexes: list[int]):
            assert blob_indexes == [1, 2]
            table_bytes = path.read_bytes()
            header_size = struct.unpack_from("<H", table_bytes, HeaderOffset.HEADER_SIZE)[0]
            rec = table_bytes[header_size + 6 : header_size + 6 + spec.record_size]
            return [rec], [{1: "memo from decryptor".encode("cp1252"), 2: None}]

    (record,) = open_table(tmp_path / "e.DB", decryptor=PassThrough()).records()
    assert record["Note"].value == "memo from decryptor"
    assert record["Payload"].state is ValueState.NULL
