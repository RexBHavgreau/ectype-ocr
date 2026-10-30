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

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QImage, QPainterPath, QPainterPathStroker, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGraphicsItem, QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsView, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton, QRadioButton, QScrollArea, QSpinBox,
    QSplitter, QTableWidget,
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
    fraction = Signal(float)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, project: Project, start: str):
        super().__init__()
        self.project, self.start_stage = project, start

    def run(self) -> None:
        try:
            pdf = self.project.run(self.start_stage, lambda stage, msg: self.progress.emit(stage, msg),
                                   lambda f: self.fraction.emit(f))
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


STAGE_NAMES = {"preprocess": "Preparing the pages", "ocr": "Reading the text (OCR)", "segment": "Finding each letter",
               "cluster": "Grouping the letters into glyphs", "fonts": "Building the fonts", "traced": "Writing the PDF",
               "substitute": "Writing the PDF", "textlayer": "Writing the PDF", "train": "Training the OCR model"}


class ProgressWindow(QDialog):
    """A run in progress: what it is doing, a progress bar with a percentage, and the log.
    Hiding it leaves the run going; the Log tab has the same lines."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.resize(640, 400)
        self.stage = QLabel("Starting…")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFormat("%p%")
        self.bar.setTextVisible(True)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.buttons = QDialogButtonBox()
        self.hide_button = self.buttons.addButton("Hide", QDialogButtonBox.RejectRole)
        self.buttons.rejected.connect(self.hide)
        layout = QVBoxLayout(self)
        layout.addWidget(self.stage)
        layout.addWidget(self.bar)
        layout.addWidget(self.text, 1)
        layout.addWidget(self.buttons)

    def busy(self) -> None:
        """No measure of how far along it is: the bar just shows activity."""
        self.bar.setRange(0, 0)

    def add(self, stage: str, msg: str) -> None:
        self.stage.setText(STAGE_NAMES.get(stage, stage.capitalize()) + (f": {msg}" if msg != "running" else "…"))
        self.text.appendPlainText(f"{stage}: {msg}")

    def set_fraction(self, f: float) -> None:
        self.bar.setRange(0, 1000)
        self.bar.setValue(round(1000 * max(0.0, min(1.0, f))))

    def finish(self, ok: bool, message: str) -> None:
        self.bar.setRange(0, 1000)
        if ok:
            self.bar.setValue(1000)
        self.stage.setText(message)
        self.text.appendPlainText(message)
        self.hide_button.setText("Close")


class ImageView(QGraphicsView):
    """A zoomable view; Ctrl+wheel zooms, and `zoomed` lets a partner view follow. After
    `start_drawing`, a drag draws a box instead of moving the page (`drawn`)."""

    zoomed = Signal(float)
    drawn = Signal(QRectF)

    def __init__(self):
        self._scene = QGraphicsScene()  # held here, or Python frees it under Qt
        super().__init__(self._scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.drawing = False
        self._band: QGraphicsRectItem | None = None
        self._origin = QPointF()

    def start_drawing(self) -> None:
        self.drawing = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def stop_drawing(self) -> None:
        self.drawing = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.viewport().unsetCursor()

    def mousePressEvent(self, event):
        if self.drawing and event.button() == Qt.MouseButton.LeftButton:
            self._origin = self.mapToScene(event.position().toPoint())
            pen = QPen(COLOURS["selected"], 2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._band = self.scene().addRect(QRectF(self._origin, self._origin), pen)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._band is not None:
            self._band.setRect(QRectF(self._origin, self.mapToScene(event.position().toPoint())).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._band is not None:
            rect = self._band.rect()
            self.scene().removeItem(self._band)
            self._band = None
            self.stop_drawing()
            if rect.width() > 2 and rect.height() > 2:
                self.drawn.emit(rect)
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            self.zoomed.emit(factor)
        else:
            super().wheelEvent(event)

    def show_image(self, image: QImage) -> QGraphicsPixmapItem:
        self._band = None
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
    """A word's box on the scan, or a piece of it (`span`): a punctuation mark at its start or
    end, or the letters between (`review.parts`)."""

    def __init__(self, rect: QRectF, line: int, word: int, text: str, status: str, on_click,
                 span: tuple[int, int] | None = None, word_box: list[int] | None = None):
        super().__init__(rect)
        self.line, self.word, self.text, self.on_click, self.span = line, word, text, on_click, span
        self.word_box = word_box
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


class BoxEditor(QGraphicsRectItem):
    """The selected word's whole box, dashed: drag an edge or a corner to stretch it over ink
    the OCR left out (a first letter missed), or to shrink it. Only its edges take the mouse,
    so the word's pieces inside can still be clicked."""

    CURSORS = {"l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
               "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
               "lt": Qt.CursorShape.SizeFDiagCursor, "rb": Qt.CursorShape.SizeFDiagCursor,
               "rt": Qt.CursorShape.SizeBDiagCursor, "lb": Qt.CursorShape.SizeBDiagCursor}

    def __init__(self, rect: QRectF, tolerance: float):
        super().__init__(rect)
        self.tol, self.start, self.edges = tolerance, QRectF(rect), ""
        pen = QPen(COLOURS["selected"], 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)

    def box(self) -> list[int]:
        r = self.rect()
        return [round(r.left()), round(r.top()), round(r.right()), round(r.bottom())]

    def changed(self) -> bool:
        r = self.start
        return self.box() != [round(r.left()), round(r.top()), round(r.right()), round(r.bottom())]

    def boundingRect(self) -> QRectF:
        return self.rect().adjusted(-self.tol, -self.tol, self.tol, self.tol)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.rect())
        stroker = QPainterPathStroker()
        stroker.setWidth(2 * self.tol)
        return stroker.createStroke(path)

    def _edges_at(self, p: QPointF) -> str:
        r, t = self.rect(), self.tol
        across = r.top() - t <= p.y() <= r.bottom() + t
        along = r.left() - t <= p.x() <= r.right() + t
        out = ""
        if across and abs(p.x() - r.left()) <= t:
            out += "l"
        elif across and abs(p.x() - r.right()) <= t:
            out += "r"
        if along and abs(p.y() - r.top()) <= t:
            out += "t"
        elif along and abs(p.y() - r.bottom()) <= t:
            out += "b"
        return out

    def hoverMoveEvent(self, event):
        edges = self._edges_at(event.pos())
        if edges in self.CURSORS:
            self.setCursor(self.CURSORS[edges])
        else:
            self.unsetCursor()

    def mousePressEvent(self, event):
        self.edges = self._edges_at(event.pos())
        if not self.edges:
            event.ignore()

    def mouseMoveEvent(self, event):
        if not self.edges:
            return
        r, p = QRectF(self.rect()), event.pos()
        if "l" in self.edges:
            r.setLeft(min(p.x(), r.right() - 2))
        if "r" in self.edges:
            r.setRight(max(p.x(), r.left() + 2))
        if "t" in self.edges:
            r.setTop(min(p.y(), r.bottom() - 2))
        if "b" in self.edges:
            r.setBottom(max(p.y(), r.top() + 2))
        self.setRect(r)

    def mouseReleaseEvent(self, event):
        self.edges = ""


class CharacterGrid(QListWidget):
    """Characters in a wrapping grid; a click sends the one clicked (`picked`)."""

    picked = Signal(str)

    def __init__(self, cell: int = 44, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setUniformItemSizes(True)
        self.setGridSize(QSize(cell, cell))
        font = self.font()
        font.setPointSizeF(max(15.0, font.pointSizeF() * 1.5))
        self.setFont(font)
        self.itemClicked.connect(lambda item: self.picked.emit(item.data(Qt.ItemDataRole.UserRole)))

    def show_chars(self, chars: list[str]) -> None:
        from . import characters

        self.clear()
        for shown in chars:
            item = QListWidgetItem(shown)
            item.setData(Qt.ItemDataRole.UserRole, shown)
            item.setToolTip(characters.name(shown))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.addItem(item)


class CharacterPane(QDialog):
    """A floating window of special characters (superscripts, transliteration, Hebrew points
    and accents...), in sets or found by name; a click inserts one where the cursor is in
    Review's correction box. It stays open beside the scan, like InDesign's Glyphs panel."""

    picked = Signal(str)

    def __init__(self, parent=None):
        from . import characters

        super().__init__(parent)
        self.setWindowTitle("Special characters")
        self.setWindowFlag(Qt.WindowType.Tool)
        self.setModal(False)
        self.resize(460, 420)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.sets = QComboBox()
        self.sets.addItems(["Recently used", *characters.SETS])
        self.sets.currentIndexChanged.connect(self._show)
        row.addWidget(self.sets)
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find by name: macron, dot below, superscript two…")
        self.find.textChanged.connect(self._show)
        row.addWidget(self.find, 1)
        layout.addLayout(row)
        self.grid = CharacterGrid()
        self.grid.picked.connect(self.picked.emit)
        layout.addWidget(self.grid, 1)
        self.name = QLabel("Point at a character to see its name; click it to insert it.")
        layout.addWidget(self.name)
        self.grid.itemEntered.connect(lambda item: self.name.setText(item.toolTip()))
        self.grid.setMouseTracking(True)
        self.sets.setCurrentIndex(0 if characters.recent() else 1)
        self._show()

    def _show(self, *_) -> None:
        from . import characters

        query = self.find.text().strip()
        if query:
            self.grid.show_chars(characters.search(query))
        elif self.sets.currentIndex() == 0:
            self.grid.show_chars(characters.recent())
        else:
            self.grid.show_chars(characters.SETS[self.sets.currentText()])


class ReviewTab(QWidget):
    message = Signal(str)

    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        self.list = QListWidget()
        self.list.setMaximumWidth(200)
        self.list.currentRowChanged.connect(self.show_page)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Click a word on the scan to correct it (or Save it unchanged to confirm it)")
        font = self.edit.font()
        font.setPointSizeF(max(16.0, font.pointSizeF() * 1.6))
        self.edit.setFont(font)
        self.edit.returnPressed.connect(self.save)
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        self.special = QToolButton()
        self.special.setText("Ω")
        self.special.setToolTip("Insert a special character: the ones used last, or the character pane")
        self.special.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.special_menu = QMenu(self.special)
        self.special_menu.aboutToShow.connect(self._fill_special_menu)
        self.special.setMenu(self.special_menu)
        self.pane: CharacterPane | None = None
        add = QPushButton("Add a word")
        add.setToolTip("Draw a box around a word the OCR missed, then type it and Save")
        add.clicked.connect(self.start_adding)
        self.info = QLabel()
        legend = QLabel('<span style="color:#dc2828">■ low OCR confidence</span> &nbsp; '
                        '<span style="color:#e8890c">■ glyph looks like another character</span> &nbsp; '
                        '<span style="color:#1a9850">■ corrected or confirmed</span>')
        bar = QHBoxLayout()
        bar.addWidget(self.edit, 1)
        bar.addWidget(self.special)
        bar.addWidget(save)
        bar.addWidget(add)
        bar.addWidget(self.info, 1)
        self.scan, self.output = ImageView(), ImageView()
        self.scan.drawn.connect(self._drawn)
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
        self.editor: BoxEditor | None = None  # the selected word's box, to stretch
        self.adding = False  # the editor holds a box drawn for a word the OCR missed
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
        self.items, self.selected, self.editor, self.adding = [], None, None, False
        self._draw_words()
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

    def _draw_words(self) -> None:
        """The page's word boxes, coloured by what is known of each (made again after a change)."""
        for it in self.items:
            self.scan.scene().removeItem(it)
        self._drop_editor()
        self.items, self.selected = [], None
        ocr = self.project.ocr(self.page)
        fixed = self.project.corrected(self.page)
        flagged = self.flags.get(self.page, set())
        if ocr:
            for li, line in enumerate(ocr.lines):
                for wi, w in enumerate(line.words):
                    status = ("fixed" if (li, wi) in fixed else "suspect" if (li, wi) in flagged
                              else "low" if w.conf < LOW_CONF else "plain")
                    self._add_word(li, wi, w, status)

    def _fill_special_menu(self) -> None:
        """The characters used last, as a grid, and the way to the character pane."""
        from . import characters

        self.special_menu.clear()
        recent = characters.recent()
        if recent:
            grid = CharacterGrid(cell=40)
            grid.show_chars(recent)
            grid.setFixedSize(8 * 40 + 8, 40 * ((len(recent) + 7) // 8) + 8)
            grid.picked.connect(lambda c: (self.special_menu.close(), self.insert_char(c)))
            holder = QWidgetAction(self.special_menu)
            holder.setDefaultWidget(grid)
            self.special_menu.addAction(holder)
            self.special_menu.addSeparator()
        else:
            self.special_menu.addAction("No characters used yet").setEnabled(False)
        self.special_menu.addAction("More characters…", self.show_pane)

    def show_pane(self) -> None:
        if self.pane is None:
            self.pane = CharacterPane(self)
            self.pane.picked.connect(self.insert_char)
        self.pane.show()
        self.pane.raise_()

    def insert_char(self, shown: str) -> None:
        """A special character typed where the cursor is in the correction box (a combining mark
        after the letter before it), and remembered as used."""
        from . import characters

        self.edit.insert(characters.as_typed(shown))
        self.edit.setFocus()
        characters.remember(shown)

    def _drop_editor(self) -> None:
        if self.editor is not None:
            self.scan.scene().removeItem(self.editor)
        self.editor, self.adding = None, False

    def _show_editor(self, box) -> None:
        self._drop_editor()
        x0, y0, x1, y1 = box
        self.editor = BoxEditor(QRectF(x0, y0, x1 - x0, y1 - y0), 6 / max(1e-6, self.scan.transform().m11()))
        self.scan.scene().addItem(self.editor)

    def start_adding(self) -> None:
        if self.page is None:
            return
        self.scan.start_drawing()
        self.info.setText("Drag a box around the word the OCR missed.")

    def _drawn(self, rect: QRectF) -> None:
        if self.selected is not None:
            self.selected.set_status(self.selected.status)
            self.selected = None
        self._show_editor([rect.left(), rect.top(), rect.right(), rect.bottom()])
        self.adding = True
        self.edit.clear()
        self.edit.setFocus()
        self.info.setText("Type the word, then Save. Drag the box's edges to adjust it.")

    def _add_word(self, li: int, wi: int, w, status: str) -> None:
        """A word's boxes: one, or one for each punctuation mark at its start or end and one for
        its letters, so each is corrected by itself."""
        from .review import part_box, parts

        pieces = parts(w)
        for span in pieces:
            whole = len(pieces) == 1
            x0, y0, x1, y1 = w.box if whole else part_box(w, span)
            text = w.text if whole else "".join(c.text for c in w.chars[span[0]:span[1]])
            it = WordItem(QRectF(x0, y0, x1 - x0, y1 - y0), li, wi, text, status, self.select, None if whole else span,
                          list(w.box))
            it.setToolTip(w.text)
            self.scan.scene().addItem(it)
            self.items.append(it)

    def _page_dpi(self, page: str) -> float:
        from PIL import Image

        from .report import image_dpi

        return image_dpi(Image.open(self.project.page_image(page)))

    def select(self, item: WordItem) -> None:
        if self.selected is not None and self.selected is not item:
            self.selected.set_status(self.selected.status)
        self.selected = item
        item.setPen(QPen(COLOURS["selected"], 3))
        if self.adding or self.editor is None or self.editor.start != self._rect(item.word_box):
            self._show_editor(item.word_box)  # another word: its box, to stretch over a letter it missed
        self.edit.setText(item.text)
        self.edit.setFocus()
        self.edit.selectAll()
        self.info.setText(f"line {item.line + 1}, word {item.word + 1}. Drag the dashed box's edges to take in "
                          "a letter it missed.")

    @staticmethod
    def _rect(box) -> QRectF:
        x0, y0, x1, y1 = box
        return QRectF(x0, y0, x1 - x0, y1 - y0)

    def select_word(self, page: str, line: int, word: int) -> None:
        pages = self.project.pages()
        if page in pages:
            self.list.setCurrentRow(pages.index(page))
            pieces = [it for it in self.items if (it.line, it.word) == (line, word)]
            if pieces:
                it = max(pieces, key=lambda it: len(it.text))  # the letters, not a mark beside them
                self.select(it)
                self.scan.centerOn(it)

    def save(self) -> None:
        if self.page is None:
            return
        text = self.edit.text().strip()
        if not text:
            return
        update = "Press “Update after corrections” to rebuild the PDF."
        if self.adding and self.editor is not None:
            result = self.project.add_word(self.page, self.editor.box(), text)
            li, wi = result["line"], result["word"]
            flags = self.flags.get(self.page, set())  # the line's later words moved along one
            self.flags[self.page] = {(l, w + 1) if l == li and w >= wi else (l, w) for l, w in flags}
            self._draw_words()
            self.info.setText(f"Added: {result['new']}")
            self.message.emit("Word added. " + update)
            return
        if self.selected is None:
            return
        # The same text saved confirms a flagged word as right: it turns green and its line
        # becomes training data, as a correction does, but the PDF needs no rebuilding.
        item = self.selected
        box = self.editor.box() if self.editor is not None and self.editor.changed() else None
        result = self.project.correct(self.page, item.line, item.word, text, item.span, box)
        self._draw_words()  # a piece corrected, or the box stretched, moves the word's other pieces
        old, new = result.get("part") or (result["old"], result["new"])
        stretched = " (its box changed)" if box else ""
        if old == new and not box:
            self.info.setText(f"Confirmed: {new}")
        else:
            self.info.setText(f"Saved: {old} → {new}{stretched}")
            self.message.emit("Correction saved. " + update)


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
    models_requested = Signal()

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
        self.menu_.addAction("Trained models…", self.models_requested.emit)
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
    """Pick languages to download; they are added to the OCR languages afterwards. Two tabs:
    Tesseract's languages, and Kraken's models (for Greek, Hebrew and other scripts)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add languages")
        self.resize(760, 600)
        self.downloaded: list[str] = []
        self.task: Task | None = None
        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        page = QWidget()
        self.tabs.addTab(page, "Tesseract languages")
        self.kraken = KrakenModelsPanel()
        self.kraken.installed.connect(self._kraken_installed)
        self.tabs.addTab(self.kraken, "Kraken models (Greek, Hebrew…)")
        layout = QVBoxLayout(page)
        intro = QLabel("Choose languages to download (from the Tesseract project's language data, "
                       "a few megabytes each). They become available to every project. Tesseract reads "
                       "the main text; for Greek, Hebrew and other scripts, a Kraken model reads better.")
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

    def _kraken_installed(self, codes: list) -> None:
        self.downloaded = list(codes)
        self.accept()

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


class ModelsDialog(QDialog):
    """The trained models and the training pools: what there is, and removing it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Trained models")
        self.resize(720, 480)
        layout = QVBoxLayout(self)
        intro = QLabel("Each language has one general model, trained on everything in its pool: every project's "
                       "corrections, lines set in your fonts, and matched scans. Typeface families with enough "
                       "material have models of their own, chosen automatically for each book.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text, 1)
        row = QHBoxLayout()
        self.choice = QComboBox()
        row.addWidget(self.choice, 1)
        self.remove_button = QPushButton("Remove its trained models")
        self.remove_button.clicked.connect(self.remove_models)
        row.addWidget(self.remove_button)
        self.empty_button = QPushButton("Empty its pool")
        self.empty_button.clicked.connect(self.empty_pool)
        row.addWidget(self.empty_button)
        layout.addLayout(row)
        self.legacy_button = QPushButton("Remove per-book models from earlier versions")
        self.legacy_button.clicked.connect(self.remove_legacy)
        layout.addWidget(self.legacy_button)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        from . import models, pool

        lines, codes = [], []
        for code, e in sorted(models.registry().items()):
            have = models.choices(code)
            if not have:
                continue
            codes.append(code)
            g = e.get("general", {})
            lines.append(f"{languages.name(code)}: trained {g.get('trained', '?')} on {g.get('items', '?')} images from "
                         f"{len(g.get('books', []))} sources; {models.size_of(code) / 1e6:.1f} MB")
            for fid, f in (e.get("families") or {}).items():
                lines.append(f"    typeface family {fid[1:]}: {', '.join(f.get('books', []))}")
        if not lines:
            lines.append("No trained models yet.")
        lines.append("")
        for lang in pool.languages():
            books = pool.summary(lang)
            if lang not in codes:
                codes.append(lang)
            lines.append(f"Pool {languages.name(lang)}: {sum(b['train'] + b['test'] for b in books.values())} images "
                         f"from {len(books)} sources")
            for book, b in sorted(books.items()):
                lines.append(f"    {book} ({b['source']}): {b['train'] + b['test']}")
        old = models.legacy()
        if old:
            lines += ["", "Per-book models from earlier versions: " + ", ".join(old)]
        self.text.setPlainText("\n".join(lines))
        self.choice.clear()
        for code in codes:
            self.choice.addItem(languages.name(code), code)
        self.remove_button.setEnabled(bool(codes))
        self.empty_button.setEnabled(bool(codes))
        self.legacy_button.setEnabled(bool(old))

    def _confirm(self, text: str) -> bool:
        return QMessageBox.question(self, "Trained models", text) == QMessageBox.Yes

    def remove_models(self) -> None:
        from . import models

        code = self.choice.currentData()
        if code and self._confirm(f"Remove the trained models of {languages.name(code)}? Its stock model is used "
                                  "again; the pool stays, so they can be trained again."):
            models.remove(code)
            self.refresh()

    def empty_pool(self) -> None:
        from . import pool

        code = self.choice.currentData()
        lang = pool.key_for(code) if code else None
        if lang and self._confirm(f"Empty the training pool of {languages.name(lang)}? The corrections stay in their "
                                  "projects and are added again the next time each is trained."):
            pool.remove_language(lang)
            self.refresh()

    def remove_legacy(self) -> None:
        from . import models

        if self._confirm("Remove the per-book models made by earlier versions? Their books are read with the "
                         "language's trained model instead."):
            for code in models.legacy():
                models.remove_legacy(code)
            self.refresh()


class KrakenModelsPanel(QWidget):
    """Kraken's models from its repository on Zenodo: the engine first (an optional download in
    the Windows app), then the list of models, filtered by script, to download and use."""

    installed = Signal(list)
    COLUMNS = ["Script", "Model", "Kind", "Error rate", "Date", "Licence"]

    def __init__(self):
        super().__init__()
        from . import kraken

        self.kraken = kraken
        self.records: list[dict] = []
        self.task: Task | None = None
        self.engine_ok = False
        layout = QVBoxLayout(self)
        intro = QLabel("Kraken reads Greek, Hebrew and other scripts with models trained by scholars and "
                       "libraries and shared in its repository on Zenodo. Tesseract still reads the main text; "
                       "words in a model's script are read again by Kraken. Choose a model that matches your "
                       "book: printed or handwritten, and the period of the type. The description says what "
                       "each was trained on.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.engine_label = QLabel("")
        self.engine_label.setWordWrap(True)
        self.engine_label.setOpenExternalLinks(True)
        layout.addWidget(self.engine_label)
        row = QHBoxLayout()
        self.engine_button = QPushButton("Download the Kraken engine")
        self.engine_button.clicked.connect(self.install_engine)
        row.addWidget(self.engine_button)
        self.list_button = QPushButton("Show the models")
        self.list_button.clicked.connect(lambda: self.load(refresh=False))
        row.addWidget(self.list_button)
        self.script = QComboBox()
        self.script.currentIndexChanged.connect(self._filter)
        row.addWidget(self.script)
        self.printed = QCheckBox("Printed text only")
        self.printed.setChecked(True)
        self.printed.toggled.connect(self._filter)
        row.addWidget(self.printed)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.textChanged.connect(self._filter)
        row.addWidget(self.search, 1)
        layout.addLayout(row)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._describe)
        layout.addWidget(self.table, 3)
        self.description = QPlainTextEdit()
        self.description.setReadOnly(True)
        layout.addWidget(self.description, 2)
        self.bar = QProgressBar()
        self.bar.hide()
        layout.addWidget(self.bar)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.download_button = QPushButton("Download the selected models")
        self.download_button.clicked.connect(self.download)
        buttons.addWidget(self.download_button)
        layout.addLayout(buttons)
        self.check_engine()

    # -- the engine

    def check_engine(self) -> None:
        self.engine_label.setText("Checking for the Kraken engine…")
        self._busy(True)
        self._run(lambda progress: self.kraken.engine_status(), self._engine_checked)

    def _engine_checked(self, status: dict) -> None:
        self._busy(False)
        self.engine_ok = status.get("installed", False)
        if self.engine_ok:
            self.engine_label.setText(f"Kraken engine {status.get('kraken')} (PyTorch {status.get('torch')}).")
            self.engine_button.hide()
        elif getattr(sys, "frozen", False):
            self.engine_label.setText("The Kraken engine is a separate download (it includes PyTorch, a few hundred "
                                      "megabytes). It is saved in your user folder and used by every project.")
            self.engine_button.show()
        else:
            self.engine_label.setText("Kraken isn't installed beside Ectype. Install it with "
                                      "<code>pip install \"ectype-ocr[kraken]\"</code>, or set ECTYPE_KRAKEN to a command "
                                      "that runs <code>python -m ectype.kraken_worker</code> where it is installed.")
            self.engine_button.hide()
        self.list_button.setEnabled(self.engine_ok)
        self.download_button.setEnabled(self.engine_ok)

    def install_engine(self) -> None:
        self.status.setText("Looking for the Kraken engine in this version's release…")
        self._busy(True)

        def work(progress):
            asset = self.kraken.engine_asset()
            if asset is None:
                raise self.kraken.KrakenError(f"This version's release ({__version__}) has no Kraken engine download.")
            progress("size", str(asset["size"]))
            self.kraken.install_engine(asset, progress=lambda f: progress("download", str(f)))
            return self.kraken.engine_status()

        self._run(work, lambda status: (self.status.setText("Kraken engine installed."), self._engine_checked(status)))

    # -- the models

    def load(self, refresh: bool = False) -> None:
        self.status.setText("Reading the list of models from Zenodo (the first time takes a minute or two)…")
        self._busy(True)
        self._run(lambda progress: self.kraken.list_models(refresh, lambda done, total: progress("list", f"{done} {total}")),
                  self._loaded)

    def _loaded(self, records: list) -> None:
        self._busy(False)
        self.records = sorted(records, key=lambda r: (",".join(self.kraken.scripts_of(r)), r.get("summary", "")))
        scripts = sorted({s for r in self.records for s in self.kraken.scripts_of(r)})
        self.script.blockSignals(True)
        self.script.clear()
        self.script.addItem("All scripts", "")
        for s in scripts:
            self.script.addItem(s, s)
        default = next((i for i in range(self.script.count()) if self.script.itemData(i) in ("Hebrew", "Greek")), 0)
        self.script.setCurrentIndex(default)
        self.script.blockSignals(False)
        self.status.setText(f"{len(self.records)} recognition models in the repository.")
        self._filter()

    def _filter(self) -> None:
        script = self.script.currentData() or ""
        text = self.search.text().lower()
        have = {m.get("doi") for m in self.kraken.installed_models().values()}
        shown = [r for r in self.records
                 if (not script or script in self.kraken.scripts_of(r))
                 and (not self.printed.isChecked() or self.kraken.printed(r) is not False)
                 and (not text or text in (r.get("summary", "") + " " + r.get("description", "")).lower())]
        self.table.setRowCount(len(shown))
        for row, r in enumerate(shown):
            kind = {True: "Printed", False: "Handwritten", None: ""}[self.kraken.printed(r)]
            cer = f"{r['cer']:.1f}%" if isinstance(r.get("cer"), (int, float)) else ""
            title = r.get("summary") or r["doi"]
            values = [", ".join(self.kraken.scripts_of(r)), title + ("  (installed)" if r["doi"] in have else ""),
                      kind, cer, r.get("date", ""), r.get("license", "")]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, r)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()

    def selected(self) -> list[dict]:
        rows = sorted({i.row() for i in self.table.selectedItems()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    def _describe(self) -> None:
        chosen = self.selected()
        if not chosen:
            self.description.setPlainText("")
            return
        r = chosen[0]
        by = ", ".join(r.get("creators") or [])
        self.description.setPlainText(f"{r.get('summary', '')}\n{r['doi']}" + (f"\nBy {by}" if by else "")
                                      + f"\nLanguages: {', '.join(r.get('language') or [])}\n\n{r.get('description', '')}")

    def download(self) -> None:
        chosen = self.selected()
        if not chosen:
            self.status.setText("Select one or more models in the list.")
            return
        self._busy(True)

        def work(progress):
            codes = []
            for r in chosen:
                progress("model", r.get("summary") or r["doi"])
                codes.append(self.kraken.install_model(r, lambda done, total: progress("bytes", f"{done} {total}")))
            return codes

        self._run(work, lambda codes: (self._busy(False), self.installed.emit(codes)))

    # -- running

    def _busy(self, on: bool) -> None:
        self.bar.setVisible(on)
        self.bar.setRange(0, 0)
        self.engine_button.setEnabled(not on)
        for b in (self.list_button, self.download_button):
            b.setEnabled(not on and self.engine_ok)

    def _run(self, fn, done) -> None:
        self.task = Task(fn)
        self.task.progress.connect(self._progress)
        self.task.done.connect(done)
        self.task.failed.connect(self._failed)
        self.task.start()

    def _progress(self, kind: str, msg: str) -> None:
        if kind == "download":
            self.bar.setRange(0, 1000)
            self.bar.setValue(round(1000 * float(msg)))
        elif kind == "size":
            self.status.setText(f"Downloading the Kraken engine ({int(msg) / 1e6:.0f} MB)…")
        elif kind == "model":
            self.status.setText(f"Downloading {msg}…")
        elif kind in ("bytes", "list"):
            done, total = (int(v) for v in msg.split())
            if total:
                self.bar.setRange(0, total)
                self.bar.setValue(min(done, total))

    def _failed(self, error: str) -> None:
        self._busy(False)
        self.status.setText("Failed: " + error.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# IDML export


class FontTable(QTableWidget):
    """Each type style on the scan with the installed font most like it, to change if wished."""

    def __init__(self, matches: dict, parent=None):
        from .fontmatch import installed_faces

        super().__init__(0, 3, parent)
        self.matches = matches
        faces = installed_faces()
        self.faces_by_family: dict[str, list] = {}
        for f in faces:
            self.faces_by_family.setdefault(f.family, []).append(f)
        self.setHorizontalHeaderLabels(["Type on the scan", "Font", "Size"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.verticalHeader().hide()
        self.style_boxes: dict[int, QComboBox] = {}
        rows = [m for m in sorted(matches.values(), key=lambda m: -m.instances) if m.face is not None]
        self.setRowCount(len(rows))
        for r, m in enumerate(rows):
            what = {"head": "Running head", "foot": "Running foot"}.get(m.zone, "Text" if m.size_class == 1.0 else
                                                                         "Notes" if m.size_class < 1 else "Headings")
            what += ", italic" if m.italic else ""
            what += ", bold" if m.bold else ""
            self.setItem(r, 0, QTableWidgetItem(f"{what} ({m.instances:,} letters)"))
            box = QComboBox()
            box.setEditable(True)
            labels = [m.face.label] + [label for label, _ in m.runners_up]
            labels += sorted({f.label for f in faces if f.italic == m.italic} - set(labels), key=str.lower)
            box.addItems(labels)
            self.setCellWidget(r, 1, box)
            self.setItem(r, 2, QTableWidgetItem(f"{m.point_size:g} pt" if m.point_size else ""))
            self.style_boxes[m.style] = box

    def faces(self) -> dict:
        """The fonts changed from the matches: glyph style -> Face."""
        from .idml import Face

        out = {}
        for sid, box in self.style_boxes.items():
            m = self.matches[sid]
            label = box.currentText()
            if label != m.face.label:
                face = next((f for fs in self.faces_by_family.values() for f in fs if f.label == label), None)
                out[sid] = Face(face.family, face.style) if face else Face(label, "Regular")
        return out


class IdmlDialog(QDialog):
    """Reflow or facsimile, and their options, asked before anything is prepared (the layout,
    and for Facsimile the font matching, take a while; they used to come first, and a damaged
    font stopped the export before the choice was ever shown). With `prepared`, Facsimile's
    fonts are shown too."""

    def __init__(self, prepared: dict | None = None, parent=None):
        from . import converter

        super().__init__(parent)
        self.setWindowTitle("Export IDML")
        self.resize(640, 600 if prepared else 360)
        layout = QVBoxLayout(self)

        self.reflow = QRadioButton("Reflow (live): the text laid out as a book by docx-to-idml: masters, running "
                                   "heads, real footnotes, in the fonts of its layout profile")
        self.facsimile = QRadioButton("Facsimile (static): the text where it stands on the scan, line for line, "
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
        self.table = FontTable(prepared["matches"]) if prepared and prepared.get("matches") else None
        if self.table is not None:
            fl.addWidget(self.table)
        else:
            later = QLabel("The scan's typefaces are compared with your installed fonts next; you can change "
                           "the fonts chosen before the IDML is written.")
            later.setWordWrap(True)
            fl.addWidget(later)
        self.scan_layer = QCheckBox("Place the scan under the text, on a locked layer that doesn't print")
        self.scan_layer.setChecked(True)
        fl.addWidget(self.scan_layer)
        layout.addWidget(self.fac_box)

        self.reflow.toggled.connect(self._mode_changed)
        self._mode_changed()
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton("Export…" if prepared else "Continue", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _mode_changed(self) -> None:
        self.reflow_box.setEnabled(self.reflow.isChecked())
        self.fac_box.setEnabled(self.facsimile.isChecked())

    def options(self):
        from .idml import IdmlOptions

        if self.reflow.isChecked():
            return IdmlOptions(mode="reflow", page_markers=self.markers.isChecked(),
                               check_below=70.0 if self.check.isChecked() else 0.0, profile=self.profile.currentData())
        return IdmlOptions(mode="facsimile", faces=self.table.faces() if self.table is not None else {},
                           include_scan=self.scan_layer.isChecked())


class FontChoiceDialog(QDialog):
    """Facsimile: the installed fonts most like the scan's type styles, to confirm or change."""

    def __init__(self, matches: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export IDML: fonts")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        intro = QLabel("Each type style on the scan, with the installed font most like it. Change any you like; "
                       "each becomes a character style in InDesign, so a font can also be changed there later.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = FontTable(matches)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton("Export…", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


# ---------------------------------------------------------------------------
# Main window


class FontTrainingDialog(QDialog):
    """Training from fonts: the languages to train, then the fonts to set their known text in
    (the computer's and any folders added), each with the chosen languages it supports, fully
    or only partly (the letters without marks such as Hebrew's vowel points)."""

    LEVELS = {"full": "full support", "letters": "the letters, without some marks (for Hebrew, the vowel points); "
                                                 "its lines are set without them"}
    TRANSLIT_LEVELS = {"full": "nearly every letter used in transliteration",
                       "letters": "some of the letters used in transliteration; only lines it has every letter of are set"}

    def __init__(self, parent=None):
        from . import synthtrain

        super().__init__(parent)
        self.setWindowTitle("Train from fonts")
        self.resize(760, 620)
        self.settings = self._load()
        self.fonts: list = []
        self.support: dict[str, dict[str, str]] = {}
        self.scanner: Task | None = None
        layout = QVBoxLayout(self)
        intro = QLabel("Ectype sets passages of a known text (the Hebrew Bible, the Greek New Testament) in each "
                       "font you choose, makes the lines look scanned, and trains each language's model on them: no "
                       "correcting by hand. Fonts are only read, never copied. The more typefaces like your books' "
                       "the better.\nTransliteration teaches the English model the letters English lacks: Hebrew in "
                       "SBL style (ḥ ṭ ṣ š ʾ ʿ ḇ ā ê ə), Arabic, Akkadian and Ugaritic (ḫ ẓ ḍ ġ), IPA (ʃ ŋ ɛ ʔ ː) "
                       "accented letters (ü ç ø č ō), and raised note numbers and verse parts (word¹, 3ᵃ). Without it "
                       "they are read as plain letters or as ! ? %.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        langs = QHBoxLayout()
        langs.addWidget(QLabel("Languages:"))
        self.lang_boxes = {}
        for script, (_, _, name) in synthtrain.SCRIPTS.items():
            box = QCheckBox(name)
            box.setChecked(script in self.settings.get("scripts", ["hebrew", "greek"]))
            box.toggled.connect(self._scripts_changed)
            langs.addWidget(box)
            self.lang_boxes[script] = box
        langs.addStretch(1)
        layout.addLayout(langs)
        folders = QHBoxLayout()
        self.folders_label = QLabel()
        self.folders_label.setWordWrap(True)
        folders.addWidget(self.folders_label, 1)
        add = QPushButton("Add a folder of fonts…")
        add.clicked.connect(self.add_folder)
        folders.addWidget(add)
        self.clear_folders = QPushButton("Remove added folders")
        self.clear_folders.clicked.connect(self.remove_folders)
        folders.addWidget(self.clear_folders)
        layout.addLayout(folders)
        self.only_all = QCheckBox("Show only fonts with every language chosen")
        self.only_all.setChecked(self.settings.get("only_all", True))
        self.only_all.toggled.connect(self._fill)
        layout.addWidget(self.only_all)
        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemChanged.connect(self._count)
        layout.addWidget(self.table, 1)
        key = QLabel("Full: every letter and mark.   Partial: the letters, without some marks (for Hebrew, the "
                     "vowel points); its lines are set without them, as in unpointed books. For transliteration, "
                     "some of its letters; only lines it has every letter of are set.   —: not supported.")
        key.setWordWrap(True)
        layout.addWidget(key)
        self.status = QLabel()
        layout.addWidget(self.status)
        row = QHBoxLayout()
        for text, state in (("Choose all", True), ("Choose none", False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, state=state: self._check_all(state))
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(QLabel("Lines per font:"))
        self.lines = QSpinBox()
        self.lines.setRange(50, 2000)
        self.lines.setSingleStep(50)
        self.lines.setValue(int(self.settings.get("lines", 400)))
        self.lines.setToolTip("Lines set in each font for each language. 400 is a good start.")
        row.addWidget(self.lines)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.train_button = buttons.addButton("Train", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._scan()

    # -- what is remembered between times ------------------------------------

    @staticmethod
    def _path() -> Path:
        from .resources import user_data_dir

        return user_data_dir() / "font-training.json"

    def _load(self) -> dict:
        import json

        try:
            return json.loads(self._path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        import json

        self.settings.update(scripts=self.scripts(), lines=self.lines.value(), only_all=self.only_all.isChecked(),
                             unchosen=sorted(set(self.settings.get("unchosen", [])) - set(self._shown())
                                             | {n for n in self._shown() if n not in self.chosen_names()}))
        try:
            self._path().parent.mkdir(parents=True, exist_ok=True)
            self._path().write_text(json.dumps(self.settings, indent=1), encoding="utf-8")
        except OSError:
            pass

    # -- the fonts -----------------------------------------------------------

    def scripts(self) -> list[str]:
        return [s for s, box in self.lang_boxes.items() if box.isChecked()]

    def folders(self) -> list[Path]:
        from . import synthtrain

        return synthtrain.system_font_folders() + [Path(f) for f in self.settings.get("folders", [])]

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "A folder of fonts (its subfolders are included)")
        if folder:
            self.settings["folders"] = list(dict.fromkeys(self.settings.get("folders", []) + [folder]))
            self._scan()

    def remove_folders(self) -> None:
        self.settings["folders"] = []
        self._scan()

    def _scan(self) -> None:
        """Find the fonts and which of the languages each supports, off the interface thread
        (the first time, the known texts are downloaded)."""
        from . import synthtrain

        added = self.settings.get("folders", [])
        self.folders_label.setText("Fonts installed on this computer" + (", and in: " + "; ".join(added) if added else ""))
        self.clear_folders.setEnabled(bool(added))
        if self.scanner is not None and self.scanner.isRunning():
            self._rescan = True
            return
        self._rescan = False
        scripts = list(synthtrain.SCRIPTS)  # all of them, so choosing another language needs no new look
        folders = self.folders()
        self.train_button.setEnabled(False)
        self.status.setText("Looking at the fonts…")

        def work(progress):
            fonts = synthtrain.fonts_in(*folders)
            return fonts, synthtrain.support(fonts, scripts, lambda m: progress("fonts", m),
                                             lambda k, n: progress("count", f"{k} {n}") if k % 25 == 0 or k == n else None)

        self.scanner = Task(work)
        self.scanner.progress.connect(self._scan_progress)
        self.scanner.done.connect(self._scanned)
        self.scanner.failed.connect(lambda tb: self.status.setText("Couldn't look at the fonts: " + tb.strip().splitlines()[-1]))
        self.scanner.start()

    def _scan_progress(self, stage: str, msg: str) -> None:
        if stage == "count":
            k, n = msg.split()
            self.status.setText(f"Looking at the fonts… {k} of {n}")
        else:
            self.status.setText(msg)

    def _scanned(self, result) -> None:
        if self._rescan:
            self._scan()
            return
        self.fonts, self.support = result
        self._fill()

    def _shown(self) -> list[str]:
        return [self.table.item(r, 0).text() for r in range(self.table.rowCount())]

    def chosen_names(self) -> list[str]:
        return [self.table.item(r, 0).text() for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.Checked]

    def _fill(self) -> None:
        from . import synthtrain

        scripts = self.scripts()
        before = {n: n in self.chosen_names() for n in self._shown()}
        unchosen = set(self.settings.get("unchosen", []))
        want = set(scripts)
        if self.only_all.isChecked():
            fonts = [f for f in self.fonts if want and want <= set(self.support.get(f.name, {}))]
        else:
            fonts = [f for f in self.fonts if want & set(self.support.get(f.name, {}))]
        fonts.sort(key=lambda f: f.name.lower())
        self.table.blockSignals(True)
        self.table.clear()
        self.table.setColumnCount(2 + len(scripts))
        self.table.setHorizontalHeaderLabels(["Font"] + [synthtrain.SCRIPTS[s][2] for s in scripts] + ["File"])
        self.table.setRowCount(len(fonts))
        for r, font in enumerate(fonts):
            item = QTableWidgetItem(font.name)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            chosen = before.get(font.name, font.name not in unchosen)
            item.setCheckState(Qt.Checked if chosen else Qt.Unchecked)
            item.setToolTip(str(font.path))
            self.table.setItem(r, 0, item)
            for c, script in enumerate(scripts, 1):
                lv = self.support.get(font.name, {}).get(script)
                cell = QTableWidgetItem({synthtrain.FULL: "Full", synthtrain.LETTERS: "Partial"}.get(lv, "—"))
                cell.setTextAlignment(Qt.AlignCenter)
                levels = self.TRANSLIT_LEVELS if script == "transliteration" else self.LEVELS
                cell.setToolTip(f"{synthtrain.SCRIPTS[script][2]}: " + levels.get(lv, "not supported"))
                cell.setFlags(Qt.ItemIsEnabled)
                self.table.setItem(r, c, cell)
            path = QTableWidgetItem(font.path.name)
            path.setFlags(Qt.ItemIsEnabled)
            path.setToolTip(str(font.path))
            self.table.setItem(r, 1 + len(scripts), path)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 2 + len(scripts)):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.blockSignals(False)
        self._count()

    def _check_all(self, state: bool) -> None:
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.Checked if state else Qt.Unchecked)
        self.table.blockSignals(False)
        self._count()

    def _count(self, *_) -> None:
        if not self.fonts:
            return
        n, shown, scripts = len(self.chosen_names()), self.table.rowCount(), self.scripts()
        if not scripts:
            self.status.setText("Choose one or more languages.")
        elif not shown:
            self.status.setText("No font here supports " + ("every language chosen." if self.only_all.isChecked()
                                                              else "the languages chosen."))
        else:
            self.status.setText(f"{n} of {shown} fonts chosen. Each is used for the chosen languages it supports.")
        self.train_button.setEnabled(bool(n and scripts))

    def _scripts_changed(self, *_) -> None:
        if self.fonts:
            self._fill()

    def chosen_fonts(self) -> list:
        names = set(self.chosen_names())
        return [f for f in self.fonts if f.name in names]

    def _accept(self) -> None:
        self._save()
        self.accept()

    def reject(self) -> None:
        if self.scanner is not None and self.scanner.isRunning():
            self.scanner.wait()
        super().reject()


class MainWindow(QMainWindow):
    def __init__(self, project: Project | None = None):
        super().__init__()
        self.setWindowTitle("Ectype OCR")
        self.resize(1400, 900)
        self.project = project or Project(Path.home() / ".ectype" / "scratch")
        self.worker: Worker | None = None
        self.task: Task | None = None
        self.progress_window: ProgressWindow | None = None
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
        self.langs.models_requested.connect(lambda: ModelsDialog(self).exec())
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

        training_menu = self.menuBar().addMenu("&Training")
        self.actions["train_fonts"] = training_menu.addAction("Train from fonts…", self.train_from_fonts)
        training_menu.addAction("Trained models…", lambda: ModelsDialog(self).exec())
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
        lines, words = self.project.corrected_lines(), self.project.corrected_words()
        count = ", ".join(t for t in (f"{lines} lines" if lines else "", f"{words} words in other scripts" if words else "") if t)
        self.actions["train"].setText(f"Train OCR model ({count})…" if count else "Train OCR model…")
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
        """Run fn off the interface thread with the toolbar disabled; progress in its window and
        in the Log, which isn't brought forward (the tab in view stays)."""
        if (self.worker is not None and self.worker.isRunning()) or (self.task is not None and self.task.isRunning()):
            return
        for a in self.actions.values():
            a.setEnabled(False)
        self.log.appendPlainText(f"— {title} —")
        self.progress_window = ProgressWindow(title, self)
        self.progress_window.busy()
        self.progress_window.show()
        self.task = Task(fn)
        self.task.progress.connect(self._progress_line)
        self.task.done.connect(lambda result: (self._close_progress(), self._enable(), on_done(result)))
        self.task.failed.connect(self._failed)
        self.task.start()

    def _progress_line(self, stage: str, msg: str) -> None:
        self.log.appendPlainText(f"{stage}: {msg}")
        self.statusBar().showMessage(f"{stage}: {msg}")
        if self.progress_window is not None:
            self.progress_window.add(stage, msg)

    def _close_progress(self) -> None:
        if self.progress_window is not None:
            self.progress_window.close()
            self.progress_window = None

    def _enable(self) -> None:
        for a in self.actions.values():
            a.setEnabled(True)
        self.refresh()

    def export_idml(self) -> None:
        """Reflow or Facsimile first; then the layout (and for Facsimile the font matching, then
        the fonts to confirm); then where to save it."""
        dialog = IdmlDialog(None, self)
        if not dialog.exec():
            return
        options = dialog.options()
        fonts = options.mode == "facsimile"
        self._start(lambda progress: self.project.prepare_idml(progress, match_fonts=fonts),
                    lambda prepared: self._idml_prepared(prepared, options), "Preparing the IDML export")

    def _idml_prepared(self, prepared: dict, options) -> None:
        if options.mode == "facsimile" and prepared.get("matches"):
            fonts = FontChoiceDialog(prepared["matches"], self)
            if not fonts.exec():
                return
            options.faces = fonts.table.faces()
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
        self.log.appendPlainText(f"— Running from {start} —")
        self.progress_window = ProgressWindow(f"Running {self.project.settings.name or self.project.workspace.name}", self)
        self.progress_window.show()
        self.worker = Worker(self.project, start)
        self.worker.progress.connect(self._progress_line)
        self.worker.fraction.connect(lambda f: self.progress_window and self.progress_window.set_fraction(f))
        self.worker.done.connect(self._finished)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _finished(self, pdf: str) -> None:
        self._close_progress()
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
        if self.progress_window is not None:
            self.progress_window.finish(False, "Stopped: " + error.strip().splitlines()[-1])
            self.progress_window = None
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
        plan = self.project.trainable()
        if not plan:
            from .training import MIN_ITEMS

            lines, words = self.project.corrected_lines(), self.project.corrected_words()
            QMessageBox.information(self, "Train OCR model",
                                    f"Each language needs at least {MIN_ITEMS} corrected lines or words to train on "
                                    f"(this book: {lines} lines, {words} words in other scripts).\n\n"
                                    "Every line or word you correct in Review, in any project, goes into that language's "
                                    "training pool, so what is learned from one book carries over to the next. Lines "
                                    "set in your fonts can be added too: Training → Train from fonts.")
            return
        from .training import plan as training_plan

        plans = {code: training_plan(code) for code, _ in plan}
        what = {"quick": "{new} new since the last training: a quick update",
                "none": "nothing new since the last training", "full": "trained afresh"}

        def line(code: str, n: int) -> str:
            p = plans[code]
            return f"• {languages.name(code)}: {n} images in its pool; " + what[p["mode"]].format(new=p["new"])

        box = QMessageBox(self)
        box.setWindowTitle("Train OCR model")
        box.setText("Train these languages?\n\n" + "\n".join(line(code, n) for code, n in plan))
        box.setInformativeText(
            "A quick update trains the model in use further on what is new (this book's corrections and others'), "
            "with some earlier material so nothing is forgotten: minutes rather than hours. Training afresh starts "
            "again from the stock model on the whole pool: slower, but worth doing now and then, after much has "
            "been added. Either way the new model replaces the one in use only if it makes fewer errors on images "
            "kept aside and no book does worse. The first time, each trainable stock model (about 15 MB) is "
            "downloaded.")
        afresh = QCheckBox("Train afresh from the stock models (slower)")
        afresh.setChecked(all(p["mode"] == "full" for p in plans.values()))
        afresh.setEnabled(any(p["mode"] != "full" for p in plans.values()))
        box.setCheckBox(afresh)
        go = box.addButton("Train", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is go:
            mode = "full" if afresh.isChecked() else "auto"
            self._start(lambda progress: self.project.train_model(progress, mode), self._trained, "Training the OCR models")

    def train_from_fonts(self) -> None:
        from . import train

        problem = train.available()
        if problem:
            QMessageBox.warning(self, "Train from fonts", problem + " Training needs them; reinstall Ectype OCR.")
            return
        dialog = FontTrainingDialog(self)
        if not dialog.exec():
            return
        fonts, scripts, lines = dialog.chosen_fonts(), dialog.scripts(), dialog.lines.value()

        def work(progress):
            from . import synthtrain, trainall

            say = (lambda msg: progress("train", msg))
            out = synthtrain.generate(fonts, scripts, lines, progress=say)
            report = []
            for script in scripts:
                names = out["fonts"].get(script, [])
                report.append(f"{synthtrain.SCRIPTS[script][2]}: lines set in {len(names)} fonts.")
            report += [f"  {name}: {why}" for name, why in out["skipped"].items()]
            return report + trainall.train_scripts(scripts, say)

        self._start(work, self._trained_from_fonts, "Training from fonts")

    def _trained_from_fonts(self, report: list[str]) -> None:
        for line in report:
            self.log.appendPlainText(line)
        box = QMessageBox(self)
        box.setWindowTitle("Train from fonts")
        box.setText("Training finished.")
        box.setInformativeText("\n".join(report) + "\n\nA model replaces the one in use only if it makes fewer "
                               "errors. Training → Trained models shows what there is.")
        box.exec()

    def _trained(self, reports) -> None:
        self.langs.set_languages(self.project.languages())
        lines = []
        for r in reports:
            lines += r["lines"]
            if r.get("chosen"):
                lines.append(f"  For this book: {r['chosen']}.")
        for line in lines:
            self.log.appendPlainText(line)
        changed = any(r["general"]["installed"] or any(f["kept"] for f in r["families"].values()) for r in reports)
        box = QMessageBox(self)
        box.setWindowTitle("Train OCR model")
        idle = all(r.get("mode") == "none" for r in reports)
        box.setText("Nothing new to train on since the last training." if idle else "Training finished." + (
            "" if changed else " The models in use were kept: the new ones weren't better."))
        box.setInformativeText("\n".join(lines) + ("\n\nRead the pages again now? Your corrections are kept." if changed else ""))
        again = box.addButton("Read again", QMessageBox.AcceptRole) if changed else None
        box.addButton("Later" if changed else "OK", QMessageBox.RejectRole)
        box.exec()
        if again is not None and box.clickedButton() is again:
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
    from .resources import quiet_windows_errors

    quiet_windows_errors()
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
