"""Where Ectype finds its tools and data: inside the installed app, or on the system.

The Windows installer ships Tesseract (with English and orientation data) and EB
Garamond inside the program folder. From source, Tesseract comes from PATH, from
the ECTYPE_TESSERACT setting, or from its usual install folder on Windows.
Languages downloaded from the app live in the user's own folder (`user_data_dir`).
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


def user_data_dir() -> Path:
    """The user's own Ectype folder (downloaded languages); ECTYPE_DATA_DIR overrides it."""
    if os.environ.get("ECTYPE_DATA_DIR"):
        return Path(os.environ["ECTYPE_DATA_DIR"])
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", home)) / "Ectype OCR"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Ectype OCR"
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "ectype"


def user_tessdata_dir() -> Path:
    return user_data_dir() / "tessdata"


@lru_cache(maxsize=1)
def base_tessdata_dir() -> Path | None:
    """The language folder of the app's own Tesseract, or of the system's."""
    bundle = bundle_dir()
    if bundle and (bundle / "tesseract" / "tessdata").is_dir():
        return bundle / "tesseract" / "tessdata"
    prefix = os.environ.get("TESSDATA_PREFIX")
    if prefix and Path(prefix).is_dir() and Path(prefix) != user_tessdata_dir():  # not one we set ourselves
        return Path(prefix)
    cmd = tesseract_cmd()
    if not cmd:
        return None
    try:
        out = subprocess.run([cmd, "--list-langs"], capture_output=True, text=True, timeout=30, **subprocess_flags())
    except (OSError, subprocess.SubprocessError):
        return None
    # First line: List of available languages in "/usr/share/tesseract-ocr/5/tessdata/" (2):
    first = (out.stdout or out.stderr).splitlines()[:1]
    if first and '"' in first[0]:
        folder = Path(first[0].split('"')[1])
        if folder.is_dir():
            return folder
    return None


def tessdata_dir() -> Path | None:
    """The folder Tesseract reads languages from: the user's, once languages have been
    downloaded there (it then holds the shipped ones too), else the app's or system's."""
    user = user_tessdata_dir()
    if user.is_dir() and any(user.glob("*.traineddata")):
        return user
    return base_tessdata_dir()


def tesseract_env() -> dict[str, str] | None:
    """Environment for running Tesseract: point it at the language folder in use, when
    that isn't the one it would find by itself."""
    folder = tessdata_dir()
    bundle = bundle_dir()
    if folder is not None and (folder == user_tessdata_dir() or bundle is not None):
        return {**os.environ, "TESSDATA_PREFIX": str(folder)}
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
        os.environ["TESSDATA_PREFIX"] = env["TESSDATA_PREFIX"]


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
