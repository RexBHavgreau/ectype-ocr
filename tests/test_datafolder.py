from __future__ import annotations

import json
import os
import shutil

import pytest

from ectype import cli, datafolder, resources, stopping


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("ECTYPE_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "local"))
    default = resources.default_data_dir()
    assert str(default).startswith(str(tmp_path))
    default.mkdir(parents=True)
    (default / "models").mkdir()
    (default / "models" / "heb.traineddata").write_bytes(bytes(range(256)) * 50)
    (default / "pool" / "heb").mkdir(parents=True)
    (default / "pool" / "heb" / "Oración.json").write_text('{"a": "ñ"}', encoding="utf-8")
    (default / "settings.json").write_text("{}", encoding="utf-8")
    (default / "crash").mkdir()
    (default / "crash" / "running-1.txt").write_text("held open", encoding="utf-8")
    return default


def snapshot(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_move_copies_verifies_points_and_deletes(home, tmp_path):
    before = {k: v for k, v in snapshot(home).items() if "running-" not in k}
    dest = tmp_path / "D" / "Ectype OCR"
    messages = []
    result = datafolder.move_data(dest, lambda s, m: messages.append((s, m)))
    assert result["files"] == len(before) and result["left"] == [] and result["dest"] == str(dest.resolve())
    assert snapshot(dest) == before
    assert resources.user_data_dir() == dest.resolve()  # at once, in the same process
    assert json.loads((home / "location.json").read_text(encoding="utf-8"))["data_dir"] == str(dest.resolve())
    assert not (home / "models").exists() and not (home / "pool").exists()
    assert (home / "crash" / "running-1.txt").exists()  # a running copy's record stays where it is
    assert any(s == "move" and "files" in m for s, m in messages) and any(s == "fraction" for s, m in messages)


def test_refusals(home, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="inside"):
        datafolder.move_data(home / "sub")
    with pytest.raises(ValueError, match="already"):
        datafolder.move_data(home)
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("x")
    with pytest.raises(ValueError, match="not empty"):
        datafolder.move_data(full)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 9, 1))
    with pytest.raises(ValueError, match="Not enough room.*GB"):
        datafolder.move_data(tmp_path / "small")
    monkeypatch.undo()
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(home))
    with pytest.raises(ValueError, match="ECTYPE_DATA_DIR"):
        datafolder.move_data(tmp_path / "other")
    assert (home / "models").exists() and not (home / "location.json").exists()


def test_failure_while_copying_changes_nothing(home, tmp_path, monkeypatch):
    before = snapshot(home)
    real = shutil.copy2
    calls = []

    def flaky(a, b, *args, **kw):
        calls.append(a)
        if len(calls) == 3:
            raise OSError("disk error")
        return real(a, b, *args, **kw)

    monkeypatch.setattr(shutil, "copy2", flaky)
    dest = tmp_path / "D" / "Ectype OCR"
    with pytest.raises(OSError):
        datafolder.move_data(dest)
    assert snapshot(home) == before and not (home / "location.json").exists()
    assert not dest.exists() or not any(dest.rglob("*"))
    assert resources.user_data_dir() == home


def test_stop_midway_leaves_everything(home, tmp_path):
    before = snapshot(home)
    flag = stopping.StopFlag()
    dest = tmp_path / "D" / "Ectype OCR"

    def progress(stage, msg):
        if stage == "move":
            flag.request()

    with stopping.active(flag), pytest.raises(stopping.Stopped):
        datafolder.move_data(dest, progress)
    assert snapshot(home) == before and not (home / "location.json").exists()
    assert not dest.exists() or not any(dest.rglob("*"))


def test_move_twice_and_back(home, tmp_path):
    before = {k: v for k, v in snapshot(home).items() if "running-" not in k}
    one, two = tmp_path / "E1", tmp_path / "E2"
    datafolder.move_data(one)
    datafolder.move_data(two)
    assert resources.user_data_dir() == two.resolve() and snapshot(two) == before
    assert not one.exists()
    assert (home / "location.json").exists()
    datafolder.move_data(home)  # back to the default: the pointer goes
    assert resources.user_data_dir() == home and not (home / "location.json").exists()
    assert {k: v for k, v in snapshot(home).items() if "running-" not in k} == before


def test_cli(home, tmp_path, capsys):
    assert cli.main(["data-folder"]) == 0
    assert str(home) in capsys.readouterr().out
    dest = tmp_path / "D" / "Ectype OCR"
    assert cli.main(["data-folder", "--move", str(dest)]) == 0
    assert "now in" in capsys.readouterr().out and resources.user_data_dir() == dest.resolve()
    assert cli.main(["data-folder", "--move", str(dest)]) == 2


def test_gui_menu(home):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ectype.gui import MainWindow
    from ectype.project import Project

    app = qt.QApplication.instance() or qt.QApplication([])
    w = MainWindow(Project.for_scan(home.parent / "scan.pdf"))
    menus = {a.text(): a.menu() for a in w.menuBar().actions()}
    assert "Data folder…" in [a.text() for a in menus["&Settings"].actions()]
    w.close()
    w.deleteLater()
    app.processEvents()
