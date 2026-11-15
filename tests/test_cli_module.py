"""``python -m paradoxdb`` works and the parser exposes the three documented commands."""

from __future__ import annotations

import subprocess
import sys

from paradoxdb.cli import build_parser


def test_module_entry_point_prints_version() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "paradoxdb", "--version"], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip().startswith("paradoxdb 0.1.0")


def test_parser_commands() -> None:
    parser = build_parser()
    subparsers = next(a for a in parser._actions if a.dest == "command")
    assert set(subparsers.choices) == {"profile", "export", "verify-index"}
    ns = parser.parse_args(["profile", ".", "--decrypt"])
    assert ns.decrypt == ""
    ns = parser.parse_args(["profile", ".", "--decrypt", "pkg.mod:Thing"])
    assert ns.decrypt == "pkg.mod:Thing"
    ns = parser.parse_args(["export", "t.DB", "-f", "json"])
    assert ns.format == "json"
