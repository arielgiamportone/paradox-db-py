"""Shared fixtures: synthetic table specifications used across the test-suite."""

from __future__ import annotations

import pytest

from paradoxdb.fields import FieldType
from paradoxdb.synthetic import FieldSpec, TableSpec


@pytest.fixture
def mixed_spec() -> TableSpec:
    """One field of every non-BLOB type, version 7 header, CP437, 2 KiB blocks."""
    return TableSpec(
        fields=(
            FieldSpec("Id", FieldType.LONG),
            FieldSpec("Label", FieldType.ALPHA, 20),
            FieldSpec("When", FieldType.DATE),
            FieldSpec("Qty", FieldType.SHORT),
            FieldSpec("Price", FieldType.CURRENCY),
            FieldSpec("Ratio", FieldType.NUMBER),
            FieldSpec("Flag", FieldType.LOGICAL),
            FieldSpec("Clock", FieldType.TIME),
            FieldSpec("Stamp", FieldType.TIMESTAMP),
            FieldSpec("Seq", FieldType.AUTOINCREMENT),
            FieldSpec("Amount", FieldType.BCD, 2),
            FieldSpec("Raw", FieldType.BYTES, 4),
        ),
        version=12,
        code_page=437,
        block_size_kib=2,
        key_fields=1,
        table_name="MIXED",
    )


@pytest.fixture
def memo_spec() -> TableSpec:
    return TableSpec(
        fields=(
            FieldSpec("Id", FieldType.LONG),
            FieldSpec("Note", FieldType.MEMO, 30),
            FieldSpec("Payload", FieldType.BLOB, 10),
        ),
        version=12,
        code_page=1252,
        key_fields=1,
        table_name="NOTES",
    )
