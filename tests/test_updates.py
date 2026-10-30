from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from pathlib import Path

import pytest

from ectype import updates

SETUP = b"installer bytes" * 1000


class _Response(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _release(tag="v0.3.0", digest=None):
    return json.dumps({
        "tag_name": tag, "html_url": f"https://github.com/{updates.REPO}/releases/tag/{tag}", "body": "Notes",
        "assets": [{"name": "source.zip", "browser_download_url": "https://x/source.zip"},
                   {"name": f"Ectype-OCR-{tag[1:]}-Setup.exe", "browser_download_url": "https://x/setup.exe",
                    "size": len(SETUP), "digest": digest or "sha256:" + hashlib.sha256(SETUP).hexdigest()}],
    }).encode()


def test_versions_and_check():
    assert updates.parse_version("v0.10.2") > updates.parse_version("0.9.9") > updates.parse_version("0.9")
    r = updates.check(opener=lambda req, timeout: _Response(_release()), current="0.2.0")
    assert r["status"] == "newer" and r["latest"] == "0.3.0"
    assert r["asset"]["name"] == "Ectype-OCR-0.3.0-Setup.exe", "the installer, not another file"
    assert updates.check(opener=lambda req, timeout: _Response(_release("v0.2.0")), current="0.2.0")["status"] == "current"

    def missing(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    def offline(req, timeout):
        raise OSError("no network")

    assert updates.check(opener=missing)["status"] == "norelease"
    assert updates.check(opener=offline)["status"] == "error"


def test_offer_and_skip():
    newer = {"status": "newer", "latest": "0.3.0"}
    assert updates.should_offer(newer, {}, manual=False)
    assert not updates.should_offer(newer, {"skipped_version": "0.3.0"}, manual=False), "skipped at startup"
    assert updates.should_offer(newer, {"skipped_version": "0.3.0"}, manual=True), "offered when asked"
    assert not updates.should_offer({"status": "current"}, {}, manual=True)
    assert updates.check_at_start({}) and not updates.check_at_start({"check_updates": False})


def test_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path))
    assert updates.load_settings() == {}
    updates.save_settings({"check_updates": False})
    assert updates.load_settings() == {"check_updates": False}


def test_download_checks_the_digest(tmp_path):
    asset = updates._pick_asset(json.loads(_release())["assets"])
    seen = []
    path = updates.download(asset, tmp_path, progress=seen.append, opener=lambda req, timeout: _Response(SETUP))
    assert path == tmp_path / asset["name"] and path.read_bytes() == SETUP and seen[-1] == 1.0
    bad = {**asset, "digest": "sha256:" + "0" * 64}
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(updates.UpdateError, match="didn't match"):
        updates.download(bad, other, opener=lambda req, timeout: _Response(SETUP))
    assert not list(other.iterdir()), "nothing left behind"


def test_installer_runs_over_the_same_install(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    for key in ("ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.delenv(key, raising=False)
    everyone = tmp_path / "Program Files" / "Ectype OCR"
    mine = tmp_path / "Users" / "me" / "AppData" / "Local" / "Programs" / "Ectype OCR"
    for d in (everyone, mine):
        d.mkdir(parents=True)
    setup = Path("Setup.exe")
    assert "/ALLUSERS" in updates.installer_command(setup, everyone)
    cmd = updates.installer_command(setup, mine)
    assert "/CURRENTUSER" in cmd and "/SILENT" in cmd and "/UPDATE" in cmd
    assert cmd[-1] == f"/DIR={mine.resolve()}", "into the same folder, wherever it is"
    # Installed for everyone outside C:\Program Files (on another drive): as Windows lists it.
    other = tmp_path / "D" / "Program Files" / "Ectype OCR"
    other.mkdir(parents=True)
    monkeypatch.setattr(updates, "_registered", lambda app: True)
    assert "/ALLUSERS" in updates.installer_command(setup, other)
