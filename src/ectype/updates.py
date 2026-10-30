"""Check GitHub for a newer release of Ectype OCR, and install it.

The check reads the latest release of REPO (its tag, e.g. "v0.3.0") and compares it with the
running version: at every start of the desktop app (unless turned off) and from Help > Check for
updates. Nothing is downloaded unless the user says so. The installer of the new version
(`Ectype-OCR-<version>-Setup.exe`) is downloaded to a temporary folder through a ".part" name,
checked against the size and SHA-256 digest GitHub gives each release file, then started with
Inno Setup's silent options, for the same kind of install as the running one (for this user or
for everyone; else a second copy would go elsewhere). The app then closes so the installer can
replace its files; the installer starts the new version when it's done (its `/UPDATE` option).

Adapted from docx-to-idml's updater (MIT), which saves a new single .exe beside the old one;
Ectype is installed, so its update is an installer run. Settings (whether to check, a version
the user chose to skip) are in settings.json in the user's Ectype folder, so they survive updates.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__
from .resources import user_data_dir

REPO = "RexBHavgreau/ectype-ocr"
API = "https://api.github.com/repos/{repo}/releases/latest"
RELEASES = f"https://github.com/{REPO}/releases"
SETUP_RE = re.compile(r"^Ectype-OCR-(\d+(?:\.\d+){1,2})-Setup\.exe$", re.I)


class UpdateError(Exception):
    """A plain message for the user."""


def parse_version(v: str | None) -> tuple[int, int, int]:
    nums = [int(n) for n in re.findall(r"\d+", v or "")[:3]]
    return tuple(nums + [0] * (3 - len(nums)))  # type: ignore[return-value]


def _agent() -> str:
    return f"Ectype-OCR/{__version__}"


def _pick_asset(assets) -> dict | None:
    for a in assets or []:
        if SETUP_RE.match(a.get("name") or "") and a.get("browser_download_url"):
            return {"name": a["name"], "url": a["browser_download_url"], "size": a.get("size") or 0,
                    "digest": a.get("digest") or ""}
    return None


def check(timeout: float = 5.0, opener=None, current: str = __version__) -> dict:
    """{"status": "newer"|"current"|"norelease"|"error", "latest", "url", "notes", "asset", "error"}"""
    try:
        req = urllib.request.Request(API.format(repo=REPO),
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": _agent()})
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        latest = (data.get("tag_name") or data.get("name") or "").lstrip("v")
        status = "newer" if parse_version(latest) > parse_version(current) else "current"
        return {"status": status, "latest": latest, "url": data.get("html_url") or RELEASES,
                "notes": (data.get("body") or "")[:2000], "asset": _pick_asset(data.get("assets"))}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # no release published yet
            return {"status": "norelease"}
        return {"status": "error", "error": str(exc)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# -- settings -------------------------------------------------------------------


def settings_path() -> Path:
    return user_data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def check_at_start(settings: dict) -> bool:
    return bool(settings.get("check_updates", True))


def should_offer(result: dict, settings: dict, manual: bool) -> bool:
    """Offer a newer version: always when the user asked; at start, unless they chose to skip it."""
    if result.get("status") != "newer":
        return False
    return manual or result.get("latest") != settings.get("skipped_version")


# -- installing -------------------------------------------------------------------


def installed_app() -> Path | None:
    """The installed program folder, when this is the packaged Windows app (not run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def can_install() -> bool:
    return sys.platform == "win32" and installed_app() is not None


APP_ID = "{2112C073-44B6-4BC2-83FD-3FD79D290C90}"  # the installer's AppId (installer.iss)


def _registered(app: Path) -> bool | None:
    """True if Windows lists the app installed for everyone, False if for this user, as the
    installer recorded it; None if it can't tell (not Windows, or not listed for that folder)."""
    try:
        import winreg
    except ImportError:
        return None
    key = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}_is1"
    for hive, everyone in ((winreg.HKEY_LOCAL_MACHINE, True), (winreg.HKEY_CURRENT_USER, False)):
        try:
            with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                folder = winreg.QueryValueEx(k, "InstallLocation")[0]
        except OSError:
            continue
        if Path(folder).resolve() == Path(app).resolve():
            return everyone
    return None


def for_everyone(app: Path) -> bool:
    """Whether the app was installed for everyone rather than for this user: as Windows lists
    it, else by whether it's under Program Files."""
    listed = _registered(app)
    if listed is not None:
        return listed
    roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")]
    app = Path(app).resolve()
    for root in filter(None, roots):
        try:
            app.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


def download(asset: dict, folder: Path | None = None, progress: Callable[[float], None] | None = None,
             timeout: float = 30.0, opener=None) -> Path:
    """Download the installer into folder (default: a temporary one) through a ".part" name,
    checking its size and SHA-256 digest. Raises UpdateError with a message for the user."""
    folder = Path(folder or tempfile.mkdtemp(prefix="ectype-update-"))
    dest = folder / asset["name"]
    part = dest.with_name(dest.name + ".part")
    total = got = 0
    h = hashlib.sha256()
    try:
        req = urllib.request.Request(asset["url"], headers={"User-Agent": _agent()})
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r, open(part, "wb") as out:
            total = int(asset.get("size") or r.headers.get("Content-Length") or 0)
            while chunk := r.read(256 * 1024):
                out.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if total and progress:
                    progress(min(got / total, 1.0))
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(f"The download didn't finish ({exc}). Nothing was changed; try again later.") from exc
    digest = (asset.get("digest") or "").lower()
    if (total and got != total) or (digest.startswith("sha256:") and h.hexdigest() != digest[7:]):
        part.unlink(missing_ok=True)
        raise UpdateError("The downloaded installer didn't match the release (it may have been damaged on the "
                          "way), so it was deleted. Nothing was changed; try again later.")
    part.replace(dest)
    return dest


def installer_command(setup: Path, app: Path) -> list[str]:
    """The installer, run silently over the existing install (same folder, tasks and kind of
    install), starting the app again afterwards."""
    return [str(setup), "/SILENT", "/SP-", "/NORESTART", "/CLOSEAPPLICATIONS", "/UPDATE",
            "/ALLUSERS" if for_everyone(app) else "/CURRENTUSER", f"/DIR={Path(app).resolve()}"]


def run_installer(setup: Path, app: Path) -> None:
    """Start the installer, independent of this process, which should then quit."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(installer_command(setup, app), cwd=str(Path(setup).parent), close_fds=True, creationflags=flags)
