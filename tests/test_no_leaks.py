"""Leak guard for the public clean-room repository.

The project must never contain the names of the company's clients, people,
internal products, legacy schema objects or file-system paths of the private
source. This test sweeps every file in the working tree plus the full git
history (``git log -p``) and fails if any forbidden term appears as a whole
word, case-insensitively.

The term list is stored base64-encoded so that the list itself does not become
a readable leak, and this file is excluded from the sweep. Two attribution
sentences are explicitly allowed (removed before scanning) because the
publication rules require them verbatim in the README.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()

_TERMS_B64 = (
    "WyJCcnVnYXJvbGFzIiwgIkNlbnR1cmlvbiIsICJNQ00iLCAiUG9uZGVyVHJhZGUiLCAiUG9uZGVy"
    "IFRyYWRlIiwgIlJvYmFtYSIsICJTb3BlbGEiLCAiT2xhYmVycmlhIiwgIkFpemFybmF6YWJhbCIs"
    "ICJaYW11ZGlvIiwgIkZpcmdhcyIsICJTYW4gQmFydG9sb23DqSIsICJTYW4gQmFydG9sb21lIiwg"
    "IlRpcmFqYW5hIiwgIlNhbkJhcnRvbG9tZVRpcmFqYW5hIiwgIlNhbiBBZ3VzdMOtbiBkZSBHdWFk"
    "YWxpeCIsICJTYW4gQWd1c3RpbiBkZSBHdWFkYWxpeCIsICJHdWFkYWxpeCIsICJNZXRybyBkZSBN"
    "YWRyaWQiLCAiQUVOQSIsICJBZ8OobmNpYSBkZSBSZXNpZHVzIiwgIkFnZW5jaWEgZGUgUmVzaWR1"
    "cyIsICJEaXB1dGFjacOzbiBkZSBTb3JpYSIsICJEaXB1dGFjaW9uIGRlIFNvcmlhIiwgIkVVSVBP"
    "IiwgIkJhcmFqYXMiLCAiTUFEIDY5LzIzIiwgIk1BRC0xMTIiLCAiT1QxIiwgIkludGVydmFsb3Ii"
    "LCAiSW50ZXJ2YWxvciBEYXRhIiwgIkludGVydmFsb3IgQ29uc3VsdGluZyBHcm91cCIsICJJbnRl"
    "cnZhbG9yLURhdGEiLCAiaW50ZXJ2YWxvci5lcyIsICJpbnRlcnZhbG9yc29mdCIsICJpbnRlcnZh"
    "bG9yY29uc3VsdGluZ2dyb3VwLnNoYXJlcG9pbnQuY29tIiwgIk1lZ2FzaXN0ZW1hcyIsICJNYW5z"
    "aXMiLCAiSW5nZXJ0ZWMiLCAiQURPSyIsICJEYXRhU3RhciIsICJnZXRwbHVnZ2VkIiwgIkFyaWVs"
    "IiwgIkdpYW1wb3J0b25lIiwgIkdpYW1wb3J0ZSIsICJBbm55IiwgIkNsYXVkaW8iLCAiQWxlamFu"
    "ZHJvIE1hbG5pcyIsICJNYWxuaXMiLCAiQWFyw7NuIFPDoW5jaGV6IiwgIkFhcm9uIFNhbmNoZXoi"
    "LCAiTnVyaWEiLCAiSnVsacOhbiIsICJKdWxpYW4iLCAiVmljdG9yaWEiLCAiRXZhIiwgIkNyaXN0"
    "aW5hIENhcnJhc2NvIiwgIkZlcm5hbmRvIE1lbGUiLCAiUGlsYXIgQ2hpdmEiLCAiRWxlbmEgQmFn"
    "YXJpYSIsICJJTlRFUlZBTCIsICJJTlRFUkRBVEEiLCAiQVlVTkRBVEEiLCAiSU5WQVlVTlQiLCAi"
    "S0FUSEFSU0lEIiwgIkludkRhdGEiLCAiSW52TGVucyIsICJBc3NldERhdGEiLCAiQXNzZXRWaXNp"
    "b24iLCAiTGljRGF0YSIsICJQTFVHIiwgImludGVydmFsLXB5dGhvbiIsICJpbnZlbnRhcmlvLW11"
    "bmljaXBhbCIsICJjYWxjMzIxLnBhcyIsICJJbnRlcjEucGFzIiwgIkludGVyMjciLCAiSW50ZXI1"
    "NiIsICJBY2Nlc29zIiwgIkNvbnRyb2wiLCAiRW1wcmVzYXMiLCAiZWxlbWVudG9zIiwgIkJhamFz"
    "IiwgIk1vZGlmaWNhY2lvbmVzIiwgIk1vZGlmaWNhY2lvbmVhIiwgInRpcG9zIiwgIlBsYW5lcyIs"
    "ICJEZXNwbGFuIiwgImRlZmNhbXBvcyIsICJkZWZjb25maWciLCAiQ3VlbnRhcyIsICJpbm11ZWJs"
    "ZXMiLCAiSU1BR0VORVMiLCAiYm9uaWZfaWRpIiwgIkZhY3R1cmFzIiwgIkF5dW4iLCAiRXBpcyIs"
    "ICJFcGlnRGVzY3JpcCIsICJBw7FvX0ZhYnIiLCAiQW5vX0ZhYnIiLCAiRV9WX0hpc3QiLCAiTWF0"
    "cmljdWxhIiwgIkFtb3JfYyIsICJBbW9yX2YiLCAiRl9jb21wcmEiLCAiRl9pbnN0IiwgIkhpc3RS"
    "ZWd1bCIsICJDb3N0ZVJlcG8iLCAiU2VndW5kYV9NYW5vIiwgIk9ic2VyIiwgIkt3IiwgIkM6XFxV"
    "c2VycyIsICJFOlxcSW50ZXJEYXRhIiwgIkRlc2t0b3BcXElOVEVSVkFMIiwgIk9uZURyaXZlIC0g"
    "Il0="
)

_ALLOWED_B64 = (
    "WyJQYXRyb25lcyBkZXNhcnJvbGxhZG9zIGR1cmFudGUgbWkgdHJhYmFqbyBlbiBJbnRlcnZhbG9y"
    "IERhdGEgeSBwdWJsaWNhZG9zIGNvbiBhdXRvcml6YWNpw7NuLiBJbXBsZW1lbnRhY2nDs24gaW5k"
    "ZXBlbmRpZW50ZSAoY2xlYW4tcm9vbSkgY29uIGRhdG9zIHNpbnTDqXRpY29zOyBubyBjb250aWVu"
    "ZSBjw7NkaWdvIG5pIGRhdG9zIGRlIGxhIGVtcHJlc2EgbmkgZGUgc3VzIGNsaWVudGVzLiIsICJQ"
    "YXR0ZXJucyBkZXZlbG9wZWQgZHVyaW5nIG15IHdvcmsgYXQgSW50ZXJ2YWxvciBEYXRhIGFuZCBw"
    "dWJsaXNoZWQgd2l0aCBwZXJtaXNzaW9uLiBJbmRlcGVuZGVudCBjbGVhbi1yb29tIGltcGxlbWVu"
    "dGF0aW9uIHdpdGggc3ludGhldGljIGRhdGE7IGl0IGNvbnRhaW5zIG5vIGNvZGUgb3IgZGF0YSBm"
    "cm9tIHRoZSBjb21wYW55IG9yIGl0cyBjbGllbnRzLiJd"
)

# Git trailers and metadata lines that legitimately contain personal handles or
# session identifiers and are not leaks of the private source.
_IGNORED_LINE_PREFIXES = ("Author:", "Co-Authored-By:", "Claude-Session:")

# The public GitHub handle of the repository owner (base64, like the terms). It
# is the only place where the owner's name may appear (package metadata,
# licence, repository URLs); the forbidden list still bans the name and surname
# as stand-alone words, which is how the private source leaks them (user paths).
_ALLOWED_HANDLE_B64 = "YXJpZWxnaWFtcG9ydG9uZQ=="


def _decode(blob: str) -> list[str]:
    return json.loads(base64.b64decode(blob).decode("utf-8"))


def forbidden_terms() -> list[str]:
    return _decode(_TERMS_B64)


def allowed_phrases() -> list[str]:
    return _decode(_ALLOWED_B64)


def _pattern(term: str) -> re.Pattern[str]:
    # Whole-word match: the term must not be glued to other word characters.
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)


def _scrub(text: str) -> str:
    for phrase in allowed_phrases():
        text = text.replace(phrase, "")
    handle = base64.b64decode(_ALLOWED_HANDLE_B64).decode("ascii")
    text = re.sub(re.escape(handle), "", text, flags=re.IGNORECASE)
    lines = [
        line
        for line in text.splitlines()
        if not line.lstrip("+- ").startswith(_IGNORED_LINE_PREFIXES)
    ]
    return "\n".join(lines)


def _tracked_and_untracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    paths = [REPO_ROOT / p.decode("utf-8") for p in out.split(b"\0") if p]
    return [p for p in paths if p.is_file() and p.resolve() != THIS_FILE]


def _git_history() -> str:
    try:
        out = subprocess.run(
            ["git", "log", "-p", "--all", "--no-color"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        ).stdout
    except subprocess.CalledProcessError:  # pragma: no cover - empty repository
        return ""
    return out.decode("utf-8", errors="ignore")


def _find_hits(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    text = _scrub(text)
    hits: list[str] = []
    for term, pattern in patterns:
        match = pattern.search(text)
        if match:
            line_no = text.count("\n", 0, match.start()) + 1
            hits.append(f"term #{len(term)}:{term[:1]}… at line {line_no}")
    return hits


@pytest.fixture(scope="module")
def patterns() -> list[tuple[str, re.Pattern[str]]]:
    terms = forbidden_terms()
    assert len(terms) > 50
    return [(t, _pattern(t)) for t in terms]


def test_term_list_is_obfuscated_in_this_file() -> None:
    source = THIS_FILE.read_text(encoding="utf-8")
    body = source.split("_TERMS_B64", 1)[1]
    for term in forbidden_terms():
        assert term.lower() not in body.lower(), "term list must stay obfuscated"


def test_working_tree_has_no_forbidden_terms(patterns) -> None:
    offenders: dict[str, list[str]] = {}
    for path in _tracked_and_untracked_files():
        text = path.read_bytes().decode("utf-8", errors="ignore")
        hits = _find_hits(text, patterns)
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits
    assert not offenders, f"forbidden terms found in working tree: {offenders}"


def test_git_history_has_no_forbidden_terms(patterns) -> None:
    history = _git_history()
    hits = _find_hits(history, patterns)
    assert not hits, f"forbidden terms found in git history: {hits}"
