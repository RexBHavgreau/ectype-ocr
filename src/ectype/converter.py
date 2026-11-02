"""Running docx-to-idml, the installed converter that lays out Reflow exports.

docx-to-idml (https://github.com/RexBHavgreau/docx-to-idml) is a separate program: Ectype
writes a .docx, runs the converter's `convert` command, and reads its JSON result, as its
docs/ENGINE.md describes (API version 1). The two programs share only files, and the
converter updates itself, so Ectype always uses its latest layout without being rebuilt.

The converter records where it is in program.json in its settings folder
(%APPDATA%\\docx-to-idml on Windows, ~/.config/docx-to-idml elsewhere) every time it starts.
ECTYPE_DOCX2IDML overrides that with a command line, e.g. "python -m docx2idml" to use a copy
run from source (as the tests do).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .resources import subprocess_flags

API = 1
DOWNLOAD = "https://github.com/RexBHavgreau/docx-to-idml/releases"


class ConverterMissing(RuntimeError):
    pass


@dataclass
class Program:
    command: list[str]
    version: str
    api: int


def settings_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "docx-to-idml"


MIN_VERSION = (0, 8, 0)  # the first docx-to-idml with `convert` and program.json
EXE_NAME = re.compile(r"^docx-to-idml-(\d+)\.(\d+)\.(\d+)\.exe$", re.IGNORECASE)


def version_of(exe: Path) -> tuple[int, int, int] | None:
    """The version in the .exe's name ("docx-to-idml-0.8.0.exe"), or None."""
    m = EXE_NAME.match(Path(exe).name)
    return tuple(int(g) for g in m.groups()) if m else None


def search_places() -> list[Path]:
    """Where a downloaded docx-to-idml .exe usually is: it is one file, kept wherever the user
    saved it, and saves its updates beside itself (Downloads, the desktop, Documents...)."""
    home = Path.home()
    places = [home / "Downloads", home / "Desktop", home / "Documents", home, home / "OneDrive" / "Desktop",
              home / "OneDrive" / "Documents"]
    for var in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "USERPROFILE"):
        if os.environ.get(var):
            places.append(Path(os.environ[var]))
    if os.environ.get("LOCALAPPDATA"):
        places.append(Path(os.environ["LOCALAPPDATA"]) / "Programs")
    return places


def search() -> list[Path]:
    """docx-to-idml .exes in the usual places (and folders in them named for it), newest first."""
    found: dict[str, Path] = {}
    for place in search_places():
        try:
            folders = [place] + [d for d in place.iterdir() if d.is_dir() and "docx" in d.name.lower()]
        except OSError:
            continue
        for folder in folders:
            try:
                for f in folder.glob("docx-to-idml-*.exe"):
                    if version_of(f) and f.is_file():
                        found[str(f.resolve()).lower()] = f
            except OSError:
                continue
    return sorted(found.values(), key=lambda f: version_of(f), reverse=True)


def _remembered() -> Path | None:
    from .updates import load_settings

    exe = load_settings().get("docx2idml_exe")
    return Path(exe) if exe and Path(exe).is_file() else None


def remember(exe: Path) -> None:
    """The .exe the user pointed to (Export IDML → Find docx-to-idml…), used when program.json
    doesn't lead to one."""
    from .updates import load_settings, save_settings

    settings = load_settings()
    settings["docx2idml_exe"] = str(Path(exe).resolve())
    save_settings(settings)


def _program(exe: Path, version: str = "", api: int = API) -> Program:
    v = version_of(exe)
    return Program([str(exe)], version or (".".join(map(str, v)) if v else ""), api)


def find_program() -> Program | None:
    """The installed converter, or None. ECTYPE_DOCX2IDML (a command line) comes first, then
    the converter's own program.json, then a copy the user pointed to, then the newest copy
    (0.8.0 or later) in the usual places: program.json is written only when docx-to-idml
    starts, so a copy saved but not opened since (or moved) is still found."""
    override = os.environ.get("ECTYPE_DOCX2IDML")
    if override:
        return Program(shlex.split(override, posix=sys.platform != "win32"), "from ECTYPE_DOCX2IDML", API)
    path = settings_dir() / "program.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    exe = data.get("exe")
    if exe and Path(exe).is_file():
        return Program([exe], str(data.get("version", "")), int(data.get("api", API)))
    mine = _remembered()
    if mine is not None and (version_of(mine) or MIN_VERSION) >= MIN_VERSION:
        return _program(mine)
    new = [f for f in search() if version_of(f) >= MIN_VERSION]
    return _program(new[0]) if new else None


def too_old() -> Path | None:
    """A copy found that is too old to be run by other programs (before 0.8.0), if any."""
    old = [f for f in search() if version_of(f) < MIN_VERSION]
    mine = _remembered()
    if mine is not None and version_of(mine) and version_of(mine) < MIN_VERSION:
        old.insert(0, mine)
    return old[0] if old else None


def missing_message() -> str:
    old = too_old()
    if old is not None:
        return (f"Reflow is laid out by docx-to-idml 0.8.0 or later; the copy found ({old.name}) is older and "
                "can't be run by other programs. Open it and update it (or download the latest from "
                f"{DOWNLOAD}), then export again.")
    return ("Reflow is laid out by docx-to-idml, which Ectype couldn't find (it looks where docx-to-idml says it is "
            "when it starts, and in Downloads, the desktop and Documents). Open docx-to-idml once, or point Ectype "
            f"to it in Export IDML (Find docx-to-idml…). Download: {DOWNLOAD}")


def convert(docx: Path, out_dir: Path, name: str, profile: str | None = None, timeout: float = 1800) -> dict:
    """Run `convert` on the .docx. Returns docx-to-idml's result (keys: ok, idml, folder,
    report, alerts, error). Raises ConverterMissing when the converter isn't installed and
    RuntimeError when it fails."""
    program = find_program()
    if program is None:
        raise ConverterMissing(missing_message())
    with tempfile.TemporaryDirectory(prefix="ectype-convert-") as tmp:
        result_path = Path(tmp) / "result.json"
        cmd = program.command + ["convert", "--result", str(result_path), "--out", str(out_dir), "--name", name]
        if profile:
            cmd += ["--profile", profile]
        cmd.append(str(docx))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **subprocess_flags())
        if not result_path.is_file():
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no result written"]
            raise RuntimeError(f"docx-to-idml didn't finish: {detail[0]}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"docx-to-idml: {result.get('error') or 'the conversion failed'}")
    return result
