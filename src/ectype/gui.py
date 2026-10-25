"""The Ectype desktop app (PySide6/Qt), over the same core as the command line.

    ectype gui [scan.pdf | project folder]

Tabs: Spreads (where each scan page splits, with a draggable gutter line, a
"split this spread" switch and rotate buttons), Review (the scan and the output
side by side; click a word to correct it), Glyphs (the averaged glyph sheets and
the probable misreadings), Log. Run builds the PDF; "Update after corrections"
reruns only the stages corrections affect; Export saves the PDF elsewhere.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QImage, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QGraphicsItem, QGraphicsLineItem, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from .project import Project

LOW_CONF = 70.0
COLOURS = {"low": QColor(220, 40, 40), "suspect": QColor(232, 137, 12), "fixed": QColor(26, 152, 80),
           "plain": QColor(40, 90, 220, 70), "selected": QColor(40, 90, 220)}


class Worker(QThread):
    """Runs the pipeline off the interface thread."""

    progress = Signal(str, str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, project: Project, start: str):
        super().__init__()
        self.project, self.start_stage = project, start

    def run(self) -> None:
        try:
            pdf = self.project.run(self.start_stage, lambda stage, msg: self.progress.emit(stage, msg))
            self.done.emit(str(pdf or ""))
        except Exception:
            self.failed.emit(traceback.format_exc())


class ImageView(QGraphicsView):
    """A zoomable view; Ctrl+wheel zooms, and `zoomed` lets a partner view follow."""

    zoomed = Signal(float)

    def __init__(self):
        self._scene = QGraphicsScene()  # held here, or Python frees it under Qt
        super().__init__(self._scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            self.zoomed.emit(factor)
        else:
            super().wheelEvent(event)

    def show_image(self, image: QImage) -> QGraphicsPixmapItem:
        self.scene().clear()
        item = self.scene().addPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(QRectF(image.rect()))
        return item

    def fit(self) -> None:
        if not self.scene().sceneRect().isEmpty():
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


def _pdf_page_image(pdf: Path, index: int, dpi: float = 150) -> QImage | None:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        if index >= len(doc):
            return None
        pil = doc[index].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        doc.close()
    data = pil.tobytes()
    return QImage(data, pil.width, pil.height, pil.width * 3, QImage.Format.Format_RGB888).copy()


# ---------------------------------------------------------------------------
# Spreads


class GutterLine(QGraphicsLineItem):
    """A vertical line that can only be dragged sideways; reports where it was dropped."""

    def __init__(self, x: float, height: float, on_drop):
        super().__init__(0, 0, 0, height)
        self.setPos(x, 0)
        self.on_drop = on_drop
        self.setPen(QPen(QColor(220, 40, 40), 3))
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            return QPointF(value.x(), 0)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.on_drop(self.pos().x())


class SpreadsTab(QWidget):
    changed = Signal(str)

    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        self.list = QListWidget()
        self.list.setMaximumWidth(260)
        self.list.currentRowChanged.connect(self.show_source)
        self.view = ImageView()
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.split = QCheckBox("Split this spread")
        self.split.toggled.connect(self.toggle_split)
        rotate_left = QPushButton("Rotate left")
        rotate_left.clicked.connect(lambda: self.rotate(-90))
        rotate_right = QPushButton("Rotate right")
        rotate_right.clicked.connect(lambda: self.rotate(90))
        reset = QPushButton("Back to automatic")
        reset.clicked.connect(self.reset)
        self.note = QLabel()
        self.note.setWordWrap(True)
        controls = QHBoxLayout()
        for w in (self.split, rotate_left, rotate_right, reset):
            controls.addWidget(w)
        controls.addStretch()
        right = QVBoxLayout()
        right.addLayout(controls)
        right.addWidget(self.note)
        right.addWidget(self.view, 1)
        layout = QHBoxLayout(self)
        layout.addWidget(self.list)
        holder = QWidget()
        holder.setLayout(right)
        layout.addWidget(holder, 1)
        self.sources: list[dict] = []

    def refresh(self) -> None:
        self.sources = self.project.sources()
        overrides = self.project.overrides()
        row = max(0, self.list.currentRow())
        self.list.blockSignals(True)
        self.list.clear()
        for src in self.sources:
            ov = overrides.get(str(src["page"]), {})
            if ov.get("split") is False or (src["split_x"] is None and "split" not in ov):
                how = "not split"
            else:
                how = f"split at {ov.get('split', src['split_x'])}" + (" (yours)" if "split" in ov else "")
            flag = " ⚠" if src["flags"] else ""
            QListWidgetItem(f"Scan page {src['page']}: {how}{flag}", self.list)
        self.list.blockSignals(False)
        if self.sources:
            self.list.setCurrentRow(min(row, len(self.sources) - 1))
            self.show_source(self.list.currentRow())
        else:
            self.note.setText("Open a scan and press Run to see its pages here.")

    def _current(self) -> dict | None:
        row = self.list.currentRow()
        return self.sources[row] if 0 <= row < len(self.sources) else None

    def show_source(self, row: int) -> None:
        src = self._current()
        if src is None:
            return
        ov = self.project.overrides().get(str(src["page"]), {})
        image = QImage(str(self.project.workspace / "preprocess" / src["view"]))
        pending = (ov.get("rotate", src["rotate"]) - src["rotate"]) % 360
        if pending:
            image = image.transformed(QTransform().rotate(pending))
        self.view.show_image(image)
        w0, h0 = src["size_px"]
        upright_w = h0 if src["rotate"] in (90, 270) else w0
        self.scale = image.width() / upright_w if not pending else None
        split = ov.get("split", src["split_x"])
        self.split.blockSignals(True)
        self.split.setChecked(split not in (None, False))
        self.split.blockSignals(False)
        if pending:
            self.note.setText(f"Turned {pending}° from what was detected. Run to apply; the split is found again afterwards.")
        elif split not in (None, False):
            self.view.scene().addItem(GutterLine(split * self.scale, image.height(), self.drop_gutter))
            self.note.setText(("Drag the red line to where the pages meet. " if src["spread"] else "")
                              + ("Flags: " + ", ".join(src["flags"]) if src["flags"] else ""))
        else:
            self.note.setText("This scan page is kept whole.")
        self.view.fit()

    def drop_gutter(self, x_view: float) -> None:
        src = self._current()
        if src is None or not self.scale:
            return
        self.project.set_split(src["page"], int(round(x_view / self.scale)))
        self.changed.emit(f"Scan page {src['page']} will split at {int(round(x_view / self.scale))}. Run to apply.")
        self._relabel()

    def toggle_split(self, on: bool) -> None:
        src = self._current()
        if src is None:
            return
        if on:
            self.project.set_split(src["page"], src["split_x"] if src["split_x"] is not None else
                                   (src["size_px"][1] if src["rotate"] in (90, 270) else src["size_px"][0]) // 2)
        else:
            self.project.set_split(src["page"], False)
        self.changed.emit(f"Scan page {src['page']} will be {'split' if on else 'kept whole'}. Run to apply.")
        self._relabel()
        self.show_source(self.list.currentRow())

    def rotate(self, degrees: int) -> None:
        src = self._current()
        if src is None:
            return
        current = self.project.overrides().get(str(src["page"]), {}).get("rotate", src["rotate"])
        self.project.set_rotation(src["page"], current + degrees)
        self.changed.emit(f"Scan page {src['page']} will be turned. Run to apply.")
        self.show_source(self.list.currentRow())

    def reset(self) -> None:
        src = self._current()
        if src is None:
            return
        self.project.reset_page(src["page"])
        self.changed.emit(f"Scan page {src['page']} is back to automatic. Run to apply.")
        self._relabel()
        self.show_source(self.list.currentRow())

    def _relabel(self) -> None:
        row = self.list.currentRow()
        self.refresh()
        self.list.setCurrentRow(row)


# ---------------------------------------------------------------------------
# Review


class WordItem(QGraphicsRectItem):
    def __init__(self, rect: QRectF, line: int, word: int, text: str, status: str, on_click):
        super().__init__(rect)
        self.line, self.word, self.text, self.on_click = line, word, text, on_click
        self.set_status(status)
        self.setToolTip(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_status(self, status: str) -> None:
        self.status = status
        colour = COLOURS[status]
        self.setPen(QPen(colour, 1 if status == "plain" else 3))
        self.setBrush(QBrush(QColor(colour.red(), colour.green(), colour.blue(), 40)) if status == "fixed" else QBrush())

    def mousePressEvent(self, event):
        self.on_click(self)


class ReviewTab(QWidget):
    message = Signal(str)

    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        self.list = QListWidget()
        self.list.setMaximumWidth(200)
        self.list.currentRowChanged.connect(self.show_page)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Click a word on the scan to correct it")
        self.edit.returnPressed.connect(self.save)
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        self.info = QLabel()
        legend = QLabel('<span style="color:#dc2828">■ low OCR confidence</span> &nbsp; '
                        '<span style="color:#e8890c">■ glyph looks like another character</span> &nbsp; '
                        '<span style="color:#1a9850">■ corrected</span>')
        bar = QHBoxLayout()
        bar.addWidget(self.edit, 1)
        bar.addWidget(save)
        bar.addWidget(self.info, 1)
        self.scan, self.output = ImageView(), ImageView()
        self.scan.zoomed.connect(lambda f: self.output.scale(f, f))
        self.output.zoomed.connect(lambda f: self.scan.scale(f, f))
        for a, b in ((self.scan, self.output), (self.output, self.scan)):
            a.horizontalScrollBar().valueChanged.connect(lambda v, b=b, a=a: self._follow(a, b, Qt.Orientation.Horizontal))
            a.verticalScrollBar().valueChanged.connect(lambda v, b=b, a=a: self._follow(a, b, Qt.Orientation.Vertical))
        split = QSplitter()
        split.addWidget(self.scan)
        split.addWidget(self.output)
        right = QVBoxLayout()
        right.addLayout(bar)
        right.addWidget(legend)
        right.addWidget(split, 1)
        holder = QWidget()
        holder.setLayout(right)
        layout = QHBoxLayout(self)
        layout.addWidget(self.list)
        layout.addWidget(holder, 1)
        self.items: list[WordItem] = []
        self.selected: WordItem | None = None
        self.page: str | None = None
        self._syncing = False

    def _follow(self, a: ImageView, b: ImageView, orientation) -> None:
        if self._syncing:
            return
        self._syncing = True
        sa = a.horizontalScrollBar() if orientation == Qt.Orientation.Horizontal else a.verticalScrollBar()
        sb = b.horizontalScrollBar() if orientation == Qt.Orientation.Horizontal else b.verticalScrollBar()
        if sa.maximum() > 0:
            sb.setValue(round(sa.value() / sa.maximum() * sb.maximum()))
        self._syncing = False

    def refresh(self) -> None:
        pages = self.project.pages()
        current = self.page
        self.flags = self.project.suspect_words()
        self.list.blockSignals(True)
        self.list.clear()
        for page in pages:
            ocr = self.project.ocr(page)
            low = sum(w.conf < LOW_CONF for w in ocr.words()) if ocr else 0
            sus = len(self.flags.get(page, ()))
            mark = f"  ({low + sus})" if low + sus else ""
            QListWidgetItem(page + mark, self.list).setData(Qt.ItemDataRole.UserRole, page)
        self.list.blockSignals(False)
        if pages:
            self.list.setCurrentRow(pages.index(current) if current in pages else 0)
            self.show_page(self.list.currentRow())

    def show_page(self, row: int) -> None:
        item = self.list.item(row)
        if item is None:
            return
        page = item.data(Qt.ItemDataRole.UserRole)
        self.page = page
        self.scan.show_image(QImage(str(self.project.page_image(page))))
        self.items, self.selected = [], None
        ocr = self.project.ocr(page)
        fixed = self.project.corrected(page)
        flagged = self.flags.get(page, set())
        if ocr:
            for li, line in enumerate(ocr.lines):
                for wi, w in enumerate(line.words):
                    status = ("fixed" if (li, wi) in fixed else "suspect" if (li, wi) in flagged
                              else "low" if w.conf < LOW_CONF else "plain")
                    x0, y0, x1, y1 = w.box
                    it = WordItem(QRectF(x0, y0, x1 - x0, y1 - y0), li, wi, w.text, status, self.select)
                    self.scan.scene().addItem(it)
                    self.items.append(it)
        pdf = self.project.output_pdf()
        pages = self.project.pages()
        image = _pdf_page_image(pdf, pages.index(page), dpi=self._page_dpi(page)) if pdf else None
        if image is not None:
            self.output.show_image(image)
        else:
            self.output.scene().clear()
            self.output.scene().addText("Run to see the output here.")
        self.scan.fit()
        self.output.fit()
        self.edit.clear()
        self.info.clear()

    def _page_dpi(self, page: str) -> float:
        from PIL import Image

        from .report import image_dpi

        return image_dpi(Image.open(self.project.page_image(page)))

    def select(self, item: WordItem) -> None:
        if self.selected is not None and self.selected is not item:
            self.selected.set_status(self.selected.status)
        self.selected = item
        item.setPen(QPen(COLOURS["selected"], 3))
        self.edit.setText(item.text)
        self.edit.setFocus()
        self.edit.selectAll()
        self.info.setText(f"line {item.line + 1}, word {item.word + 1}")

    def select_word(self, page: str, line: int, word: int) -> None:
        pages = self.project.pages()
        if page in pages:
            self.list.setCurrentRow(pages.index(page))
            for it in self.items:
                if (it.line, it.word) == (line, word):
                    self.select(it)
                    self.scan.centerOn(it)
                    break

    def save(self) -> None:
        if self.selected is None or self.page is None:
            return
        text = self.edit.text().strip()
        if not text or text == self.selected.text:
            return
        result = self.project.correct(self.page, self.selected.line, self.selected.word, text)
        self.selected.text = result["new"]
        self.selected.setToolTip(result["new"])
        self.selected.set_status("fixed")
        self.info.setText(f"Saved: {result['old']} → {result['new']}")
        self.message.emit("Correction saved. Press “Update after corrections” to rebuild the PDF.")


# ---------------------------------------------------------------------------
# Glyphs


class GlyphsTab(QWidget):
    jump = Signal(str, int, int)

    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        self.sheets = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self.sheets)
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["OCR text", "Looks like", "Instances", "Where"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(self._jump)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(scroll)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.addWidget(QLabel("Probable OCR misreadings (double-click to correct):"))
        lay.addWidget(self.table)
        split.addWidget(box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        QVBoxLayout(self).addWidget(split)
        self._where: list[tuple[str, int, int]] = []

    def refresh(self) -> None:
        while self.sheets.count():
            w = self.sheets.takeAt(0).widget()
            if w:
                w.deleteLater()
        for path in self.project.style_sheets():
            self.sheets.addWidget(QLabel(f"<b>Style {path.stem.split('-')[1]}</b>"))
            label = QLabel()
            label.setPixmap(QPixmap(str(path)))
            self.sheets.addWidget(label)
        self.sheets.addStretch()
        import json

        units: dict[str, dict[int, tuple[int, int]]] = {}
        self._where = []
        suspects = self.project.suspects()
        self.table.setRowCount(len(suspects))
        for r, s in enumerate(suspects):
            page, unit = s["instances"][0]
            if page not in units:
                seg = self.project.workspace / "segment" / f"{page}.seg.json"
                units[page] = ({u["id"]: (u["line"], u["word"]) for u in json.loads(seg.read_text(encoding="utf-8"))["units"]}
                               if seg.is_file() else {})
            line, word = units[page].get(unit, (0, 0))
            self._where.append((page, line, word))
            for c, v in enumerate([s["text"], s["looks_like"], str(s["count"]), f"{page}, line {line + 1}"]):
                self.table.setItem(r, c, QTableWidgetItem(v))

    def _jump(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._where):
            self.jump.emit(*self._where[row])


# ---------------------------------------------------------------------------
# Main window


class MainWindow(QMainWindow):
    def __init__(self, project: Project | None = None):
        super().__init__()
        self.setWindowTitle("Ectype OCR")
        self.resize(1400, 900)
        self.project = project or Project(Path.home() / ".ectype" / "scratch")
        self.worker: Worker | None = None
        self._build()
        self.load(self.project)

    def _build(self) -> None:
        bar = QToolBar("Main")
        self.addToolBar(bar)
        self.actions: dict[str, QAction] = {}
        for key, text, slot in (("open_scan", "Open scan…", self.open_scan), ("open_project", "Open project…", self.open_project)):
            self.actions[key] = bar.addAction(text, slot)
        bar.addSeparator()
        self.mode = QComboBox()
        self.mode.addItems(["Traced (fonts from the scan)", "Substitute (a real font)"])
        self.mode.currentIndexChanged.connect(self._mode_changed)
        bar.addWidget(self.mode)
        self.split = QCheckBox("Split two-page spreads")
        self.split.toggled.connect(self._split_changed)
        bar.addWidget(self.split)
        bar.addSeparator()
        for key, text, slot in (("run", "Run", lambda: self.run("preprocess")),
                                ("update", "Update after corrections", lambda: self.run("segment")),
                                ("export", "Export PDF…", self.export)):
            self.actions[key] = bar.addAction(text, slot)
        self.tabs = QTabWidget()
        self.spreads = SpreadsTab(self.project)
        self.review = ReviewTab(self.project)
        self.glyphs = GlyphsTab(self.project)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.tabs.addTab(self.spreads, "Spreads")
        self.tabs.addTab(self.review, "Review")
        self.tabs.addTab(self.glyphs, "Glyphs")
        self.tabs.addTab(self.log, "Log")
        self.setCentralWidget(self.tabs)
        self.spreads.changed.connect(self.statusBar().showMessage)
        self.review.message.connect(self.statusBar().showMessage)
        self.glyphs.jump.connect(self._jump)

    def load(self, project: Project) -> None:
        self.project = project
        for tab in (self.spreads, self.review, self.glyphs):
            tab.project = project
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(0 if project.settings.mode == "traced" else 1)
        self.mode.blockSignals(False)
        self.split.blockSignals(True)
        self.split.setChecked(project.settings.split_spreads)
        self.split.blockSignals(False)
        source = Path(project.settings.source).name if project.settings.source else "no scan yet"
        self.setWindowTitle(f"Ectype OCR — {source}")
        self.refresh()
        if project.pages():
            self.tabs.setCurrentWidget(self.review)

    def refresh(self) -> None:
        self.spreads.refresh()
        self.review.refresh()
        self.glyphs.refresh()
        has_pdf = self.project.output_pdf() is not None
        self.actions["export"].setEnabled(has_pdf)
        self.actions["update"].setEnabled(bool(self.project.pages()))
        self.actions["run"].setEnabled(bool(self.project.settings.source))

    def _mode_changed(self, index: int) -> None:
        self.project.settings.mode = "traced" if index == 0 else "substitute"
        self.project.save_settings()
        self.refresh()

    def _split_changed(self, on: bool) -> None:
        self.project.settings.split_spreads = on
        self.project.save_settings()
        self.statusBar().showMessage("Spreads will be " + ("split" if on else "kept whole") + ". Run to apply.")

    def _jump(self, page: str, line: int, word: int) -> None:
        self.tabs.setCurrentWidget(self.review)
        self.review.select_word(page, line, word)

    def open_scan(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open a scan", "", "Scans (*.pdf *.png *.tif *.tiff *.jpg *.jpeg)")
        if path:
            self.load(Project.for_scan(Path(path)))
            self.statusBar().showMessage("Press Run to process the scan.")

    def open_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open an Ectype project folder")
        if path:
            self.load(Project(Path(path)))

    def run(self, start: str) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        for a in self.actions.values():
            a.setEnabled(False)
        self.tabs.setCurrentWidget(self.log)
        self.log.appendPlainText(f"— Running from {start} —")
        self.worker = Worker(self.project, start)
        self.worker.progress.connect(lambda stage, msg: (self.log.appendPlainText(f"{stage}: {msg}"),
                                                        self.statusBar().showMessage(f"{stage}: {msg}")))
        self.worker.done.connect(self._finished)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _finished(self, pdf: str) -> None:
        self.log.appendPlainText(f"Done: {pdf}" if pdf else "Done.")
        self.statusBar().showMessage("Done.")
        for a in self.actions.values():
            a.setEnabled(True)
        self.refresh()
        self.tabs.setCurrentWidget(self.review)

    def _failed(self, error: str) -> None:
        self.log.appendPlainText(error)
        for a in self.actions.values():
            a.setEnabled(True)
        QMessageBox.warning(self, "Ectype OCR", error.strip().splitlines()[-1])

    def export(self) -> None:
        default = str(Path(self.project.settings.source).with_name((self.project.settings.name or "output") + "-ectype.pdf"))
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", default, "PDF (*.pdf)")
        if path:
            self.project.export(Path(path))
            self.statusBar().showMessage(f"Saved {path}")


def main(argv: list[str] | None = None) -> int:
    import multiprocessing

    multiprocessing.freeze_support()  # the packaged app's worker processes start through here
    argv = sys.argv[1:] if argv is None else argv
    app = QApplication.instance() or QApplication(sys.argv[:1])
    project = None
    if argv:
        target = Path(argv[0])
        project = Project(target) if target.is_dir() else Project.for_scan(target)
    window = MainWindow(project)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
