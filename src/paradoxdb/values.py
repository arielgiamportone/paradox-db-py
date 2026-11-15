"""Cell values: decoding of raw field bytes with explicit state, and the inverse encoders.

Every decoded cell carries a :class:`ValueState`, the Python value (``None``
unless the state is ``OK``, except for text that could be partially decoded)
and the raw bytes. Nothing is ever silently converted to ``0``, ``False`` or
``""``: that decision belongs to the consumer.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from . import numeric
from .errors import EncodingError, FormatError
from .fields import FieldDef, FieldType, physical_size
from .memo import POINTER_SIZE, MemoFile, MemoPointer

__all__ = [
    "MS_PER_DAY",
    "CellValue",
    "ValueState",
    "decode_value",
    "encode_alpha",
    "encode_bcd",
    "encode_blob_field",
    "encode_bool",
    "encode_bytes",
    "encode_date",
    "encode_double",
    "encode_int",
    "encode_null",
    "encode_time",
    "encode_timestamp",
    "encode_value",
]

MS_PER_DAY = 86_400_000
LOGICAL_FALSE = 0x80
LOGICAL_TRUE = 0x81


class ValueState(StrEnum):
    OK = "OK"
    NULL = "NULL"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class CellValue:
    """A decoded cell: state, Python value, raw bytes and (for invalid cells) a reason."""

    state: ValueState
    value: Any
    raw_bytes: bytes
    reason: str | None = None

    @property
    def is_ok(self) -> bool:
        return self.state is ValueState.OK

    @property
    def is_null(self) -> bool:
        return self.state is ValueState.NULL

    @property
    def is_invalid(self) -> bool:
        return self.state is ValueState.INVALID


def _ok(value: Any, raw: bytes) -> CellValue:
    return CellValue(ValueState.OK, value, raw)


def _null(raw: bytes) -> CellValue:
    return CellValue(ValueState.NULL, None, raw)


def _invalid(raw: bytes, reason: str, value: Any = None) -> CellValue:
    return CellValue(ValueState.INVALID, value, raw, reason)


def _is_all_zero(raw: bytes) -> bool:
    return not any(raw)


# --------------------------------------------------------------------------- decoding


def decode_value(
    field: FieldDef,
    raw: bytes,
    codec: str,
    memo: MemoFile | None = None,
    *,
    blob_content: bytes | None = ...,  # type: ignore[assignment]
) -> CellValue:
    """Decode the bytes of one field.

    ``codec`` is the table code page. ``memo`` is the sibling ``.MB`` file, if
    any. ``blob_content`` lets a decryptor supply the already-decrypted BLOB
    content (``None`` meaning a null BLOB) instead of resolving the pointer.
    """
    if len(raw) != field.byte_length:
        return _invalid(raw, f"expected {field.byte_length} bytes, got {len(raw)}")
    ftype = field.type
    if ftype.is_blob:
        return _decode_blob(field, raw, codec, memo, blob_content)
    if ftype is FieldType.ALPHA:
        return _decode_alpha(raw, codec)
    if _is_all_zero(raw):
        return _null(raw)
    try:
        if ftype in (FieldType.SHORT, FieldType.LONG, FieldType.AUTOINCREMENT):
            return _ok(numeric.decode_int(raw), raw)
        if ftype is FieldType.DATE:
            return _ok(dt.date.fromordinal(numeric.decode_int(raw)), raw)
        if ftype in (FieldType.CURRENCY, FieldType.NUMBER):
            value = numeric.decode_double(raw)
            if value != value or value in (float("inf"), float("-inf")):
                return _invalid(raw, "non-finite double")
            return _ok(Decimal(repr(value)), raw)
        if ftype is FieldType.LOGICAL:
            if raw[0] == LOGICAL_TRUE:
                return _ok(True, raw)
            if raw[0] == LOGICAL_FALSE:
                return _ok(False, raw)
            return _invalid(raw, f"illegal logical byte 0x{raw[0]:02X}")
        if ftype is FieldType.TIME:
            ms = numeric.decode_int(raw)
            if not 0 <= ms < MS_PER_DAY:
                return _invalid(raw, f"time {ms} ms is outside a day")
            return _ok(_time_from_ms(ms), raw)
        if ftype is FieldType.TIMESTAMP:
            return _decode_timestamp(raw)
        if ftype is FieldType.BCD:
            return _ok(numeric.decode_bcd(raw, field.decimals), raw)
        if ftype is FieldType.BYTES:
            return _ok(bytes(raw), raw)
    except (ValueError, OverflowError) as exc:
        return _invalid(raw, str(exc))
    return _invalid(raw, f"unsupported field type {ftype!r}")  # pragma: no cover


def _decode_alpha(raw: bytes, codec: str) -> CellValue:
    end = raw.find(b"\0")
    content = raw if end < 0 else raw[:end]
    if not content:
        return _null(raw)
    try:
        return _ok(content.decode(codec), raw)
    except UnicodeDecodeError as exc:
        return _invalid(
            raw,
            f"undecodable text in {codec}: {exc.reason}",
            content.decode(codec, errors="replace"),
        )


def _time_from_ms(ms: int) -> dt.time:
    seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return dt.time(hours, minutes, seconds, millis * 1000)


def _decode_timestamp(raw: bytes) -> CellValue:
    value = numeric.decode_double(raw)
    if value != value or value in (float("inf"), float("-inf")):
        return _invalid(raw, "non-finite timestamp")
    ms = int(value)
    days, rest = divmod(ms, MS_PER_DAY)
    try:
        day = dt.date.fromordinal(days)
    except (ValueError, OverflowError):
        return _invalid(raw, f"timestamp day ordinal {days} is out of range")
    return _ok(dt.datetime.combine(day, _time_from_ms(rest)), raw)


def _decode_blob(
    field: FieldDef,
    raw: bytes,
    codec: str,
    memo: MemoFile | None,
    blob_content: bytes | None,
) -> CellValue:
    leader_length = field.leader_length
    if blob_content is not ...:
        if blob_content is None:
            return _null(raw)
        content = blob_content
    else:
        try:
            pointer = MemoPointer.unpack(raw[leader_length:])
        except ValueError as exc:
            return _invalid(raw, str(exc))
        if pointer.is_null:
            return _null(raw)
        if pointer.is_embedded(leader_length):
            content = raw[: pointer.length]
        elif memo is None:
            return _invalid(raw, "memo file (.MB) is missing")
        else:
            try:
                content = memo.read(pointer)
            except FormatError as exc:
                return _invalid(raw, str(exc))
    if field.type.is_text_blob:
        try:
            return _ok(content.decode(codec), raw)
        except UnicodeDecodeError as exc:
            return _invalid(
                raw,
                f"undecodable memo in {codec}: {exc.reason}",
                content.decode(codec, errors="replace"),
            )
    return _ok(bytes(content), raw)


# --------------------------------------------------------------------------- encoding


def encode_null(field: FieldDef) -> bytes:
    """All-zero bytes: the null representation of every field type."""
    return bytes(field.byte_length)


def encode_int(value: int, size: int = 4) -> bytes:
    return numeric.encode_int(value, size)


def encode_double(value: float | Decimal | int) -> bytes:
    return numeric.encode_double(float(value))


def encode_date(value: dt.date) -> bytes:
    ordinal = value.toordinal()
    if ordinal < 1:
        raise EncodingError("dates before 0001-01-01 cannot be encoded")
    return numeric.encode_int(ordinal, 4)


def encode_time(value: dt.time) -> bytes:
    ms = ((value.hour * 60 + value.minute) * 60 + value.second) * 1000 + value.microsecond // 1000
    return numeric.encode_int(ms, 4)


def encode_timestamp(value: dt.datetime) -> bytes:
    ms = value.date().toordinal() * MS_PER_DAY
    ms += ((value.hour * 60 + value.minute) * 60 + value.second) * 1000
    ms += value.microsecond // 1000
    return numeric.encode_double(float(ms))


def encode_bool(value: bool) -> bytes:
    return bytes((LOGICAL_TRUE if value else LOGICAL_FALSE,))


def encode_alpha(value: str, length: int, codec: str) -> bytes:
    """Encode text padded with NUL to ``length`` bytes; text that does not fit is an error."""
    try:
        data = value.encode(codec)
    except UnicodeEncodeError as exc:
        raise EncodingError(f"text cannot be encoded in {codec}: {exc.reason}") from exc
    if b"\0" in data:
        raise EncodingError("text must not contain NUL characters")
    if len(data) > length:
        raise EncodingError(f"text of {len(data)} bytes does not fit in {length}")
    return data.ljust(length, b"\0")


def encode_bytes(value: bytes, length: int) -> bytes:
    if len(value) > length:
        raise EncodingError(f"{len(value)} bytes do not fit in {length}")
    return bytes(value).ljust(length, b"\0")


def encode_bcd(value: Decimal | int | str, decimals: int) -> bytes:
    return numeric.encode_bcd(value, decimals)


def encode_blob_field(field: FieldDef, content: bytes | None, pointer: MemoPointer | None) -> bytes:
    """Build the in-record bytes of a BLOB-like field: leader plus pointer.

    ``content`` is the full BLOB content (``None`` for a null BLOB). ``pointer``
    is the ``.MB`` pointer, or ``None`` when the content is stored embedded in
    the leader (only possible if it fits).
    """
    leader_length = field.leader_length
    if content is None:
        return encode_null(field)
    if pointer is None:
        if len(content) > leader_length:
            raise EncodingError("BLOB content longer than the leader needs a .MB pointer")
        pointer = MemoPointer(0, len(content), 1)
    leader = content[:leader_length].ljust(leader_length, b"\0")
    return leader + pointer.pack()


def encode_value(field: FieldDef, value: Any, codec: str) -> bytes:
    """Encode a Python value for a non-BLOB field. ``None`` encodes as null."""
    if value is None:
        return encode_null(field)
    ftype = field.type
    if ftype is FieldType.ALPHA:
        return encode_alpha(value, field.byte_length, codec)
    if ftype in (FieldType.SHORT, FieldType.LONG, FieldType.AUTOINCREMENT):
        return encode_int(value, field.byte_length)
    if ftype is FieldType.DATE:
        return encode_date(value)
    if ftype in (FieldType.CURRENCY, FieldType.NUMBER):
        return encode_double(value)
    if ftype is FieldType.LOGICAL:
        return encode_bool(value)
    if ftype is FieldType.TIME:
        return encode_time(value)
    if ftype is FieldType.TIMESTAMP:
        return encode_timestamp(value)
    if ftype is FieldType.BCD:
        return encode_bcd(value, field.decimals)
    if ftype is FieldType.BYTES:
        return encode_bytes(value, field.byte_length)
    if ftype.is_blob:
        raise EncodingError("BLOB fields are encoded with encode_blob_field")
    raise EncodingError(f"unsupported field type {ftype!r}")  # pragma: no cover


def field_length(field_type: FieldType, size_byte: int) -> int:
    """Physical length of a field, re-exported for convenience."""
    return physical_size(field_type, size_byte)


def pointer_size() -> int:
    return POINTER_SIZE
