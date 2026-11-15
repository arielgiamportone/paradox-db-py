"""Primary index builder and verifier."""

from __future__ import annotations

import struct
from pathlib import Path

from paradoxdb import DataBlock, build_index, open_table, read_data_blocks, verify_index
from paradoxdb.fields import FieldType
from paradoxdb.header import HeaderOffset
from paradoxdb.synthetic import FieldSpec, TableSpec, build_px_template, write_synthetic_table


def _spec(block_kib: int = 1) -> TableSpec:
    return TableSpec(
        fields=(FieldSpec("K", FieldType.LONG), FieldSpec("V", FieldType.ALPHA, 30)),
        key_fields=1,
        block_size_kib=block_kib,
    )


def test_read_data_blocks_lists_non_empty_blocks(tmp_path: Path) -> None:
    spec = _spec()
    files = write_synthetic_table(tmp_path, "t", spec, [{"K": i, "V": "v"} for i in range(100)])
    blocks = read_data_blocks(files.db)
    table = open_table(files.db)
    assert len(blocks) == table.block_count > 1
    assert blocks[0] == DataBlock(1, struct.pack(">I", 0x80000000), table.header.block_capacity)
    assert sum(b.count for b in blocks) == 100
    assert read_data_blocks(files.db.read_bytes(), 4) == blocks


def test_build_index_single_level(tmp_path: Path) -> None:
    spec = _spec()
    template = build_px_template(spec)
    blocks = [
        DataBlock(1, struct.pack(">I", 0x80000000), 10),
        DataBlock(2, struct.pack(">I", 0x8000000A), 7),
    ]
    px = build_index(template, blocks)
    assert px[HeaderOffset.PX_LEVELS] == 1
    assert struct.unpack_from("<H", px, HeaderOffset.PX_ROOT_BLOCK)[0] == 1
    assert struct.unpack_from("<I", px, HeaderOffset.RECORD_COUNT)[0] == 2
    header_size = struct.unpack_from("<H", px, HeaderOffset.HEADER_SIZE)[0]
    first_entry = px[header_size + 6 : header_size + 6 + 10]
    assert first_entry == struct.pack(">I", 0x80000000) + b"\x80\x01" + b"\x80\x0a" + b"\x80\x00"
    assert verify_index(px, blocks) == []


def test_build_index_multi_level_numbers_root_last(tmp_path: Path) -> None:
    spec = _spec()
    template = build_px_template(spec, block_size_kib=1)
    capacity = (1024 - 6) // 10
    blocks = [
        DataBlock(i + 1, struct.pack(">I", 0x80000000 + i * 5), 5) for i in range(capacity * 3 + 1)
    ]
    px = build_index(template, blocks)
    assert px[HeaderOffset.PX_LEVELS] == 2
    block_count = struct.unpack_from("<H", px, HeaderOffset.BLOCKS_IN_USE)[0]
    assert block_count == 5  # 4 leaves + root
    assert struct.unpack_from("<H", px, HeaderOffset.PX_ROOT_BLOCK)[0] == 5
    assert struct.unpack_from("<I", px, HeaderOffset.RECORD_COUNT)[0] == len(blocks) + 4
    assert verify_index(px, blocks) == []


def test_empty_index(tmp_path: Path) -> None:
    px = build_index(build_px_template(_spec()), [])
    assert px[HeaderOffset.PX_LEVELS] == 0
    assert verify_index(px, []) == []
    assert any("0 levels" in p for p in verify_index(px, [DataBlock(1, b"\x80\0\0\0", 1)]))


def test_verifier_detects_wrong_count(tmp_path: Path) -> None:
    spec = _spec()
    template = build_px_template(spec)
    blocks = [
        DataBlock(1, struct.pack(">I", 0x80000000), 10),
        DataBlock(2, struct.pack(">I", 0x8000000A), 7),
    ]
    px = bytearray(build_index(template, blocks))
    header_size = struct.unpack_from("<H", px, HeaderOffset.HEADER_SIZE)[0]
    count_pos = header_size + 6 + 4 + 2  # first leaf entry, second short
    px[count_pos : count_pos + 2] = b"\x80\x0b"
    problems = verify_index(bytes(px), blocks)
    assert problems and any("count" in p for p in problems)


def test_verifier_detects_wrong_key_zero_tail_and_coverage(tmp_path: Path) -> None:
    spec = _spec()
    template = build_px_template(spec)
    blocks = [
        DataBlock(1, struct.pack(">I", 0x80000000), 10),
        DataBlock(2, struct.pack(">I", 0x8000000A), 7),
    ]
    px = bytearray(build_index(template, blocks))
    header_size = struct.unpack_from("<H", px, HeaderOffset.HEADER_SIZE)[0]
    px[header_size + 6] ^= 0x01  # corrupt the first key byte
    px[header_size + 6 + 9] = 0x05  # third short no longer 0
    problems = verify_index(bytes(px), blocks)
    assert any("key" in p for p in problems)
    assert any("third short" in p for p in problems)
    # a data block the leaves do not cover
    problems = verify_index(
        build_index(template, blocks), [*blocks, DataBlock(3, b"\x80\0\0\x63", 1)]
    )
    assert any("not covered" in p for p in problems)
    # an extra leaf entry pointing to a block that does not exist
    problems = verify_index(build_index(template, blocks), blocks[:1])
    assert any("does not exist" in p for p in problems)


def test_verifier_detects_declared_count_mismatch_and_inner_errors(tmp_path: Path) -> None:
    spec = _spec()
    template = build_px_template(spec, block_size_kib=1)
    capacity = (1024 - 6) // 10
    blocks = [DataBlock(i + 1, struct.pack(">I", 0x80000000 + i), 1) for i in range(capacity + 1)]
    px = bytearray(build_index(template, blocks))
    struct.pack_into("<I", px, HeaderOffset.RECORD_COUNT, 1)
    problems = verify_index(bytes(px), blocks)
    assert any("declares" in p for p in problems)
    # corrupt the root's first inner entry count
    header_size = struct.unpack_from("<H", px, HeaderOffset.HEADER_SIZE)[0]
    root_start = header_size + 2 * 1024
    px = bytearray(build_index(template, blocks))
    px[root_start + 6 + 6 : root_start + 6 + 8] = b"\x80\x01"
    problems = verify_index(bytes(px), blocks)
    assert any("subtree" in p for p in problems)
