"""Profiles, exports and the command line."""

from __future__ import annotations

import csv
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

from paradoxdb import export_csv, export_json, profile_table
from paradoxdb.cli import main
from paradoxdb.fields import FieldType
from paradoxdb.synthetic import FieldSpec, TableSpec, build_table_bytes, encode_row


def _profile_table(tmp_path: Path) -> Path:
    spec = TableSpec(
        fields=(
            FieldSpec("Amount", FieldType.BCD, 2),
            FieldSpec("Day", FieldType.DATE),
            FieldSpec("Name", FieldType.ALPHA, 8),
        ),
        key_fields=0,
    )
    broken = bytearray(17)
    broken[0] = 0xC2
    broken[16] = 0xFF
    rows = [
        encode_row(spec, {"Amount": Decimal("10.50"), "Day": dt.date(2020, 1, 1), "Name": "a"}),
        encode_row(spec, {"Amount": Decimal("-0.50"), "Day": dt.date(2021, 6, 30), "Name": "bb"}),
        encode_row(spec, {"Amount": None, "Day": None, "Name": None}),
        bytes(broken) + encode_row(spec, {"Day": dt.date(2019, 5, 5), "Name": "cccc"})[17:],
    ]
    path = tmp_path / "prof.DB"
    path.write_bytes(build_table_bytes(spec, rows, declared_count=3))
    return path


def test_profile_aggregates(tmp_path: Path) -> None:
    profile = profile_table(_profile_table(tmp_path))
    assert profile.declared_records == 3 and profile.found_records == 4
    assert profile.header_mismatch == 1
    amount = profile.fields[0]
    assert (amount.ok, amount.null, amount.invalid) == (2, 1, 1)
    assert amount.sum == "10.00"
    assert amount.min == "-0.50" and amount.max == "10.50"
    assert "nibble" in next(iter(amount.invalid_reasons))
    day = profile.fields[1]
    assert (day.min, day.max) == ("2019-05-05", "2021-06-30")
    assert day.sum is None
    name = profile.fields[2]
    assert name.max_length == 4 and name.null == 1
    assert len(profile.sha256) == 64 and all(c in "0123456789abcdef" for c in profile.sha256)
    assert len(amount.sha256) == 64
    payload = json.loads(profile.to_json())
    assert payload["fields"][0]["ok"] == 2
    assert payload["anomalies"] == [] and payload["encrypted"] is False


def test_export_json_and_csv(tmp_path: Path) -> None:
    path = _profile_table(tmp_path)
    count = export_json(path, tmp_path / "out.json")
    assert count == 4
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    cells = payload["records"][3]["cells"]
    assert cells["Amount"]["state"] == "INVALID" and "nibble" in cells["Amount"]["reason"]
    assert payload["records"][2]["cells"]["Day"] == {"value": None, "state": "NULL", "reason": None}
    assert payload["records"][0]["cells"]["Amount"]["value"] == "10.50"

    export_csv(path, tmp_path / "out.csv")
    with (tmp_path / "out.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0][:5] == ["block", "position", "Amount", "Amount.state", "Amount.reason"]
    assert rows[1][2:5] == ["10.50", "OK", ""]
    assert rows[3][2:5] == ["", "NULL", ""]
    assert rows[4][3] == "INVALID"


def test_cli_profile_export_verify(tmp_path: Path, capsys) -> None:
    _profile_table(tmp_path)
    out = tmp_path / "profile.json"
    assert main(["profile", str(tmp_path), "--output", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["tables"][0]["found_records"] == 4 and payload["errors"] == {}
    assert main(["profile", str(tmp_path)]) == 0
    assert '"found_records": 4' in capsys.readouterr().out

    assert main(["export", str(tmp_path / "prof.DB"), "--format", "json"]) == 0
    assert (tmp_path / "prof.json").exists()
    assert (
        main(["export", str(tmp_path / "prof.DB"), "-f", "csv", "-o", str(tmp_path / "x.csv")]) == 0
    )
    assert (tmp_path / "x.csv").exists()

    assert main(["verify-index", str(tmp_path / "prof.DB")]) == 2  # no .PX


def test_cli_verify_index_ok_and_broken(tmp_path: Path, capsys) -> None:
    from paradoxdb import open_table, write_table
    from paradoxdb.synthetic import write_synthetic_table

    spec = TableSpec(fields=(FieldSpec("K", FieldType.LONG),), key_fields=1, block_size_kib=1)
    files = write_synthetic_table(
        tmp_path / "src", "k", spec, [{"K": i} for i in range(500)], with_px_template=True
    )
    result = write_table(
        files.db, tmp_path / "out" / "k.DB", [r.raw_bytes for r in open_table(files.db)]
    )
    assert main(["verify-index", str(result.db_path)]) == 0
    px = bytearray(result.px_path.read_bytes())
    px[-1] ^= 0xFF
    px[0x06] = 0x7F
    result.px_path.write_bytes(bytes(px))
    assert main(["verify-index", str(result.db_path)]) == 1
    assert "problem" in capsys.readouterr().out


def test_cli_reports_errors_for_unreadable_tables(tmp_path: Path, capsys) -> None:
    (tmp_path / "junk.db").write_bytes(b"\0" * 10)
    assert main(["profile", str(tmp_path)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert "junk.db" in payload["errors"]
    assert main(["export", str(tmp_path / "junk.db")]) == 1
    assert "error:" in capsys.readouterr().err
