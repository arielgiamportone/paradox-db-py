"""Memo and BLOB resolution: embedded, single-block, sub-allocated, missing file, bad pointers."""

from __future__ import annotations

from pathlib import Path

from paradoxdb import ValueState, open_table
from paradoxdb.fields import FieldType
from paradoxdb.memo import MemoPointer
from paradoxdb.synthetic import (
    BlobRef,
    FieldSpec,
    MemoBuilder,
    TableSpec,
    build_table_bytes,
    encode_row,
)


def _notes_spec(cp: int = 437) -> TableSpec:
    return TableSpec(
        fields=(FieldSpec("Id", FieldType.SHORT), FieldSpec("Note", FieldType.MEMO, 24)),
        code_page=cp,
    )


def test_three_memo_storage_forms_and_mixed_case_file_name(tmp_path: Path) -> None:
    spec = _notes_spec()
    mb = MemoBuilder()
    embedded = "corto ñ"
    single = "L" * 5000 + "áéí"
    sub_a, sub_b = "sub-a ºª", "sub-b " * 20
    ptr_a, ptr_b = mb.add_suballocated([sub_a.encode("cp437"), sub_b.encode("cp437")])
    rows = [
        encode_row(spec, {"Id": 1, "Note": embedded}, mb),
        encode_row(spec, {"Id": 2, "Note": single}, mb),
        encode_row(spec, {"Id": 3, "Note": BlobRef(sub_a.encode("cp437"), ptr_a)}, mb),
        encode_row(spec, {"Id": 4, "Note": BlobRef(sub_b.encode("cp437"), ptr_b)}, mb),
        encode_row(spec, {"Id": 5, "Note": None}, mb),
    ]
    (tmp_path / "notes.DB").write_bytes(build_table_bytes(spec, rows))
    (tmp_path / "NoTeS.Mb").write_bytes(mb.to_bytes())
    table = open_table(tmp_path / "notes.DB")
    assert table.memo is not None
    cells = [r["Note"] for r in table.records()]
    assert [c.state for c in cells] == [ValueState.OK] * 4 + [ValueState.NULL]
    assert [c.value for c in cells[:4]] == [embedded, single, sub_a, sub_b]


def test_missing_memo_file_marks_cells_invalid(tmp_path: Path) -> None:
    spec = _notes_spec()
    mb = MemoBuilder()
    rows = [encode_row(spec, {"Id": 1, "Note": "x" * 200}, mb)]
    (tmp_path / "lost.DB").write_bytes(build_table_bytes(spec, rows))
    (record,) = open_table(tmp_path / "lost.DB").records()
    cell = record["Note"]
    assert cell.state is ValueState.INVALID
    assert ".MB" in (cell.reason or "")
    assert cell.raw_bytes == rows[0][2:]


def test_embedded_memo_does_not_need_the_file(tmp_path: Path) -> None:
    spec = _notes_spec()
    rows = [encode_row(spec, {"Id": 1, "Note": "fits"})]
    (tmp_path / "emb.DB").write_bytes(build_table_bytes(spec, rows))
    (record,) = open_table(tmp_path / "emb.DB").records()
    assert record["Note"].value == "fits"


def test_incoherent_pointer_is_invalid_with_reason(tmp_path: Path) -> None:
    spec = _notes_spec()
    mb = MemoBuilder()
    ptr = mb.add_single(b"y" * 100)
    bad = MemoPointer(ptr.offset, 99, ptr.modcount)  # wrong length
    rows = [encode_row(spec, {"Id": 1, "Note": BlobRef(b"y" * 100, bad)})]
    out_of_range = MemoPointer(0x10000 | 0xFF, 5, 1)
    rows.append(encode_row(spec, {"Id": 2, "Note": BlobRef(b"zzzzz", out_of_range)}))
    (tmp_path / "bad.DB").write_bytes(build_table_bytes(spec, rows))
    (tmp_path / "bad.MB").write_bytes(mb.to_bytes())
    first, second = open_table(tmp_path / "bad.DB").records()
    assert first["Note"].state is ValueState.INVALID and "mismatch" in first["Note"].reason
    assert second["Note"].state is ValueState.INVALID and "outside" in second["Note"].reason


def test_memo_text_uses_table_code_page_and_bad_bytes_are_invalid(tmp_path: Path) -> None:
    spec = _notes_spec(1252)
    mb = MemoBuilder()
    good = "Ñandú".encode("cp1252") * 30
    bad = b"\x81" * 50  # undefined in cp1252
    rows = [
        encode_row(spec, {"Id": 1, "Note": BlobRef(good, mb.add_single(good))}),
        encode_row(spec, {"Id": 2, "Note": BlobRef(bad, mb.add_single(bad))}),
    ]
    (tmp_path / "cp.DB").write_bytes(build_table_bytes(spec, rows))
    (tmp_path / "cp.MB").write_bytes(mb.to_bytes())
    first, second = open_table(tmp_path / "cp.DB").records()
    assert first["Note"].value == "Ñandú" * 30
    assert second["Note"].state is ValueState.INVALID
    assert second["Note"].value is not None and "�" in second["Note"].value


def test_binary_blob_round_trip(tmp_path: Path) -> None:
    spec = TableSpec(
        fields=(FieldSpec("Id", FieldType.SHORT), FieldSpec("Bin", FieldType.BLOB, 10))
    )
    mb = MemoBuilder()
    payload = bytes(range(256)) * 20
    rows = [encode_row(spec, {"Id": 1, "Bin": payload}, mb)]
    (tmp_path / "bin.DB").write_bytes(build_table_bytes(spec, rows))
    (tmp_path / "bin.MB").write_bytes(mb.to_bytes())
    (record,) = open_table(tmp_path / "bin.DB").records()
    assert record["Bin"].value == payload
