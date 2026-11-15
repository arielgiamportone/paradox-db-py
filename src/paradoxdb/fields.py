"""Field types, field definitions and code pages of Paradox tables."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from enum import IntEnum

__all__ = ["CODE_PAGES", "FieldDef", "FieldType", "codec_for_code_page", "physical_size"]


class FieldType(IntEnum):
    """Field type codes as stored in the 2-byte field descriptors."""

    ALPHA = 0x01
    DATE = 0x02
    SHORT = 0x03
    LONG = 0x04
    CURRENCY = 0x05
    NUMBER = 0x06
    LOGICAL = 0x09
    MEMO = 0x0C
    BLOB = 0x0D
    FORMATTED_MEMO = 0x0E
    OLE = 0x0F
    GRAPHIC = 0x10
    TIME = 0x14
    TIMESTAMP = 0x15
    AUTOINCREMENT = 0x16
    BCD = 0x17
    BYTES = 0x18

    @property
    def is_blob(self) -> bool:
        """True for every type whose content lives (at least partly) in the ``.MB`` file."""
        return self in _BLOB_TYPES

    @property
    def is_text_blob(self) -> bool:
        """True for memo types, whose content is text decoded with the table code page."""
        return self in (FieldType.MEMO, FieldType.FORMATTED_MEMO)

    @property
    def is_numeric(self) -> bool:
        return self in _NUMERIC_TYPES

    @property
    def is_fixed_size(self) -> bool:
        return self in _FIXED_SIZES


_BLOB_TYPES = frozenset(
    {FieldType.MEMO, FieldType.BLOB, FieldType.FORMATTED_MEMO, FieldType.OLE, FieldType.GRAPHIC}
)
_NUMERIC_TYPES = frozenset(
    {
        FieldType.SHORT,
        FieldType.LONG,
        FieldType.CURRENCY,
        FieldType.NUMBER,
        FieldType.AUTOINCREMENT,
        FieldType.BCD,
    }
)
_FIXED_SIZES: dict[FieldType, int] = {
    FieldType.DATE: 4,
    FieldType.SHORT: 2,
    FieldType.LONG: 4,
    FieldType.CURRENCY: 8,
    FieldType.NUMBER: 8,
    FieldType.LOGICAL: 1,
    FieldType.TIME: 4,
    FieldType.TIMESTAMP: 8,
    FieldType.AUTOINCREMENT: 4,
    FieldType.BCD: 17,
}

#: Length of the pointer that closes every BLOB-like field inside the record.
BLOB_POINTER_SIZE = 10

#: DOS / Windows code pages found in the header extension and their Python codecs.
CODE_PAGES: dict[int, str] = {437: "cp437", 850: "cp850", 1252: "cp1252"}


def codec_for_code_page(code_page: int | None, default: str) -> str:
    """Return the Python codec for a Paradox code page, or ``default`` if unknown."""
    if code_page is None:
        return default
    return CODE_PAGES.get(code_page, default)


def physical_size(field_type: FieldType, size_byte: int) -> int:
    """Number of bytes the field occupies in the record.

    ``size_byte`` is the second byte of the descriptor: a length for alpha,
    bytes and BLOB fields, the number of decimals for BCD, ignored otherwise.
    """
    fixed = _FIXED_SIZES.get(field_type)
    if fixed is not None:
        return fixed
    return size_byte


@dataclass(frozen=True, slots=True)
class FieldDef:
    """One column of the table, with its position inside the fixed-size record."""

    name: str
    type: FieldType
    size_byte: int
    offset: int
    byte_length: int

    @property
    def decimals(self) -> int:
        """Number of decimals of a BCD field (``size_byte`` for that type)."""
        return self.size_byte if self.type is FieldType.BCD else 0

    @property
    def leader_length(self) -> int:
        """Bytes of BLOB content kept inside the record, before the 10-byte pointer."""
        if not self.type.is_blob:
            return 0
        return max(self.byte_length - BLOB_POINTER_SIZE, 0)

    def slice(self, record: bytes) -> bytes:
        return record[self.offset : self.offset + self.byte_length]


def validate_codec(name: str) -> str:
    """Normalise a codec name, raising ``LookupError`` if Python does not know it."""
    return codecs.lookup(name).name
