"""Layout and parsing of the ``.DB`` / ``.PX`` header.

All header integers are little-endian. Offsets follow the public description of
the format; the ones marked *extension* only exist in tables of version 4.0 and
later whose file type is one of :data:`EXTENDED_FILE_TYPES`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from .errors import FormatError
from .fields import FieldDef, FieldType, codec_for_code_page, physical_size

__all__ = [
    "DESCRIPTORS_BASIC",
    "DESCRIPTORS_EXTENDED",
    "EXTENDED_FILE_TYPES",
    "EXTENSION_MARKER",
    "FileType",
    "HeaderOffset",
    "TableHeader",
    "parse_header",
    "patch_counters",
    "version_label",
]


class HeaderOffset(IntEnum):
    """Byte offsets inside the header."""

    RECORD_SIZE = 0x00  # u16
    HEADER_SIZE = 0x02  # u16
    FILE_TYPE = 0x04  # u8
    BLOCK_SIZE_KIB = 0x05  # u8, block size in KiB
    RECORD_COUNT = 0x06  # u32, declared (unreliable)
    NEXT_BLOCK = 0x0A  # u16
    BLOCKS_IN_USE = 0x0C  # u16
    FIRST_BLOCK = 0x0E  # u16, 1-based, 0 = none
    LAST_BLOCK = 0x10  # u16
    PX_ROOT_BLOCK = 0x1E  # u16, .PX only
    PX_LEVELS = 0x20  # u8, .PX only
    FIELD_COUNT = 0x21  # u16
    KEY_FIELD_COUNT = 0x23  # u16
    LEGACY_ENCRYPTION = 0x25  # u32, key in 3.x; marker 0xFF00FF00 with extension
    VERSION = 0x39  # u8
    AUTOINCREMENT = 0x49  # u32
    ENCRYPTION_KEY = 0x5C  # u32, extension only
    CODE_PAGE = 0x6A  # u16, extension only


DESCRIPTORS_BASIC = 0x58
DESCRIPTORS_EXTENDED = 0x78
EXTENSION_MARKER = 0xFF00FF00
EXTENDED_FILE_TYPES = frozenset({0, 2, 8, 10, 13, 15})
TABLE_NAME_AREA_V7 = 261
TABLE_NAME_AREA = 79
MIN_EXTENDED_VERSION = 5
MIN_V7_VERSION = 12
HEADER_PROBE = 4096  # the first 4 KiB are enough to interpret any header
INDEX_ENTRY_TAIL = 6  # child block, count and zero: three Paradox shorts per .PX entry


class FileType(IntEnum):
    INDEXED_TABLE = 0
    PRIMARY_INDEX = 1
    PLAIN_TABLE = 2
    SECONDARY_INDEX_NONINC = 3
    SECONDARY_INDEX_NONINC_2 = 4
    SECONDARY_INDEX = 5
    SECONDARY_INDEX_2 = 6
    SECONDARY_INDEX_INC = 7
    SECONDARY_INDEX_INC_2 = 8
    TABLE_TYPE_10 = 10
    TABLE_TYPE_13 = 13
    TABLE_TYPE_15 = 15


_VERSION_LABELS = {
    3: "3.0",
    4: "3.5",
    5: "4.x",
    6: "4.x",
    7: "4.x",
    8: "4.x",
    9: "4.x",
    10: "5.x",
    11: "5.x",
    12: "7.x",
}


def version_label(version: int) -> str:
    return _VERSION_LABELS.get(version, f"unknown ({version})")


def has_extension(file_type: int, version: int) -> bool:
    return file_type in EXTENDED_FILE_TYPES and version >= MIN_EXTENDED_VERSION


@dataclass(frozen=True, slots=True)
class TableHeader:
    """Immutable view of a ``.DB`` or ``.PX`` header."""

    record_size: int
    header_size: int
    file_type: int
    block_size: int
    declared_record_count: int
    next_block: int
    blocks_in_use: int
    first_block: int
    last_block: int
    root_block: int
    levels: int
    field_count: int
    key_field_count: int
    encryption_key: int
    version: int
    autoincrement: int
    code_page: int | None
    codec: str
    has_extension: bool
    descriptor_offset: int
    table_name: str
    fields: tuple[FieldDef, ...]

    @property
    def is_encrypted(self) -> bool:
        return self.encryption_key != 0

    @property
    def is_primary_index(self) -> bool:
        return self.file_type == FileType.PRIMARY_INDEX

    @property
    def version_label(self) -> str:
        return version_label(self.version)

    @property
    def key_fields(self) -> tuple[FieldDef, ...]:
        return self.fields[: self.key_field_count]

    @property
    def key_length(self) -> int:
        """Bytes of the primary key: the first ``key_field_count`` fields."""
        return sum(f.byte_length for f in self.key_fields)

    @property
    def blob_fields(self) -> tuple[FieldDef, ...]:
        return tuple(f for f in self.fields if f.type.is_blob)

    @property
    def block_capacity(self) -> int:
        return (self.block_size - 6) // self.record_size

    def field(self, name: str) -> FieldDef:
        """Look a field up by name, case-insensitively (names vary between installations)."""
        wanted = name.casefold()
        for f in self.fields:
            if f.name.casefold() == wanted:
                return f
        raise KeyError(name)

    def field_index(self, name: str) -> int:
        wanted = name.casefold()
        for i, f in enumerate(self.fields):
            if f.name.casefold() == wanted:
                return i
        raise KeyError(name)


def _u8(data: bytes, offset: int) -> int:
    return data[offset]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def parse_header(data: bytes, default_codec: str = "cp437") -> TableHeader:
    """Interpret the header found at the start of ``data``.

    Raises :class:`~paradoxdb.errors.FormatError` for incoherent headers:
    zero record / block / field counts, a file shorter than its header, an
    unknown field type, or field sizes that do not add up to the record size.
    """
    if len(data) < DESCRIPTORS_EXTENDED:
        raise FormatError("file too short to contain a Paradox header")
    record_size = _u16(data, HeaderOffset.RECORD_SIZE)
    header_size = _u16(data, HeaderOffset.HEADER_SIZE)
    file_type = _u8(data, HeaderOffset.FILE_TYPE)
    block_size = _u8(data, HeaderOffset.BLOCK_SIZE_KIB) * 1024
    field_count = _u16(data, HeaderOffset.FIELD_COUNT)
    version = _u8(data, HeaderOffset.VERSION)
    if record_size == 0:
        raise FormatError("record size is 0")
    if block_size == 0:
        raise FormatError("block size is 0")
    if field_count == 0:
        raise FormatError("field count is 0")
    if header_size == 0 or len(data) < header_size:
        raise FormatError(
            f"file is shorter ({len(data)} bytes) than its header ({header_size} bytes)"
        )
    extended = has_extension(file_type, version)
    descriptor_offset = DESCRIPTORS_EXTENDED if extended else DESCRIPTORS_BASIC
    if extended:
        encryption_key = _u32(data, HeaderOffset.ENCRYPTION_KEY)
        code_page: int | None = _u16(data, HeaderOffset.CODE_PAGE)
    else:
        encryption_key = _u32(data, HeaderOffset.LEGACY_ENCRYPTION)
        code_page = None
    codec = codec_for_code_page(code_page, default_codec)

    # Field descriptors: 2 bytes each (type, size byte).
    descriptors_end = descriptor_offset + 2 * field_count
    if descriptors_end > len(data):
        raise FormatError("field descriptors run past the end of the file")
    types_and_sizes: list[tuple[FieldType, int]] = []
    for i in range(field_count):
        type_code = data[descriptor_offset + 2 * i]
        size_byte = data[descriptor_offset + 2 * i + 1]
        try:
            ftype = FieldType(type_code)
        except ValueError as exc:
            raise FormatError(f"unknown field type 0x{type_code:02X} in field {i + 1}") from exc
        types_and_sizes.append((ftype, size_byte))

    # Skip the table-name pointer and one pointer per field name, then the
    # fixed table-name area, then NUL-terminated field names.
    pos = descriptors_end + 4 + 4 * field_count
    name_area = TABLE_NAME_AREA_V7 if version >= MIN_V7_VERSION else TABLE_NAME_AREA
    table_name = _c_string(data, pos, name_area, codec)
    pos += name_area
    names: list[str] = []
    for i in range(field_count):
        end = data.find(b"\0", pos, header_size)
        if end < 0:
            names.extend(f"field_{j + 1}" for j in range(i, field_count))
            break
        names.append(_decode_name(data[pos:end], codec, i))
        pos = end + 1

    fields: list[FieldDef] = []
    offset = 0
    for name, (ftype, size_byte) in zip(names, types_and_sizes, strict=True):
        length = physical_size(ftype, size_byte)
        fields.append(FieldDef(name, ftype, size_byte, offset, length))
        offset += length
    # In a .PX every entry is the key followed by three 2-byte shorts.
    expected = offset + INDEX_ENTRY_TAIL if file_type == FileType.PRIMARY_INDEX else offset
    if expected != record_size:
        raise FormatError(
            f"field sizes add up to {expected} bytes but the record size is {record_size}"
        )

    return TableHeader(
        record_size=record_size,
        header_size=header_size,
        file_type=file_type,
        block_size=block_size,
        declared_record_count=_u32(data, HeaderOffset.RECORD_COUNT),
        next_block=_u16(data, HeaderOffset.NEXT_BLOCK),
        blocks_in_use=_u16(data, HeaderOffset.BLOCKS_IN_USE),
        first_block=_u16(data, HeaderOffset.FIRST_BLOCK),
        last_block=_u16(data, HeaderOffset.LAST_BLOCK),
        root_block=_u16(data, HeaderOffset.PX_ROOT_BLOCK),
        levels=_u8(data, HeaderOffset.PX_LEVELS),
        field_count=field_count,
        key_field_count=_u16(data, HeaderOffset.KEY_FIELD_COUNT),
        encryption_key=encryption_key,
        version=version,
        autoincrement=_u32(data, HeaderOffset.AUTOINCREMENT),
        code_page=code_page,
        codec=codec,
        has_extension=extended,
        descriptor_offset=descriptor_offset,
        table_name=table_name,
        fields=tuple(fields),
    )


def _c_string(data: bytes, start: int, area: int, codec: str) -> str:
    chunk = data[start : start + area]
    end = chunk.find(b"\0")
    if end >= 0:
        chunk = chunk[:end]
    return chunk.decode(codec, errors="replace")


def _decode_name(raw: bytes, codec: str, index: int) -> str:
    if not raw:
        return f"field_{index + 1}"
    return raw.decode(codec, errors="replace")


def patch_counters(
    header: bytearray,
    *,
    record_count: int,
    block_count: int,
    autoincrement: int | None = None,
    root_block: int | None = None,
    levels: int | None = None,
) -> None:
    """Rewrite the counters of a header copy in place.

    Only the declared record count, the block counters (next, in use, first,
    last), the autoincrement and -- for ``.PX`` headers -- the root block and
    level count change; everything else is kept byte for byte.
    """
    struct.pack_into("<I", header, HeaderOffset.RECORD_COUNT, record_count)
    struct.pack_into("<H", header, HeaderOffset.NEXT_BLOCK, block_count + 1)
    struct.pack_into("<H", header, HeaderOffset.BLOCKS_IN_USE, block_count)
    struct.pack_into("<H", header, HeaderOffset.FIRST_BLOCK, 1 if block_count else 0)
    struct.pack_into("<H", header, HeaderOffset.LAST_BLOCK, block_count)
    if autoincrement is not None:
        struct.pack_into("<I", header, HeaderOffset.AUTOINCREMENT, autoincrement)
    if root_block is not None:
        struct.pack_into("<H", header, HeaderOffset.PX_ROOT_BLOCK, root_block)
    if levels is not None:
        header[HeaderOffset.PX_LEVELS] = levels
