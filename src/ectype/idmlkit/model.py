"""The book as the IDML writer sees it: pages, masters, styles, stories and sections.

Built from the Word manuscript (step 4) or by hand for the feature test files (step 3). Measures
are in points. Style attributes use IDML's own names (PointSize, FirstLineIndent, Justification,
FontStyle, Position...), so they pass straight into the file.
"""

from dataclasses import dataclass, field


@dataclass
class Style:
    name: str
    based_on: str = None          # another style's name; None = the root style
    attrs: dict = field(default_factory=dict)   # e.g. {"PointSize": 11, "Justification": "LeftJustified"}
    font: str = None              # font family, e.g. "Arno Pro"
    leading: object = None        # points, or "Auto"
    next_style: str = None        # paragraph styles only
    props: dict = field(default_factory=dict)   # other <Properties>: name -> (type, value)
    tabs: list = field(default_factory=list)    # (alignment, position, leader), e.g. ("RightAlign", 315, ". ")


@dataclass
class Run:
    text: str = ""
    style: str = None             # character style name; None = no character style
    attrs: dict = field(default_factory=dict)   # local formatting, e.g. {"FontStyle": "Italic"}
    footnote: list = None         # a footnote here: its paragraphs (list of Paragraph)
    special: str = None           # "page_number" or "section_marker", before the text
    table: object = None          # a Table here (in a paragraph of its own)
    image: object = None          # an Image anchored here, inline (in a paragraph of its own)


@dataclass
class Paragraph:
    style: str
    runs: list = field(default_factory=list)
    attrs: dict = field(default_factory=dict)   # local paragraph formatting, e.g. Justification


@dataclass
class Table:
    rows: list                    # rows of cells; a cell is a list of Paragraphs, or None where a
                                  # cell to its left spans over it
    widths: list                  # column widths, points
    header_rows: int = 0
    style: str = None             # table style name
    spans: dict = field(default_factory=dict)   # (row, column) -> columns spanned


@dataclass
class Image:
    path: str                     # the image file (JPEG or PNG)
    width: float = None           # frame width, points; None = the image's size
    height: float = None          # frame height; None = in proportion to the width


@dataclass
class PlacedImage:
    """An image on a page, not in the text, with the text wrapping around it."""
    page: int
    top: float                    # frame position on the page, points
    left: float
    image: Image
    wrap: tuple = (6, 6, 6, 6)    # text wrap offsets (top, left, bottom, right); None = no wrap


@dataclass
class Story:
    paragraphs: list
    pages: list                   # page indexes (0-based) whose frames the story is threaded through
    sink: float = 0               # extra top margin on the first page (chapter openers)
    linked: bool = False          # page frames made from the master's primary text frames
    optical_margins: float = None # optical margin alignment at this size, or None for off
    valign: str = None            # vertical justification of its frames, e.g. "BottomAlign"


@dataclass
class Section:
    page: int                     # first page index
    marker: str = ""              # section marker (running heads)
    numbering: str = "Arabic"     # Arabic, LowerRoman, UpperRoman
    start: int = None             # restart numbering at this number; None = continue


@dataclass
class Frame:
    """A text frame on a master page (running head, folio)."""
    side: str                     # "verso" or "recto" ("recto" for single pages)
    bounds: tuple                 # (top, left, bottom, right) in page coordinates
    paragraphs: list
    key: str = None               # a name, so a child master can remove it
    valign: str = None            # e.g. "BottomAlign"
    primary: bool = False         # the master's primary text frame (verso and recto, threaded)


@dataclass
class Master:
    prefix: str = "A"
    base: str = "Body"
    based_on: str = None          # name of the parent master, e.g. "A-Body"
    frames: list = field(default_factory=list)
    removes: list = field(default_factory=list)   # keys of the parent's frames not shown here

    @property
    def name(self):
        return "%s-%s" % (self.prefix, self.base)


@dataclass
class TableStyle:
    name: str
    header_cell_style: str = None
    body_cell_style: str = None
    attrs: dict = field(default_factory=dict)


@dataclass
class CellStyle:
    name: str
    paragraph_style: str = None
    fill: str = None              # colour name
    attrs: dict = field(default_factory=dict)   # insets etc.


@dataclass
class TOCStyle:
    name: str
    title: str = "Contents"
    title_style: str = None
    entries: list = field(default_factory=list)   # (paragraph style, level, format style)


@dataclass
class Document:
    width: float = 432            # 6 in
    height: float = 648           # 9 in
    margins: dict = field(default_factory=lambda: {"top": 54, "bottom": 63, "inside": 54,
                                                   "outside": 63})
    facing: bool = True
    bleed: float = 0              # points, on the top, bottom and outside edges
    page_count: int = 1
    page_masters: dict = field(default_factory=dict)   # page index -> master name (default: first)
    masters: list = field(default_factory=lambda: [Master()])
    paragraph_styles: list = field(default_factory=list)
    character_styles: list = field(default_factory=list)
    stories: list = field(default_factory=list)
    sections: list = field(default_factory=lambda: [Section(0)])
    footnote_style: str = "Footnote"
    footnote_marker_style: str = "Footnote Reference"
    footnote_separator: str = ". "
    footnotes: dict = field(default_factory=dict)      # the profile's footnote settings
    name: str = "Untitled"
    colors: list = field(default_factory=list)         # (name, (c, m, y, k))
    table_styles: list = field(default_factory=list)
    cell_styles: list = field(default_factory=list)
    toc_styles: list = field(default_factory=list)
    images: list = field(default_factory=list)          # PlacedImage
    baseline_grid: dict = None    # {"start": pt, "division": pt, "shown": bool, "relative": "TopOfMargin..."}

    def style(self, name, kind="paragraph"):
        for s in (self.paragraph_styles if kind == "paragraph" else self.character_styles):
            if s.name == name:
                return s
        return None

    def resolved(self, name):
        """A paragraph style's settings with those of the styles it's based on filled in."""
        chain, seen = [], set()
        s = self.style(name)
        while s and s.name not in seen:
            seen.add(s.name)
            chain.append(s)
            s = self.style(s.based_on) if s.based_on else None
        attrs, font, leading = {}, None, None
        for s in reversed(chain):
            attrs.update(s.attrs)
            font = s.font or font
            leading = s.leading if s.leading is not None else leading
        return attrs, font, leading
