"""The Ectype desktop app (PySide6/Qt), over the same core as the command line.

    ectype gui [scan.pdf | project folder]

Tabs: Spreads (where each scan page splits, with a draggable gutter line, a
"split this spread" switch and rotate buttons), Review (the scan and the output
side by side; click a word to correct it), Glyphs (the averaged glyph sheets and
the probable misreadings), Log. Run builds the PDF; "Update after corrections"
reruns only the stages corrections affect; Export saves the PDF elsewhere, and
Export IDML lays the text out as an InDesign document (reflowed, or in facsimile).
The Languages menu chooses the OCR languages and downloads more.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QImage, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGraphicsItem, QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsView, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton, QRadioButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

from . import __version__, languages, updates
from .project import Project

MODES = [("traced", "Traced (fonts from the scan)"), ("substitute", "Substitute (a real font)"),
         ("text", "Text layer only (the scan + OCR)")]

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


class Task(QThread):
    """Runs any function off the interface thread; the function gets a progress callback."""

    progress = Signal(str, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self.fn(lambda stage, msg: self.progress.emit(stage, msg)))
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


def source_pages(path: Path) -> int:
    """How many pages a selected scan has (a page image has one)."""
    if Path(path).suffix.lower() != ".pdf":
        return 1
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception:
        return 0
    try:
        return len(doc)
    finally:
        doc.close()


def _source_preview(path: Path, dpi: float = 60) -> QImage | None:
    """The first page of a selected scan, small, before it has been read."""
    if Path(path).suffix.lower() == ".pdf":
        try:
            return _pdf_page_image(Path(path), 0, dpi)
        except Exception:
            return None
    image = QImage(str(path))
    return None if image.isNull() else image.scaledToWidth(min(image.width(), 900))


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
            source = self.project.settings.source
            if source and Path(source).is_file():
                n = source_pages(Path(source))
                self.note.setText(f"<b>Selected: {Path(source).name}</b> ({n} page{'s' if n != 1 else ''}). "
                                  "Press <b>Run</b> to read it; its pages appear here, ready to check how spreads split.")
                preview = _source_preview(Path(source))
                if preview is not None:
                    self.view.show_image(preview)
                    self.view.fit()
            else:
                self.note.setText("Choose <b>Select PDF…</b> to pick a scanned book, then press <b>Run</b>.")

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
# Languages


class LanguageButton(QToolButton):
    """The OCR languages: a checkable list of the installed ones, and "Add languages…"."""

    changed = Signal(list)
    add_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setPopupMode(QToolButton.InstantPopup)
        self.menu_ = QMenu(self)
        self.setMenu(self.menu_)
        self.boxes: dict[str, QCheckBox] = {}
        self.selected: list[str] = ["eng"]

    def set_languages(self, selected: list[str]) -> None:
        self.selected = list(selected) or ["eng"]
        self.menu_.clear()
        self.boxes = {}
        codes = languages.installed()
        for code in self.selected:  # chosen but not installed (yet): listed, so they can be unticked
            if code not in codes:
                codes.append(code)
        for code in codes:
            box = QCheckBox(languages.name(code) + ("" if code in languages.installed() else " (not installed)"))
            box.setChecked(code in self.selected)
            box.setContentsMargins(6, 2, 6, 2)
            box.toggled.connect(lambda on, c=code: self._toggled(c, on))
            action = QWidgetAction(self.menu_)
            action.setDefaultWidget(box)  # a checkbox keeps the menu open while choosing several
            self.menu_.addAction(action)
            self.boxes[code] = box
        self.menu_.addSeparator()
        self.menu_.addAction("Add languages…", self.add_requested.emit)
        self._label()

    def _toggled(self, code: str, on: bool) -> None:
        if on and code not in self.selected:
            self.selected.append(code)
        elif not on and code in self.selected:
            if len(self.selected) == 1:  # at least one language
                self.boxes[code].blockSignals(True)
                self.boxes[code].setChecked(True)
                self.boxes[code].blockSignals(False)
                return
            self.selected.remove(code)
        self._label()
        self.changed.emit(list(self.selected))

    def _label(self) -> None:
        names = [languages.name(c) for c in self.selected]
        text = ", ".join(names) if len(names) <= 2 else f"{names[0]} + {len(names) - 1} more"
        self.setText(f"Languages: {text}")
        self.setToolTip("OCR languages: " + ", ".join(names))


class AddLanguagesDialog(QDialog):
    """Pick languages to download; they are added to the OCR languages afterwards."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add languages")
        self.resize(420, 520)
        self.downloaded: list[str] = []
        self.task: Task | None = None
        layout = QVBoxLayout(self)
        intro = QLabel("Choose languages to download (from the Tesseract project's language data, "
                       "a few megabytes each). They become available to every project.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.list = QListWidget()
        installed = set(languages.installed())
        for code in languages.available():
            item = QListWidgetItem(f"{languages.name(code)}  ({code})")
            item.setData(Qt.UserRole, code)
            if code in installed:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setText(item.text() + " — installed")
            else:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
            self.list.addItem(item)
        layout.addWidget(self.list)
        self.bar = QProgressBar()
        self.bar.hide()
        layout.addWidget(self.bar)
        self.status = QLabel("")
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.download_button = self.buttons.addButton("Download", QDialogButtonBox.AcceptRole)
        self.buttons.accepted.connect(self.download)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _filter(self, text: str) -> None:
        for k in range(self.list.count()):
            item = self.list.item(k)
            item.setHidden(text.lower() not in item.text().lower())

    def chosen(self) -> list[str]:
        return [self.list.item(k).data(Qt.UserRole) for k in range(self.list.count())
                if self.list.item(k).flags() & Qt.ItemIsEnabled and self.list.item(k).checkState() == Qt.Checked]

    def download(self) -> None:
        codes = self.chosen()
        if not codes:
            self.status.setText("Tick the languages to download.")
            return
        self.download_button.setEnabled(False)
        self.bar.show()
        self.bar.setRange(0, 0)

        def work(progress):
            return languages.download(codes, lambda code, got, total: progress(code, f"{got} {total}"))

        self.task = Task(work)
        self.task.progress.connect(self._progress)
        self.task.done.connect(self._done)
        self.task.failed.connect(self._failed)
        self.task.start()

    def _progress(self, code: str, msg: str) -> None:
        got, total = (int(v) for v in msg.split())
        self.status.setText(f"Downloading {languages.name(code)}: {got / 1e6:.1f} MB" + (f" of {total / 1e6:.1f} MB" if total else ""))
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(got)

    def _done(self, codes) -> None:
        self.downloaded = list(codes)
        self.accept()

    def _failed(self, error: str) -> None:
        self.bar.hide()
        self.download_button.setEnabled(True)
        self.status.setText("Download failed: " + error.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# IDML export


class IdmlDialog(QDialog):
    """Reflow or facsimile, and their options, before the IDML is written."""

    def __init__(self, prepared: dict, parent=None):
        from . import converter
        from .fontmatch import installed_faces

        super().__init__(parent)
        self.setWindowTitle("Export IDML")
        self.resize(640, 600)
        self.matches = prepared["matches"]
        faces = installed_faces()
        self.faces_by_family: dict[str, list] = {}
        for f in faces:
            self.faces_by_family.setdefault(f.family, []).append(f)
        layout = QVBoxLayout(self)

        self.reflow = QRadioButton("Reflow: the text laid out as a book by docx-to-idml (masters, running heads, "
                                   "footnotes), in the fonts of its layout profile")
        self.facsimile = QRadioButton("Facsimile: the text where it stands on the scan, line for line, "
                                      "in the installed fonts most like the scan's")
        group = QButtonGroup(self)
        group.addButton(self.reflow)
        group.addButton(self.facsimile)
        layout.addWidget(self.reflow)

        self.reflow_box = QGroupBox()
        form = QFormLayout(self.reflow_box)
        program = converter.find_program()
        status = QLabel(f"docx-to-idml {program.version} is installed." if program else
                        "docx-to-idml isn't installed (or hasn't been opened since it was installed). "
                        f'<a href="{converter.DOWNLOAD}">Download it</a>, open it once, then export again.')
        status.setOpenExternalLinks(True)
        status.setWordWrap(True)
        form.addRow(status)
        self.profile = QComboBox()
        self.profile.addItem("docx-to-idml's defaults", None)
        for path in sorted((converter.settings_dir() / "profiles").glob("*.json")):
            self.profile.addItem(path.stem, str(path))
        form.addRow("Layout profile", self.profile)
        self.markers = QCheckBox("Mark where each page of the scan begins, as {p. 172} (\"Page Marker\" style)")
        self.check = QCheckBox("Mark words the OCR was unsure of (\"Check OCR\" style)")
        self.check.setChecked(True)
        form.addRow(self.markers)
        form.addRow(self.check)
        note = QLabel("Running heads and page numbers are left out. Notes become footnotes where their reference "
                      "figure was read; the rest are gathered at the end. The Word manuscript is kept beside the IDML.")
        note.setWordWrap(True)
        form.addRow(note)
        layout.addWidget(self.reflow_box)
        (self.reflow if program else self.facsimile).setChecked(True)

        layout.addWidget(self.facsimile)
        self.fac_box = QGroupBox()
        fl = QVBoxLayout(self.fac_box)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Type on the scan", "Font", "Size"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.style_boxes: dict[int, QComboBox] = {}
        rows = [m for m in sorted(self.matches.values(), key=lambda m: -m.instances) if m.face is not None]
        self.table.setRowCount(len(rows))
        for r, m in enumerate(rows):
            what = {"head": "Running head", "foot": "Running foot"}.get(m.zone, "Text" if m.size_class == 1.0 else
                                                                         "Notes" if m.size_class < 1 else "Headings")
            what += ", italic" if m.italic else ""
            what += ", bold" if m.bold else ""
            self.table.setItem(r, 0, QTableWidgetItem(f"{what} ({m.instances:,} letters)"))
            box = QComboBox()
            box.setEditable(True)
            labels = [m.face.label] + [label for label, _ in m.runners_up]
            labels += sorted({f.label for f in faces if f.italic == m.italic} - set(labels), key=str.lower)
            box.addItems(labels)
            self.table.setCellWidget(r, 1, box)
            self.table.setItem(r, 2, QTableWidgetItem(f"{m.point_size:g} pt" if m.point_size else ""))
            self.style_boxes[m.style] = box
        fl.addWidget(self.table)
        self.scan_layer = QCheckBox("Place the scan under the text, on a locked layer that doesn't print")
        self.scan_layer.setChecked(True)
        fl.addWidget(self.scan_layer)
        layout.addWidget(self.fac_box)

        self.reflow.toggled.connect(self._mode_changed)
        self._mode_changed()
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton("Export…", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _mode_changed(self) -> None:
        self.reflow_box.setEnabled(self.reflow.isChecked())
        self.fac_box.setEnabled(self.facsimile.isChecked())

    def options(self):
        from .idml import Face, IdmlOptions

        if self.reflow.isChecked():
            return IdmlOptions(mode="reflow", page_markers=self.markers.isChecked(),
                               check_below=70.0 if self.check.isChecked() else 0.0, profile=self.profile.currentData())
        faces = {}
        for sid, box in self.style_boxes.items():
            m = self.matches[sid]
            label = box.currentText()
            if label != m.face.label:
                face = next((f for fs in self.faces_by_family.values() for f in fs if f.label == label), None)
                faces[sid] = Face(face.family, face.style) if face else Face(label, "Regular")
        return IdmlOptions(mode="facsimile", faces=faces, include_scan=self.scan_layer.isChecked())


# ---------------------------------------------------------------------------
# Main window


class MainWindow(QMainWindow):
    def __init__(self, project: Project | None = None):
        super().__init__()
        self.setWindowTitle("Ectype OCR")
        self.resize(1400, 900)
        self.project = project or Project(Path.home() / ".ectype" / "scratch")
        self.worker: Worker | None = None
        self.task: Task | None = None
        self._build()
        self.load(self.project)

    def _build(self) -> None:
        bar = QToolBar("Main")
        self.addToolBar(bar)
        self.actions: dict[str, QAction] = {}
        for key, text, slot in (("open_scan", "Select PDF…", self.open_scan), ("open_project", "Open project…", self.open_project)):
            self.actions[key] = bar.addAction(text, slot)
        self.actions["open_scan"].setToolTip("Choose the scanned book (a PDF, or page images) to read")
        self.source_label = QLabel()
        self.source_label.setContentsMargins(8, 0, 8, 0)
        bar.addWidget(self.source_label)
        bar.addSeparator()
        self.mode = QComboBox()
        self.mode.addItems([label for _, label in MODES])
        self.mode.currentIndexChanged.connect(self._mode_changed)
        bar.addWidget(self.mode)
        self.page_image = QComboBox()
        self.page_image.addItems(["Page image: grayscale", "Page image: black and white (smallest)"])
        self.page_image.setToolTip("Text-layer mode: how the scanned page is kept under the text")
        self.page_image.currentIndexChanged.connect(self._page_image_changed)
        self.page_image_action = bar.addWidget(self.page_image)
        self.langs = LanguageButton()
        self.langs.changed.connect(self._languages_changed)
        self.langs.add_requested.connect(self.add_languages)
        bar.addWidget(self.langs)
        self.split = QCheckBox("Split two-page spreads")
        self.split.toggled.connect(self._split_changed)
        bar.addWidget(self.split)
        bar.addSeparator()
        for key, text, slot in (("run", "Run", lambda: self.run("preprocess")),
                                ("update", "Update after corrections", lambda: self.run(self.project.update_stage())),
                                ("train", "Train OCR model…", self.train_model),
                                ("export", "Export PDF…", self.export),
                                ("export_idml", "Export IDML…", self.export_idml)):
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

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("Check for updates…", lambda: self.check_for_updates(manual=True))
        self.check_at_start = help_menu.addAction("Check for updates at startup")
        self.check_at_start.setCheckable(True)
        self.check_at_start.setChecked(updates.check_at_start(updates.load_settings()))
        self.check_at_start.toggled.connect(self._check_at_start_changed)
        help_menu.addSeparator()
        help_menu.addAction("About Ectype OCR", self.about)

    def load(self, project: Project) -> None:
        self.project = project
        for tab in (self.spreads, self.review, self.glyphs):
            tab.project = project
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(next((k for k, (m, _) in enumerate(MODES) if m == project.settings.mode), 0))
        self.mode.blockSignals(False)
        self.page_image.blockSignals(True)
        self.page_image.setCurrentIndex(1 if project.settings.page_image == "bw" else 0)
        self.page_image.blockSignals(False)
        self.page_image_action.setVisible(project.settings.mode == "text")
        self.langs.set_languages(project.languages())
        self.split.blockSignals(True)
        self.split.setChecked(project.settings.split_spreads)
        self.split.blockSignals(False)
        source = Path(project.settings.source).name if project.settings.source else "no scan yet"
        self.setWindowTitle(f"Ectype OCR — {source}")
        self._show_source()
        self.refresh()
        if project.pages():
            self.tabs.setCurrentWidget(self.review)

    def refresh(self) -> None:
        self.spreads.refresh()
        self.review.refresh()
        self.glyphs.refresh()
        has_pdf = self.project.output_pdf() is not None
        self.actions["export"].setEnabled(has_pdf)
        self.actions["export_idml"].setEnabled(any((self.project.workspace / "ocr").glob("*.ocr.json")))
        lines = self.project.corrected_lines()
        self.actions["train"].setText(f"Train OCR model ({lines} lines)…" if lines else "Train OCR model…")
        self.actions["train"].setToolTip("Teach the OCR model this book's type from the lines you corrected in Review")
        self.actions["update"].setEnabled(bool(self.project.pages()))
        self.actions["run"].setEnabled(bool(self.project.settings.source))

    def _mode_changed(self, index: int) -> None:
        self.project.settings.mode = MODES[index][0]
        self.project.save_settings()
        self.page_image_action.setVisible(self.project.settings.mode == "text")
        self.refresh()

    def _page_image_changed(self, index: int) -> None:
        self.project.settings.page_image = "bw" if index == 1 else "gray"
        self.project.save_settings()

    def _languages_changed(self, codes: list[str]) -> None:
        self.project.set_languages(codes)
        msg = "OCR languages: " + ", ".join(languages.name(c) for c in codes) + ". Run to read the pages again."
        if self.project.has_corrections():
            msg += " Corrections made so far are carried over to the new reading."
        self.statusBar().showMessage(msg)

    def add_languages(self) -> None:
        dialog = AddLanguagesDialog(self)
        if dialog.exec() and dialog.downloaded:
            codes = self.project.languages() + [c for c in dialog.downloaded if c not in self.project.languages()]
            self.project.set_languages(codes)
            self.langs.set_languages(codes)
            self._languages_changed(codes)

    def _start(self, fn, on_done, title: str) -> None:
        """Run fn off the interface thread with the toolbar disabled and progress in the Log."""
        if (self.worker is not None and self.worker.isRunning()) or (self.task is not None and self.task.isRunning()):
            return
        for a in self.actions.values():
            a.setEnabled(False)
        self.tabs.setCurrentWidget(self.log)
        self.log.appendPlainText(f"— {title} —")
        self.task = Task(fn)
        self.task.progress.connect(lambda stage, msg: (self.log.appendPlainText(f"{stage}: {msg}"),
                                                      self.statusBar().showMessage(f"{stage}: {msg}")))
        self.task.done.connect(lambda result: (self._enable(), on_done(result)))
        self.task.failed.connect(self._failed)
        self.task.start()

    def _enable(self) -> None:
        for a in self.actions.values():
            a.setEnabled(True)
        self.refresh()

    def export_idml(self) -> None:
        self._start(lambda progress: self.project.prepare_idml(progress), self._idml_prepared, "Preparing the IDML export")

    def _idml_prepared(self, prepared: dict) -> None:
        dialog = IdmlDialog(prepared, self)
        if not dialog.exec():
            return
        options = dialog.options()
        default = str(Path(self.project.settings.source or self.project.workspace).with_name(
            (self.project.settings.name or "output") + f"-{options.mode}.idml"))
        path, _ = QFileDialog.getSaveFileName(self, "Export IDML (saved in a folder of its own, with its Links)",
                                              default, "InDesign Markup (*.idml)")
        if not path:
            return
        self._start(lambda progress: self.project.export_idml(Path(path), options, prepared, progress),
                    self._idml_done, "Exporting IDML")

    def _idml_done(self, out: dict) -> None:
        self.statusBar().showMessage(f"Saved {out['idml']}")
        if out.get("alerts"):
            QMessageBox.information(self, "Export IDML", "\n\n".join(out["alerts"]))

    def _split_changed(self, on: bool) -> None:
        self.project.settings.split_spreads = on
        self.project.save_settings()
        self.statusBar().showMessage("Spreads will be " + ("split" if on else "kept whole") + ". Run to apply.")

    def _jump(self, page: str, line: int, word: int) -> None:
        self.tabs.setCurrentWidget(self.review)
        self.review.select_word(page, line, word)

    def _show_source(self) -> None:
        """The selected scan in the toolbar, and Run in bold until it has been read."""
        source = self.project.settings.source
        if source and Path(source).is_file():
            n = source_pages(Path(source))
            state = "read" if self.project.pages() else "not read yet: press Run"
            self.source_label.setText(f"<b>PDF:</b> {Path(source).name} ({n} page{'s' if n != 1 else ''}) · {state}")
            self.source_label.setToolTip(source)
        else:
            self.source_label.setText("<i>No PDF selected</i>")
            self.source_label.setToolTip("")
        font = self.actions["run"].font()
        font.setBold(bool(source) and not self.project.pages())
        self.actions["run"].setFont(font)

    def open_scan(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select a scanned PDF", "",
                                              "Scanned books (*.pdf *.png *.tif *.tiff *.jpg *.jpeg);;PDF (*.pdf)")
        if path:
            self.load(Project.for_scan(Path(path)))
            self.tabs.setCurrentWidget(self.spreads)
            self.statusBar().showMessage(f"Selected {Path(path).name}. Press Run to read it.")

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
        self._show_source()
        self.tabs.setCurrentWidget(self.review)
        self.offer_languages()

    def offer_languages(self) -> None:
        """Words in a script none of the OCR languages covers (Greek or Hebrew in an English
        book): offer to add the languages, downloading them if needed, and read the pages again."""
        codes = self.project.language_suggestions()
        if not codes:
            return
        unread = self.project.unread_scripts()
        found = ", ".join(f"{script} ({n} words)" for script, n in unread.items())
        names = ", ".join(languages.name(c) for c in codes)
        missing = [c for c in codes if c not in languages.installed()]
        box = QMessageBox(self)
        box.setWindowTitle("Other scripts found")
        box.setText(f"This scan has words in scripts the OCR languages don't read: {found}.")
        box.setInformativeText(f"Add {names} to the OCR languages and read the pages again?"
                               + (" (They'll be downloaded first, a few megabytes each.)" if missing else ""))
        add = box.addButton("Add and read again", QMessageBox.AcceptRole)
        never = box.addButton("Not for this book", QMessageBox.RejectRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is never:
            self.project.decline_languages(codes)
        elif box.clickedButton() is add:
            def work(progress):
                if missing:
                    languages.download(missing, lambda code, got, total: progress(
                        "languages", f"{languages.name(code)}: {got / 1e6:.1f} of {total / 1e6:.1f} MB" if total else languages.name(code)))
                return codes

            def added(_codes):
                codes_now = self.project.languages() + [c for c in codes if c not in self.project.languages()]
                self.project.set_languages(codes_now)
                self.langs.set_languages(codes_now)
                self.run("ocr")

            self._start(work, added, "Adding " + names)

    def _failed(self, error: str) -> None:
        self.log.appendPlainText(error)
        for a in self.actions.values():
            a.setEnabled(True)
        QMessageBox.warning(self, "Ectype OCR", error.strip().splitlines()[-1])

    # -- training --------------------------------------------------------------

    def train_model(self) -> None:
        from . import train

        problem = train.available()
        if problem:
            QMessageBox.warning(self, "Train OCR model", problem + " Training needs them; reinstall Ectype OCR.")
            return
        lines = self.project.corrected_lines()
        if lines < train.MIN_LINES:
            QMessageBox.information(self, "Train OCR model",
                                    f"Correct at least {train.MIN_LINES} lines in the Review tab first ({lines} so far).\n\n"
                                    "Each line you correct is saved with its image as training data. Training then teaches "
                                    "the OCR model this book's type, so the next reading makes fewer of the same mistakes.")
            return
        base = self.project.base_language()
        book = self.project.settings.name or self.project.workspace.name
        box = QMessageBox(self)
        box.setWindowTitle("Train OCR model")
        box.setText(f"Train the {languages.name(base)} model on the {lines} lines you corrected?")
        box.setInformativeText(
            "Ectype fine-tunes a copy of the model on those lines (and on lines it read confidently, so it keeps what "
            "it does well), checks it on lines kept aside, and saves it as a new language for this book, "
            f"\"{languages.name(base)}, trained on {book}\". It takes a few minutes; the first time, the trainable "
            "model (about 15 MB) is downloaded. Your corrections are kept when the pages are read again.")
        go = box.addButton("Train", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is go:
            self._start(lambda progress: self.project.train_model(progress), self._trained, "Training the OCR model")

    def _trained(self, result) -> None:
        self.langs.set_languages(self.project.languages())
        if result.cer_before is not None and result.cer_after is not None:
            check = (f"On {result.held_out} corrected lines kept aside, character errors went from "
                     f"{100 * result.cer_before:.1f}% to {100 * result.cer_after:.1f}%.")
        else:
            check = ""
        skipped = (f" {result.skipped} lines with characters the model doesn't know (another script) were left out."
                   if result.skipped else "")
        box = QMessageBox(self)
        box.setWindowTitle("Train OCR model")
        box.setText(f"Trained \"{languages.name(result.model)}\" on {result.lines} corrected lines.")
        box.setInformativeText(f"{check}{skipped}\n\nRead the pages again with it now? Your corrections are kept.")
        again = box.addButton("Read again", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is again:
            self.run("ocr")

    # -- updates and about ----------------------------------------------------

    def about(self) -> None:
        QMessageBox.about(self, "About Ectype OCR",
                          f"<b>Ectype OCR {__version__}</b><p>Scanned pages to PDFs with real, searchable type "
                          f"traced from the scan.</p><p><a href=\"{updates.RELEASES}\">Releases</a> · Apache License 2.0</p>")

    def _check_at_start_changed(self, on: bool) -> None:
        settings = updates.load_settings()
        settings["check_updates"] = on
        updates.save_settings(settings)

    def check_for_updates(self, manual: bool = False) -> None:
        """Ask GitHub for the latest release, off the interface thread. At startup, say nothing
        unless there is a version to offer; when asked, always answer."""
        if getattr(self, "update_task", None) is not None and self.update_task.isRunning():
            return
        self.update_task = Task(lambda progress: updates.check())
        self.update_task.done.connect(lambda result: self._update_checked(result, manual))
        self.update_task.start()

    def _update_checked(self, result: dict, manual: bool) -> None:
        settings = updates.load_settings()
        status = result.get("status")
        if not updates.should_offer(result, settings, manual):
            if manual:
                if status in ("current", "norelease"):
                    QMessageBox.information(self, "Check for updates", f"Ectype OCR {__version__} is the latest version.")
                else:
                    QMessageBox.warning(self, "Check for updates", "Couldn't reach GitHub to check for updates "
                                        f"({result.get('error', 'no answer')}). Try again later, or see {updates.RELEASES}.")
            return
        latest = result["latest"]
        box = QMessageBox(self)
        box.setWindowTitle("Update available")
        box.setText(f"Ectype OCR {latest} is available. You have {__version__}.")
        if result.get("notes"):
            box.setDetailedText(result["notes"])
        installable = updates.can_install() and result.get("asset")
        if installable:
            box.setInformativeText("Install it now? Ectype closes while the installer runs, then opens again. "
                                   "Your projects and settings are kept.")
            go = box.addButton("Install now", QMessageBox.AcceptRole)
        else:
            box.setInformativeText("Download it from the releases page.")
            go = box.addButton("Open the releases page", QMessageBox.AcceptRole)
        skip = None if manual else box.addButton("Skip this version", QMessageBox.RejectRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is skip:
            settings["skipped_version"] = latest
            updates.save_settings(settings)
        elif box.clickedButton() is go:
            if installable:
                self._install_update(result["asset"])
            else:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices

                QDesktopServices.openUrl(QUrl(result.get("url") or updates.RELEASES))

    def _install_update(self, asset: dict) -> None:
        if (self.worker is not None and self.worker.isRunning()) or (self.task is not None and self.task.isRunning()):
            QMessageBox.information(self, "Update", "Ectype is still working. Install the update when it's done "
                                    "(Help > Check for updates).")
            return
        dialog = QProgressDialog(f"Downloading {asset['name']}…", "Cancel", 0, 1000, self)
        dialog.setWindowTitle("Update")
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        cancelled = []

        def step(progress, fraction):
            if cancelled:
                raise updates.UpdateError("cancelled")
            progress("download", str(fraction))

        self.download_task = Task(lambda progress: updates.download(asset, progress=lambda f: step(progress, f)))
        self.download_task.progress.connect(lambda _stage, f: dialog.setValue(int(float(f) * 1000)))
        dialog.canceled.connect(lambda: cancelled.append(True))

        def done(setup):
            dialog.close()
            updates.run_installer(setup, updates.installed_app())
            QApplication.instance().quit()

        def failed(error):
            dialog.close()
            if not cancelled:
                QMessageBox.warning(self, "Update", error.strip().splitlines()[-1].split(": ", 1)[-1])

        self.download_task.done.connect(done)
        self.download_task.failed.connect(failed)
        self.download_task.start()

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
    if updates.installed_app() is not None and updates.check_at_start(updates.load_settings()):
        window.check_for_updates()  # the installed app only, not a copy run from source
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
