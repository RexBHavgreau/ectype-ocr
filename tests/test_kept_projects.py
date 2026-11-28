"""A book's project folder is kept only when it is wanted (Beaux: training is "the whole reason the
project folder exists"; Ectype should be "a simple tool to use"), and the pages can be fixed alone:
split, straightened or turned upright, with no OCR (Pages only, `ectype pages`)."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import pikepdf
import pytest
from PIL import Image

from ectype import cli, project as projects
from ectype import preprocess as pre
from ectype.project import Project, projects_dir


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OMP_THREAD_LIMIT", "1")
    return tmp_path / "data"


def _pdf(path: Path, seed: int = 3, turned: int = 0) -> Path:
    """A page of English as a one-picture PDF; `turned`: stored turned that far anticlockwise."""
    from ectype.synth import Degrade, make_page

    path.parent.mkdir(parents=True, exist_ok=True)
    truth = Path(tempfile.mkdtemp()) / path.stem
    make_page(truth, "Inspiration is the word of God given to us. " * 6, seed=seed, point_size=10,
              page_size_pt=(432, 300),
              degrade=Degrade(skew_degrees=0.0, blur_sigma=0.3, noise_sigma=2, speckles=0, jpeg_quality=None))
    image = Image.open(truth / "scan.png").convert("L")
    if turned:
        image = image.rotate(turned, expand=True)
    image.save(path, "PDF", resolution=300)
    return path


def _left(data):
    return [p for p in (data / "projects", data / "work") if p.exists() and any(
        q for q in p.iterdir() if q.name != "index.json")]


def test_a_book_not_kept_leaves_nothing_and_isnt_found(tmp_path, data):
    scan = tmp_path / "Book.pdf"
    scan.write_bytes(b"x")
    p = Project.for_scan(scan, keep=False)
    assert p.workspace.parent == projects_dir() and not p.kept()
    assert Project.find_for_scan(scan) is None, "not in the index until kept"
    assert p.discard() and not p.workspace.exists()


def test_a_correction_keeps_the_book(tmp_path, data):
    scan = tmp_path / "Book.pdf"
    scan.write_bytes(b"x")
    p = Project.for_scan(scan, keep=False)
    told = []
    p.on_kept = told.append
    assert p.keep() and p.kept() and told == [p]
    assert not p.keep(), "kept once"
    assert Project.find_for_scan(scan) == p.workspace
    assert not p.discard() and p.workspace.is_dir(), "a kept project is never discarded"
    assert Project.for_scan(scan, keep=False).workspace == p.workspace


def test_review_corrections_and_training_keep_the_book(tmp_path, data, monkeypatch):
    scan = tmp_path / "Book.pdf"
    scan.write_bytes(b"x")
    for call in ("correct", "delete_word", "add_word", "correct_line"):
        p = Project.for_scan(scan, keep=False)
        monkeypatch.setattr("ectype.review." + call if call != "correct_line" else "ectype.manuscript.correct_line",
                            lambda *a, **k: {})
        getattr(p, call)(*{"correct": ("p001", 0, 0, "x"), "delete_word": ("p001", 0, 0),
                           "add_word": ("p001", [0, 0, 1, 1], "x"), "correct_line": ("p001", 0, "x")}[call])
        assert p.kept(), call
        p.settings.kept = False  # for the next
        p.save_settings()
        p.discard()


def test_an_older_project_counts_as_kept(tmp_path, data):
    ws = tmp_path / "Old.ectype"
    ws.mkdir()
    (ws / "project.json").write_text(json.dumps({"source": "x.pdf", "mode": "text"}), encoding="utf-8")
    p = Project(ws)
    assert p.kept() and not p.discard() and ws.is_dir()


def test_a_new_book_starts_from_the_last_choices(tmp_path, data):
    first = Project.scratch()
    first.remembers = True
    first.settings.mode, first.settings.lang, first.settings.deskew = "text", "eng+grc", False
    first.save_settings()
    assert projects.last_choices()["lang"] == "eng+grc"
    scan = tmp_path / "Next.pdf"
    scan.write_bytes(b"x")
    nxt = Project.for_scan(scan, keep=False, choices=projects.last_choices())
    assert (nxt.settings.mode, nxt.settings.lang, nxt.settings.deskew) == ("text", "eng+grc", False)
    assert nxt.settings.name == "Next" and nxt.settings.source.endswith("Next.pdf")
    kept = Project.for_scan(tmp_path / "Other.pdf")  # a book already kept keeps its own choices
    kept.settings.lang = "heb"
    kept.save_settings()
    assert Project.for_scan(tmp_path / "Other.pdf", keep=False, choices={"lang": "lat"}).settings.lang == "heb"


def test_folders_left_behind_are_removed_later(tmp_path, data):
    old = Project.scratch().workspace
    recent = Project.scratch().workspace
    kept = Project.for_scan(tmp_path / "Kept.pdf").workspace
    past = time.time() - projects.ABANDONED_AFTER - 60
    for folder in (old, kept):
        for path in [folder, *folder.rglob("*")]:
            os.utime(path, (past, past))
    assert projects.abandoned_projects() == [old]
    assert projects.remove_abandoned() == 1
    assert not old.exists() and recent.is_dir() and kept.is_dir()


def test_a_run_keeps_nothing_unless_asked(tmp_path, data):
    scan = _pdf(tmp_path / "scans" / "Libro.pdf")
    assert cli.main(["run", str(scan), "--mode", "pages"]) == 0
    assert sorted(p.name for p in scan.parent.iterdir()) == ["Libro - pages.pdf", "Libro.pdf"]
    assert _left(data) == []


def test_turning_alone_and_straightening_alone(tmp_path, data):
    """Each fix can be had alone; the pages keep the scan's own picture."""
    scan = _pdf(tmp_path / "scans" / "Turned.pdf", turned=90)  # lying on its side
    with pikepdf.open(scan) as pdf:
        stored = next(iter(pdf.pages[0].Resources.XObject.values())).read_raw_bytes()
    ws = tmp_path / "ws"
    s = pre.preprocess(scan, ws, keep_spreads=True, deskew=False, orient=True)
    assert s["pages"][0]["rotate"] in (90, 270) and s["pages"][0]["skew_degrees"] == 0
    s = pre.preprocess(scan, tmp_path / "ws2", keep_spreads=True, deskew=True, orient=False)
    assert s["pages"][0]["rotate"] == 0, "not turned: as a viewer shows it"
    assert cli.main(["pages", str(scan), "--orient"]) == 0
    out = scan.with_name("Turned - pages.pdf")
    with pikepdf.open(out) as pdf:
        box = [float(v) for v in pdf.pages[0].MediaBox]
        assert box[2] - box[0] > box[3] - box[1], "upright: the page is wide again"
        images = [o for o in pdf.objects if isinstance(o, pikepdf.Stream) and o.get("/Subtype") == "/Image"]
        assert [i.read_raw_bytes() for i in images] == [stored], "the scan's picture, not coded again"
    assert cli.main(["pages", str(scan.parent)]) == 0, "a folder: its own results are passed over"
    assert sorted(p.name for p in scan.parent.iterdir()) == ["Turned - pages.pdf", "Turned.pdf"]
    assert _left(data) == []


def test_pages_only_needs_something_to_do(tmp_path, data):
    scan = _pdf(tmp_path / "S.pdf")
    p = Project.for_scan(scan, keep=False)
    p.settings.mode = "pages"
    p.settings.split_spreads = p.settings.deskew = p.settings.orient = False
    with pytest.raises(ValueError, match="at least one"):
        p.run()
    p.settings.deskew = True
    out = p.run()
    assert out == scan.with_name("S - pages.pdf") and out.is_file()
    assert p.update_stage() == "pages"


def test_the_app_keeps_a_book_only_when_wanted(tmp_path, data, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog

    from ectype.gui import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    scratch = w.project.workspace
    assert not w.project.kept() and scratch.parent == projects_dir()
    w.mode.setCurrentIndex([m for m, _ in __import__("ectype.gui", fromlist=["MODES"]).MODES].index("pages"))
    w.deskew.setChecked(False)
    scan = _pdf(tmp_path / "Book.pdf")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(scan), ""))
    w.open_scan()
    app.processEvents()
    assert not scratch.exists(), "the folder before a scan went"
    first = w.project.workspace
    assert not w.project.kept() and w.project.settings.mode == "pages" and not w.project.settings.deskew
    assert w.page_image.model().item(w.page_image.findData("original")).isEnabled(), "Pages only keeps the scan's pictures"
    w.save_project()
    assert w.project.kept() and Project.find_for_scan(scan) == first
    other = _pdf(tmp_path / "Other.pdf", seed=4)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(other), ""))
    w.open_scan()
    assert first.is_dir(), "a kept book stays"
    second = w.project.workspace
    w.discard_unkept()
    assert not second.exists(), "closing leaves nothing of a book never kept"
    w.close()
