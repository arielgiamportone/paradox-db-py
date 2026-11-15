"""Primary index (``.PX``): reading the data blocks it describes, building it and verifying it.

A ``.PX`` file has the generic header (file type 1, record size = key length +
6, its own block size, key descriptors at 0x58) and the same block layout as a
``.DB``. Every entry is the raw key followed by three Paradox shorts: child
block, count and a zero. Leaves point at data blocks (first key of the block,
record count); inner nodes point at index blocks (key of the first child, size
of the subtree). Index blocks are numbered by generation -- leaves first, then
each level up -- so the root is always the last block.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .blocks import Anomaly, iter_blocks, pack_block_groups
from .errors import FormatError, WriteError
from .header import TableHeader, parse_header, patch_counters
from .numeric import decode_int, encode_int

__all__ = [
    "ENTRY_TAIL",
    "MAX_ENTRY_COUNT",
    "DataBlock",
    "build_index",
    "read_data_blocks",
    "verify_index",
]

ENTRY_TAIL = 6
MAX_ENTRY_COUNT = 0x7FFF


@dataclass(frozen=True, slots=True)
class DataBlock:
    """What the index needs to know about one non-empty data block."""

    number: int
    first_key: bytes
    count: int


@dataclass(frozen=True, slots=True)
class IndexEntry:
    key: bytes
    child: int
    count: int
    zero: int

    def pack(self) -> bytes:
        return self.key + encode_int(self.child, 2) + encode_int(self.count, 2) + encode_int(0, 2)

    @classmethod
    def unpack(cls, raw: bytes, key_len: int) -> IndexEntry:
        return cls(
            raw[:key_len],
            decode_int(raw[key_len : key_len + 2]),
            decode_int(raw[key_len + 2 : key_len + 4]),
            decode_int(raw[key_len + 4 : key_len + 6]),
        )


def _load(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes | bytearray):
        return bytes(source)
    return Path(source).read_bytes()


def read_data_blocks(db: str | Path | bytes, key_len: int | None = None) -> list[DataBlock]:
    """List the non-empty data blocks of a ``.DB`` (path or bytes) in chain order.

    ``key_len`` defaults to the key length declared in the table header.
    """
    data = _load(db)
    header = parse_header(data)
    if key_len is None:
        key_len = header.key_length
    anomalies: list[Anomaly] = []
    blocks = [
        DataBlock(b.number, b.records[0][:key_len], b.record_count)
        for b in iter_blocks(data, header, anomalies)
        if b.record_count
    ]
    return blocks


def _px_header(data: bytes) -> TableHeader:
    header = parse_header(data)
    if not header.is_primary_index:
        raise FormatError("not a primary index: file type is not 1")
    return header


def build_index(template_px: str | Path | bytes, data_blocks: Sequence[DataBlock]) -> bytes:
    """Build a complete ``.PX`` for ``data_blocks`` on top of the header of ``template_px``.

    Only the counters, root block and level count of the template header
    change. Raises :class:`~paradoxdb.errors.WriteError` when a count exceeds
    the 15-bit limit of an entry.
    """
    template = _load(template_px)
    header = _px_header(template)
    key_len = header.record_size - ENTRY_TAIL
    capacity = header.block_capacity
    if capacity < 1:
        raise WriteError("index entry does not fit in the index block")
    for block in data_blocks:
        if len(block.first_key) != key_len:
            raise WriteError(
                f"key of {len(block.first_key)} bytes for data block {block.number}, "
                f"index expects {key_len}"
            )
    groups: list[list[IndexEntry]] = []
    levels = 0
    if data_blocks:
        entries = [IndexEntry(b.first_key, b.number, b.count, 0) for b in data_blocks]
        while True:
            chunks = [entries[i : i + capacity] for i in range(0, len(entries), capacity)]
            first_block_no = len(groups) + 1
            groups.extend(chunks)
            levels += 1
            if len(chunks) == 1:
                break
            entries = [
                IndexEntry(chunk[0].key, first_block_no + i, sum(e.count for e in chunk), 0)
                for i, chunk in enumerate(chunks)
            ]
    for group in groups:
        for entry in group:
            if entry.count > MAX_ENTRY_COUNT:
                raise WriteError(f"index entry count {entry.count} exceeds {MAX_ENTRY_COUNT}")
    packed_groups = [[e.pack() for e in group] for group in groups] or [[]]
    blocks, block_count = pack_block_groups(packed_groups, header.record_size, header.block_size)
    hdr = bytearray(template[: header.header_size])
    patch_counters(
        hdr,
        record_count=sum(len(g) for g in groups),
        block_count=block_count,
        root_block=block_count,
        levels=levels,
    )
    return bytes(hdr) + blocks


def verify_index(px: str | Path | bytes, data_blocks: Sequence[DataBlock]) -> list[str]:
    """Check a ``.PX`` against the data blocks of its table. Returns the problems found.

    An empty list means the index is consistent: level count, zero tail in
    every entry, leaves pointing at existing non-empty blocks with the exact
    first key and count, inner nodes repeating the first child's key and
    summing their subtree, leaves covering exactly the data blocks, and the
    declared entry count matching the entries reachable from the root.
    """
    data = _load(px)
    problems: list[str] = []
    try:
        header = _px_header(data)
    except FormatError as exc:
        return [f"unreadable index header: {exc}"]
    key_len = header.record_size - ENTRY_TAIL
    anomalies: list[Anomaly] = []
    blocks = {b.number: b for b in iter_blocks(data, header, anomalies)}
    problems.extend(f"block {a.block_no}: {a.message}" for a in anomalies)
    by_number = {b.number: b for b in data_blocks}
    if len(by_number) != len(data_blocks):
        problems.append("data block list contains duplicate block numbers")

    if header.levels == 0:
        if data_blocks:
            problems.append(f"index has 0 levels but the table has {len(data_blocks)} data blocks")
        if any(b.record_count for b in blocks.values()):
            problems.append("index has 0 levels but contains entries")
        if header.declared_record_count != 0:
            problems.append("index declares entries but has 0 levels")
        return problems
    if not data_blocks:
        problems.append(f"index has {header.levels} levels but the table has no data blocks")

    covered: list[int] = []
    reachable = 0

    def walk(block_no: int, depth: int) -> tuple[int, bytes | None]:
        nonlocal reachable
        block = blocks.get(block_no)
        if block is None:
            problems.append(f"index block {block_no} is missing or unreachable from the chain")
            return 0, None
        if block.record_count == 0:
            problems.append(f"index block {block_no} has no entries")
            return 0, None
        entries = [IndexEntry.unpack(r, key_len) for r in block.records]
        reachable += len(entries)
        total = 0
        is_leaf = depth == header.levels - 1
        for entry in entries:
            if entry.zero != 0:
                problems.append(f"index block {block_no}: third short is {entry.zero}, expected 0")
            if is_leaf:
                target = by_number.get(entry.child)
                if target is None:
                    problems.append(
                        f"leaf entry in index block {block_no} points to data block {entry.child}, "
                        "which does not exist or is empty"
                    )
                    continue
                covered.append(entry.child)
                if entry.key != target.first_key:
                    problems.append(
                        f"leaf entry for data block {entry.child}: key {entry.key.hex()} differs "
                        f"from the first record key {target.first_key.hex()}"
                    )
                if entry.count != target.count:
                    problems.append(
                        f"leaf entry for data block {entry.child}: count {entry.count} differs "
                        f"from the {target.count} records in the block"
                    )
                total += target.count
            else:
                if entry.child == block_no:
                    problems.append(f"index block {block_no} points to itself")
                    continue
                child_total, child_key = walk(entry.child, depth + 1)
                if child_key is not None and entry.key != child_key:
                    problems.append(
                        f"inner entry for index block {entry.child}: key {entry.key.hex()} differs "
                        f"from the first key of the child {child_key.hex()}"
                    )
                if child_total != entry.count:
                    problems.append(
                        f"inner entry for index block {entry.child}: count {entry.count} differs "
                        f"from the subtree total {child_total}"
                    )
                total += child_total
        return total, entries[0].key

    walk(header.root_block, 0)

    covered_set = set(covered)
    if len(covered) != len(covered_set):
        problems.append("some data blocks are referenced by more than one leaf entry")
    missing = sorted(set(by_number) - covered_set)
    if missing:
        problems.append(f"data blocks not covered by any leaf: {missing}")
    if header.declared_record_count != reachable:
        problems.append(
            f"header declares {header.declared_record_count} entries but {reachable} are reachable "
            "from the root"
        )
    return problems
