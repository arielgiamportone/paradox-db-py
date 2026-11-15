"""Header interpretation: code pages, extension, old versions, encryption and coherence."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from paradoxdb import open_table
from paradoxdb.errors import EncryptedTableError, FormatError
from paradoxdb.fields import FieldType
from paradoxdb.header import HeaderOffset, parse_header
from paradoxdb.synthetic import FieldSpec, TableSpec, build_table_bytes, encode_row


def test_cp437_header_with_enye_in_field_name(tmp_path: Path) -> None:
    spec = TableSpec(
        fields=(FieldSpec("Año", FieldType.SHORT), FieldSpec("Texto", FieldType.ALPHA, 10)),
        version=12,
        code_page=437,
    )
    (tmp_path / "t.DB").write_bytes(build_table_bytes(spec, [encode_row(spec, {"Año": 3})]))
    table = open_table(tmp_path / "t.DB")
    header = table.header
    assert header.code_page == 437 and header.codec == "cp437"
    assert [f.name for f in header.fields] == ["Año", "Texto"]
    assert header.encryption_key == 0 and not header.is_encrypted
    assert header.has_extension and header.descriptor_offset == 0x78
    assert header.version_label == "7.x"
    assert header.field("AÑO").name == "Año"  # case-insensitive lookup


def test_marker_at_0x25_does_not_mean_encrypted() -> None:
    spec = TableSpec(fields=(FieldSpec("A", FieldType.SHORT),), version=12)
    data = build_table_bytes(spec, [])
    assert struct.unpack_from("<I", data, HeaderOffset.LEGACY_ENCRYPTION)[0] == 0xFF00FF00
    assert not parse_header(data).is_encrypted


def test_old_version_without_extension() -> None:
    spec = TableSpec(
        fields=(FieldSpec("A", FieldType.SHORT), FieldSpec("B", FieldType.ALPHA, 6)),
        version=4,
        code_page=None,
    )
    data = build_table_bytes(spec, [encode_row(spec, {"A": -5, "B": "hola"})])
    header = parse_header(data, default_codec="cp850")
    assert not header.has_extension
    assert header.code_page is None and header.codec == "cp850"
    assert header.descriptor_offset == 0x58
    assert header.version_label == "3.5"
    assert [(f.type, f.byte_length) for f in header.fields] == [
        (FieldType.SHORT, 2),
        (FieldType.ALPHA, 6),
    ]


def test_legacy_key_at_0x25_means_encrypted_without_extension(tmp_path: Path) -> None:
    spec = TableSpec(
        fields=(FieldSpec("A", FieldType.SHORT),), version=4, code_page=None, encryption_key=0x1234
    )
    (tmp_path / "old.DB").write_bytes(build_table_bytes(spec, []))
    with pytest.raises(EncryptedTableError):
        open_table(tmp_path / "old.DB")


def test_incoherent_record_size_is_a_format_error() -> None:
    spec = TableSpec(fields=(FieldSpec("A", FieldType.SHORT), FieldSpec("B", FieldType.ALPHA, 6)))
    data = bytearray(build_table_bytes(spec, []))
    struct.pack_into("<H", data, HeaderOffset.RECORD_SIZE, spec.record_size + 1)
    with pytest.raises(FormatError, match="record size"):
        parse_header(bytes(data))


@pytest.mark.parametrize("offset", [HeaderOffset.RECORD_SIZE, HeaderOffset.FIELD_COUNT])
def test_zero_counters_are_format_errors(offset: int) -> None:
    spec = TableSpec(fields=(FieldSpec("A", FieldType.SHORT),))
    data = bytearray(build_table_bytes(spec, []))
    struct.pack_into("<H", data, offset, 0)
    with pytest.raises(FormatError):
        parse_header(bytes(data))


def test_zero_block_size_is_a_format_error() -> None:
    spec = TableSpec(fields=(FieldSpec("A", FieldType.SHORT),))
    data = bytearray(build_table_bytes(spec, []))
    data[HeaderOffset.BLOCK_SIZE_KIB] = 0
    with pytest.raises(FormatError):
        parse_header(bytes(data))


def test_truncated_file_is_a_format_error() -> None:
    spec = TableSpec(fields=(FieldSpec("A", FieldType.SHORT),))
    data = build_table_bytes(spec, [])
    with pytest.raises(FormatError):
        parse_header(data[:0x400])


def test_unknown_field_type_is_a_format_error() -> None:
    spec = TableSpec(fields=(FieldSpec("A", FieldType.SHORT),))
    data = bytearray(build_table_bytes(spec, []))
    data[0x78] = 0x7E
    with pytest.raises(FormatError, match="unknown field type"):
        parse_header(bytes(data))


def test_unknown_code_page_falls_back_to_default() -> None:
    spec = TableSpec(fields=(FieldSpec("A", FieldType.SHORT),), code_page=9999)
    header = parse_header(build_table_bytes(spec, []), default_codec="cp850")
    assert header.code_page == 9999 and header.codec == "cp850"


def test_opening_a_px_as_table_is_refused(tmp_path: Path, mixed_spec: TableSpec) -> None:
    from paradoxdb.synthetic import build_px_template

    (tmp_path / "x.PX").write_bytes(build_px_template(mixed_spec))
    with pytest.raises(FormatError):
        open_table(tmp_path / "x.PX")
