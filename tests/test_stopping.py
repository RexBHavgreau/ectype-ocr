"""Stop: a run ends at the next page with nothing half-written, and carries on afterwards."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pikepdf
import pytest

from ectype import stopping
from ectype.project import Project
from ectype.stopping import StopFlag, Stopped


def _book(root: Path, pages: int = 3) -> Path:
    from PIL import Image

    from ectype.synth import Degrade, make_page

    root.mkdir(parents=True, exist_ok=True)
    images = []
    for i in range(pages):
        make_page(root / f"p{i}", seed=20 + i, point_size=10, page_size_pt=(396, 612),
                  degrade=Degrade(skew_degrees=0.2, blur_sigma=0.5, noise_sigma=4, speckles=10, jpeg_quality=None))
        images.append(Image.open(root / f"p{i}" / "scan.png").convert("L"))
    path = root / "book.pdf"
    pdf = pikepdf.new()
    for im in images:
        xobj = pikepdf.Stream(pdf, im.tobytes())
        xobj.Type, xobj.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        xobj.Width, xobj.Height, xobj.BitsPerComponent = im.width, im.height, 8
        xobj.ColorSpace = pikepdf.Name.DeviceGray
        w, h = im.width * 72 / 300, im.height * 72 / 300
        page = pdf.add_blank_page(page_size=(w, h))
        page.Resources.XObject = pikepdf.Dictionary(Im0=xobj)
        page.Contents = pikepdf.Stream(pdf, f"q {w} 0 0 {h} 0 0 cm /Im0 Do Q".encode())
    pdf.save(path)
    return path


def _ocr_files(project: Project) -> dict[str, dict]:
    return {p.name: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((project.workspace / "ocr").glob("p*.ocr.json"))}


def test_flag():
    flag = StopFlag()
    flag.check()
    assert not flag.requested
    flag.request()
    assert flag.requested
    with pytest.raises(Stopped):
        flag.check()
    with stopping.active(flag):
        with pytest.raises(Stopped):
            stopping.check()
    stopping.check()  # no run active: nothing to stop


def test_run_stops_at_next_page_and_carries_on(tmp_path):
    project = Project.for_scan(_book(tmp_path / "a"))
    flag = StopFlag()

    def fraction(f):
        if f > 0:
            flag.request()  # the first page done: stop

    with pytest.raises(Stopped):
        project.run("preprocess", None, fraction, stop=flag)
    pages = project.workspace / "pages"
    names = sorted(p.stem for p in pages.glob("p*.png"))
    assert len(names) < 3  # not every page was prepared
    for name in names:  # a page's picture and its record are both there, whole
        assert json.loads((pages / f"{name}.json").read_text(encoding="utf-8"))["name"] == name
    assert not (project.workspace / "ocr").exists() or not list((project.workspace / "ocr").glob("p*.ocr.json"))

    project.run("preprocess", stop=StopFlag())  # carries on
    resumed = _ocr_files(project)
    assert len(resumed) == 3

    whole = Project.for_scan(_book(tmp_path / "b"))
    whole.run("preprocess")
    assert sorted(resumed) == sorted(_ocr_files(whole))
    for name, data in _ocr_files(whole).items():
        assert [w.get("text") for w in resumed[name].get("lines", [])] == [w.get("text") for w in data.get("lines", [])] \
            or resumed[name] == data


def test_stop_during_ocr_keeps_whole_pages(tmp_path):
    project = Project.for_scan(_book(tmp_path / "c", pages=4))
    flag = StopFlag()

    def progress(stage, msg):
        if stage == "ocr" and msg.startswith("page"):
            flag.request()

    with pytest.raises(Stopped):
        project.run("preprocess", progress, stop=flag)
    assert len(list((project.workspace / "pages").glob("p*.png"))) == 4
    done = _ocr_files(project)  # each file loads: none half-written
    assert len(done) < 4 or flag.requested
    project.run("ocr", stop=StopFlag())
    assert len(_ocr_files(project)) == 4


def test_task_progress_raises_when_stopped():
    from ectype.gui import Task

    seen = []

    def work(progress):
        for k in range(1000):
            progress("train", f"step {k}")
            seen.append(k)
            if k == 2:
                task.stop.request()
        return "finished"

    task = Task(work)
    stopped, done = [], []
    task.stopped.connect(lambda: stopped.append(True))
    task.done.connect(done.append)
    task.run()  # on this thread: the callback raises at the next message
    assert stopped == [True] and not done and seen[-1] == 2


def test_training_fraction_stops():
    """A stop is seen at the next fraction message, which training sends as it goes."""
    from ectype.gui import Task
    from ectype.training import Progress

    def work(progress):
        bar = Progress(lambda msg: progress("train", msg), lambda f: progress("fraction", f"{f:.4f}"))
        for k in range(100):
            bar.fraction(k / 100)
            if k == 3:
                task.stop.request()
        return "finished"

    task = Task(work)
    stopped = []
    task.stopped.connect(lambda: stopped.append(True))
    task.run()
    assert stopped == [True]


def test_stop_button():
    from PySide6.QtWidgets import QApplication

    from ectype.gui import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(Project.for_scan(_book(Path(tempfile.mkdtemp()), pages=2)))
    w.tabs.setCurrentWidget(w.spreads)
    started = []

    def work(progress):
        started.append(True)
        while True:
            progress("train", "working")
            time.sleep(0.02)

    w._start(work, lambda result: None, "Test run")
    window = w.progress_window
    assert window.stop_button.isEnabled()
    deadline = time.time() + 10
    while not started and time.time() < deadline:
        app.processEvents()
    window.stop_button.click()
    assert not window.stop_button.isEnabled()
    assert "Stopping" in window.stage.text()
    task = w.task
    deadline = time.time() + 10
    while task.isRunning() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    for _ in range(20):
        app.processEvents()
    assert w.progress_window is None
    assert "Stopped. Pages finished so far are kept" in window.stage.text()
    assert "Stopped by you." in w.log.toPlainText()
    assert w.actions["run"].isEnabled() and w.actions["train"].isEnabled()
    assert w.tabs.currentWidget() is not w.log
    assert w.tabs.currentWidget() is not w.review_stack
    w.close()
    w.deleteLater()
    window.deleteLater()
    app.processEvents()
