"""Encoders and decoders of the numeric and scalar types, including the round-trip property."""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from paradoxdb import numeric
from paradoxdb.errors import EncodingError
from paradoxdb.fields import FieldDef, FieldType
from paradoxdb.values import (
    ValueState,
    decode_value,
    encode_alpha,
    encode_bool,
    encode_date,
    encode_time,
    encode_timestamp,
)

# --------------------------------------------------------------------------- integers


@pytest.mark.parametrize(
    ("value", "size", "raw"),
    [
        (0, 2, b"\x80\x00"),
        (1, 2, b"\x80\x01"),
        (-1, 2, b"\x7f\xff"),
        (32767, 2, b"\xff\xff"),
        (-32768, 2, b"\x00\x00"),
        (0, 4, b"\x80\x00\x00\x00"),
        (-2, 4, b"\x7f\xff\xff\xfe"),
    ],
)
def test_int_known_vectors(value: int, size: int, raw: bytes) -> None:
    assert numeric.encode_int(value, size) == raw
    assert numeric.decode_int(raw) == value


def test_int_out_of_range_is_an_error() -> None:
    with pytest.raises(EncodingError):
        numeric.encode_int(40000, 2)


@given(
    st.integers(min_value=-32768, max_value=32767), st.integers(min_value=-32768, max_value=32767)
)
def test_int16_preserves_order(a: int, b: int) -> None:
    ra, rb = numeric.encode_int(a, 2), numeric.encode_int(b, 2)
    assert (ra < rb) == (a < b)
    assert numeric.decode_int(ra) == a


@given(st.integers(min_value=-(2**31), max_value=2**31 - 1))
def test_int32_round_trip(value: int) -> None:
    assert numeric.decode_int(numeric.encode_int(value, 4)) == value


# --------------------------------------------------------------------------- doubles


def test_double_sign_transformation() -> None:
    pos = numeric.encode_double(1.5)
    assert pos[0] & 0x80, "positive doubles carry bit 7 set"
    neg = numeric.encode_double(-1.5)
    assert not neg[0] & 0x80, "negative doubles have every byte inverted"
    assert numeric.decode_double(pos) == 1.5
    assert numeric.decode_double(neg) == -1.5


def test_double_zero_is_not_all_zero_bytes() -> None:
    assert numeric.encode_double(0.0) == b"\x80" + bytes(7)


def test_double_non_finite_rejected() -> None:
    for bad in (math.inf, -math.inf, math.nan):
        with pytest.raises(EncodingError):
            numeric.encode_double(bad)


@given(
    st.floats(allow_nan=False, allow_infinity=False).filter(lambda x: x != 0),
    st.floats(allow_nan=False, allow_infinity=False).filter(lambda x: x != 0),
)
def test_double_round_trip_and_order(a: float, b: float) -> None:
    ra, rb = numeric.encode_double(a), numeric.encode_double(b)
    assert numeric.decode_double(ra) == a
    assert (ra < rb) == (a < b)


# --------------------------------------------------------------------------- BCD


def test_bcd_sign_byte_and_digits() -> None:
    raw = numeric.encode_bcd(Decimal("123.45"), 2)
    assert raw[0] == 0xC0 | 2
    assert raw[-3:] == b"\x01\x23\x45"
    assert numeric.decode_bcd(raw, 2) == Decimal("123.45")
    neg = numeric.encode_bcd(Decimal("-123.45"), 2)
    assert neg[0] == 0x40 | 2
    assert neg[-3:] == bytes(b ^ 0xFF for b in b"\x01\x23\x45")
    assert numeric.decode_bcd(neg, 2) == Decimal("-123.45")


def test_bcd_decimals_come_from_the_descriptor_not_the_sign_byte() -> None:
    raw = bytearray(numeric.encode_bcd(Decimal("1.5"), 1))
    raw[0] = 0xC0 | 7  # lying sign byte
    assert numeric.decode_bcd(bytes(raw), 1) == Decimal("1.5")


def test_bcd_illegal_nibble_is_an_error() -> None:
    raw = bytearray(17)
    raw[0] = 0xC0
    raw[16] = 0x0F
    with pytest.raises(ValueError):
        numeric.decode_bcd(bytes(raw), 0)


def test_bcd_inexact_value_is_rejected() -> None:
    with pytest.raises(EncodingError):
        numeric.encode_bcd(Decimal("1.234"), 2)
    with pytest.raises(EncodingError):
        numeric.encode_bcd(Decimal("1e33"), 0)


@settings(max_examples=300)
@given(
    st.decimals(min_value=Decimal("-1e20"), max_value=Decimal("1e20"), places=6, allow_nan=False),
    st.integers(min_value=0, max_value=6),
)
def test_bcd_round_trip_property(value: Decimal, decimals: int) -> None:
    value = value.quantize(Decimal(1).scaleb(-decimals))
    raw = numeric.encode_bcd(value, decimals)
    expected_sign = 0x40 if value < 0 else 0xC0
    assert raw[0] == expected_sign | decimals
    assert numeric.decode_bcd(raw, decimals) == value


@given(
    st.decimals(min_value=Decimal("-1e12"), max_value=Decimal("1e12"), places=3, allow_nan=False),
    st.decimals(min_value=Decimal("-1e12"), max_value=Decimal("1e12"), places=3, allow_nan=False),
)
def test_bcd_preserves_order(a: Decimal, b: Decimal) -> None:
    ra, rb = numeric.encode_bcd(a, 3), numeric.encode_bcd(b, 3)
    assert (ra < rb) == (a < b)


# --------------------------------------------------------------------------- scalar encoders


def _fd(ftype: FieldType, size: int = 0) -> FieldDef:
    from paradoxdb.fields import physical_size

    return FieldDef("f", ftype, size, 0, physical_size(ftype, size))


@given(st.dates(min_value=dt.date(1, 1, 2)))
def test_date_round_trip(day: dt.date) -> None:
    cell = decode_value(_fd(FieldType.DATE), encode_date(day), "cp437")
    assert cell.state is ValueState.OK and cell.value == day


def test_date_ordinal_one_is_year_one() -> None:
    assert encode_date(dt.date(1, 1, 1)) == numeric.encode_int(1, 4)


@given(st.times().map(lambda t: t.replace(microsecond=(t.microsecond // 1000) * 1000)))
def test_time_round_trip(value: dt.time) -> None:
    cell = decode_value(_fd(FieldType.TIME), encode_time(value), "cp437")
    assert cell.state is ValueState.OK and cell.value == value


@given(
    st.datetimes(min_value=dt.datetime(1, 1, 2), max_value=dt.datetime(9999, 12, 31)).map(
        lambda t: t.replace(microsecond=(t.microsecond // 1000) * 1000)
    )
)
def test_timestamp_round_trip(value: dt.datetime) -> None:
    cell = decode_value(_fd(FieldType.TIMESTAMP), encode_timestamp(value), "cp437")
    assert cell.state is ValueState.OK and cell.value == value


def test_bool_bytes() -> None:
    assert encode_bool(True) == b"\x81"
    assert encode_bool(False) == b"\x80"


@pytest.mark.parametrize("codec", ["cp437", "cp850", "cp1252"])
@given(st.text(alphabet="ñÑáéíóúºªABC xyz", min_size=1, max_size=12))
def test_alpha_round_trip_any_code_page(codec: str, text: str) -> None:
    raw = encode_alpha(text, 12, codec)
    cell = decode_value(_fd(FieldType.ALPHA, 12), raw, codec)
    if text.rstrip("\0"):
        assert cell.state is ValueState.OK
        assert cell.value == text


def test_alpha_too_long_is_an_error_not_a_truncation() -> None:
    with pytest.raises(EncodingError):
        encode_alpha("abcdef", 3, "cp437")
