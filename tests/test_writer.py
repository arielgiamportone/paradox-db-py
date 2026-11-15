"""Writer: round-trips on a template header, legacy order, keys, autoincrement, empty tables."""

from __future__ import annotations

import struct
from decimal import Decimal
from pathlib import Path

import pytest

from paradoxdb import (
    DuplicateKeyError,
    EncryptedTableError,
    ValueState,
    WriteError,
    WriteRecord,
    open_table,
    read_data_blocks,
    verify_index,
    write_table,
)
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
from paradoxdb.writer import legacy_sort_key, legacy_text_key


def _keyed_memo_spec() -> TableSpec:
    return TableSpec(
        fields=(
            FieldSpec("Code", FieldType.LONG),
            FieldSpec("Name", FieldType.ALPHA, 12),
            FieldSpec("Amount", FieldType.BCD, 2),
            FieldSpec("Note", FieldType.MEMO, 20),
        ),
        key_fields=2,
        block_size_kib=1,
    )


def _to_write_records(table) -> list[WriteRecord]:
    blob_indexes = [i for i, f in enumerate(table.fields) if f.type.is_blob]
    out = []
    for record in table.records():
        blobs = {}
        for i in blob_indexes:
            cell = record.cells[table.fields[i].name]
            if cell.is_null:
                blobs[i] = None
            elif table.fields[i].type.is_text_blob:
                blobs[i] = cell.value.encode(table.codec)
            else:
                blobs[i] = cell.value
        out.append(WriteRecord(record.raw_bytes, blobs))
    return out


def test_round_trip_with_memos_and_index(tmp_path: Path) -> None:
    spec = _keyed_memo_spec()
    rows = random_rows(spec, 40, rng=11, null_ratio=0.0)
    rows[3]["Note"] = "M" * 5000
    rows[5]["Note"] = None
    rows.sort(key=lambda r: (r["Code"], r["Name"]))
    files = write_synthetic_table(tmp_path / "src", "orig", spec, rows, with_px_template=True)
    original = open_table(files.db)
    records = _to_write_records(original)

    result = write_table(files.db, tmp_path / "out" / "copy.DB", records)
    assert result.record_count == 40 and result.px_path is not None and result.mb_path is not None

    copy = open_table(result.db_path)
    assert copy.header == original.header
    orig_values = [r.values() for r in original.records()]
    copy_values = [r.values() for r in copy.records()]
    assert copy_values == orig_values
    null_index = next(i for i, r in enumerate(rows) if r["Note"] is None)
    assert [r["Note"].state for r in copy.records()][null_index] is ValueState.NULL
    assert [r["Note"].value for r in copy.records()][3] == rows[3]["Note"]
    assert copy.header_mismatch == 0
    assert verify_index(result.px_path, read_data_blocks(result.db_path)) == []

    # second pass: writing the copy again gives identical bytes (stable output)
    again = write_table(result.db_path, tmp_path / "out2" / "copy2.DB", _to_write_records(copy))
    assert again.db_path.read_bytes() == result.db_path.read_bytes()
    assert again.mb_path.read_bytes() == result.mb_path.read_bytes()
    assert again.px_path.read_bytes() == result.px_path.read_bytes()


def test_header_is_copied_except_counters(tmp_path: Path, mixed_spec: TableSpec) -> None:
    rows = random_rows(mixed_spec, 10, rng=1)
    files = write_synthetic_table(tmp_path, "t", mixed_spec, rows, autoincrement=5)
    original = files.db.read_bytes()
    header_size = struct.unpack_from("<H", original, HeaderOffset.HEADER_SIZE)[0]
    result = write_table(
        files.db, tmp_path / "copy.DB", [r.raw_bytes for r in open_table(files.db)]
    )
    copy = result.db_path.read_bytes()
    changed = {i for i in range(header_size) if original[i] != copy[i]}
    autoinc = set(range(0x49, 0x4D))  # rewritten: max of template and written values
    assert changed <= autoinc, sorted(changed)
    assert open_table(result.db_path).header.autoincrement == max(
        5, *(r["Seq"].value for r in open_table(files.db) if r["Seq"].is_ok)
    )
    # drop half of the records: only counters may change
    result = write_table(
        files.db, tmp_path / "half.DB", [r.raw_bytes for r in open_table(files.db)][:5]
    )
    half = result.db_path.read_bytes()
    changed = {i for i in range(header_size) if original[i] != half[i]}
    allowed = set(range(0x06, 0x0A)) | autoinc  # declared record count; blocks stay at 1
    assert changed <= allowed, sorted(changed)


def test_multi_level_index(tmp_path: Path) -> None:
    spec = TableSpec(
        fields=(FieldSpec("K", FieldType.LONG), FieldSpec("T", FieldType.ALPHA, 100)),
        key_fields=2,
        block_size_kib=1,
    )
    rows = [{"K": i, "T": f"r{i}"} for i in range(3000)]
    files = write_synthetic_table(
        tmp_path, "big", spec, rows, with_px_template=True, px_block_size_kib=1
    )
    result = write_table(
        files.db, tmp_path / "out" / "big.DB", [r.raw_bytes for r in open_table(files.db)]
    )
    px = result.px_path.read_bytes()
    assert px[HeaderOffset.PX_LEVELS] >= 2
    assert verify_index(px, read_data_blocks(result.db_path)) == []
    assert [r["K"].value for r in open_table(result.db_path)] == list(range(3000))


def test_legacy_text_key_rules() -> None:
    assert legacy_text_key("ñu") == "N￿U"
    assert legacy_text_key("Ártico") == "ARTICO"
    assert sorted(["ob", "Ñu", "NA", "Oa", "nz"], key=legacy_text_key) == [
        "NA",
        "nz",
        "Ñu",
        "Oa",
        "ob",
    ]


def test_legacy_sort_order_on_write(tmp_path: Path) -> None:
    spec = TableSpec(fields=(FieldSpec("T", FieldType.ALPHA, 4),), key_fields=1)
    rows = [{"T": t} for t in ["ob", "Ñu", "NA", "Oa", "nz"]]
    files = write_synthetic_table(tmp_path, "s", spec, rows)
    result = write_table(
        files.db, tmp_path / "sorted.DB", [r.raw_bytes for r in open_table(files.db)], sort=True
    )
    assert [r["T"].value for r in open_table(result.db_path)] == ["NA", "nz", "Ñu", "Oa", "ob"]
    unsorted = write_table(
        files.db, tmp_path / "plain.DB", [r.raw_bytes for r in open_table(files.db)]
    )
    assert [r["T"].value for r in open_table(unsorted.db_path)] == ["ob", "Ñu", "NA", "Oa", "ob"][
        :0
    ] or True
    assert [r["T"].value for r in open_table(unsorted.db_path)] == ["ob", "Ñu", "NA", "Oa", "nz"]


def test_numeric_legacy_sort_uses_raw_bytes(tmp_path: Path) -> None:
    spec = TableSpec(
        fields=(FieldSpec("N", FieldType.SHORT), FieldSpec("B", FieldType.BCD, 1)), key_fields=2
    )
    files = write_synthetic_table(
        tmp_path,
        "n",
        spec,
        [
            {"N": 5, "B": Decimal("1.0")},
            {"N": -5, "B": Decimal("-1.0")},
            {"N": 0, "B": Decimal("0.0")},
        ],
    )
    table = open_table(files.db)
    keys = [legacy_sort_key(table.header, r.raw_bytes) for r in table]
    assert sorted(keys) == [keys[1], keys[2], keys[0]]


def test_duplicate_keys_are_rejected(tmp_path: Path) -> None:
    spec = TableSpec(
        fields=(FieldSpec("K", FieldType.LONG), FieldSpec("V", FieldType.SHORT)), key_fields=1
    )
    files = write_synthetic_table(tmp_path, "d", spec, [{"K": 1, "V": 1}, {"K": 2, "V": 2}])
    raws = [r.raw_bytes for r in open_table(files.db)]
    raws.append(encode_row(spec, {"K": 1, "V": 99}))
    with pytest.raises(DuplicateKeyError):
        write_table(files.db, tmp_path / "dup.DB", raws)


def test_empty_table_keeps_structure_and_verifies(tmp_path: Path) -> None:
    spec = _keyed_memo_spec()
    files = write_synthetic_table(tmp_path, "empty", spec, [], with_px_template=True)
    result = write_table(files.db, tmp_path / "out" / "empty.DB", [])
    assert result.record_count == 0 and result.block_count == 1
    copy = open_table(result.db_path)
    assert copy.header.key_field_count == 2 and copy.header.code_page == 437
    assert copy.header.fields == open_table(files.db).header.fields
    assert list(copy.records()) == []
    px = result.px_path.read_bytes()
    assert px[HeaderOffset.PX_LEVELS] == 0
    assert verify_index(px, read_data_blocks(result.db_path)) == []
    assert result.mb_path is not None and result.mb_path.stat().st_size == 4096


def test_autoincrement_is_the_maximum(tmp_path: Path) -> None:
    spec = TableSpec(
        fields=(FieldSpec("A", FieldType.AUTOINCREMENT), FieldSpec("V", FieldType.SHORT))
    )
    files = write_synthetic_table(
        tmp_path, "a", spec, [{"A": 7, "V": 1}, {"A": 42, "V": 2}, {"A": 3, "V": 3}]
    )
    result = write_table(
        files.db, tmp_path / "auto.DB", [r.raw_bytes for r in open_table(files.db)]
    )
    assert open_table(result.db_path).header.autoincrement == 42
    # template value wins when it is larger
    data = bytearray(files.db.read_bytes())
    struct.pack_into("<I", data, HeaderOffset.AUTOINCREMENT, 100)
    (tmp_path / "tpl.DB").write_bytes(bytes(data))
    result = write_table(
        tmp_path / "tpl.DB", tmp_path / "auto2.DB", [r.raw_bytes for r in open_table(files.db)]
    )
    assert open_table(result.db_path).header.autoincrement == 100


def test_secondary_indexes_are_reported_not_regenerated(tmp_path: Path) -> None:
    spec = TableSpec(fields=(FieldSpec("K", FieldType.LONG),), key_fields=1)
    files = write_synthetic_table(tmp_path, "idx", spec, [{"K": 1}], with_px_template=True)
    (tmp_path / "idx.XG0").write_bytes(b"\0" * 16)
    (tmp_path / "IDX.Y01").write_bytes(b"\0" * 16)
    result = write_table(
        files.db, tmp_path / "out" / "idx.DB", [r.raw_bytes for r in open_table(files.db)]
    )
    assert sorted(p.name for p in result.skipped_indexes) == ["IDX.Y01", "idx.XG0"]
    assert not list((tmp_path / "out").glob("*.X*"))
    assert sorted(p.name for p in open_table(files.db).secondary_indexes) == ["IDX.Y01", "idx.XG0"]


def test_encrypted_template_is_refused(tmp_path: Path) -> None:
    spec = TableSpec(fields=(FieldSpec("K", FieldType.LONG),), encryption_key=0x55)
    (tmp_path / "enc.DB").write_bytes(build_table_bytes(spec, []))
    with pytest.raises(EncryptedTableError):
        write_table(tmp_path / "enc.DB", tmp_path / "out.DB", [])


def test_wrong_record_size_is_refused(tmp_path: Path) -> None:
    spec = TableSpec(fields=(FieldSpec("K", FieldType.LONG),))
    (tmp_path / "t.DB").write_bytes(build_table_bytes(spec, []))
    with pytest.raises(WriteError):
        write_table(tmp_path / "t.DB", tmp_path / "out.DB", [b"\x00" * 5])
    with pytest.raises(WriteError):
        write_table(tmp_path / "t.DB", tmp_path / "t.DB", [])


def test_null_bcd_survives_the_round_trip(tmp_path: Path) -> None:
    spec = TableSpec(fields=(FieldSpec("B", FieldType.BCD, 3), FieldSpec("N", FieldType.SHORT)))
    files = write_synthetic_table(
        tmp_path, "n", spec, [{"B": None, "N": 1}, {"B": Decimal("0.000"), "N": 2}]
    )
    result = write_table(
        files.db, tmp_path / "copy.DB", [r.raw_bytes for r in open_table(files.db)]
    )
    first, second = open_table(result.db_path).records()
    assert first["B"].state is ValueState.NULL
    assert second["B"].state is ValueState.OK and second["B"].value == Decimal("0.000")


def test_memo_file_header_block_is_copied_from_template(tmp_path: Path) -> None:
    spec = _keyed_memo_spec()
    files = write_synthetic_table(tmp_path, "m", spec, random_rows(spec, 3, rng=2, null_ratio=0))
    marker = b"HDR" + bytes(4093)
    files.mb.write_bytes(marker + files.mb.read_bytes()[4096:])
    result = write_table(
        files.db, tmp_path / "out" / "m.DB", _to_write_records(open_table(files.db))
    )
    assert result.mb_path.read_bytes()[:4096] == marker
    copy = open_table(result.db_path)
    assert [r["Note"].value for r in copy] == [r["Note"].value for r in open_table(files.db)]
