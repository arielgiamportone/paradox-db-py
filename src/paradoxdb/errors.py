"""Exception hierarchy of :mod:`paradoxdb`."""

from __future__ import annotations


class ParadoxError(Exception):
    """Base class for every error raised by the library."""


class FormatError(ParadoxError):
    """The file does not look like a coherent Paradox table or index."""


class EncryptedTableError(ParadoxError):
    """The table is encrypted and no decryptor was supplied (or writing was requested)."""


class WriteError(ParadoxError):
    """A record set cannot be written on top of the given template."""


class DuplicateKeyError(WriteError):
    """Two records share the same primary key."""


class EncodingError(ParadoxError):
    """A Python value cannot be represented in the requested Paradox field."""
