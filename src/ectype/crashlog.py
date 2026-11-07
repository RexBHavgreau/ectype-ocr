"""A record of what the app was doing, for when it dies without a word.

The installed app has no console, so a crash in the program itself (the kind Windows reports as
ucrtbase.dll, exception c0000409) leaves nothing behind. This keeps a file, current.txt in the
crash folder of the user's Ectype folder, that holds the app's start details, what every thread
was doing if the program aborted (faulthandler), Python and Qt error messages, the last progress
lines and how much memory was in use. Each running copy has its own file, running-<process
id>.txt; a normal exit deletes it. One left at the next start (and no longer held open by a
running copy) means a run did not end normally, and it is kept as crash-<date>-<time>.txt.
Nothing here may stop the app from starting.
"""

from __future__ import annotations

import faulthandler
import os
import platform
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import __version__, resources

KEEP = 10  # crash reports kept

_file = None  # the open current.txt (kept so it is never collected)
_path: Path | None = None
_previous: Path | None = None
_lock = threading.Lock()


def crash_dir() -> Path:
    return resources.user_data_dir() / "crash"


def previous_crash() -> Path | None:
    """The report of the last run, if it did not end normally (moved aside by install)."""
    return _previous


def _write(text: str) -> None:
    try:
        with _lock:
            if _file is not None:
                _file.write(text)
                _file.flush()
    except (OSError, ValueError):
        pass


def _memory_mb() -> str | None:
    """Memory in use by this program, e.g. '1834 MB', or None when it can't be told."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            if not kernel.K32GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return None
            return f"{counters.WorkingSetSize // 2**20} MB, committed {counters.PagefileUsage // 2**20} MB"
        with open("/proc/self/statm", encoding="ascii") as f:
            pages = int(f.read().split()[1])
        return f"{pages * os.sysconf('SC_PAGE_SIZE') // 2**20} MB"
    except (OSError, ValueError, AttributeError, IndexError):
        return None


def note(line: str) -> None:
    """Add a timestamped line (with the memory in use) to the report; never raises."""
    try:
        if _file is None:
            return
        mem = _memory_mb()
        tag = f" [mem {mem}]" if mem else ""
        _write(f"{time.strftime('%H:%M:%S')}{tag} {line}\n")
    except (OSError, ValueError, AttributeError):
        pass


def _move_leftovers(folder: Path) -> Path | None:
    """Records left by runs that did not end normally, kept as crash-*.txt; the newest. A record
    another running copy still holds open can't be moved on Windows, and is left alone."""
    newest = None
    for current in sorted(folder.glob("running-*.txt"), key=lambda p: p.stat().st_mtime):
        try:
            stamp = datetime.fromtimestamp(current.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
            target = folder / f"crash-{stamp}.txt"
            n = 2
            while target.exists():
                target = folder / f"crash-{stamp}-{n}.txt"
                n += 1
            current.replace(target)
            newest = target
        except OSError:
            continue
    return newest


def _prune(folder: Path) -> None:
    for old in sorted(folder.glob("crash-*.txt"))[:-KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def _fatal_to_disk() -> None:
    try:
        if _file is not None:
            _file.flush()
            os.fsync(_file.fileno())
    except (OSError, ValueError):
        pass


def _qt_handler(kind, context, message) -> None:
    name = getattr(kind, "name", str(kind))
    if "Debug" in name or "Info" in name:
        return
    _write(f"Qt {name.replace('Qt', '').replace('Msg', '')}: {message}\n")
    if "Fatal" in name:
        _fatal_to_disk()  # Qt aborts right after this returns


def install() -> Path | None:
    """Start the record; returns its path, or None if it couldn't be started (the app starts
    all the same). A second call changes nothing."""
    try:
        return _install()
    except Exception:  # noqa: BLE001 - a record that can't be kept must not stop the app
        return None


def _install() -> Path:
    global _file, _path, _previous
    if _path is not None:
        return _path
    folder = crash_dir()
    folder.mkdir(parents=True, exist_ok=True)
    _previous = _move_leftovers(folder)
    _prune(folder)
    _path = folder / f"running-{os.getpid()}.txt"
    f = open(_path, "w", encoding="utf-8", newline="\n", buffering=1)
    _file = f
    _write(f"Ectype OCR {__version__}\nStarted {datetime.now():%Y-%m-%d %H:%M:%S}\n"
           f"{platform.platform()}\nPython {platform.python_version()}\n"
           f"Frozen: {bool(getattr(sys, 'frozen', False))}\n\n")
    faulthandler.enable(file=f, all_threads=True)
    if sys.stderr is None:  # the windowed app: C-level messages would go nowhere
        os.dup2(f.fileno(), 2)
        sys.stderr = f
    if sys.stdout is None:
        os.dup2(f.fileno(), 1)
        sys.stdout = f

    previous_hook = sys.excepthook

    def hook(kind, value, tb) -> None:
        _write("Uncaught exception:\n" + "".join(traceback.format_exception(kind, value, tb)) + "\n")
        previous_hook(kind, value, tb)

    sys.excepthook = hook
    previous_thread_hook = threading.excepthook

    def thread_hook(args) -> None:
        name = args.thread.name if args.thread else "?"
        _write(f"Uncaught exception in thread {name}:\n"
               + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)) + "\n")
        previous_thread_hook(args)

    threading.excepthook = thread_hook
    try:
        from PySide6.QtCore import qInstallMessageHandler
    except ImportError:
        pass
    else:
        qInstallMessageHandler(_qt_handler)
    return _path


def close_cleanly() -> None:
    """Normal exit: the record is no longer wanted (its presence next time means a bad end)."""
    global _file, _path
    if _file is None:
        return
    try:
        faulthandler.disable()
    except (OSError, ValueError):
        pass
    try:
        _file.close()
    except OSError:
        pass
    _file = None
    if _path is not None:
        try:
            _path.unlink()
        except OSError:
            pass
    _path = None
