"""Command line interface: ``paradoxdb profile | export | verify-index``."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Sequence
from importlib.metadata import entry_points
from pathlib import Path

from . import __version__
from .errors import ParadoxError
from .index import read_data_blocks, verify_index
from .profile import export_csv, export_json, profile_directory
from .table import Decryptor, find_sibling

DECRYPTOR_ENTRY_POINT_GROUP = "paradoxdb.decryptors"


def load_decryptor(spec: str | None) -> Decryptor:
    """Instantiate a decryptor from ``module:attribute`` or from the first registered entry point.

    The attribute may be a ready object or a zero-argument factory (class or
    function) returning one.
    """
    if spec:
        module_name, _, attr = spec.partition(":")
        if not attr:
            raise SystemExit("--decrypt expects MODULE:ATTRIBUTE")
        target = getattr(importlib.import_module(module_name), attr)
    else:
        candidates = list(entry_points(group=DECRYPTOR_ENTRY_POINT_GROUP))
        if not candidates:
            raise SystemExit(
                f"no decryptor installed: register one under the {DECRYPTOR_ENTRY_POINT_GROUP!r} "
                "entry-point group or pass --decrypt MODULE:ATTRIBUTE"
            )
        target = candidates[0].load()
    instance = target() if callable(target) and not hasattr(target, "decrypt") else target
    if not hasattr(instance, "decrypt"):
        raise SystemExit(f"{spec or 'entry point'} does not provide a decrypt() method")
    return instance


def _cmd_profile(args: argparse.Namespace) -> int:
    decryptor = load_decryptor(args.decrypt) if args.decrypt is not None else None
    profiles, errors = profile_directory(
        args.directory, decryptor=decryptor, default_codec=args.codec
    )
    payload = {
        "tool": f"paradoxdb {__version__}",
        "directory": str(args.directory),
        "tables": [p.to_dict() for p in profiles],
        "errors": errors,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"{len(profiles)} table(s) profiled, {len(errors)} error(s) -> {args.output}")
    else:
        print(text)
    return 1 if errors and not profiles else 0


def _cmd_export(args: argparse.Namespace) -> int:
    decryptor = load_decryptor(args.decrypt) if args.decrypt is not None else None
    table = Path(args.table)
    output = Path(args.output) if args.output else table.with_suffix(f".{args.format}")
    if args.format == "csv":
        count = export_csv(table, output, decryptor=decryptor)
    else:
        count = export_json(table, output, decryptor=decryptor)
    print(f"{count} record(s) -> {output}")
    return 0


def _cmd_verify_index(args: argparse.Namespace) -> int:
    table = Path(args.table)
    px = find_sibling(table, ".PX")
    if px is None:
        print(f"{table.name}: no .PX file found next to the table", file=sys.stderr)
        return 2
    problems = verify_index(px, read_data_blocks(table))
    if problems:
        print(f"{px.name}: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"{px.name}: index is consistent with {table.name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paradoxdb", description="Read, profile and check Paradox tables."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("profile", help="aggregate every .DB in a directory into a JSON profile")
    p.add_argument("directory", type=Path)
    p.add_argument("--output", "-o", help="write the JSON here instead of stdout")
    p.add_argument(
        "--decrypt",
        nargs="?",
        const="",
        default=None,
        metavar="MODULE:ATTR",
        help="use an external decryptor (entry point if no value is given)",
    )
    p.add_argument("--codec", default="cp437", help="fallback code page (default cp437)")
    p.set_defaults(func=_cmd_profile)

    e = sub.add_parser("export", help="dump a table with value, state and reason per cell")
    e.add_argument("table", help="path to the .DB file")
    e.add_argument("--format", "-f", choices=("csv", "json"), default="csv")
    e.add_argument("--output", "-o", help="destination file (default: table name + format)")
    e.add_argument("--decrypt", nargs="?", const="", default=None, metavar="MODULE:ATTR")
    e.set_defaults(func=_cmd_export)

    v = sub.add_parser("verify-index", help="check the .PX of a table against its data blocks")
    v.add_argument("table", help="path to the .DB file")
    v.set_defaults(func=_cmd_verify_index)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ParadoxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
