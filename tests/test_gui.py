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
    assert all(it.status == "fixed" for it in w.review.items if (it.line, it.word) == (item.line, item.word))
    ocr = json.loads((project.workspace / "ocr" / "p001L.ocr.json").read_text())
    assert ocr["lines"][item.line]["words"][item.word]["text"].startswith("Corrected")
    assert (project.workspace / "training").is_dir()
    # A flagged word that was read right: saved unchanged, it is confirmed (green).
    other = next(it for it in w.review.items if (it.line, it.word) != (item.line, item.word))
    w.review.select(other)
    w.review.save()
    assert w.review.info.text() == f"Confirmed: {other.text}"
    assert all(it.status == "fixed" for it in w.review.items if (it.line, it.word) == (other.line, other.word))
    assert (other.line, other.word) in project.corrected("p001L")
    # A word with punctuation beside it: the mark and the letters are boxes of their own.
    punct = next((it for it in w.review.items if it.span and len(it.text) == 1 and not it.text.isalnum()), None)
    assert punct is not None, "the test page has words with punctuation"
    letters = next(it for it in w.review.items if (it.line, it.word) == (punct.line, punct.word) and it is not punct)
    before = letters.text
    w.review.select(punct)
    w.review.edit.setText(";")
    w.review.save()
    word = project.ocr("p001L").lines[punct.line].words[punct.word].text
    assert ";" in word and before in word, "the mark changed, the letters kept"
    # A letter the OCR left out of a word's box: the box stretched over it, the letter typed.
    from PySide6.QtCore import QRectF

    target = next(it for it in w.review.items if it.span is None and len(it.text) > 2 and it.text.isalpha())
    w.review.select(target)
    r = w.review.editor.rect()
    w.review.editor.setRect(r.adjusted(-25, 0, 0, 0))
    w.review.edit.setText("Z" + target.text)
    w.review.save()
    got = project.ocr("p001L").lines[target.line].words[target.word]
    assert got.text == "Z" + target.text and got.box[0] == round(r.left() - 25) and "box changed" in w.review.info.text()
    # A word the OCR missed altogether: a box drawn and typed, in its line.
    line = project.ocr("p001L").lines[0]
    x1 = line.box[2]
    w.review._drawn(QRectF(x1 + 10, line.box[1], 60, line.box[3] - line.box[1]))
    w.review.edit.setText("added")
    w.review.save()
    assert project.ocr("p001L").lines[0].words[-1].text == "added" and w.review.info.text() == "Added: added"

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

    # IDML: the choice comes first, before anything is prepared (a damaged font used to stop
    # the export before it was shown); Facsimile's fonts follow, once compared.
    first = IdmlDialog(None, w)
    first.facsimile.setChecked(True)
    assert first.table is None and first.options().mode == "facsimile" and first.options().faces == {}
    from ectype.gui import FontChoiceDialog

    assert project.prepare_idml(match_fonts=False)["matches"] is None
    prepared = project.prepare_idml()
    assert FontChoiceDialog(prepared["matches"], w).table.rowCount() >= 1
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


def test_training_shows_how_far_along_and_for_how_long(project):
    """A training's bar follows its "fraction" messages (which aren't lines of the Log), and the
    window shows how long it has been running; the Log keeps how long it took."""
    import time

    from PySide6.QtWidgets import QApplication

    from ectype.gui import MainWindow, elapsed_text

    assert [elapsed_text(s) for s in (42, 725, 4 * 3600 + 451)] == ["0:42", "12:05", "4:07:31"]
    app = QApplication.instance() or QApplication([])
    w = MainWindow(project)
    gate = []

    def work(progress):
        progress("train", "heb: training on 360 images")
        progress("fraction", "0.4200")
        while not gate:
            time.sleep(0.01)
        return 1

    done = []
    w._start(work, done.append, "Training the OCR models", measured=True)
    window = w.progress_window
    for _ in range(300):
        app.processEvents()
        if window.bar.value() == 420:
            break
        time.sleep(0.01)
    assert window.bar.maximum() == 1000 and window.bar.value() == 420
    assert window.clock.text().startswith("Running for 0:0")
    gate.append(1)
    w.task.wait()
    for _ in range(50):
        app.processEvents()
        if done:
            break
    log = w.log.toPlainText()
    assert done == [1] and "fraction" not in log and "train: heb: training on 360 images" in log
    assert "Took 0:0" in log and w.took.startswith("0:0"), "the report says how long it took"
    w.close()


def test_the_ectype_icon():
    """The app's icon, at every size it comes in, from the package (as the installed app has it)."""
    from PySide6.QtWidgets import QApplication

    from ectype.gui import app_icon

    QApplication.instance() or QApplication([])
    icon = app_icon()
    assert not icon.isNull() and {s.width() for s in icon.availableSizes()} >= {16, 32, 48, 256}


def test_work_leaves_the_tab_in_view(project):
    """Starting work shows its progress window; the Log tab isn't brought forward."""
    from PySide6.QtWidgets import QApplication

    from ectype.gui import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(project)
    w.tabs.setCurrentWidget(w.review_stack)
    done = []
    w._start(lambda progress: 1, done.append, "A task")
    assert w.tabs.currentWidget() is w.review_stack
    w.task.wait()
    for _ in range(50):
        app.processEvents()
        if done:
            break
    assert done == [1] and "— A task —" in w.log.toPlainText()
    w.close()


def test_special_characters_go_into_the_correction(project, tmp_path, monkeypatch):
    """The character pane and the menu of characters used last insert at the cursor; a
    combining mark goes after the letter before it."""
    from PySide6.QtWidgets import QApplication

    from ectype import characters
    from ectype.gui import MainWindow

    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    app = QApplication.instance() or QApplication([])
    w = MainWindow(project)
    review = w.review
    review.edit.setText("Noomi")
    review.edit.setCursorPosition(2)
    review.show_pane()
    review.pane.find.setText("modifier letter left half ring")
    assert review.pane.grid.count() >= 1
    review.pane.grid.picked.emit(review.pane.grid.item(0).data(gui_role()))
    assert review.edit.text() == "Noʿomi"
    review.edit.setCursorPosition(4)
    review.insert_char("◌̆")  # a breve over the o
    assert review.edit.text() == "Noʿŏmi".replace("ŏ", "ŏ") and characters.recent()[:2] == ["◌̆", "ʿ"]
    review._fill_special_menu()
    assert [a.text() for a in review.special_menu.actions()][-1] == "More characters…"
    review.pane.find.clear()
    review.pane.sets.setCurrentText("Superscripts")
    assert review.pane.grid.item(0).text() == "¹"
    review.pane.close()
    w.close()


def gui_role():
    from PySide6.QtCore import Qt

    return Qt.ItemDataRole.UserRole


def test_bold_italic_and_small_capitals_set_in_review(project):
    """Ctrl+I (and the I button), Ctrl+B and Ctrl+Shift+K set a word's style over what was
    detected; the box shows it, and saving keeps it in the correction log."""
    import json

    from PySide6.QtWidgets import QApplication

    from ectype.review import word_styles

    from ectype.gui import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(project)
    review = w.review
    review.show_page(review.list.currentRow())
    item = next(it for it in review.items if it.span is None and it.text.isalpha())
    review.select(item)
    assert not review.style["italic"] and not review.style_buttons["italic"].isChecked()
    review.toggle_style("italic")
    review.toggle_style("smallcaps")
    assert review.style_buttons["italic"].isChecked() and review.edit.font().italic()
    assert review.edit.font().capitalization() == review.edit.font().Capitalization.SmallCaps
    review.save()
    assert word_styles(project.workspace, review.page)[(item.line, item.word)] == {"italic": True, "bold": False, "smallcaps": True}
    assert "italic, small capitals" in review.info.text()
    again = next(it for it in review.items if (it.line, it.word) == (item.line, item.word))
    review.select(again)
    assert review.style["italic"] and review.style["smallcaps"], "shown again when the word is chosen again"
    w.close()


def test_manuscript_mode_menu_and_the_kraken_view(tmp_path, monkeypatch):
    """Manuscript Mode: the Languages menu's switch and hands, the Review tab's Kraken view
    (correct a line, cut and join lines), sample pages to train for the book."""
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication

    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    from test_manuscript import FakeKraken, _book, _hand

    from ectype import manuscript
    from ectype.gui import MainWindow

    app = QApplication.instance() or QApplication([])
    p = _book(tmp_path, pages=3)
    a = _hand(tmp_path, "Square")
    p.set_hands("heb", [a])
    fake = FakeKraken({manuscript.hands()[a]["file"]: [("אבג", 95.0), ("דהו", 55.0), ("זח", 97.0)]})
    manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    w = MainWindow(p)
    assert w.langs.text() == "Manuscript: Hebrew (Square)"
    labels = [x.text() for x in w.langs.menu_.actions()]
    assert labels[0] == "Switch to Print Mode" and "Hebrew  (1 hand)" in labels and "Greek" in labels
    assert w.review_stack.currentWidget() is w.ms_review and not w.tabs.isTabVisible(w.tabs.indexOf(w.glyphs))
    assert w.actions["train_book"].isEnabled() and not w.actions["train_fonts"].isEnabled()
    r = w.ms_review
    assert r.list.count() == 3 and len(r.items) == 3
    assert [it.status for it in r.items] == ["plain", "unsure", "plain"]
    r.select(r.items[1])
    assert r.edit.text() == "דהו" and "55% sure" in r.info.text()
    r.edit.setText("דהז")
    r.save()
    assert r.items[1].status == "fixed" and r.selected.line["index"] == 2, "on to the next line"
    assert [w_.text for w_ in p.ocr("p001").lines[1].words] == ["דהז"]
    r.start_split()
    r._clicked(r.items[0], QPointF(200, 60))
    assert len(r.items) == 4 and manuscript.edited_pages(p.workspace) == ["p001"]
    r.select(r.items[0])
    r.join()
    assert len(r.items) == 3
    r.show_view("least")
    assert r.list.count() >= 1
    p.choose_sample(pages=["p002"])
    r.show_view("sample")
    assert r.list.count() == 1 and r.page == "p002"
    w._script_requested("print")
    assert w.langs.text().startswith("Languages:") and w.review_stack.currentWidget() is w.review
    assert [x.text() for x in w.langs.menu_.actions()][0] == "Switch to Manuscript Mode"
    w.close()
    app.processEvents()
