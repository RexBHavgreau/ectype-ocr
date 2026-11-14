"""Running Ectype IDML (called docx-to-idml before its 1.0.0), the installed converter that
lays out Reflow exports.

Ectype IDML (https://github.com/RexBHavgreau/ectype-idml) is a separate program: Ectype
writes a .docx, runs the converter's `convert` command, and reads its JSON result, as its
docs/ENGINE.md describes (API version 1). The two programs share only files, and the
converter updates itself, so Ectype always uses its latest layout without being rebuilt.

The converter records where it is in program.json in its settings folder every time it
starts: %APPDATA%\\Ectype IDML (1.0.0 and later; it also writes the old folder while that
exists) or %APPDATA%\\docx-to-idml (before), ~/.config/... elsewhere. Its program file is
Ectype-IDML-X.Y.Z.exe, docx-to-idml-X.Y.Z.exe before 1.0.0. ECTYPE_DOCX2IDML overrides all
this with a command line, e.g. "python -m ectype_idml" to use a copy run from source (as the
tests do).
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
DOWNLOAD = "https://github.com/RexBHavgreau/ectype-idml/releases"


class ConverterMissing(RuntimeError):
    pass


@dataclass
class Program:
    command: list[str]
    version: str
    api: int


def _config_base() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming"))
    return Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))


def settings_dirs() -> list[Path]:
    """The converter's settings folders, its current name first."""
    return [_config_base() / "Ectype IDML", _config_base() / "docx-to-idml"]


def settings_dir() -> Path:
    return settings_dirs()[0]


MIN_VERSION = (0, 8, 0)  # the first docx-to-idml with `convert` and program.json
EXE_NAME = re.compile(r"^(?:ectype-idml|docx-to-idml)-(\d+)\.(\d+)\.(\d+)\.exe$", re.IGNORECASE)
EXE_GLOBS = ("Ectype-IDML-*.exe", "docx-to-idml-*.exe")


def version_of(exe: Path) -> tuple[int, int, int] | None:
    """The version in the .exe's name ("Ectype-IDML-1.1.0.exe", "docx-to-idml-0.8.0.exe"), or None."""
    m = EXE_NAME.match(Path(exe).name)
    return tuple(int(g) for g in m.groups()) if m else None


def search_places() -> list[Path]:
    """Where a downloaded Ectype IDML (docx-to-idml) .exe usually is: it is one file, kept wherever the user
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
    """Ectype IDML / docx-to-idml .exes in the usual places (and folders in them named for it),
    newest first."""
    found: dict[str, Path] = {}
    for place in search_places():
        try:
            folders = [place] + [d for d in place.iterdir() if d.is_dir()
                                 and ("docx" in d.name.lower() or "ectype idml" in d.name.lower().replace("-", " "))]
        except OSError:
            continue
        for folder in folders:
            try:
                for pattern in EXE_GLOBS:
                    for f in folder.glob(pattern):
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
    for folder in settings_dirs():  # the current name's first; the old one for copies before 1.0.0
        try:
            data = json.loads((folder / "program.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        exe = data.get("exe") if isinstance(data, dict) else None
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
        return (f"Reflow is laid out by Ectype IDML (docx-to-idml 0.8.0 or later); the copy found ({old.name}) is older and "
                "can't be run by other programs. Open it and update it (or download the latest from "
                f"{DOWNLOAD}), then export again.")
    return ("Reflow is laid out by Ectype IDML (formerly docx-to-idml), which Ectype couldn't find (it looks where "
            "Ectype IDML says it is when it starts, and in Downloads, the desktop and Documents). Open Ectype IDML "
            f"once, or point Ectype to it in Export IDML (Find Ectype IDML…). Download: {DOWNLOAD}")


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
        from .resources import clean_dll_search, frozen_child_env

        with clean_dll_search():  # docx-to-idml is a PyInstaller program too: a clean start
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    env=frozen_child_env(), **subprocess_flags())
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        proc = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        if not result_path.is_file():
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no result written"]
            raise RuntimeError(f"docx-to-idml didn't finish: {detail[0]}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"docx-to-idml: {result.get('error') or 'the conversion failed'}")
    return result
