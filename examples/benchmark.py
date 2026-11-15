"""Reproducible measurements on synthetic tables.

Usage::

    python examples/benchmark.py [--rows 50000] [--memo-rows 5000]

Generates a keyed table with mixed field types, writes it with the library on
top of its own header, verifies the primary index and reports timings, sizes
and the .MB growth caused by single-block memos. Nothing is read from disk that
this script did not create.
"""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

from paradoxdb import open_table, read_data_blocks, verify_index, write_table
from paradoxdb.fields import FieldType
from paradoxdb.synthetic import FieldSpec, TableSpec, random_rows, write_synthetic_table
from paradoxdb.writer import WriteRecord


def _timed(label: str, fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    print(f"{label:<42} {time.perf_counter() - start:8.3f} s")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--memo-rows", type=int, default=5_000)
    args = parser.parse_args()

    spec = TableSpec(
        fields=(
            FieldSpec("Id", FieldType.LONG),
            FieldSpec("Code", FieldType.ALPHA, 12),
            FieldSpec("Day", FieldType.DATE),
            FieldSpec("Amount", FieldType.BCD, 2),
            FieldSpec("Price", FieldType.CURRENCY),
            FieldSpec("Flag", FieldType.LOGICAL),
            FieldSpec("Stamp", FieldType.TIMESTAMP),
        ),
        key_fields=1,
        block_size_kib=2,
    )
    memo_spec = TableSpec(
        fields=(FieldSpec("Id", FieldType.LONG), FieldSpec("Note", FieldType.MEMO, 40)),
        key_fields=1,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rows = random_rows(spec, args.rows, rng=2024, null_ratio=0.05)
        rows.sort(key=lambda r: r["Id"])
        files = _timed(
            f"generate {args.rows} rows (synthetic)",
            lambda: write_synthetic_table(root / "src", "bench", spec, rows, with_px_template=True),
        )
        table = _timed("open + decode every record", lambda: list(open_table(files.db).records()))
        assert len(table) == args.rows
        result = _timed(
            "write_table (.DB + .PX) on template",
            write_table,
            files.db,
            root / "out" / "bench.DB",
            [r.raw_bytes for r in table],
        )
        problems = _timed(
            "verify_index", verify_index, result.px_path, read_data_blocks(result.db_path)
        )
        print(f"{'index problems':<42} {len(problems):8d}")
        print(f"{'.PX levels':<42} {result.px_path.read_bytes()[0x20]:8d}")
        print(f"{'.DB size (MiB)':<42} {result.db_path.stat().st_size / 2**20:8.2f}")

        memo_rows = random_rows(memo_spec, args.memo_rows, rng=7, null_ratio=0.0)
        mfiles = write_synthetic_table(root / "memo", "memo", memo_spec, memo_rows)
        src = open_table(mfiles.db)
        records = [
            WriteRecord(r.raw_bytes, {1: r["Note"].value.encode(src.codec)}) for r in src.records()
        ]
        payload = sum(len(r.blobs[1]) for r in records)
        mres = _timed(
            f"write_table with {args.memo_rows} memos",
            write_table,
            mfiles.db,
            root / "mo" / "m.DB",
            records,
        )
        mb_size = mres.mb_path.stat().st_size
        print(f"{'memo payload (KiB)':<42} {payload / 1024:8.1f}")
        print(f"{'.MB written, single-block (KiB)':<42} {mb_size / 1024:8.1f}")
        print(f"{'.MB growth factor':<42} {mb_size / max(payload, 1):8.1f}")


if __name__ == "__main__":
    main()
