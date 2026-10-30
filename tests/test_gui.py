from __future__ import annotations

import json
import os

import numpy as np
import pikepdf
import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from ectype.project import Project  # noqa: E402
from ectype.synth import Degrade, make_page  # noqa: E402


def _spread_pdf(root):
    """Two synthetic pages side by side as one scanned spread."""
    halves = []
    for i, seed in enumerate((5, 6)):
        make_page(root / f"half{i}", seed=seed, point_size=10, page_size_pt=(396, 612),
                  degrade=Degrade(skew_degrees=0.2, blur_sigma=0.5, noise_sigma=4, speckles=10, jpeg_quality=None))
        halves.append(np.asarray(Image.open(root / f"half{i}" / "scan.png")))
    image = np.hstack([halves[0], np.full((halves[0].shape[0], 60), 230, np.uint8), halves[1]])
    pdf = pikepdf.new()
    xobj = pikepdf.Stream(pdf, image.tobytes())
    xobj.Type, xobj.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    xobj.Width, xobj.Height, xobj.BitsPerComponent = image.shape[1], image.shape[0], 8
    xobj.ColorSpace = pikepdf.Name.DeviceGray
    w, h = image.shape[1] * 72 / 300, image.shape[0] * 72 / 300
    page = pdf.add_blank_page(page_size=(w, h))
    page.Resources.XObject = pikepdf.Dictionary(Im0=xobj)
    page.Contents = pikepdf.Stream(pdf, f"q {w} 0 0 {h} 0 0 cm /Im0 Do Q".encode())
    path = root / "book.pdf"
    pdf.save(path)
    return path


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("gui")
    p = Project.for_scan(_spread_pdf(root))
    stages, fractions = [], []
    p.run(progress=lambda stage, msg: stages.append(stage), fraction=fractions.append)
    assert list(dict.fromkeys(stages)) == ["preprocess", "ocr", "segment", "cluster", "fonts", "traced"]
    assert fractions[0] == 0 and fractions[-1] == 1 and fractions == sorted(fractions), "the progress bar only moves on"
    assert len(fractions) > 8, "page by page within stages"
    return p


def test_project_runs_and_splits(project):
    assert project.workspace.name == "book.ectype"
    assert project.pages() == ["p001L", "p001R"]
    assert project.output_pdf() is not None and project.output_pdf().is_file()


def test_overrides_and_rerun(project):
    project.set_split(1, False)
    assert project.overrides() == {"1": {"split": False}}
    project.run("preprocess")
    assert project.pages() == ["p001"], "stale halves are removed"
    project.reset_page(1)
    project.run("preprocess")
    assert project.pages() == ["p001L", "p001R"]


def test_window_review_and_correction(project, tmp_path):
    from PySide6.QtWidgets import QApplication

    from ectype.gui import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(project)
    w.resize(1200, 800)
    app.processEvents()
    assert w.review.list.count() == 2 and w.review.items
    item = w.review.items[0]
    w.review.select(item)
    w.review.edit.setText("Corrected")
    w.review.save()
    assert item.status == "fixed"
    ocr = json.loads((project.workspace / "ocr" / "p001L.ocr.json").read_text())
    assert ocr["lines"][item.line]["words"][item.word]["text"] == "Corrected"
    assert (project.workspace / "training").is_dir()
    # A flagged word that was read right: saved unchanged, it is confirmed (green).
    other = w.review.items[1]
    w.review.select(other)
    w.review.save()
    assert other.status == "fixed" and w.review.info.text() == f"Confirmed: {other.text}"
    assert (other.line, other.word) in project.corrected("p001L")

    w.tabs.setCurrentWidget(w.spreads)
    app.processEvents()
    assert w.spreads.list.count() == 1
    w.spreads.drop_gutter(w.spreads.scale * 1700)
    assert project.overrides()["1"]["split"] == 1700
    w.spreads.rotate(90)
    assert project.overrides()["1"]["rotate"] == 90
    w.spreads.reset()
    assert project.overrides() == {}
    assert not w.grab().isNull()

    out = project.export(tmp_path / "book-ectype.pdf")
    assert out.is_file() and out.stat().st_size > 0
    w.close()


def test_languages_text_mode_and_idml_dialog(project, tmp_path):
    from PySide6.QtWidgets import QApplication

    from ectype.gui import AddLanguagesDialog, IdmlDialog, MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(project)
    app.processEvents()

    # Languages: several can be ticked, the last one can't be unticked.
    assert "eng" in w.langs.boxes and w.langs.boxes["eng"].isChecked()
    w.langs.boxes["eng"].setChecked(False)
    assert w.langs.boxes["eng"].isChecked() and project.languages() == ["eng"]
    w._languages_changed(["eng", "grc"])
    assert project.settings.lang == "eng+grc"
    w.langs.set_languages(project.languages())
    assert "grc" in w.langs.boxes, "a chosen language not yet installed is still listed"
    project.set_languages(["eng"])
    dialog = AddLanguagesDialog(w)
    assert dialog.list.count() > 100 and dialog.chosen() == []

    # Text-layer mode: its own stages and page-image choice.
    w.mode.setCurrentIndex(2)
    assert project.settings.mode == "text" and project.stages() == ["preprocess", "ocr", "textlayer"]
    assert project.update_stage() == "textlayer"
    w.page_image.setCurrentIndex(1)
    assert project.settings.page_image == "bw"
    project.run("textlayer")
    assert project.output_pdf() is not None and project.output_pdf().parent.name == "textlayer"
    w.mode.setCurrentIndex(0)

    # IDML: the dialog offers both layouts, Reflow's options and the matched fonts.
    prepared = project.prepare_idml()
    d = IdmlDialog(prepared, w)
    d.reflow.setChecked(True)
    assert d.reflow_box.isEnabled() and not d.fac_box.isEnabled()
    d.markers.setChecked(True)
    options = d.options()
    assert options.mode == "reflow" and options.page_markers and options.check_below > 0
    d.facsimile.setChecked(True)
    assert d.fac_box.isEnabled() and d.table.rowCount() >= 1
    options = d.options()
    assert options.mode == "facsimile" and options.faces == {} and options.include_scan
    out = project.export_idml(tmp_path / "book.idml", options, prepared)
    assert (tmp_path / "book" / "book.idml").is_file() and out["pages"] == 2
    w.close()


def test_help_menu_and_update_check(project, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ectype import __version__
    from ectype.gui import MainWindow

    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    w = MainWindow(project)
    titles = [a.text() for a in w.menuBar().actions()]
    assert "&Help" in titles
    assert w.check_at_start.isChecked()
    w.check_at_start.setChecked(False)
    from ectype import updates

    assert updates.load_settings()["check_updates"] is False
    said = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: said.append(a[2]))
    w._update_checked({"status": "current"}, manual=True)
    assert said and __version__ in said[0]
    w.close()
    app.processEvents()


def test_select_pdf_shows_the_selection_and_training_route(tmp_path):
    from PySide6.QtWidgets import QApplication

    from ectype.gui import MainWindow

    app = QApplication.instance() or QApplication([])
    scan = _spread_pdf(tmp_path)
    w = MainWindow(Project.for_scan(scan))
    app.processEvents()
    assert w.actions["open_scan"].text() == "Select PDF…"
    assert "book.pdf" in w.source_label.text() and "1 page" in w.source_label.text()
    assert "not read yet" in w.source_label.text() and w.actions["run"].font().bold()
    assert "Selected: book.pdf" in w.spreads.note.text()
    assert w.actions["train"].text() == "Train OCR model…"
    w.offer_languages()  # no other scripts found: nothing asked
    w.close()


def test_progress_window_during_a_run(project):
    from PySide6.QtWidgets import QApplication

    from ectype.gui import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(project)
    w.run("traced")
    window = w.progress_window
    assert window is not None and window.isVisible()
    seen = set()
    while w.worker.isRunning() or w.progress_window is not None:
        app.processEvents()
        if w.progress_window is not None:
            seen.add(window.bar.text())
    assert w.progress_window is None and not window.isVisible(), "closed when the run is done"
    assert "100%" in seen or window.bar.value() == 1000
    assert "traced:" in window.text.toPlainText()
    w.close()
