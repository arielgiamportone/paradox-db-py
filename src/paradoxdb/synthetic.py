"""Synthetic Paradox table factory.

Everything the test-suite and the examples read is produced here, from
scratch, following the format description: headers with or without the 4.0+
extension, any combination of field types, multi-block data, ``.MB`` files with
embedded, single-block and sub-allocated memos, empty ``.PX`` templates and
random rows. No real table is ever used.
"""

from __future__ import annotations

import datetime as dt
import random
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .blocks import pack_blocks
from .fields import CODE_PAGES, FieldDef, FieldType, physical_size
from .header import (
    DESCRIPTORS_BASIC,
    DESCRIPTORS_EXTENDED,
    EXTENSION_MARKER,
    MIN_V7_VERSION,
    TABLE_NAME_AREA,
    TABLE_NAME_AREA_V7,
    FileType,
    HeaderOffset,
    has_extension,
    patch_counters,
)
from .memo import (
    MB_BLOCK_SIZE,
    SINGLE_BLOCK_HEADER,
    SINGLE_BLOCK_INDEX,
    SINGLE_BLOCK_TYPE,
    SUBALLOC_BLOCK_TYPE,
    SUBALLOC_DATA_START,
    SUBALLOC_ENTRY_SIZE,
    SUBALLOC_FIRST_ENTRY,
    SUBALLOC_MAX_ENTRIES,
    SUBALLOC_UNIT,
    MemoPointer,
)
from .values import encode_blob_field, encode_value

__all__ = [
    "BlobRef",
    "FieldSpec",
    "MemoBuilder",
    "SyntheticFiles",
    "TableSpec",
    "build_header",
    "build_px_template",
    "build_table_bytes",
    "encode_row",
    "random_rows",
    "random_value",
    "write_synthetic_table",
]

HEADER_GRANULARITY = 0x800
_INDEX_ENTRY_TAIL = 6  # three Paradox shorts after the key in every .PX entry


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """A column to synthesise: name, type and size byte (length / decimals)."""

    name: str
    type: FieldType
    size: int = 0

    def to_field_def(self, offset: int) -> FieldDef:
        return FieldDef(
            self.name, self.type, self.size, offset, physical_size(self.type, self.size)
        )


@dataclass(frozen=True, slots=True)
class TableSpec:
    """Shape of a synthetic table."""

    fields: tuple[FieldSpec, ...]
    version: int = 12
    code_page: int | None = 437
    block_size_kib: int = 2
    key_fields: int = 0
    table_name: str = "SYNTH"
    encryption_key: int = 0
    autoincrement: int = 0
    file_type: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))
        if not self.fields:
            raise ValueError("a table needs at least one field")
        if self.key_fields > len(self.fields):
            raise ValueError("more key fields than fields")

    @property
    def field_defs(self) -> tuple[FieldDef, ...]:
        defs: list[FieldDef] = []
        offset = 0
        for spec in self.fields:
            fd = spec.to_field_def(offset)
            defs.append(fd)
            offset += fd.byte_length
        return tuple(defs)

    @property
    def record_size(self) -> int:
        return sum(f.byte_length for f in self.field_defs)

    @property
    def block_size(self) -> int:
        return self.block_size_kib * 1024

    @property
    def codec(self) -> str:
        if self.code_page is None:
            return "cp437"
        return CODE_PAGES.get(self.code_page, "cp437")

    @property
    def resolved_file_type(self) -> int:
        if self.file_type is not None:
            return self.file_type
        return FileType.INDEXED_TABLE if self.key_fields else FileType.PLAIN_TABLE

    @property
    def has_extension(self) -> bool:
        return has_extension(self.resolved_file_type, self.version)

    @property
    def key_length(self) -> int:
        return sum(f.byte_length for f in self.field_defs[: self.key_fields])

    def field(self, name: str) -> FieldDef:
        wanted = name.casefold()
        for f in self.field_defs:
            if f.name.casefold() == wanted:
                return f
        raise KeyError(name)


def _round_up(value: int, granularity: int) -> int:
    return -(-value // granularity) * granularity


def build_header(
    spec: TableSpec,
    *,
    declared_count: int = 0,
    block_count: int = 0,
    autoincrement: int | None = None,
    file_type: int | None = None,
    fields: Sequence[FieldSpec] | None = None,
    record_size: int | None = None,
    block_size_kib: int | None = None,
    key_fields: int | None = None,
    root_block: int = 0,
    levels: int = 0,
) -> bytearray:
    """Build a complete header from scratch.

    The optional arguments let :func:`build_px_template` reuse the routine for
    ``.PX`` headers (type 1, key descriptors only, record size = key + 6).
    """
    ftype = spec.resolved_file_type if file_type is None else file_type
    field_specs = tuple(spec.fields if fields is None else fields)
    extended = has_extension(ftype, spec.version)
    descriptor_offset = DESCRIPTORS_EXTENDED if extended else DESCRIPTORS_BASIC
    n = len(field_specs)
    name_area = TABLE_NAME_AREA_V7 if spec.version >= MIN_V7_VERSION else TABLE_NAME_AREA
    codec = spec.codec
    names = [f.name.encode(codec) + b"\0" for f in field_specs]
    needed = descriptor_offset + 2 * n + 4 + 4 * n + name_area + sum(map(len, names))
    header_size = _round_up(needed, HEADER_GRANULARITY)
    if record_size is None:
        record_size = sum(physical_size(f.type, f.size) for f in field_specs)
    hdr = bytearray(header_size)
    struct.pack_into("<H", hdr, HeaderOffset.RECORD_SIZE, record_size)
    struct.pack_into("<H", hdr, HeaderOffset.HEADER_SIZE, header_size)
    hdr[HeaderOffset.FILE_TYPE] = ftype
    hdr[HeaderOffset.BLOCK_SIZE_KIB] = (
        spec.block_size_kib if block_size_kib is None else block_size_kib
    )
    struct.pack_into("<H", hdr, HeaderOffset.FIELD_COUNT, n)
    struct.pack_into(
        "<H",
        hdr,
        HeaderOffset.KEY_FIELD_COUNT,
        spec.key_fields if key_fields is None else key_fields,
    )
    hdr[HeaderOffset.VERSION] = spec.version
    if extended:
        struct.pack_into("<I", hdr, HeaderOffset.LEGACY_ENCRYPTION, EXTENSION_MARKER)
        struct.pack_into("<I", hdr, HeaderOffset.ENCRYPTION_KEY, spec.encryption_key)
        struct.pack_into("<H", hdr, HeaderOffset.CODE_PAGE, spec.code_page or 0)
    else:
        struct.pack_into("<I", hdr, HeaderOffset.LEGACY_ENCRYPTION, spec.encryption_key)
    pos = descriptor_offset
    for f in field_specs:
        hdr[pos] = int(f.type)
        hdr[pos + 1] = f.size & 0xFF
        pos += 2
    # Table-name pointer and field-name pointers (opaque in-memory pointers in
    # the original engine); zeros are enough for readers that skip them.
    pos += 4 + 4 * n
    table_name = spec.table_name.encode(codec)[: name_area - 1]
    hdr[pos : pos + len(table_name)] = table_name
    pos += name_area
    for name in names:
        hdr[pos : pos + len(name)] = name
        pos += len(name)
    patch_counters(
        hdr,
        record_count=declared_count,
        block_count=block_count,
        autoincrement=spec.autoincrement if autoincrement is None else autoincrement,
        root_block=root_block,
        levels=levels,
    )
    return hdr


def build_table_bytes(
    spec: TableSpec,
    records: Sequence[bytes],
    *,
    declared_count: int | None = None,
    autoincrement: int | None = None,
) -> bytes:
    """Assemble a ``.DB`` file: header plus the chain of data blocks.

    ``declared_count`` overrides the record count written at offset 0x06, to
    reproduce legacy tables whose header lags behind the blocks.
    """
    blocks, block_count = pack_blocks(records, spec.record_size, spec.block_size)
    hdr = build_header(
        spec,
        declared_count=len(records) if declared_count is None else declared_count,
        block_count=block_count,
        autoincrement=autoincrement,
    )
    return bytes(hdr) + blocks


def build_px_template(spec: TableSpec, *, block_size_kib: int | None = None) -> bytes:
    """An empty primary index for ``spec``: one block without entries, level count 0."""
    if spec.key_fields == 0:
        raise ValueError("a primary index needs key fields")
    key_specs = spec.fields[: spec.key_fields]
    record_size = spec.key_length + _INDEX_ENTRY_TAIL
    kib = spec.block_size_kib if block_size_kib is None else block_size_kib
    hdr = build_header(
        spec,
        file_type=FileType.PRIMARY_INDEX,
        fields=key_specs,
        record_size=record_size,
        block_size_kib=kib,
        key_fields=spec.key_fields,
        declared_count=0,
        block_count=1,
        autoincrement=0,
        root_block=1,
        levels=0,
    )
    blocks, _ = pack_blocks([], record_size, kib * 1024)
    return bytes(hdr) + blocks


# --------------------------------------------------------------------------- memos


class MemoBuilder:
    """Accumulates memo / BLOB content into a ``.MB`` image."""

    def __init__(self, header_block: bytes | None = None) -> None:
        if header_block is None:
            header_block = bytes(MB_BLOCK_SIZE)
        if len(header_block) != MB_BLOCK_SIZE:
            raise ValueError("the .MB header block is 4096 bytes long")
        self._data = bytearray(header_block)
        self._counter = 1

    def _next_counter(self) -> int:
        value = self._counter
        self._counter = (self._counter % 0xFFFF) + 1
        return value

    def add_single(self, content: bytes) -> MemoPointer:
        """Store ``content`` in its own run of 4 KiB blocks (type 2)."""
        start = len(self._data)
        payload = SINGLE_BLOCK_HEADER.size + len(content)
        block_count = _round_up(payload, MB_BLOCK_SIZE) // MB_BLOCK_SIZE
        chunk = bytearray(block_count * MB_BLOCK_SIZE)
        counter = self._next_counter()
        SINGLE_BLOCK_HEADER.pack_into(
            chunk, 0, SINGLE_BLOCK_TYPE, block_count, len(content), counter
        )
        chunk[SINGLE_BLOCK_HEADER.size : SINGLE_BLOCK_HEADER.size + len(content)] = content
        self._data += chunk
        return MemoPointer(start | SINGLE_BLOCK_INDEX, len(content), counter)

    def add_suballocated(self, contents: Sequence[bytes]) -> list[MemoPointer]:
        """Store several short contents in one sub-allocated block (type 3)."""
        if not 0 < len(contents) <= SUBALLOC_MAX_ENTRIES:
            raise ValueError("a sub-allocated block holds 1 to 64 entries")
        start = len(self._data)
        block = bytearray(MB_BLOCK_SIZE)
        block[0] = SUBALLOC_BLOCK_TYPE
        pointers: list[MemoPointer] = []
        data_pos = SUBALLOC_DATA_START
        for index, content in enumerate(contents):
            if not content:
                raise ValueError("sub-allocated entries cannot be empty")
            units = _round_up(len(content), SUBALLOC_UNIT) // SUBALLOC_UNIT
            tail = len(content) - (units - 1) * SUBALLOC_UNIT
            if data_pos + units * SUBALLOC_UNIT > MB_BLOCK_SIZE:
                raise ValueError("sub-allocated contents do not fit in one 4 KiB block")
            counter = self._next_counter()
            entry = SUBALLOC_FIRST_ENTRY + index * SUBALLOC_ENTRY_SIZE
            struct.pack_into("<BBHB", block, entry, data_pos // SUBALLOC_UNIT, units, counter, tail)
            block[data_pos : data_pos + len(content)] = content
            pointers.append(MemoPointer(start | index, len(content), counter))
            data_pos += units * SUBALLOC_UNIT
        self._data += block
        return pointers

    def to_bytes(self) -> bytes:
        return bytes(self._data)


@dataclass(frozen=True, slots=True)
class BlobRef:
    """Explicit BLOB value: content plus the pointer that was already stored."""

    content: bytes
    pointer: MemoPointer


def encode_row(
    spec: TableSpec,
    values: Mapping[str, Any] | Sequence[Any],
    memo: MemoBuilder | None = None,
    *,
    embed_short_blobs: bool = True,
) -> bytes:
    """Encode one row. Missing names and ``None`` encode as null.

    BLOB values may be ``str`` (memo types), ``bytes``, ``None`` or a
    :class:`BlobRef`. Content that fits in the leader is embedded unless
    ``embed_short_blobs`` is false; anything else goes to ``memo`` as a
    single-block entry.
    """
    defs = spec.field_defs
    if isinstance(values, Mapping):
        lookup = {k.casefold(): v for k, v in values.items()}
        row = [lookup.get(f.name.casefold()) for f in defs]
    else:
        row = list(values)
        if len(row) != len(defs):
            raise ValueError(f"expected {len(defs)} values, got {len(row)}")
    out = bytearray()
    for fd, value in zip(defs, row, strict=True):
        if fd.type.is_blob:
            out += _encode_blob(fd, value, spec.codec, memo, embed_short_blobs)
        else:
            out += encode_value(fd, value, spec.codec)
    return bytes(out)


def _encode_blob(
    fd: FieldDef, value: Any, codec: str, memo: MemoBuilder | None, embed: bool
) -> bytes:
    if value is None:
        return encode_blob_field(fd, None, None)
    if isinstance(value, BlobRef):
        return encode_blob_field(fd, value.content, value.pointer)
    content = value.encode(codec) if isinstance(value, str) else bytes(value)
    if embed and len(content) <= fd.leader_length:
        return encode_blob_field(fd, content, None)
    if memo is None:
        raise ValueError(
            f"field {fd.name!r} needs a MemoBuilder for content of {len(content)} bytes"
        )
    return encode_blob_field(fd, content, memo.add_single(content))


# --------------------------------------------------------------------------- files


@dataclass(frozen=True, slots=True)
class SyntheticFiles:
    db: Path
    mb: Path | None = None
    px: Path | None = None


def write_synthetic_table(
    directory: str | Path,
    name: str,
    spec: TableSpec,
    rows: Iterable[Mapping[str, Any] | Sequence[Any]],
    *,
    memo: MemoBuilder | None = None,
    declared_count: int | None = None,
    autoincrement: int | None = None,
    with_px_template: bool = False,
    px_block_size_kib: int | None = None,
    memo_extension: str = ".MB",
) -> SyntheticFiles:
    """Write ``name.DB`` (plus ``.MB`` / ``.PX`` when requested) into ``directory``."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    has_blobs = any(f.type.is_blob for f in spec.fields)
    if memo is None and has_blobs:
        memo = MemoBuilder()
    records = [encode_row(spec, row, memo) for row in rows]
    db_path = directory / f"{name}.DB"
    db_path.write_bytes(
        build_table_bytes(spec, records, declared_count=declared_count, autoincrement=autoincrement)
    )
    mb_path = None
    if memo is not None:
        mb_path = directory / f"{name}{memo_extension}"
        mb_path.write_bytes(memo.to_bytes())
    px_path = None
    if with_px_template:
        px_path = directory / f"{name}.PX"
        px_path.write_bytes(build_px_template(spec, block_size_kib=px_block_size_kib))
    return SyntheticFiles(db_path, mb_path, px_path)


# --------------------------------------------------------------------------- random rows

#: Characters present in CP437, CP850 and CP1252 alike, so that the same text
#: round-trips through any of the supported code pages.
TEXT_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ñÑáéíóúºª-_.,"


def _random_day(rng: random.Random) -> dt.date:
    first, last = dt.date(1900, 1, 1).toordinal(), dt.date(2100, 1, 1).toordinal()
    return dt.date.fromordinal(rng.randint(first, last))


def _random_time(rng: random.Random) -> dt.time:
    return dt.time(
        rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59), rng.randint(0, 999) * 1000
    )


def random_value(rng: random.Random, fd: FieldDef, *, null_ratio: float = 0.1) -> Any:
    """A random Python value suitable for ``fd`` (``None`` with ``null_ratio`` probability)."""
    if rng.random() < null_ratio:
        return None
    t = fd.type
    if t is FieldType.ALPHA:
        length = rng.randint(1, max(1, min(fd.byte_length, 40)))
        return "".join(rng.choice(TEXT_ALPHABET) for _ in range(length)).rstrip() or "x"
    if t is FieldType.SHORT:
        return rng.randint(-32768, 32767)
    if t in (FieldType.LONG, FieldType.AUTOINCREMENT):
        return (
            rng.randint(-(2**31), 2**31 - 1) if t is FieldType.LONG else rng.randint(1, 2**31 - 1)
        )
    if t is FieldType.DATE:
        return dt.date.fromordinal(
            rng.randint(dt.date(1900, 1, 1).toordinal(), dt.date(2100, 1, 1).toordinal())
        )
    if t in (FieldType.CURRENCY, FieldType.NUMBER):
        return Decimal(repr(round(rng.uniform(-1e9, 1e9), 4)))
    if t is FieldType.LOGICAL:
        return rng.random() < 0.5
    if t is FieldType.TIME:
        return dt.time(
            rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59), rng.randint(0, 999) * 1000
        )
    if t is FieldType.TIMESTAMP:
        day = dt.date.fromordinal(
            rng.randint(dt.date(1900, 1, 1).toordinal(), dt.date(2100, 1, 1).toordinal())
        )
        return dt.datetime.combine(
            day,
            dt.time(
                rng.randint(0, 23),
                rng.randint(0, 59),
                rng.randint(0, 59),
                rng.randint(0, 999) * 1000,
            ),
        )
    if t is FieldType.BCD:
        digits = rng.randint(1, 12)
        magnitude = rng.randint(0, 10**digits - 1)
        sign = -1 if rng.random() < 0.4 else 1
        return Decimal(sign * magnitude).scaleb(-fd.decimals)
    if t is FieldType.BYTES:
        return bytes(rng.randrange(256) for _ in range(rng.randint(1, fd.byte_length)))
    if t.is_text_blob:
        length = rng.randint(1, 300)
        return "".join(rng.choice(TEXT_ALPHABET) for _ in range(length))
    if t.is_blob:
        return bytes(rng.randrange(256) for _ in range(rng.randint(1, 300)))
    raise ValueError(f"no random generator for {t!r}")  # pragma: no cover


def random_rows(
    spec: TableSpec, count: int, rng: random.Random | int | None = None, *, null_ratio: float = 0.1
) -> list[dict[str, Any]]:
    """``count`` random rows for ``spec``. Key fields are kept unique and never null."""
    if not isinstance(rng, random.Random):
        rng = random.Random(rng)
    defs = spec.field_defs
    rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()
    while len(rows) < count:
        row = {fd.name: random_value(rng, fd, null_ratio=null_ratio) for fd in defs}
        if spec.key_fields:
            key = tuple(row[fd.name] for fd in defs[: spec.key_fields])
            if any(v is None for v in key) or key in seen_keys:
                for fd in defs[: spec.key_fields]:
                    row[fd.name] = random_value(rng, fd, null_ratio=0.0)
                key = tuple(row[fd.name] for fd in defs[: spec.key_fields])
                if any(v is None for v in key) or key in seen_keys:
                    continue
            seen_keys.add(key)
        rows.append(row)
    return rows
