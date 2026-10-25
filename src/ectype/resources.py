"""Where Ectype finds its tools and data: inside the installed app, or on the system.

The Windows installer ships Tesseract (with English and orientation data) and EB
Garamond inside the program folder. From source, Tesseract comes from PATH, from
the ECTYPE_TESSERACT setting, or from its usual install folder on Windows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


def bundle_dir() -> Path | None:
    """The program folder of a packaged (PyInstaller) build; None when run from source."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return None


def tesseract_cmd() -> str | None:
    """Path of the Tesseract executable to use, or None if there is none."""
    env = os.environ.get("ECTYPE_TESSERACT")
    if env and Path(env).is_file():
        return env
    bundle = bundle_dir()
    if bundle:
        for name in ("tesseract.exe", "tesseract"):
            if (bundle / "tesseract" / name).is_file():
                return str(bundle / "tesseract" / name)
    found = shutil.which("tesseract")
    if found:
        return found
    if sys.platform == "win32":
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "")):
            candidate = Path(root) / ("Programs" if root == os.environ.get("LOCALAPPDATA") else "") / "Tesseract-OCR" / "tesseract.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def tesseract_env() -> dict[str, str] | None:
    """Environment for running a bundled Tesseract: point it at its own language data."""
    bundle = bundle_dir()
    if bundle and (bundle / "tesseract" / "tessdata").is_dir():
        return {**os.environ, "TESSDATA_PREFIX": str(bundle / "tesseract" / "tessdata")}
    return None


def subprocess_flags() -> dict:
    """Keep child processes from flashing a console window from the desktop app on Windows."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def configure_pytesseract() -> None:
    import pytesseract

    cmd = tesseract_cmd()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    env = tesseract_env()
    if env:
        os.environ.setdefault("TESSDATA_PREFIX", env["TESSDATA_PREFIX"])


def font_dirs() -> list[Path]:
    """Folders to search for fonts: the app's own first, then the system's and the user's."""
    dirs = []
    bundle = bundle_dir()
    if bundle:
        dirs.append(bundle / "fonts")
    home = Path.home()
    if sys.platform == "win32":
        dirs += [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                 Path(os.environ.get("LOCALAPPDATA", home)) / "Microsoft" / "Windows" / "Fonts"]
    elif sys.platform == "darwin":
        dirs += [home / "Library" / "Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts")]
    else:
        dirs += [home / ".local" / "share" / "fonts", home / ".fonts", Path("/usr/local/share/fonts"), Path("/usr/share/fonts")]
    return [d for d in dirs if d.is_dir()]


@lru_cache(maxsize=1)
def font_index() -> list[tuple[str, str, Path]]:
    """(family, style, file) for every TrueType/OpenType font found, the app's own first."""
    from fontTools.ttLib import TTFont

    out = []
    for d in font_dirs():
        for path in sorted(d.rglob("*")):
            if path.suffix.lower() not in (".ttf", ".otf"):
                continue
            try:
                name = TTFont(str(path), lazy=True, fontNumber=0)["name"]
                family = name.getDebugName(16) or name.getDebugName(1) or ""
                style = name.getDebugName(17) or name.getDebugName(2) or ""
            except Exception:
                continue
            out.append((family, style, path))
    return out


def find_font_file(family: str) -> Path | None:
    """A regular-style file of the named family, searching the font folders directly."""
    family = family.lower()
    matches = [(f, s, p) for f, s, p in font_index() if f.lower() == family]
    for f, s, p in matches:
        if s.lower() in ("regular", "book", "roman", "normal"):
            return p
    return matches[0][2] if matches else None
