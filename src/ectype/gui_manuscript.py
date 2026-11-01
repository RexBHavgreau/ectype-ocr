"""Manuscript Mode in the app (manuscript.py): the Kraken view in Review, where each line Kraken
found is a band to click, correct, and reshape; the dialogs for a new hand and for training on
the book; and the Languages menu's manuscript half.

Imported by gui.py when needed (it uses gui.py's views and the character pane).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPen, QPolygonF
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QGraphicsPolygonItem, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QRadioButton, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget, QWidgetAction)

from . import manuscript

BAND = {"plain": QColor(40, 90, 220), "unsure": QColor(220, 40, 40), "disagree": QColor(232, 137, 12),
        "fixed": QColor(26, 152, 80), "unread": QColor(120, 120, 120), "selected": QColor(40, 90, 220)}
VIEWS = [("pages", "By page"), ("least", "Least sure lines first"), ("sample", "Sample pages (train for this book)")]


class LineItem(QGraphicsPolygonItem):
    """A line Kraken found: its outline as a band, coloured by what is known of it."""

    def __init__(self, line: dict, on_click):
        super().__init__(QPolygonF([QPointF(x, y) for x, y in line["boundary"]]))
        self.line, self.on_click = line, on_click
        self.status = ("fixed" if line["corrected"] else "unread" if line["unread"] else "disagree" if line["disagree"]
                       else "unsure" if line["unsure"] else "plain")
        self.set_selected(False)
        self.setToolTip(line["text"] or "(not read yet)")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_selected(self, on: bool) -> None:
        colour = BAND["selected" if on else self.status]
        pen = QPen(colour, 3 if on else 2)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(colour.red(), colour.green(), colour.blue(), 70 if on else 35)))

    def mousePressEvent(self, event):
        self.on_click(self, event.scenePos())


class ManuscriptReviewTab(QWidget):
    """Review in Manuscript Mode: the Kraken view. Each line found is a band (red: read
    unsurely; orange: the hands read it differently; green: corrected; grey: not read yet).
    Click a band to correct its line; drag the dashed box's edges to stretch it over strokes
    it missed; join a line Kraken split, cut one it ran together, delete or draw lines. Changed
    lines teach the line-finding when the book is trained."""

    message = Signal(str)

    def __init__(self, project):
        super().__init__()
        from .gui import ImageView

        self.project = project
        self.view = QComboBox()
        self.view.addItems([label for _, label in VIEWS])
        self.view.currentIndexChanged.connect(lambda _: self.refresh())
        self.list = QListWidget()
        self.list.setMaximumWidth(240)
        self.list.currentRowChanged.connect(self._picked)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Click a line on the scan to correct it (Save it unchanged to confirm it)")
        font = self.edit.font()
        font.setPointSizeF(max(18.0, font.pointSizeF() * 1.8))
        self.edit.setFont(font)
        self.edit.returnPressed.connect(self.save)
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        self.special = QToolButton()
        self.special.setText("Ω")
        self.special.setToolTip("Insert a special character or an editorial mark: the ones used last, or the character pane")
        self.special.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.special_menu = QMenu(self.special)
        self.special_menu.aboutToShow.connect(self._fill_special_menu)
        self.special.setMenu(self.special_menu)
        self.pane = None
        tools = QHBoxLayout()
        self.buttons = {}
        for key, text, tip, slot in (
                ("join", "Join with next line", "Make this line and the next one line (a line Kraken split in two)",
                 self.join),
                ("split", "Cut line…", "Then click on the line where it should be cut in two", self.start_split),
                ("delete", "Delete line", "Remove a line Kraken found where there is no text", self.delete),
                ("draw", "Draw a line", "Drag a box around a line Kraken missed", self.start_drawing),
                ("reread", "Read this page again", "Read the page's changed lines with the hands", self.reread)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            tools.addWidget(b)
            self.buttons[key] = b
        tools.addStretch(1)
        self.info = QLabel()
        self.info.setWordWrap(True)
        legend = QLabel('<span style="color:#dc2828">■ read unsurely</span> &nbsp; '
                        '<span style="color:#e8890c">■ the hands read it differently</span> &nbsp; '
                        '<span style="color:#1a9850">■ corrected or confirmed</span> &nbsp; '
                        '<span style="color:#787878">■ not read yet</span>')
        bar = QHBoxLayout()
        bar.addWidget(self.edit, 1)
        bar.addWidget(self.special)
        bar.addWidget(save)
        self.scan = ImageView()
        self.scan.drawn.connect(self._drawn)
        right = QVBoxLayout()
        right.addLayout(bar)
        right.addLayout(tools)
        right.addWidget(self.info)
        right.addWidget(legend)
        right.addWidget(self.scan, 1)
        left = QVBoxLayout()
        left.addWidget(self.view)
        left.addWidget(self.list, 1)
        layout = QHBoxLayout(self)
        layout.addLayout(left)
        layout.addLayout(right, 1)
        self.page: str | None = None
        self.items: list[LineItem] = []
        self.selected: LineItem | None = None
        self.editor = None
        self.splitting = False
        self.entries: list[tuple[str, int | None]] = []  # the list's rows: (page, line or None)

    # -- the list on the left ------------------------------------------------------

    def view_key(self) -> str:
        return VIEWS[self.view.currentIndex()][0]

    def show_view(self, key: str) -> None:
        self.view.setCurrentIndex(next(k for k, (v, _) in enumerate(VIEWS) if v == key))

    def refresh(self) -> None:
        ws = self.project.workspace
        pages = self.project.pages()
        key = self.view_key()
        self.list.blockSignals(True)
        self.list.clear()
        self.entries = []
        if key == "least":
            for page, index, conf in manuscript.least_sure(ws, pages):
                label = f"{page} line {index + 1}: " + ("hands differ" if conf < 0 else f"{conf:.0f}%")
                self._row(label, page, index)
        else:
            shown = self.project.settings.sample if key == "sample" else pages
            if key == "sample" and not shown:
                self.list.addItem("No sample pages yet: Training → Train for this book…")
            for page in shown:
                if page not in pages:
                    continue
                lines = manuscript.page_lines(ws, page)
                todo = sum(1 for l in lines if l["unsure"] or l["disagree"])
                done = sum(1 for l in lines if l["corrected"])
                mark = f"  ({todo} to check" + (f", {done} done)" if done else ")") if todo or done else ""
                self._row(page + mark, page, None)
        self.list.blockSignals(False)
        if self.entries:
            current = next((k for k, (p, i) in enumerate(self.entries) if p == self.page), 0)
            self.list.setCurrentRow(current)
            self._picked(current)
        else:
            self.scan.scene().clear()
            self.page = None

    def _row(self, label: str, page: str, index: int | None) -> None:
        QListWidgetItem(label, self.list)
        self.entries.append((page, index))

    def _picked(self, row: int) -> None:
        if not 0 <= row < len(self.entries):
            return
        page, index = self.entries[row]
        if page != self.page:
            self.show_page(page)
        if index is not None:
            item = next((it for it in self.items if it.line["index"] == index), None)
            if item is not None:
                self.select(item)
                self.scan.centerOn(item)

    # -- the page and its lines ------------------------------------------------------

    def show_page(self, page: str) -> None:
        self.page = page
        self.scan.show_image(QImage(str(self.project.page_image(page))))
        self.items, self.selected, self.editor = [], None, None
        self._draw_lines()
        self.scan.fit()
        self.edit.clear()
        self.info.setText("")

    def _draw_lines(self, select: int | None = None) -> None:
        for it in self.items:
            self.scan.scene().removeItem(it)
        self._drop_editor()
        self.items, self.selected = [], None
        if self.page is None:
            return
        for line in manuscript.page_lines(self.project.workspace, self.page):
            it = LineItem(line, self._clicked)
            self.scan.scene().addItem(it)
            self.items.append(it)
        if select is not None and 0 <= select < len(self.items):
            self.select(self.items[select])

    def _clicked(self, item: LineItem, pos) -> None:
        if self.splitting:
            self.splitting = False
            self.scan.viewport().unsetCursor()
            manuscript.split_line(self.project.workspace, self.page, item.line["index"], pos.x())
            self._changed("Line cut in two. Read this page again to read both parts.")
            self._draw_lines(item.line["index"])
            return
        self.select(item)

    def select(self, item: LineItem) -> None:
        from .gui import BoxEditor

        if self.selected is not None and self.selected is not item:
            self.selected.set_selected(False)
        self.selected = item
        item.set_selected(True)
        self._drop_editor()
        x0, y0, x1, y1 = item.line["box"]
        self.editor = BoxEditor(QRectF(x0, y0, x1 - x0, y1 - y0), 6 / max(1e-6, self.scan.transform().m11()))
        self.scan.scene().addItem(self.editor)
        line = item.line
        rtl = manuscript.direction(self.project.settings) == "horizontal-rl"
        self.edit.setLayoutDirection(Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight)
        self.edit.setText(line["text"])
        self.edit.setFocus()
        notes = [f"Line {line['index'] + 1}"]
        if line["hand"]:
            notes.append(f"read by {manuscript.hand_name(line['hand'])} ({line['conf']:.0f}% sure)")
        others = {h: t for h, t in line["readings"].items() if t != line["read"]}
        if others:
            notes.append("other hands: " + "; ".join(f"{h}: {t}" for h, t in others.items()))
        if line["corrected"]:
            notes.append("corrected")
        self.info.setText(". ".join(notes) + ". Drag the dashed box's edges to stretch the line.")

    def _drop_editor(self) -> None:
        if self.editor is not None:
            self.scan.scene().removeItem(self.editor)
            self.editor = None

    # -- correcting -----------------------------------------------------------------------

    def save(self) -> None:
        if self.page is None or self.selected is None:
            return
        text = self.edit.text().strip()
        if not text:
            return
        index = self.selected.line["index"]
        if self.editor is not None and self.editor.changed():
            manuscript.set_line_box(self.project.workspace, self.page, index, self.editor.box())
        result = self.project.correct_line(self.page, index, text)
        self._draw_lines()
        if index + 1 < len(self.items):  # on to the next line, as in reading
            self.select(self.items[index + 1])
        self.info.setText(("Confirmed: " if result["old"] == result["new"] else f"Saved: {result['old']} → ")
                          + result["new"] + ". " + self.info.text())
        self.message.emit("Line saved. Press “Update after corrections” to rebuild the PDF.")

    def _fill_special_menu(self) -> None:
        from .gui import ReviewTab

        ReviewTab._fill_special_menu(self)

    def show_pane(self) -> None:
        from .gui import ReviewTab

        ReviewTab.show_pane(self)

    def insert_char(self, shown: str) -> None:
        from .gui import ReviewTab

        ReviewTab.insert_char(self, shown)

    # -- changing the lines -------------------------------------------------------------

    def _changed(self, note: str) -> None:
        self.message.emit(note)
        self.info.setText(note)

    def join(self) -> None:
        if self.selected is None:
            return
        index = self.selected.line["index"]
        if index + 1 >= len(self.items):
            return
        manuscript.join_lines(self.project.workspace, self.page, index)
        self._draw_lines(index)
        self._changed("Lines joined. Read this page again to read the joined line.")

    def start_split(self) -> None:
        if self.page is None:
            return
        self.splitting = True
        self.scan.viewport().setCursor(Qt.CursorShape.SplitHCursor)
        self.info.setText("Click on the line where it should be cut in two.")

    def delete(self) -> None:
        if self.selected is None:
            return
        index = self.selected.line["index"]
        manuscript.delete_line(self.project.workspace, self.page, index)
        self._draw_lines(min(index, len(self.items) - 2))
        self._changed("Line removed.")

    def start_drawing(self) -> None:
        if self.page is None:
            return
        self.scan.start_drawing()
        self.info.setText("Drag a box around the line Kraken missed.")

    def _drawn(self, rect: QRectF) -> None:
        index = manuscript.add_line(self.project.workspace, self.page,
                                    [rect.left(), rect.top(), rect.right(), rect.bottom()])
        self._draw_lines(index)
        self._changed("Line added. Type its text and Save, or read this page again.")

    def reread(self) -> None:
        if self.page is None:
            return
        window = self.window()
        page = self.page
        start = getattr(window, "_start", None)
        if start is None:
            self.project.read_pages([page])
            self.show_page(page)
            return
        start(lambda progress: self.project.read_pages([page]), lambda _: self.show_page(page),
              f"Reading {page} again")


class NewHandDialog(QDialog):
    """A new hand for a language: its name, and the model it starts from."""

    def __init__(self, lang: str, parent=None):
        super().__init__(parent)
        self.lang = lang
        self.setWindowTitle(f"New hand: {manuscript.LANGUAGES.get(lang, lang)}")
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Sephardi square, Masorah hand, Scribe of MS 12")
        self.start = QComboBox()
        self.starts = manuscript.starting_points(lang)
        for label, _ in self.starts:
            self.start.addItem(label)
        form = QFormLayout(self)
        form.addRow("Name", self.name)
        form.addRow("Start from", self.start)
        note = QLabel("The hand starts as a copy of this model. Correcting lines it reads, then Train OCR model, "
                      "teaches it this scribe's hand.")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def create(self) -> str:
        return manuscript.new_hand(self.name.text(), self.lang, self.starts[self.start.currentIndex()][1])


def new_hand(parent, lang: str) -> str | None:
    """Ask for a new hand; its id, or None. Without a model to start from, say how to get one."""
    from . import kraken

    if not manuscript.starting_points(lang):
        name = manuscript.LANGUAGES.get(lang, lang)
        if kraken.worker_command() is None:
            why = "Hands are read by the Kraken engine, which isn't installed. Add it in Languages → Add languages… → Kraken models."
        else:
            why = (f"There is no {name} handwriting model to start from yet. In Languages → Add languages… → Kraken "
                   f"models, untick “Printed only” and install a {name} manuscript model.")
        QMessageBox.information(parent, "New hand", why)
        return None
    dialog = NewHandDialog(lang, parent)
    while dialog.exec():
        try:
            return dialog.create()
        except ValueError as exc:
            QMessageBox.warning(parent, "New hand", str(exc))
    return None


class TrainForBookDialog(QDialog):
    """Train for this book: which pages to correct first (at random from the middle of the
    book, away from front and back matter, or chosen)."""

    def __init__(self, pages: list[str], chosen: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Train for this book")
        self.pages = pages
        self.random = QRadioButton("At random from the middle of the book (away from front and back matter)")
        self.mine = QRadioButton("Pages I choose")
        group = QButtonGroup(self)
        group.addButton(self.random)
        group.addButton(self.mine)
        self.count = QSpinBox()
        self.count.setRange(1, max(1, len(pages)))
        self.count.setValue(min(5, max(1, len(pages))))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for page in pages:
            item = QListWidgetItem(page, self.list)
            item.setSelected(page in chosen)
        (self.mine if chosen else self.random).setChecked(True)
        self.list.setEnabled(self.mine.isChecked())
        self.mine.toggled.connect(self.list.setEnabled)
        how = QLabel("Correct the lines of these pages in Review (Review shows them as “Sample pages”), then "
                     "Train OCR model: the hands learn this book's script, and Run reads the rest with them.")
        how.setWordWrap(True)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(self.random, 1)
        row.addWidget(QLabel("Pages:"))
        row.addWidget(self.count)
        layout.addLayout(row)
        layout.addWidget(self.mine)
        layout.addWidget(self.list, 1)
        layout.addWidget(how)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choice(self) -> tuple[int, list[str] | None]:
        if self.mine.isChecked():
            return 0, [it.text() for it in self.list.selectedItems()]
        return self.count.value(), None


def fill_manuscript_menu(button, menu: QMenu, settings) -> None:
    """The Languages menu in Manuscript Mode: each language with its hands to tick, the way
    several hands read, and New hand…."""
    chosen_total = sum(len([h for h in settings.hands.get(lang, []) if h in manuscript.hands()]) for lang in manuscript.LANGUAGES)
    for lang, name in manuscript.LANGUAGES.items():
        mine = [h for h in settings.hands.get(lang, []) if h in manuscript.hands(lang)]
        sub = menu.addMenu(f"{name}" + (f"  ({len(mine)} hand{'s' if len(mine) != 1 else ''})" if mine else ""))
        all_hands = manuscript.hands(lang)
        order = mine + [h for h in all_hands if h not in mine]
        for hand in order:
            box = QCheckBox(all_hands[hand]["name"])
            box.setChecked(hand in mine)
            box.setContentsMargins(6, 2, 6, 2)
            box.toggled.connect(lambda on, lang=lang, hand=hand: button.toggle_hand(lang, hand, on))
            action = QWidgetAction(sub)
            action.setDefaultWidget(box)  # a checkbox keeps the menu open while choosing
            sub.addAction(action)
        if not all_hands:
            sub.addAction("No hands yet").setEnabled(False)
        if mine and chosen_total >= 2:
            sub.addSeparator()
            group = QButtonGroup(sub)
            for how, label in ((manuscript.EVERY, "Every line with every hand"),
                               (manuscript.UNSURE, "Other hands only on unsure lines")):
                radio = QRadioButton(label)
                radio.setChecked(settings.hand_reading == how)
                radio.setContentsMargins(6, 2, 6, 2)
                radio.toggled.connect(lambda on, how=how: on and button.reading_changed.emit(how))
                group.addButton(radio)
                action = QWidgetAction(sub)
                action.setDefaultWidget(radio)
                sub.addAction(action)
            sub._group = group  # kept with the menu
        sub.addSeparator()
        sub.addAction("New hand…", lambda lang=lang: button.new_hand_requested.emit(lang))
