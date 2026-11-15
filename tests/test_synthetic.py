"""The synthetic factory must produce files that follow the documented layout byte for byte."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from paradoxdb.fields import FieldType
from paradoxdb.header import HeaderOffset
from paradoxdb.memo import MB_BLOCK_SIZE, MemoPointer
from paradoxdb.synthetic import (
    BlobRef,
    FieldSpec,
    MemoBuilder,
    TableSpec,
    build_header,
    build_px_template,
    build_table_bytes,
    encode_row,
    random_rows,
    write_synthetic_table,
)


def u16(b: bytes, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]


def u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def test_header_layout_with_extension(mixed_spec: TableSpec) -> None:
    hdr = build_header(mixed_spec, declared_count=5, block_count=2)
    assert u16(hdr, HeaderOffset.RECORD_SIZE) == mixed_spec.record_size
    assert u16(hdr, HeaderOffset.HEADER_SIZE) == len(hdr)
    assert len(hdr) % 0x800 == 0
    assert hdr[HeaderOffset.FILE_TYPE] == 0  # keyed table
    assert hdr[HeaderOffset.BLOCK_SIZE_KIB] == 2
    assert u32(hdr, HeaderOffset.RECORD_COUNT) == 5
    assert u16(hdr, HeaderOffset.NEXT_BLOCK) == 3
    assert u16(hdr, HeaderOffset.BLOCKS_IN_USE) == 2
    assert u16(hdr, HeaderOffset.FIRST_BLOCK) == 1
    assert u16(hdr, HeaderOffset.LAST_BLOCK) == 2
    assert u16(hdr, HeaderOffset.FIELD_COUNT) == 12
    assert u16(hdr, HeaderOffset.KEY_FIELD_COUNT) == 1
    assert u32(hdr, HeaderOffset.LEGACY_ENCRYPTION) == 0xFF00FF00
    assert hdr[HeaderOffset.VERSION] == 12
    assert u32(hdr, HeaderOffset.ENCRYPTION_KEY) == 0
    assert u16(hdr, HeaderOffset.CODE_PAGE) == 437
    # descriptors at 0x78: (type, size) pairs
    assert hdr[0x78:0x7A] == bytes((FieldType.LONG, 0))
    assert hdr[0x7A:0x7C] == bytes((FieldType.ALPHA, 20))
    bcd_index = [f.name for f in mixed_spec.fields].index("Amount")
    assert hdr[0x78 + 2 * bcd_index : 0x7A + 2 * bcd_index] == bytes((FieldType.BCD, 2))
    names_start = 0x78 + 2 * 12 + 4 + 4 * 12 + 261
    assert hdr[names_start:].startswith(b"Id\0Label\0When\0")


def test_header_layout_without_extension() -> None:
    spec = TableSpec(
        fields=(FieldSpec("A", FieldType.SHORT), FieldSpec("B", FieldType.ALPHA, 5)),
        version=4,
        code_page=None,
    )
    hdr = build_header(spec)
    assert hdr[HeaderOffset.FILE_TYPE] == 2
    assert u32(hdr, HeaderOffset.LEGACY_ENCRYPTION) == 0
    assert hdr[0x58:0x5C] == bytes((FieldType.SHORT, 0, FieldType.ALPHA, 5))
    names_start = 0x58 + 4 + 4 + 8 + 79
    assert hdr[names_start:].startswith(b"A\0B\0")


def test_field_names_use_the_table_code_page() -> None:
    spec = TableSpec(fields=(FieldSpec("Año", FieldType.SHORT),), code_page=437)
    hdr = build_header(spec)
    assert "Año".encode("cp437") + b"\0" in bytes(hdr)
    assert "Año".encode("cp1252") + b"\0" not in bytes(hdr)


def test_blocks_are_chained_and_sized(mixed_spec: TableSpec) -> None:
    rows = random_rows(mixed_spec, 150, rng=1)
    records = [encode_row(mixed_spec, r) for r in rows]
    data = build_table_bytes(mixed_spec, records)
    header_size = u16(data, HeaderOffset.HEADER_SIZE)
    block_size = mixed_spec.block_size
    capacity = (block_size - 6) // mixed_spec.record_size
    expected_blocks = -(-150 // capacity)
    assert expected_blocks > 1
    assert len(data) == header_size + expected_blocks * block_size
    assert u16(data, HeaderOffset.BLOCKS_IN_USE) == expected_blocks
    first = data[header_size : header_size + 6]
    nxt, prev, last = struct.unpack("<HHh", first)
    assert (nxt, prev, last) == (2, 0, (capacity - 1) * mixed_spec.record_size)
    tail = data[header_size + (expected_blocks - 1) * block_size :][:6]
    nxt, prev, last = struct.unpack("<HHh", tail)
    remaining = 150 - capacity * (expected_blocks - 1)
    assert (nxt, prev, last) == (0, expected_blocks - 1, (remaining - 1) * mixed_spec.record_size)


def test_empty_table_has_one_empty_block(mixed_spec: TableSpec) -> None:
    data = build_table_bytes(mixed_spec, [])
    header_size = u16(data, HeaderOffset.HEADER_SIZE)
    assert u16(data, HeaderOffset.BLOCKS_IN_USE) == 1
    _, _, last = struct.unpack("<HHh", data[header_size : header_size + 6])
    assert last == -mixed_spec.record_size


def test_declared_count_can_differ_from_reality(mixed_spec: TableSpec) -> None:
    records = [encode_row(mixed_spec, r) for r in random_rows(mixed_spec, 10, rng=2)]
    data = build_table_bytes(mixed_spec, records, declared_count=8)
    assert u32(data, HeaderOffset.RECORD_COUNT) == 8


def test_px_template_header(mixed_spec: TableSpec) -> None:
    px = build_px_template(mixed_spec, block_size_kib=1)
    assert px[HeaderOffset.FILE_TYPE] == 1
    assert u16(px, HeaderOffset.RECORD_SIZE) == 4 + 6
    assert px[HeaderOffset.BLOCK_SIZE_KIB] == 1
    assert px[HeaderOffset.PX_LEVELS] == 0
    assert u16(px, HeaderOffset.PX_ROOT_BLOCK) == 1
    assert u16(px, HeaderOffset.FIELD_COUNT) == 1
    assert px[0x58:0x5A] == bytes((FieldType.LONG, 0))  # no extension for type 1
    assert len(px) == u16(px, HeaderOffset.HEADER_SIZE) + 1024


def test_memo_builder_single_block() -> None:
    mb = MemoBuilder()
    content = b"x" * 5000
    ptr = mb.add_single(content)
    data = mb.to_bytes()
    assert ptr.index == 0xFF
    assert ptr.block_start == MB_BLOCK_SIZE
    assert ptr.length == 5000
    kind, blocks, length, counter = struct.unpack_from("<BHIH", data, MB_BLOCK_SIZE)
    assert (kind, blocks, length, counter) == (2, 2, 5000, 1)
    assert len(data) == 3 * MB_BLOCK_SIZE
    assert data[MB_BLOCK_SIZE + 9 : MB_BLOCK_SIZE + 9 + 5000] == content


def test_memo_builder_suballocated() -> None:
    mb = MemoBuilder()
    ptrs = mb.add_suballocated([b"abc", b"d" * 40])
    data = mb.to_bytes()
    assert [p.index for p in ptrs] == [0, 1]
    assert data[MB_BLOCK_SIZE] == 3
    off, units, _counter, tail = struct.unpack_from("<BBHB", data, MB_BLOCK_SIZE + 12)
    assert (off, units, tail) == (0x150 // 16, 1, 3)
    off2, units2, _, tail2 = struct.unpack_from("<BBHB", data, MB_BLOCK_SIZE + 12 + 5)
    assert (off2, units2, tail2) == (0x150 // 16 + 1, 3, 8)
    assert data[MB_BLOCK_SIZE + off2 * 16 : MB_BLOCK_SIZE + off2 * 16 + 40] == b"d" * 40


def test_encode_row_embeds_short_blobs_and_externalises_long_ones(memo_spec: TableSpec) -> None:
    mb = MemoBuilder()
    raw = encode_row(memo_spec, {"Id": 1, "Note": "short", "Payload": None}, mb)
    note = memo_spec.field("Note")
    leader = raw[note.offset : note.offset + note.leader_length]
    pointer = MemoPointer.unpack(
        raw[note.offset + note.leader_length : note.offset + note.byte_length]
    )
    assert leader.startswith(b"short")
    assert pointer == MemoPointer(0, 5, 1)
    assert mb.to_bytes() == bytes(MB_BLOCK_SIZE)  # nothing externalised yet
    raw = encode_row(memo_spec, {"Id": 2, "Note": "n" * 100}, mb)
    pointer = MemoPointer.unpack(
        raw[note.offset + note.leader_length : note.offset + note.byte_length]
    )
    assert pointer.index == 0xFF and pointer.length == 100
    assert len(mb.to_bytes()) == 2 * MB_BLOCK_SIZE


def test_encode_row_accepts_blob_refs(memo_spec: TableSpec) -> None:
    mb = MemoBuilder()
    (ptr,) = mb.add_suballocated([b"hello"])
    raw = encode_row(memo_spec, {"Id": 3, "Note": BlobRef(b"hello", ptr)}, mb)
    note = memo_spec.field("Note")
    assert MemoPointer.unpack(raw[note.offset + note.leader_length :][:10]) == ptr


def test_random_rows_respect_key_uniqueness(mixed_spec: TableSpec) -> None:
    rows = random_rows(mixed_spec, 200, rng=7)
    ids = [r["Id"] for r in rows]
    assert len(set(ids)) == 200 and None not in ids


def test_write_synthetic_table_creates_files(tmp_path: Path, memo_spec: TableSpec) -> None:
    files = write_synthetic_table(
        tmp_path, "notes", memo_spec, random_rows(memo_spec, 5, rng=3), with_px_template=True
    )
    assert files.db.exists() and files.mb is not None and files.mb.exists()
    assert files.px is not None and files.px.exists()
    assert files.db.name == "notes.DB"


def test_spec_validation() -> None:
    with pytest.raises(ValueError):
        TableSpec(fields=())
    with pytest.raises(ValueError):
        TableSpec(fields=(FieldSpec("A", FieldType.SHORT),), key_fields=2)
