"""Freeing disk space in Ectype's data folder, without touching anyone's own work.

Three things can go, each safe in its own way:
- the prepared copies of training lines (`training-cache/`): made again by the next training;
- leftovers of stopped or failed runs (`work/`, the folder text command's temporary folders;
  a folder touched in the last hour may be in use and stays);
- lines set from fonts (the pool's "synthetic" items): made again by Training -> Train from
  fonts. A font's lines count as done only while its images exist (`synthtrain.font_done`), and
  its record in books.json is removed too, so they are made again.

Corrections from Review ("correction"), lines matched to scans ("aligned") and lines read
surely ("confident") are never removed here. Nothing else in the data folder is touched.

Sizes come from file listings only (no file is read). Each removal checks `stopping.check()`
between files or books, so Stop works, and reports at most once a second.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Callable

from . import pool, stopping
from .resources import user_data_dir

RECENT = 3600  # seconds: a work folder touched within this may be in use


def cache_dir() -> Path:
    return user_data_dir() / "training-cache"


def work_dir() -> Path:
    return user_data_dir() / "work"


class _Reporter:
    """Progress for the app's window: ("free", text) and ("fraction", "0.42"), at most once a second."""

    def __init__(self, progress: Callable[[str, str], None] | None, total: int, what: str, lo: float = 0.0,
                 hi: float = 1.0) -> None:
        self.progress, self.total, self.what, self.lo, self.hi = progress, max(1, total), what, lo, hi
        self.last = 0.0

    def step(self, done: int) -> None:
        stopping.check()
        now = time.monotonic()
        if self.progress and now - self.last >= 1.0:
            self.last = now
            self.progress("free", f"removed {done:,} of {self.total:,} {self.what}…")
            self.progress("fraction", f"{self.lo + (self.hi - self.lo) * done / self.total:.3f}")


def _walk(folder: Path):
    """(path, size) of every file under folder, by listing only."""
    stack = [folder]
    while stack:
        here = stack.pop()
        try:
            with os.scandir(here) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        else:
                            yield Path(e.path), e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue


def _tree(folder: Path) -> tuple[int, int]:
    files = size = 0
    if folder.is_dir():
        for _, n in _walk(folder):
            files += 1
            size += n
    return files, size


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _newest(folder: Path) -> float:
    """When anything in the folder was last touched."""
    try:
        newest = folder.stat().st_mtime
    except OSError:
        return 0.0
    for path, _ in _walk(folder):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            pass
    return newest


def leftover_folders() -> list[Path]:
    """Work folders not touched within the hour."""
    if not work_dir().is_dir():
        return []
    now = time.time()
    return sorted(p for p in work_dir().iterdir() if now - _newest(p) > RECENT)


def lines_report(lang: str) -> dict:
    """Per source: lines and bytes (image and text file of each), for one pool language."""
    out = {s: {"lines": 0, "bytes": 0} for s in pool.SOURCES}
    base = pool.root() / lang
    for i, m in pool.index(lang).items():
        folder = base / pool.slug(m["book"])
        slot = out.setdefault(m["source"], {"lines": 0, "bytes": 0})
        slot["lines"] += 1
        slot["bytes"] += _size(folder / f"{i}.png") + _size(folder / f"{i}.gt.txt")
    return out


def report() -> dict:
    files, size = _tree(cache_dir())
    leftovers = [_tree(p) for p in leftover_folders()]
    total_files, total = _tree(user_data_dir())
    return {
        "folder": str(user_data_dir()),
        "total": {"files": total_files, "bytes": total},
        "cache": {"files": files, "bytes": size},
        "leftovers": {"files": sum(f for f, _ in leftovers), "bytes": sum(b for _, b in leftovers),
                      "folders": len(leftovers)},
        "pool": {lang: lines_report(lang) for lang in pool.languages()},
    }


def remove_cache(progress: Callable[[str, str], None] | None = None) -> int:
    """Delete the prepared copies of training lines. Returns the bytes freed."""
    return _remove_tree(cache_dir(), progress, "prepared copies")


def _remove_tree(folder: Path, progress, what: str, lo: float = 0.0, hi: float = 1.0) -> int:
    if not folder.is_dir():
        return 0
    listing = list(_walk(folder))
    rep = _Reporter(progress, len(listing), what, lo, hi)
    freed = 0
    for k, (path, n) in enumerate(listing, 1):
        rep.step(k)
        try:
            path.unlink()
            freed += n
        except OSError:
            pass
    shutil.rmtree(folder, ignore_errors=True)
    return freed


def remove_leftovers(progress: Callable[[str, str], None] | None = None) -> int:
    """Delete the temporary folders of stopped or failed text runs (not ones touched within the
    hour). Returns the bytes freed."""
    freed = 0
    folders = leftover_folders()
    for k, folder in enumerate(folders):
        stopping.check()
        freed += _remove_tree(folder, progress, "leftover files", k / len(folders), (k + 1) / len(folders))
    return freed


def remove_font_lines(langs: list[str] | None = None,
                      progress: Callable[[str, str], None] | None = None) -> dict:
    """Delete lines set from fonts ("synthetic" only), per language: {lang: {"lines", "bytes"}}.
    Done a font (book) at a time, the index saved after each, so a stop leaves the pool
    consistent; a font with no lines left loses its record in books.json too."""
    chosen = [lang for lang in pool.languages() if langs is None or lang in langs]
    plan = []
    for lang in chosen:
        data = pool.index(lang)
        by_book: dict[str, int] = {}
        for m in data.values():
            if m["source"] == "synthetic":
                by_book[m["book"]] = by_book.get(m["book"], 0) + 1
        plan.append((lang, by_book))
    total = sum(sum(b.values()) for _, b in plan)
    rep = _Reporter(progress, total, "lines")
    out: dict = {}
    done = 0
    for lang, by_book in plan:
        result = out.setdefault(lang, {"lines": 0, "bytes": 0})
        for book in by_book:
            rep.step(done)
            w = pool.Writer(lang)
            folder = pool.root() / lang / pool.slug(book)
            gone = [i for i, m in w.data.items() if m["book"] == book and m["source"] == "synthetic"]
            freed = sum(_size(folder / f"{i}{s}") for i in gone for s in (".png", ".gt.txt"))
            w.remove_book(book, source="synthetic")
            left = any(m["book"] == book for m in w.data.values())
            w.close()
            if not left:
                records = pool.books(lang)
                if records.pop(book, None) is not None:
                    pool._save(pool.root() / lang / "books.json", records)
                try:
                    folder.rmdir()
                except OSError:
                    pass
            result["lines"] += len(gone)
            result["bytes"] += freed
            done += len(gone)
    rep.step(total)
    return out
