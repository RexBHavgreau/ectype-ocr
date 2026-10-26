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


def find_program() -> Program | None:
    """The installed converter, or None. ECTYPE_DOCX2IDML (a command line) comes first."""
    override = os.environ.get("ECTYPE_DOCX2IDML")
    if override:
        return Program(shlex.split(override, posix=sys.platform != "win32"), "from ECTYPE_DOCX2IDML", API)
    path = settings_dir() / "program.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    exe = data.get("exe")
    if not exe or not Path(exe).is_file():
        return None
    return Program([exe], str(data.get("version", "")), int(data.get("api", API)))


def missing_message() -> str:
    return ("Reflow is laid out by docx-to-idml, which isn't installed (or hasn't been opened since it was "
            f"installed or moved). Download it from {DOWNLOAD}, open it once, then export again.")


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
