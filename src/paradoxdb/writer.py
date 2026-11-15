"""Writing a record set as new ``.DB`` / ``.MB`` / ``.PX`` files on top of a template header.

The template is an existing ``.DB`` with the wanted structure. Its header is
copied byte for byte and only the counters (declared records, block pointers)
and the autoincrement change, so version, code page, descriptors, key and file
type survive even for empty tables. Files are always new: nothing is modified
in place.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .blocks import pack_blocks
from .errors import DuplicateKeyError, EncryptedTableError, WriteError
from .fields import FieldDef, FieldType
from .header import TableHeader, parse_header, patch_counters
from .index import build_index, read_data_blocks
from .memo import MB_BLOCK_SIZE, MemoFile, MemoWriter
from .numeric import decode_int
from .table import find_secondary_indexes, find_sibling
from .values import encode_blob_field

__all__ = ["WriteRecord", "WriteResult", "legacy_sort_key", "legacy_text_key", "write_table"]


@dataclass(frozen=True, slots=True)
class WriteRecord:
    """Raw record bytes plus the BLOB content per field index (``None`` = null BLOB).

    Whatever the BLOB fields of ``raw_bytes`` contain is ignored and rebuilt
    from ``blobs``.
    """

    raw_bytes: bytes
    blobs: dict[int, bytes | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WriteResult:
    db_path: Path
    mb_path: Path | None
    px_path: Path | None
    record_count: int
    block_count: int
    skipped_indexes: tuple[Path, ...]


# --------------------------------------------------------------------------- legacy order

_ENYE_KEY = "N￿"  # sorts after every "N…" and before "O"


def legacy_text_key(text: str) -> str:
    """Collation key of the legacy engine: upper case, no diacritics, Ñ right after N."""
    out: list[str] = []
    for ch in text:
        if ch in "ñÑ":
            out.append(_ENYE_KEY)
            continue
        decomposed = unicodedata.normalize("NFD", ch)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        out.append(base.upper())
    return "".join(out)


def legacy_sort_key(header: TableHeader, raw: bytes) -> tuple:
    """Sort key reproducing the legacy order for the key fields (all fields if there is no key).

    Numeric, date and time fields compare by their raw bytes, which the
    encodings make order-preserving. Alpha fields compare by
    :func:`legacy_text_key` with the raw bytes as tie-breaker.
    """
    fields = header.key_fields if header.key_field_count else header.fields
    parts: list[object] = []
    for fd in fields:
        chunk = fd.slice(raw)
        if fd.type is FieldType.ALPHA:
            end = chunk.find(b"\0")
            text = (chunk if end < 0 else chunk[:end]).decode(header.codec, errors="replace")
            parts.append((legacy_text_key(text), chunk))
        else:
            parts.append(chunk)
    return tuple(parts)


# --------------------------------------------------------------------------- writing


def _as_write_record(item: WriteRecord | bytes) -> WriteRecord:
    if isinstance(item, WriteRecord):
        return item
    if isinstance(item, bytes | bytearray):
        return WriteRecord(bytes(item))
    raise WriteError(f"records must be WriteRecord or bytes, not {type(item).__name__}")


def _rebuild_blobs(header: TableHeader, record: WriteRecord, memo: MemoWriter | None) -> bytes:
    if not header.blob_fields:
        return record.raw_bytes
    raw = bytearray(record.raw_bytes)
    for index, fd in enumerate(header.fields):
        if not fd.type.is_blob:
            continue
        content = record.blobs.get(index)
        if content is None:
            raw[fd.offset : fd.offset + fd.byte_length] = encode_blob_field(fd, None, None)
        else:
            if memo is None:  # pragma: no cover - memo is always created when blob fields exist
                raise WriteError("BLOB content without a memo writer")
            pointer = memo.add_single(bytes(content))
            raw[fd.offset : fd.offset + fd.byte_length] = encode_blob_field(fd, content, pointer)
    return bytes(raw)


def _max_autoincrement(header: TableHeader, records: list[bytes]) -> int:
    best = header.autoincrement
    auto_fields = [f for f in header.fields if f.type is FieldType.AUTOINCREMENT]
    for fd in auto_fields:
        for raw in records:
            chunk = fd.slice(raw)
            if any(chunk):
                best = max(best, decode_int(chunk))
    return best


def _check_unique_keys(header: TableHeader, records: list[bytes]) -> None:
    key_len = header.key_length
    seen: set[bytes] = set()
    for raw in records:
        key = raw[:key_len]
        if key in seen:
            raise DuplicateKeyError(f"duplicate primary key {key.hex()}")
        seen.add(key)


def _sibling_path(dest_db: Path, extension: str) -> Path:
    return dest_db.with_name(dest_db.stem + extension)


def write_table(
    template_db: str | Path,
    dest_db: str | Path,
    records: Iterable[WriteRecord | bytes],
    *,
    sort: bool = False,
) -> WriteResult:
    """Write ``records`` as a new table at ``dest_db`` using the structure of ``template_db``.

    * ``.MB`` is written next to ``dest_db`` whenever the structure has BLOB
      fields; every content gets a single-block entry and the first block of
      the template's ``.MB`` is copied (zeros if there was none).
    * ``.PX`` is written when the table has a primary key and a ``.PX``
      template sits next to ``template_db``.
    * ``sort=True`` applies the legacy order; otherwise records keep the order
      given. Duplicate primary keys raise :class:`DuplicateKeyError`.
    * Encrypted templates and records of the wrong size are errors.
    """
    template_db = Path(template_db)
    dest_db = Path(dest_db)
    if dest_db.resolve() == template_db.resolve():
        raise WriteError("destination must be a new file, never the template itself")
    template = template_db.read_bytes()
    header = parse_header(template)
    if header.is_primary_index:
        raise WriteError("the template must be a .DB table, not a .PX index")
    if header.is_encrypted:
        raise EncryptedTableError("encrypted tables are never written")

    items = [_as_write_record(r) for r in records]
    for item in items:
        if len(item.raw_bytes) != header.record_size:
            raise WriteError(
                f"record of {len(item.raw_bytes)} bytes, template record size is "
                f"{header.record_size}"
            )
    if sort:
        items.sort(key=lambda r: legacy_sort_key(header, r.raw_bytes))
    if header.key_field_count:
        _check_unique_keys(header, [r.raw_bytes for r in items])

    memo: MemoWriter | None = None
    if header.blob_fields:
        template_mb = find_sibling(template_db, ".MB")
        first_block = (
            MemoFile.load(template_mb).header_block if template_mb else bytes(MB_BLOCK_SIZE)
        )
        memo = MemoWriter(first_block)
    raw_records = [_rebuild_blobs(header, item, memo) for item in items]

    blocks, block_count = pack_blocks(raw_records, header.record_size, header.block_size)
    hdr = bytearray(template[: header.header_size])
    patch_counters(
        hdr,
        record_count=len(raw_records),
        block_count=block_count,
        autoincrement=_max_autoincrement(header, raw_records),
    )
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    dest_db.write_bytes(bytes(hdr) + blocks)

    mb_path = None
    if memo is not None:
        mb_path = _sibling_path(dest_db, ".MB")
        mb_path.write_bytes(memo.to_bytes())

    px_path = None
    template_px = find_sibling(template_db, ".PX") if header.key_field_count else None
    if template_px is not None:
        data_blocks = read_data_blocks(bytes(hdr) + blocks, header.key_length)
        px_path = _sibling_path(dest_db, ".PX")
        px_path.write_bytes(build_index(template_px.read_bytes(), data_blocks))

    return WriteResult(
        db_path=dest_db,
        mb_path=mb_path,
        px_path=px_path,
        record_count=len(raw_records),
        block_count=block_count,
        skipped_indexes=tuple(find_secondary_indexes(template_db)),
    )


def blob_field_indexes(header: TableHeader) -> list[int]:
    """Positions of the BLOB-like fields, the keys expected in :attr:`WriteRecord.blobs`."""
    return [i for i, f in enumerate(header.fields) if f.type.is_blob]


def field_for_index(header: TableHeader, index: int) -> FieldDef:
    return header.fields[index]
