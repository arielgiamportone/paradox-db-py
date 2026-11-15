"""Memo / BLOB file (``.MB``) layout and reader.

Inside the record a BLOB-like field is made of a *leader* (the first
``field_length - 10`` bytes of the content) followed by a 10-byte pointer:
offset ``u32``, length ``u32`` and modification counter ``u16``, little-endian.

Inside the ``.MB`` file everything is addressed in 4096-byte blocks. The first
block is the file header. A pointer's low byte selects the storage kind:
``0xFF`` means a *single block* entry (type 2), any other value is the index of
an entry in a *sub-allocated* block (type 3).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .errors import FormatError

__all__ = [
    "MB_BLOCK_SIZE",
    "SINGLE_BLOCK_INDEX",
    "SUBALLOC_DATA_START",
    "SUBALLOC_ENTRY_SIZE",
    "SUBALLOC_FIRST_ENTRY",
    "SUBALLOC_MAX_ENTRIES",
    "MemoFile",
    "MemoPointer",
]

MB_BLOCK_SIZE = 4096
SINGLE_BLOCK_INDEX = 0xFF
SINGLE_BLOCK_TYPE = 2
SUBALLOC_BLOCK_TYPE = 3
SINGLE_BLOCK_HEADER = struct.Struct("<BHIH")  # type, block count, length, counter
SUBALLOC_FIRST_ENTRY = 12
SUBALLOC_ENTRY_SIZE = 5
SUBALLOC_MAX_ENTRIES = 64
SUBALLOC_UNIT = 16
SUBALLOC_DATA_START = 0x150
POINTER_STRUCT = struct.Struct("<IIH")
POINTER_SIZE = POINTER_STRUCT.size


@dataclass(frozen=True, slots=True)
class MemoPointer:
    """The 10-byte pointer that closes a BLOB-like field."""

    offset: int
    length: int
    modcount: int

    @classmethod
    def unpack(cls, raw: bytes) -> MemoPointer:
        if len(raw) != POINTER_SIZE:
            raise ValueError("a memo pointer is 10 bytes long")
        return cls(*POINTER_STRUCT.unpack(raw))

    def pack(self) -> bytes:
        return POINTER_STRUCT.pack(self.offset, self.length, self.modcount)

    @property
    def block_start(self) -> int:
        return self.offset & 0xFFFFFF00

    @property
    def index(self) -> int:
        return self.offset & 0xFF

    @property
    def is_null(self) -> bool:
        return self.length == 0

    def is_embedded(self, leader_length: int) -> bool:
        """Content fits in the leader: offset 0 and length not longer than the leader."""
        return self.offset == 0 and self.length <= leader_length


class MemoFile:
    """In-memory view of a ``.MB`` file."""

    def __init__(self, data: bytes, path: Path | None = None) -> None:
        if len(data) < MB_BLOCK_SIZE:
            raise FormatError("memo file shorter than its header block")
        self.data = data
        self.path = path

    @classmethod
    def load(cls, path: str | Path) -> MemoFile:
        path = Path(path)
        return cls(path.read_bytes(), path)

    @property
    def header_block(self) -> bytes:
        """First 4 KiB of the file; the writer copies it verbatim."""
        return self.data[:MB_BLOCK_SIZE]

    def read(self, pointer: int | MemoPointer, length: int | None = None) -> bytes:
        """Return the content addressed by ``pointer`` and check it is ``length`` bytes long.

        Raises :class:`~paradoxdb.errors.FormatError` with a reason whenever the
        pointer, the block header or the entry are incoherent.
        """
        if isinstance(pointer, MemoPointer):
            offset = pointer.offset
            if length is None:
                length = pointer.length
        else:
            offset = pointer
            if length is None:
                raise ValueError("length is required with an integer pointer")
        start = offset & 0xFFFFFF00
        index = offset & 0xFF
        if start >= len(self.data) or start < MB_BLOCK_SIZE:
            raise FormatError(f"memo block at 0x{start:X} is outside the .MB file")
        kind = self.data[start]
        if index == SINGLE_BLOCK_INDEX:
            return self._read_single(start, kind, length)
        return self._read_suballocated(start, kind, index, length)

    def _read_single(self, start: int, kind: int, length: int) -> bytes:
        if kind != SINGLE_BLOCK_TYPE:
            raise FormatError(f"expected a single-block memo (type 2) at 0x{start:X}, found {kind}")
        _, _blocks, stored_length, _counter = SINGLE_BLOCK_HEADER.unpack_from(self.data, start)
        if stored_length != length:
            raise FormatError(
                f"memo length mismatch at 0x{start:X}: pointer says {length}, block says "
                f"{stored_length}"
            )
        begin = start + SINGLE_BLOCK_HEADER.size
        end = begin + length
        if end > len(self.data):
            raise FormatError(f"memo at 0x{start:X} runs past the end of the .MB file")
        return self.data[begin:end]

    def _read_suballocated(self, start: int, kind: int, index: int, length: int) -> bytes:
        if kind != SUBALLOC_BLOCK_TYPE:
            raise FormatError(
                f"expected a sub-allocated memo block (type 3) at 0x{start:X}, found {kind}"
            )
        if index >= SUBALLOC_MAX_ENTRIES:
            raise FormatError(f"sub-allocation index {index} out of range")
        entry = start + SUBALLOC_FIRST_ENTRY + index * SUBALLOC_ENTRY_SIZE
        unit_offset, units, _counter, tail = struct.unpack_from("<BBHB", self.data, entry)
        if units == 0:
            raise FormatError(f"sub-allocation entry {index} at 0x{start:X} is empty")
        size = (units - 1) * SUBALLOC_UNIT + tail
        if size != length:
            raise FormatError(
                f"memo length mismatch at 0x{start:X}[{index}]: pointer says {length}, "
                f"entry says {size}"
            )
        begin = start + unit_offset * SUBALLOC_UNIT
        end = begin + length
        if end > len(self.data):
            raise FormatError(f"memo entry {index} at 0x{start:X} runs past the end of the file")
        return self.data[begin:end]
