"""Source profiles: per-table and per-field aggregates that allow reconciliation without
exposing values, plus CSV / JSON exports with state and reason per cell."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import ParadoxError
from .fields import FieldDef, FieldType
from .table import Decryptor, ParadoxTable, open_table
from .values import CellValue, ValueState

__all__ = [
    "FieldProfile",
    "TableProfile",
    "export_csv",
    "export_json",
    "profile_directory",
    "profile_table",
    "to_jsonable",
]

_RANGE_TYPES = frozenset(
    {
        FieldType.SHORT,
        FieldType.LONG,
        FieldType.AUTOINCREMENT,
        FieldType.CURRENCY,
        FieldType.NUMBER,
        FieldType.BCD,
        FieldType.DATE,
        FieldType.TIME,
        FieldType.TIMESTAMP,
    }
)


def to_jsonable(value: Any) -> Any:
    """Turn a decoded cell value into something :mod:`json` can serialise."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray):
        return value.hex()
    return str(value)


@dataclass
class FieldProfile:
    name: str
    type: str
    size_byte: int
    byte_length: int
    ok: int = 0
    null: int = 0
    invalid: int = 0
    sum: str | None = None
    min: str | None = None
    max: str | None = None
    max_length: int | None = None
    sha256: str = ""
    invalid_reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class TableProfile:
    path: str
    table_name: str
    version: str
    code_page: int | None
    codec: str
    encrypted: bool
    record_size: int
    block_size: int
    header_size: int
    declared_records: int
    found_records: int
    header_mismatch: int
    blocks: int
    anomalies: list[str]
    secondary_indexes: list[str]
    memo_file: str | None
    sha256: str
    fields: list[FieldProfile]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, **kwargs: Any) -> str:
        kwargs.setdefault("indent", 2)
        kwargs.setdefault("ensure_ascii", False)
        return json.dumps(self.to_dict(), **kwargs)


class _FieldAccumulator:
    def __init__(self, fd: FieldDef) -> None:
        self.fd = fd
        self.ok = self.null = self.invalid = 0
        self.total: Decimal | int | None = None
        self.min: Any = None
        self.max: Any = None
        self.max_length: int | None = 0 if fd.type in (FieldType.ALPHA, FieldType.BYTES) else None
        if fd.type.is_blob:
            self.max_length = 0
        self.hash = hashlib.sha256()
        self.reasons: Counter[str] = Counter()

    def add(self, cell: CellValue) -> None:
        self.hash.update(cell.raw_bytes)
        if cell.state is ValueState.NULL:
            self.null += 1
            return
        if cell.state is ValueState.INVALID:
            self.invalid += 1
            self.reasons[cell.reason or "unknown"] += 1
            return
        self.ok += 1
        value = cell.value
        if self.fd.type.is_numeric:
            self.total = value if self.total is None else self.total + value
        if self.fd.type in _RANGE_TYPES:
            if self.min is None or value < self.min:
                self.min = value
            if self.max is None or value > self.max:
                self.max = value
        if self.max_length is not None:
            self.max_length = max(self.max_length, len(value))

    def result(self) -> FieldProfile:
        return FieldProfile(
            name=self.fd.name,
            type=self.fd.type.name,
            size_byte=self.fd.size_byte,
            byte_length=self.fd.byte_length,
            ok=self.ok,
            null=self.null,
            invalid=self.invalid,
            sum=None if self.total is None else str(self.total),
            min=to_jsonable(self.min),
            max=to_jsonable(self.max),
            max_length=self.max_length,
            sha256=self.hash.hexdigest(),
            invalid_reasons=dict(self.reasons.most_common(10)),
        )


def profile_table(
    path: str | Path, decryptor: Decryptor | None = None, default_codec: str = "cp437"
) -> TableProfile:
    """Aggregate a table: counts per state, sums and ranges of OK values, SHA-256 fingerprints."""
    table = open_table(path, default_codec=default_codec, decryptor=decryptor)
    return profile_open_table(table)


def profile_open_table(table: ParadoxTable) -> TableProfile:
    header = table.header
    accumulators = [_FieldAccumulator(fd) for fd in header.fields]
    table_hash = hashlib.sha256()
    found = 0
    for record in table.records():
        found += 1
        table_hash.update(record.raw_bytes)
        for acc, fd in zip(accumulators, header.fields, strict=True):
            acc.add(record.cells[fd.name])
    return TableProfile(
        path=str(table.path),
        table_name=header.table_name,
        version=header.version_label,
        code_page=header.code_page,
        codec=header.codec,
        encrypted=header.is_encrypted,
        record_size=header.record_size,
        block_size=header.block_size,
        header_size=header.header_size,
        declared_records=header.declared_record_count,
        found_records=found,
        header_mismatch=found - header.declared_record_count,
        blocks=table.block_count,
        anomalies=[f"block {a.block_no}: {a.message}" for a in table.anomalies],
        secondary_indexes=[p.name for p in table.secondary_indexes],
        memo_file=None if table.memo is None or table.memo.path is None else table.memo.path.name,
        sha256=table_hash.hexdigest(),
        fields=[acc.result() for acc in accumulators],
    )


def profile_directory(
    directory: str | Path, decryptor: Decryptor | None = None, default_codec: str = "cp437"
) -> tuple[list[TableProfile], dict[str, str]]:
    """Profile every ``.DB`` (any letter case) in ``directory``.

    Returns the profiles and a mapping ``file name -> error`` for the tables
    that could not be opened (encrypted without decryptor, bad format...).
    """
    directory = Path(directory)
    profiles: list[TableProfile] = []
    errors: dict[str, str] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.casefold() != ".db":
            continue
        try:
            profiles.append(profile_table(path, decryptor=decryptor, default_codec=default_codec))
        except (ParadoxError, OSError) as exc:
            errors[path.name] = str(exc)
    return profiles, errors


# --------------------------------------------------------------------------- export


def _as_table(source: ParadoxTable | str | Path, decryptor: Decryptor | None) -> ParadoxTable:
    if isinstance(source, ParadoxTable):
        return source
    return open_table(source, decryptor=decryptor)


def export_json(
    source: ParadoxTable | str | Path, dest: str | Path, *, decryptor: Decryptor | None = None
) -> int:
    """Dump every record as JSON with value, state and reason per cell. Returns the record count."""
    table = _as_table(source, decryptor)
    rows = []
    for record in table.records():
        rows.append(
            {
                "block": record.block_no,
                "position": record.position,
                "cells": {
                    name: {
                        "value": to_jsonable(cell.value),
                        "state": cell.state.value,
                        "reason": cell.reason,
                    }
                    for name, cell in record.cells.items()
                },
            }
        )
    payload = {
        "table": table.header.table_name,
        "path": str(table.path),
        "declared_records": table.declared_record_count,
        "found_records": len(rows),
        "fields": [
            {"name": f.name, "type": f.type.name, "size": f.size_byte} for f in table.fields
        ],
        "records": rows,
    }
    Path(dest).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(rows)


def export_csv(
    source: ParadoxTable | str | Path, dest: str | Path, *, decryptor: Decryptor | None = None
) -> int:
    """Dump every record as CSV: for each field ``name``, ``name.state`` and ``name.reason``."""
    table = _as_table(source, decryptor)
    names = [f.name for f in table.fields]
    header = ["block", "position"]
    for name in names:
        header.extend((name, f"{name}.state", f"{name}.reason"))
    count = 0
    with Path(dest).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for record in table.records():
            row: list[Any] = [record.block_no, record.position]
            for name in names:
                cell = record.cells[name]
                value = to_jsonable(cell.value)
                row.extend(("" if value is None else value, cell.state.value, cell.reason or ""))
            writer.writerow(row)
            count += 1
    return count
