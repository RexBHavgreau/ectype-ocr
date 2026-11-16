from __future__ import annotations

import os
import time

import pytest

from ectype import cli, freespace, pool, stopping, synthtrain

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    for lang in ("heb", "grc"):
        with pool.Writer(lang) as w:
            for k in range(6):
                w.add(PNG + bytes([k]) * 10, f"synth {lang} {k}", "font: A", "line", "synthetic", origin=f"s{k}")
                w.add(PNG + bytes([k]) * 20, f"synth b {lang} {k}", "font: B", "line", "synthetic", origin=f"t{k}")
            for k in range(3):
                w.add(PNG + bytes([k]) * 30, f"corr {lang} {k}", "book", "line", "correction", origin=f"c{k}")
                w.add(PNG + bytes([k]) * 40, f"aligned {lang} {k}", "scan", "line", "aligned")
                w.add(PNG + bytes([k]) * 50, f"conf {lang} {k}", "book", "line", "confident")
        pool.set_book(lang, "font: A", source="synthetic", stamp="abc")
        pool.set_book(lang, "font: B", source="synthetic", stamp="abd")
    cache = tmp_path / "data" / "training-cache" / "lstmf" / "ab"
    cache.mkdir(parents=True)
    for k in range(5):
        (cache / f"{k}.lstmf").write_bytes(b"c" * 1000)
    work = tmp_path / "data" / "work"
    (work / "old.ectype").mkdir(parents=True)
    (work / "old.ectype" / "x.png").write_bytes(b"w" * 500)
    (work / "new.ectype").mkdir()
    (work / "new.ectype" / "y.png").write_bytes(b"w" * 700)
    old = time.time() - 7200
    for p in (work / "old.ectype" / "x.png", work / "old.ectype"):
        os.utime(p, (old, old))
    return tmp_path / "data"


def snapshot(lang, sources):
    out = {}
    for it in pool.items(lang, sources=sources):
        out[it.id] = (it.png.read_bytes(), it.png.with_suffix("").with_suffix(".gt.txt").read_bytes(), it.text)
    return out


def test_report(data):
    r = freespace.report()
    assert r["cache"] == {"files": 5, "bytes": 5000}
    assert r["leftovers"]["bytes"] == 500 and r["leftovers"]["folders"] == 1
    heb = r["pool"]["heb"]
    assert heb["synthetic"]["lines"] == 12 and heb["correction"]["lines"] == 3
    assert heb["aligned"]["lines"] == 3 and heb["confident"]["lines"] == 3
    assert heb["synthetic"]["bytes"] > 12 * 100
    assert r["total"]["bytes"] >= 5000 + 500 + 700


def test_remove_font_lines_only_synthetic(data):
    keep = ("correction", "aligned", "confident")
    before_heb, before_grc = snapshot("heb", keep), snapshot("grc", keep)
    messages = []
    out = freespace.remove_font_lines(["heb"], lambda s, m: messages.append((s, m)))
    assert out["heb"]["lines"] == 12 and out["heb"]["bytes"] > 0 and "grc" not in out
    assert snapshot("heb", keep) == before_heb and snapshot("grc", keep) == before_grc
    assert not pool.items("heb", sources=("synthetic",))
    assert len(pool.items("grc", sources=("synthetic",))) == 12
    assert all(m["source"] != "synthetic" for m in pool.index("heb").values())
    assert "font: A" not in pool.books("heb") and "font: A" in pool.books("grc")
    assert not (pool.root() / "heb" / "font-a").exists()
    assert len(messages) <= 6
    assert freespace.remove_font_lines(None)["grc"]["lines"] == 12
    assert not pool.items("grc", sources=("synthetic",))


def test_font_made_again(data):
    w = pool.Writer("heb")
    assert synthtrain.font_done(pool.books("heb"), w, "font: A", "abc")
    freespace.remove_font_lines(["heb"])
    w = pool.Writer("heb")
    assert not synthtrain.font_done(pool.books("heb"), w, "font: A", "abc")
    # even with the record kept, no images means not done
    pool.set_book("heb", "font: A", source="synthetic", stamp="abc")
    assert not synthtrain.font_done(pool.books("heb"), w, "font: A", "abc")


def test_cache_and_leftovers(data):
    assert freespace.remove_cache() == 5000
    assert not (data / "training-cache").exists()
    assert freespace.remove_leftovers() == 500
    assert not (data / "work" / "old.ectype").exists() and (data / "work" / "new.ectype" / "y.png").exists()


def test_stop_midway_is_consistent(data, monkeypatch):
    flag = stopping.StopFlag()
    real = pool.Writer.remove_book
    calls = []

    def once(self, book, source=None):
        n = real(self, book, source)
        calls.append(book)
        flag.request()  # stop after the first font
        return n

    monkeypatch.setattr(pool.Writer, "remove_book", once)
    with stopping.active(flag), pytest.raises(stopping.Stopped):
        freespace.remove_font_lines(["heb"])
    assert len(calls) == 1
    index = pool.index("heb")
    for i, m in index.items():
        assert (pool.root() / "heb" / pool.slug(m["book"]) / f"{i}.png").is_file()
    on_disk = {p.stem for p in (pool.root() / "heb").glob("*/*.png")}
    assert on_disk == set(index)
    assert len(pool.items("heb", sources=("synthetic",))) == 6


def test_cli(data, capsys):
    assert cli.main(["free-space"]) == 0
    out = capsys.readouterr().out
    assert "heb" in out and "never removed" in out and "--font-lines" in out
    assert cli.main(["free-space", "--cache", "--font-lines", "heb"]) == 0
    out = capsys.readouterr().out
    assert "Removed 12 lines set from fonts in heb" in out
    assert not pool.items("heb", sources=("synthetic",)) and len(pool.items("grc", sources=("synthetic",))) == 12
    assert not (data / "training-cache").exists()
    assert len(pool.items("heb", sources=("correction",))) == 3


def test_gui(data, monkeypatch):
    qt = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ectype.gui import FreeSpaceDialog, MainWindow
    from ectype.project import Project

    app = qt.QApplication.instance() or qt.QApplication([])
    w = MainWindow(Project.for_scan(data.parent / "scan.pdf"))
    menus = {a.text(): a.menu() for a in w.menuBar().actions()}
    assert "Free up space…" in [a.text() for a in menus["&Settings"].actions()]
    d = FreeSpaceDialog(w)
    d.sizer.wait()
    app.processEvents()
    assert [k for _, _, k, _ in d.checks] == ["cache", "leftovers", "project_work", "font", "font"]
    assert [b.isChecked() for b, *_ in d.checks] == [True, True, True, False, False]
    assert d.chosen() == {"cache": True, "leftovers": True, "project_work": True, "langs": []}
    d.checks[3][0].setChecked(True)
    assert d.chosen()["langs"] == [d.checks[3][3]]
    d.close()
    d.deleteLater()
    w.close()
    w.deleteLater()
    app.processEvents()


def test_gui_free(data, monkeypatch):
    qt = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ectype import gui
    from ectype.project import Project

    app = qt.QApplication.instance() or qt.QApplication([])
    said = []
    monkeypatch.setattr(gui.QMessageBox, "information", lambda *a: said.append(a[2]))
    w = gui.MainWindow(Project.for_scan(data.parent / "scan.pdf"))
    w.free_space({"cache": True, "leftovers": False, "langs": ["grc"]})
    w.task.wait()
    for _ in range(20):
        app.processEvents()
    assert said and "corrections were not touched" in said[0]
    assert not (data / "training-cache").exists() and not pool.items("grc", sources=("synthetic",))
    assert pool.items("heb", sources=("synthetic",))
    w.close()
    w.deleteLater()
    app.processEvents()


def test_project_working_files(data):
    """Beaux: a book's project swelled to 124 GB. Its glyph-grouping cache is working files only:
    listed and removable; the project's readings and corrections stay."""
    book = data / "projects" / "Grammar.ectype"
    (book / "cluster" / "cache" / "v-abc").mkdir(parents=True)
    (book / "cluster" / "cache" / "v-abc" / "g.npz").write_bytes(b"g" * 900)
    (book / "ocr").mkdir()
    (book / "ocr" / "p001.ocr.json").write_text("{}")
    (book / "corrections").mkdir()
    (book / "corrections" / "p001.json").write_text("{}")
    assert freespace.report()["project_work"]["bytes"] == 900
    assert freespace.remove_project_work() == 900
    assert not (book / "cluster" / "cache").exists()
    assert (book / "ocr" / "p001.ocr.json").is_file() and (book / "corrections" / "p001.json").is_file()


def test_an_older_versions_glyph_cache_is_removed_at_the_next_run(tmp_path):
    """Every update and every stopped run left a whole copy of the cache behind (only a finished
    run removed what it hadn't used). Another version's folder, or files an older version left at
    the top, go when the cache is opened; the pages' own results stay."""
    from ectype.cluster import Cache

    root = tmp_path / "cache"
    for old in (root / "v-0000000000000000", root / "average", root / "group", root / "pages"):
        old.mkdir(parents=True)
        (old / "x.npz").write_bytes(b"o" * 100)
    cache = Cache(root)
    assert sorted(p.name for p in root.iterdir()) == ["pages"]
    assert cache.folder.parent == root and cache.folder.name.startswith("v-")
