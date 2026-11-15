"""Moving Ectype's data folder (models, training pools, downloads) to another drive.

The data stays where it is until every file has been copied and checked; only then is the
pointer (`location.json`, always in the default folder) written, and only then are the old
copies removed. A failure or a Stop before the pointer is written removes the partial copy and
leaves the old folder as it was."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from . import resources, stopping

MARGIN = 1 << 30  # room to spare on the new drive
SMALL = 1 << 20  # files under this size are compared byte for byte
GB = 1e9
EVERY = 1.0  # seconds between progress reports: the data can be ~2 million small files


def pointer_path() -> Path:
    return resources.default_data_dir() / "location.json"


def _is_running_record(rel: Path) -> bool:
    return len(rel.parts) == 2 and rel.parts[0] == "crash" and rel.name.startswith("running-") and rel.suffix == ".txt"


def _files(folder: Path):
    for root, _dirs, names in os.walk(folder):
        for name in names:
            yield Path(root) / name


def size_of(folder: Path) -> tuple[int, int]:
    """(number of files, bytes) in a folder."""
    count = total = 0
    for f in _files(Path(folder)):
        try:
            total += f.stat().st_size
            count += 1
        except OSError:
            pass
    return count, total


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _gb(n: float) -> str:
    return f"{n / GB:.1f} GB"


def _nearest_existing(p: Path) -> Path:
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _plan(dest: Path):
    """Check the request; return (source, dest, [(path, relative)], bytes)."""
    if os.environ.get("ECTYPE_DATA_DIR"):
        raise ValueError("The data folder is set by ECTYPE_DATA_DIR; remove that setting first.")
    src = resources.user_data_dir().resolve()
    dest = Path(dest).expanduser().absolute()
    if not dest.is_absolute() or not str(dest).strip():
        raise ValueError("Choose a full folder path, such as D:\\Ectype OCR.")
    dest = dest.resolve()
    if dest == src:
        raise ValueError(f"Ectype's data is already in {src}.")
    if _inside(dest, src):
        raise ValueError(f"Choose a folder outside the current one ({src}); {dest} is inside it.")
    if _inside(src, dest):
        raise ValueError(f"Choose a folder that doesn't contain the current one ({src}).")
    if dest.exists():
        if not dest.is_dir():
            raise ValueError(f"{dest} is a file, not a folder.")
        # empty, or holding only the pointer, empty folders and a closed copy's crash record
        others = [f for f in _files(dest) if f.relative_to(dest).as_posix() != "location.json"
                  and not _is_running_record(f.relative_to(dest))]
        if others:
            raise ValueError(f"{dest} is not empty. Choose an empty folder (or one that doesn't exist yet).")
    entries = []
    total = 0
    if src.is_dir():
        for f in _files(src):
            rel = f.relative_to(src)
            if rel.name == "location.json" and len(rel.parts) == 1:
                continue
            if _is_running_record(rel):
                continue
            try:
                total += f.stat().st_size
            except OSError:
                continue
            entries.append((f, rel))
    free = shutil.disk_usage(_nearest_existing(dest)).free
    if free < total + MARGIN:
        raise ValueError(f"Not enough room on that drive: Ectype's data needs {_gb(total + MARGIN)} "
                         f"(its {_gb(total)} and 1 GB to spare) and {_gb(free)} is free.")
    return src, dest, entries, total


def _same(a: Path, b: Path) -> bool:
    sa, sb = a.stat().st_size, b.stat().st_size
    if sa != sb:
        return False
    if sa < SMALL:
        return a.read_bytes() == b.read_bytes()
    return True


def _remove_copy(dest: Path, made_dest: bool, copied: list[Path]) -> None:
    for f in copied:
        try:
            f.unlink()
        except OSError:
            pass
    for root, dirs, _names in os.walk(dest, topdown=False):
        for d in dirs:
            try:
                (Path(root) / d).rmdir()
            except OSError:
                pass
    if made_dest:
        try:
            dest.rmdir()
        except OSError:
            pass


def move_data(dest: Path, progress=None) -> dict:
    """Move all of Ectype's data to `dest`. Returns {"files", "bytes", "left", "dest"}; `left`
    are old files that could not be deleted (in use). Raises ValueError for a request that can't
    be done, OSError / RuntimeError when copying or checking failed (nothing changed then), and
    stopping.Stopped if the person pressed Stop before the end of the copy."""
    progress = progress or (lambda stage, msg: None)
    src, dest, entries, total = _plan(Path(dest))
    to_default = dest == resources.default_data_dir().resolve()
    made_dest = not dest.exists()
    copied: list[Path] = []
    n = len(entries)
    done_bytes = 0
    last = 0.0
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for i, (f, rel) in enumerate(entries, 1):
            stopping.check()
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(f, target)
            except FileNotFoundError:
                if f.exists():
                    raise
                continue  # vanished since the count
            copied.append(target)
            done_bytes += target.stat().st_size
            now = time.monotonic()
            if now - last >= EVERY or i == n:  # not a line per file: that would flood the Log
                last = now
                progress("move", f"copied {i:,} of {n:,} files, {done_bytes / GB:.1f} of {total / GB:.1f} GB")
                progress("fraction", f"{(done_bytes / total if total else i / max(n, 1)) * 0.9:.4f}")
        progress("move", "checking the copy")
        for f, rel in entries:
            stopping.check()
            target = dest / rel
            if not f.exists():
                continue
            if not target.is_file() or not _same(f, target):
                raise RuntimeError(f"The copy of {rel} does not match the original; nothing was changed.")
        stopping.check()
    except BaseException:
        _remove_copy(dest, made_dest, copied)
        raise
    progress("fraction", "0.95")
    # Point at the new place (or drop the pointer when going back to the default folder).
    pointer = pointer_path()
    try:
        if to_default:
            pointer.unlink(missing_ok=True)
        else:
            pointer.parent.mkdir(parents=True, exist_ok=True)
            part = pointer.with_name(pointer.name + ".part")
            part.write_text(json.dumps({"data_dir": str(dest)}), encoding="utf-8", newline="\n")
            os.replace(part, pointer)
    except BaseException:
        _remove_copy(dest, made_dest, copied)
        raise
    # Past this point the new copy is the data: a Stop no longer undoes it.
    progress("move", "removing the old copies")
    left: list[str] = []
    for target in copied:
        old = src / target.relative_to(dest)
        try:
            old.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            left.append(str(old))
    for root, dirs, _names in os.walk(src, topdown=False):
        for d in dirs:
            try:
                (Path(root) / d).rmdir()
            except OSError:
                pass
    if not any(src.iterdir()) if src.is_dir() else False:
        try:
            src.rmdir()
        except OSError:
            pass
    progress("fraction", "1.0")
    return {"files": len(copied), "bytes": done_bytes, "left": left, "dest": str(dest)}
