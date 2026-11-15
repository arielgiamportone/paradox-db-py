"""Data blocks of ``.DB`` and ``.PX`` files: walking the chain and packing records.

Blocks are numbered from 1 and start at ``header_size + (number - 1) * block_size``.
Each block begins with a 6-byte header: ``next`` (u16), ``previous`` (u16) and
the offset of the last record relative to the start of the block data (i16).
An empty block stores ``-record_size`` there.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from .header import TableHeader

__all__ = ["BLOCK_HEADER_SIZE", "Anomaly", "RawBlock", "iter_blocks", "pack_blocks"]

BLOCK_HEADER_SIZE = 6
_BLOCK_HEADER = struct.Struct("<HHh")
MAX_BLOCKS = 0xFFFF


@dataclass(frozen=True, slots=True)
class Anomaly:
    """Something odd found while walking the block chain. Never fatal."""

    block_no: int
    message: str


@dataclass(frozen=True, slots=True)
class RawBlock:
    number: int
    next_block: int
    prev_block: int
    last_offset: int
    records: tuple[bytes, ...]

    @property
    def record_count(self) -> int:
        return len(self.records)


def block_start(header: TableHeader, number: int) -> int:
    return header.header_size + (number - 1) * header.block_size


def iter_blocks(data: bytes, header: TableHeader, anomalies: list[Anomaly]) -> Iterator[RawBlock]:
    """Follow the block chain from the header's first block.

    Every record present in a block is yielded, whatever the declared record
    count says. Cycles, blocks outside the file and blocks declaring more
    records than they can hold are appended to ``anomalies`` (the latter count
    as empty); none of them aborts the walk.
    """
    number = header.first_block
    seen: set[int] = set()
    record_size = header.record_size
    capacity = header.block_capacity
    while number:
        if number in seen:
            anomalies.append(Anomaly(number, "block chain loops back to an earlier block"))
            return
        seen.add(number)
        start = block_start(header, number)
        end = start + header.block_size
        if start < header.header_size or end > len(data):
            anomalies.append(Anomaly(number, "block lies outside the file"))
            return
        next_block, prev_block, last_offset = _BLOCK_HEADER.unpack_from(data, start)
        if last_offset < 0:
            count = 0
        else:
            count = last_offset // record_size + 1
            if count > capacity:
                anomalies.append(
                    Anomaly(
                        number,
                        f"block declares {count} records but can hold {capacity}; counted as 0",
                    )
                )
                count = 0
        base = start + BLOCK_HEADER_SIZE
        records = tuple(
            bytes(data[base + i * record_size : base + (i + 1) * record_size]) for i in range(count)
        )
        yield RawBlock(number, next_block, prev_block, last_offset, records)
        number = next_block


def pack_blocks(records: Sequence[bytes], record_size: int, block_size: int) -> tuple[bytes, int]:
    """Lay ``records`` out in a chain of full-size blocks.

    Returns the block bytes and the number of blocks. An empty record set still
    produces one empty block, as Paradox does.
    """
    capacity = (block_size - BLOCK_HEADER_SIZE) // record_size
    if capacity < 1:
        raise ValueError("record does not fit in a block")
    chunks = [records[i : i + capacity] for i in range(0, len(records), capacity)] or [()]
    if len(chunks) > MAX_BLOCKS:
        raise ValueError("too many blocks for a 16-bit block number")
    out = bytearray()
    total = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        block = bytearray(block_size)
        last_offset = (len(chunk) - 1) * record_size if chunk else -record_size
        _BLOCK_HEADER.pack_into(block, 0, i + 1 if i < total else 0, i - 1, last_offset)
        pos = BLOCK_HEADER_SIZE
        for rec in chunk:
            if len(rec) != record_size:
                raise ValueError(f"record of {len(rec)} bytes, expected {record_size}")
            block[pos : pos + record_size] = rec
            pos += record_size
        out += block
    return bytes(out), total
