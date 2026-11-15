"""Opening a table: header, block chain, sibling ``.MB`` and decoded records."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .blocks import Anomaly, iter_blocks
from .errors import EncryptedTableError, FormatError
from .fields import FieldDef
from .header import TableHeader, parse_header
from .memo import MemoFile
from .values import CellValue, decode_value

__all__ = [
    "SECONDARY_INDEX_SUFFIXES",
    "Decryptor",
    "ParadoxTable",
    "Record",
    "find_secondary_indexes",
    "find_sibling",
    "open_table",
]


@dataclass(frozen=True, slots=True)
class Record:
    """One decoded record. ``cells`` is keyed by field name as written in the header."""

    block_no: int
    position: int
    raw_bytes: bytes
    cells: dict[str, CellValue]

    def get(self, name: str) -> CellValue:
        """Cell lookup that ignores case, since field names vary between installations."""
        try:
            return self.cells[name]
        except KeyError:
            wanted = name.casefold()
            for key, cell in self.cells.items():
                if key.casefold() == wanted:
                    return cell
            raise

    def __getitem__(self, name: str) -> CellValue:
        return self.get(name)

    def values(self) -> dict[str, Any]:
        """Python values only (``None`` for null and invalid cells). Lossy by design."""
        return {k: (c.value if c.is_ok else None) for k, c in self.cells.items()}


@runtime_checkable
class Decryptor(Protocol):
    """External component that turns an encrypted table into plain bytes.

    ``blob_indexes`` lists the positions of the BLOB-like fields. The result is
    the list of plain records and, per record, a mapping ``field index ->
    content`` (``None`` for a null BLOB). The library never implements the
    cipher itself.
    """

    def decrypt(
        self, path: Path, blob_indexes: list[int]
    ) -> tuple[list[bytes], list[dict[int, bytes | None]]]: ...


SECONDARY_INDEX_SUFFIXES = ("X", "Y", "XG", "YG")


def find_sibling(path: Path, extension: str) -> Path | None:
    """Find ``stem.extension`` next to ``path`` regardless of the case of the file name."""
    wanted = (path.stem + extension).casefold()
    if not path.parent.is_dir():
        return None
    for candidate in path.parent.iterdir():
        if candidate.is_file() and candidate.name.casefold() == wanted:
            return candidate
    return None


def find_secondary_indexes(path: Path) -> list[Path]:
    """Secondary index files (``.Xnn``, ``.Ynn``, ``.XGn``, ``.YGn``) of the table at ``path``."""
    stem = path.stem.casefold()
    found: list[Path] = []
    if not path.parent.is_dir():
        return found
    for candidate in path.parent.iterdir():
        if not candidate.is_file() or candidate.stem.casefold() != stem:
            continue
        if _is_secondary_index_suffix(candidate.suffix[1:].upper()):
            found.append(candidate)
    return sorted(found)


def _is_secondary_index_suffix(suffix: str) -> bool:
    if len(suffix) != 3:
        return False
    if suffix[0] in ("X", "Y") and suffix[1:].isdigit():  # .X00 .. .Y99
        return True
    return suffix[:2] in ("XG", "YG") and suffix[2].isdigit()  # .XG0 .. .YG9


class ParadoxTable:
    """A ``.DB`` file loaded in memory with its optional ``.MB`` sibling."""

    def __init__(
        self,
        path: Path,
        data: bytes,
        header: TableHeader,
        memo: MemoFile | None,
        *,
        decrypted: tuple[list[bytes], list[dict[int, bytes | None]]] | None = None,
    ) -> None:
        self.path = path
        self.data = data
        self.header = header
        self.memo = memo
        self._decrypted = decrypted
        self.anomalies: list[Anomaly] = []
        #: Set when a sibling .MB exists but cannot be read; BLOB cells are then INVALID.
        self.memo_problem: str | None = None
        self._blocks = None if decrypted is None else []
        self.secondary_indexes = find_secondary_indexes(path)

    # ------------------------------------------------------------------ metadata
    @property
    def fields(self) -> tuple[FieldDef, ...]:
        return self.header.fields

    @property
    def codec(self) -> str:
        return self.header.codec

    @property
    def declared_record_count(self) -> int:
        return self.header.declared_record_count

    # ------------------------------------------------------------------ blocks
    def _load_blocks(self) -> list:
        if self._blocks is None:
            self.anomalies = []
            self._blocks = list(iter_blocks(self.data, self.header, self.anomalies))
        return self._blocks

    def raw_records(self) -> Iterator[tuple[int, int, bytes]]:
        """``(block_no, position, raw_bytes)`` for every record found in the blocks."""
        if self._decrypted is not None:
            for position, raw in enumerate(self._decrypted[0]):
                yield 0, position, raw
            return
        for block in self._load_blocks():
            for position, raw in enumerate(block.records):
                yield block.number, position, raw

    def block_record_count(self) -> int:
        """Records actually present in the data blocks (not the declared count)."""
        if self._decrypted is not None:
            return len(self._decrypted[0])
        return sum(b.record_count for b in self._load_blocks())

    @property
    def block_count(self) -> int:
        if self._decrypted is not None:
            return 0
        return len(self._load_blocks())

    @property
    def header_mismatch(self) -> int:
        """Records found in blocks minus records declared in the header."""
        return self.block_record_count() - self.declared_record_count

    # ------------------------------------------------------------------ records
    def decode_record(self, raw: bytes, blobs: dict[int, bytes | None] | None = None) -> dict:
        cells: dict[str, CellValue] = {}
        for index, field in enumerate(self.header.fields):
            chunk = field.slice(raw)
            if blobs is not None and field.type.is_blob:
                cells[field.name] = decode_value(
                    field, chunk, self.codec, None, blob_content=blobs.get(index)
                )
            else:
                cells[field.name] = decode_value(field, chunk, self.codec, self.memo)
        return cells

    def records(self) -> Iterator[Record]:
        """Iterate over every record present in the blocks, in chain order."""
        if self._decrypted is not None:
            plain, blobs = self._decrypted
            for position, raw in enumerate(plain):
                yield Record(0, position, raw, self.decode_record(raw, blobs[position]))
            return
        for block_no, position, raw in self.raw_records():
            yield Record(block_no, position, raw, self.decode_record(raw))

    def __iter__(self) -> Iterator[Record]:
        return self.records()

    def __len__(self) -> int:
        return self.block_record_count()


def open_table(
    path: str | Path, default_codec: str = "cp437", decryptor: Decryptor | None = None
) -> ParadoxTable:
    """Load a ``.DB`` file.

    The header is interpreted, the sibling ``.MB`` (any letter case) is located
    and the table is ready to iterate. An encrypted table without a
    ``decryptor`` raises :class:`~paradoxdb.errors.EncryptedTableError`.
    """
    path = Path(path)
    data = path.read_bytes()
    header = parse_header(data, default_codec)
    if header.is_primary_index:
        raise FormatError(f"{path.name} is a primary index (.PX), not a table")
    memo_path = find_sibling(path, ".MB")
    memo = None
    memo_problem: str | None = None
    if memo_path is not None:
        try:
            memo = MemoFile.load(memo_path)
        except FormatError as exc:
            memo_problem = f"{memo_path.name}: {exc}; BLOB cells will be invalid"
    decrypted = None
    if header.is_encrypted:
        if decryptor is None:
            raise EncryptedTableError(
                f"{path.name} is encrypted (key 0x{header.encryption_key:08X}); a decryptor is "
                "required"
            )
        blob_indexes = [i for i, f in enumerate(header.fields) if f.type.is_blob]
        plain, blobs = decryptor.decrypt(path, blob_indexes)
        if len(blobs) != len(plain):
            raise FormatError("decryptor returned a BLOB mapping count different from records")
        for raw in plain:
            if len(raw) != header.record_size:
                raise FormatError("decryptor returned a record of the wrong size")
        decrypted = (list(plain), list(blobs))
    table = ParadoxTable(path, data, header, memo, decrypted=decrypted)
    if memo_problem is not None:
        table.memo_problem = memo_problem
    return table
