"""Stamps: what a kept result was made from, judged by content and not by file times.

A result kept on disk (a reading, a segmentation, a grouping) is used again only while the
stamp stored with it equals the stamp of what it would be made from now. File times and sizes
are not trusted (a copied folder keeps times; a change can keep its size), and no version
number is kept by hand: the stamp of the code is taken from the code itself.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_CODE: list[str] = []


def file_digest(path) -> str:
    """SHA-1 (hex) of a file's bytes; "-" when there is no such file. Not remembered between
    calls: a long-lived program (the window) would otherwise trust a size and a time, which is
    what stamps exist to avoid."""
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return "-"
    return h.hexdigest()


def code_version() -> str:
    """What the program's code is. From source: a hash of every .py file of the package (paths
    and contents), taken once a run, so any change to the code makes kept results stale. In the
    packaged app (PyInstaller), where the .py files are not there: the version number of the
    build, which the release sets from its tag (a rebuild of the same tag has the same number)."""
    if not _CODE:
        _CODE.append(_code_hash())
    return _CODE[0]


def _code_hash() -> str:
    if getattr(sys, "frozen", False):
        from . import __version__

        return f"build-{__version__}"
    root = Path(__file__).resolve().parent
    files = sorted(root.rglob("*.py"), key=lambda p: p.relative_to(root).as_posix())
    h = hashlib.sha1()
    try:
        for p in files:
            h.update(p.relative_to(root).as_posix().encode("utf-8"))
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    except OSError:
        from . import __version__

        return f"build-{__version__}"
    return h.hexdigest()


def stamp(*parts) -> str:
    """One SHA-1 (hex) over the parts as strings, kept apart from one another."""
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def write_text(path, text: str) -> None:
    """Write a small text file through a temporary name, so a crash never leaves half of it."""
    path = Path(path)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def read_text(path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
