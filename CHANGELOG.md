# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-15

### Added

- Reader for Paradox `.DB` tables, versions 3.0 to 7 (headers with and
  without the 4.0+ extension), with per-table code page and per-cell state
  (`OK` / `NULL` / `INVALID`) plus raw bytes.
- Decoders and encoders for every field type: alpha, date, short, long,
  currency, number, logical, memo, formatted memo, BLOB, OLE, graphic,
  time, timestamp, autoincrement, BCD and bytes.
- Reader for `.MB` memo files: embedded, single-block and sub-allocated
  memos.
- Block-chain walker that returns every record present in the data blocks
  and reports the mismatch with the declared record count.
- Writer on top of a template header, producing new `.DB`, `.MB` and `.PX`
  files, with optional legacy sort order and primary-key uniqueness check.
- Primary index (`.PX`) builder and verifier.
- Table profiler (aggregates per table and field, SHA-256 fingerprints) and
  CSV / JSON export.
- `paradoxdb` command line: `profile`, `export`, `verify-index`.
- Synthetic table factory used by the test-suite and the examples.
- Optional `Decryptor` protocol for encrypted tables (the library never
  implements the cipher itself).

[Unreleased]: https://github.com/arielgiamportone/paradox-db-py/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/arielgiamportone/paradox-db-py/releases/tag/v0.1.0
