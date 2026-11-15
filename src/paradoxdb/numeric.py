"""Order-preserving numeric encodings used by Paradox.

Three encodings share one property: comparing the raw bytes of two values is
equivalent to comparing the values themselves. The writer relies on that to
sort numeric keys without decoding them.

* **Paradox integer** -- big-endian two's complement with the most significant
  bit flipped.
* **Paradox double** -- IEEE 754 big-endian; positive numbers get bit 7 of the
  first byte set, negative numbers have every byte inverted.
* **BCD** -- 17 bytes: a sign/precision byte followed by 32 packed decimal
  digits, complemented (nibble XOR 0xF) when the value is negative.
"""

from __future__ import annotations

import math
import struct
from decimal import Decimal, InvalidOperation

from .errors import EncodingError

__all__ = [
    "BCD_DIGITS",
    "BCD_SIZE",
    "decode_bcd",
    "decode_double",
    "decode_int",
    "encode_bcd",
    "encode_double",
    "encode_int",
]

BCD_SIZE = 17
BCD_DIGITS = 32
_BCD_POSITIVE = 0xC0
_BCD_NEGATIVE = 0x40


def decode_int(raw: bytes) -> int:
    """Decode a Paradox integer of ``len(raw)`` bytes (2 or 4 in practice)."""
    bits = 8 * len(raw)
    value = int.from_bytes(raw, "big") ^ (1 << (bits - 1))
    if value >= 1 << (bits - 1):
        value -= 1 << bits
    return value


def encode_int(value: int, size: int) -> bytes:
    """Encode ``value`` as a Paradox integer of ``size`` bytes."""
    bits = 8 * size
    low, high = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if not low <= value <= high:
        raise EncodingError(f"integer {value} does not fit in {size} bytes")
    unsigned = (value & ((1 << bits) - 1)) ^ (1 << (bits - 1))
    return unsigned.to_bytes(size, "big")


def decode_double(raw: bytes) -> float:
    """Decode an 8-byte Paradox double. The result may be non-finite."""
    if len(raw) != 8:
        raise ValueError("a Paradox double is 8 bytes long")
    positive = bool(raw[0] & 0x80)
    ieee = bytes((raw[0] & 0x7F,)) + raw[1:] if positive else bytes(b ^ 0xFF for b in raw)
    return struct.unpack(">d", ieee)[0]


def encode_double(value: float) -> bytes:
    """Encode a finite float as an 8-byte Paradox double."""
    if not math.isfinite(value):
        raise EncodingError("only finite values can be encoded as a Paradox double")
    ieee = struct.pack(">d", value)
    if ieee[0] & 0x80:  # negative (sign bit set in IEEE)
        return bytes(b ^ 0xFF for b in ieee)
    return bytes((ieee[0] | 0x80,)) + ieee[1:]


def decode_bcd(raw: bytes, decimals: int) -> Decimal:
    """Decode a 17-byte BCD value with ``decimals`` fractional digits.

    Raises ``ValueError`` when a digit is not in ``0..9`` after removing the
    complement of negative numbers.
    """
    if len(raw) != BCD_SIZE:
        raise ValueError("a Paradox BCD value is 17 bytes long")
    negative = not raw[0] & 0x80
    digits: list[int] = []
    for byte in raw[1:]:
        for nibble in (byte >> 4, byte & 0x0F):
            if negative:
                nibble ^= 0x0F
            if nibble > 9:
                raise ValueError(f"illegal BCD nibble 0x{nibble:X}")
            digits.append(nibble)
    magnitude = int("".join(map(str, digits)))
    result = Decimal(magnitude).scaleb(-decimals)
    return -result if negative else result


def encode_bcd(value: Decimal | int | str, decimals: int) -> bytes:
    """Encode a decimal as 17-byte BCD with exactly ``decimals`` fractional digits.

    The value must be representable without rounding; otherwise
    :class:`~paradoxdb.errors.EncodingError` is raised (no silent conversions).
    """
    if not 0 <= decimals <= BCD_DIGITS:
        raise EncodingError("BCD decimals must be between 0 and 32")
    try:
        dec = Decimal(value)
    except InvalidOperation as exc:
        raise EncodingError(f"{value!r} is not a decimal number") from exc
    if not dec.is_finite():
        raise EncodingError("only finite decimals can be encoded as BCD")
    scaled = dec.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise EncodingError(f"{dec} has more than {decimals} decimals")
    magnitude = abs(int(scaled))
    text = str(magnitude)
    if len(text) > BCD_DIGITS:
        raise EncodingError(f"{dec} needs more than {BCD_DIGITS} digits")
    text = text.rjust(BCD_DIGITS, "0")
    negative = dec.is_signed() and magnitude != 0
    packed = bytearray(BCD_SIZE)
    packed[0] = (_BCD_NEGATIVE if negative else _BCD_POSITIVE) | decimals
    for i in range(0, BCD_DIGITS, 2):
        byte = (int(text[i]) << 4) | int(text[i + 1])
        packed[1 + i // 2] = byte ^ 0xFF if negative else byte
    return bytes(packed)
