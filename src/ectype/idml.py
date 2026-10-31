"""Export a workspace as an InDesign document (IDML): the OCR text, laid out.

Two layouts:

- Reflow: the text as a Word manuscript (docx.py), laid out by docx-to-idml, the installed
  converter (converter.py): masters, running heads, front matter, real footnotes, in the
  fonts of its layout profile. Ectype writes the reading text: running heads and folios
  dropped, paragraphs split by a page break joined, line-end hyphens removed where they only
  split a word, notes as footnotes where their reference was found.
- Facsimile: every text block of every page in its own frame where it stands on the scan,
  line for line (forced line breaks), in the installed fonts most like the scan's (chosen by
  `fontmatch`), one character style per type style of the scan, so a font is changed in one
  place. The scan is placed under the text on a locked layer that doesn't print.

Both are export folders as docx-to-idml writes them: `<name>/<name>.idml`, with the images in
`<name>/Links/`, linked relative to the IDML so the folder can be moved or sent on.

Facsimile is written with docx-to-idml's IDML writer (`idmlkit`, copied from it): InDesign's
own base settings and styles, pages, spreads and sections as InDesign writes them, checked
against Adobe's schema. Ectype adds, by subclassing: frames placed freely on the page, a
leading per paragraph, and the scan on a layer of its own.
"""

from __future__ import annotations

import copy
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from lxml import etree

from .idmlkit import model as M
from .idmlkit.writer import Writer
from .layout import Block, Layout, folios

LINE_BREAK = " "  # a forced line break inside a paragraph
JUSTIFY = {"justify": "LeftJustified", "left": "LeftAlign", "center": "CenterAlign", "right": "RightAlign"}
KINDS = {"text": "Facsimile Text", "heading": "Facsimile Heading", "note": "Facsimile Note",
         "head": "Facsimile Running Head", "foot": "Facsimile Running Foot"}


@dataclass
class Face:
    family: str
    style: str = "Regular"


@dataclass
class IdmlOptions:
    mode: str = "reflow"  # or "facsimile"
    # Reflow
    page_markers: bool = False  # the scan's page numbers as "{p. 172}" in the "Page Marker" style
    check_below: float = 70.0  # words read with less OCR confidence get the "Check OCR" style (0: none)
    profile: str | None = None  # a docx-to-idml layout profile (name or .json); None: its defaults
    # Facsimile
    faces: dict[int, Face] = field(default_factory=dict)  # glyph style -> face, overriding the matches
    include_scan: bool = True  # the scan on a locked, non-printing layer


# ---------------------------------------------------------------------------
# Measurements


def _pt(px: float, dpi: float) -> float:
    return px * 72.0 / dpi


def _pitch(lines) -> float | None:
    pitches = [b.baseline - a.baseline for a, b in zip(lines, lines[1:]) if b.baseline > a.baseline]
    return float(np.median(pitches)) if pitches else None


@lru_cache(maxsize=64)
def _advances(family: str, style: str) -> tuple[dict[int, int], int, int] | None:
    """(code point -> advance, units per em, a default advance) of an installed face."""
    from fontTools.ttLib import TTFont

    from .resources import font_index

    path = next((p for f, s, p in font_index() if f.lower() == family.lower() and s.lower() == style.lower()), None)
    path = path or next((p for f, s, p in font_index() if f.lower() == family.lower()), None)
    if path is None:
        return None
    font = TTFont(str(path), lazy=True, fontNumber=0)
    cmap, hmtx = font.getBestCmap() or {}, font["hmtx"]
    advances = {cp: hmtx[name][0] for cp, name in cmap.items()}
    return advances, font["head"].unitsPerEm, advances.get(ord("n"), font["head"].unitsPerEm // 2)


def _text_width(face: Face, text: str, size: float) -> float | None:
    """Width of the text set in the face, in points, from the font's advance widths."""
    metrics = _advances(face.family, face.style)
    if metrics is None:
        return None
    advances, upm, default = metrics
    return sum(advances.get(ord(c), default) for c in text) * size / upm


# ---------------------------------------------------------------------------
# Facsimile


@dataclass
class _Para(M.Paragraph):
    leading: float | None = None  # points


@dataclass
class _Frame:
    page: int
    left: float  # page points
    top: float
    width: float
    height: float
    paragraphs: list


@dataclass
class _TypeStyle:
    """A character style for one type style of the scan."""
    name: str
    face: Face
    size: float
    hscale: float  # percent


class _FacsimileWriter(Writer):
    """docx-to-idml's writer, with frames placed freely, a leading per paragraph, and the
    scan's images on a locked, non-printing layer under the text."""

    def __init__(self, doc, frames: list[_Frame], scan_pages: set[int]):
        super().__init__(doc, relative_links=True)
        self.free = frames
        self.scan_pages = scan_pages
        self.scan_layer = self.ids() if scan_pages else None

    def _write_spreads(self, masters):
        spreads, page_ids, frames = super()._write_spreads(masters)
        self.free_ids = [self.ids() for _ in self.free]
        for k, group in enumerate(self._spreads()):
            sp = self.parts["Spreads/Spread_%s.xml" % spreads[k]].find("Spread")
            if self.scan_layer:
                for rect in sp.findall("Rectangle"):
                    rect.set("ItemLayer", self.scan_layer)
                    rect.set("Locked", "true")
            for n, fr in enumerate(self.free):
                if fr.page in group:
                    f = self._frame_el(self.free_ids[n], self._page_left(fr.page) + fr.left,
                                       -self.doc.height / 2 + fr.top, fr.width, fr.height)
                    # The first baseline one leading below the frame's top, where the scan has it.
                    f.find("TextFramePreference").set("FirstBaselineOffset", "LeadingOffset")
                    sp.append(f)
        return spreads, page_ids, frames

    def _write_stories(self, frames):
        out = super()._write_stories(frames)
        for sid, fr in zip(self.free_ids, self.free):
            st = etree.Element("Story", self.snip["Story"].attrib)
            for child in self.snip["Story"]:
                st.append(copy.deepcopy(child))
            st.set("Self", sid)
            for n, para in enumerate(fr.paragraphs):
                st.append(self._paragraph(para, last=(n == len(fr.paragraphs) - 1)))
            self.parts["Stories/Story_%s.xml" % sid] = self._wrap("Story", st)
            out.append(sid)
        return out

    def _paragraph(self, para, last, note=None, number=False):
        psr = super()._paragraph(para, last, note, number)
        leading = getattr(para, "leading", None)
        if leading is not None:
            props = etree.Element("Properties")
            etree.SubElement(props, "Leading", type="unit").text = "%.3f" % leading
            psr.insert(0, props)
        return psr

    def _write_designmap(self, dm, masters, spreads, stories, page_ids):
        text = dm.find("Layer")
        text.set("Name", "Text")
        if self.scan_layer:
            scan = copy.deepcopy(text)  # below the text layer: later layers lie lower
            scan.set("Self", self.scan_layer)
            scan.set("Name", "Scan")
            scan.set("Locked", "true")
            scan.set("Printable", "false")
            text.addnext(scan)
        super()._write_designmap(dm, masters, spreads, stories, page_ids)


def _type_styles(matches: dict, options: IdmlOptions) -> tuple[dict[int, _TypeStyle], _TypeStyle | None]:
    """A character style per matched glyph style, named for what it is on the scan; and the
    body text's (for the paragraph styles' font)."""
    out: dict[int, _TypeStyle] = {}
    used: set[str] = set()
    body = None
    for m in sorted(matches.values(), key=lambda m: -m.instances):
        if m.face is None and m.style not in options.faces:
            continue
        face = options.faces.get(m.style) or Face(m.face.family, m.face.style)
        what = {"head": "Running Head", "foot": "Running Foot"}.get(m.zone) or (
            "Text" if abs(m.size_class - 1) < 0.12 else "Notes" if m.size_class < 1 else "Heading")
        name = what + (" Italic" if m.italic else "") + (" Bold" if m.bold else "")
        base, n = name, 2
        while name in used:
            name, n = f"{base} {n}", n + 1
        used.add(name)
        out[m.style] = _TypeStyle(name, face, m.point_size or 10.0, round(100 * m.horizontal_scale, 1))
        if body is None and what == "Text" and not m.italic:
            body = out[m.style]
    return out, body


def _nearest(style: int, italic: bool, zone: str, size_class: float, matches: dict, types: dict) -> tuple[_TypeStyle, float]:
    """The character style for a glyph style too rare to match (and its point size): the
    most common matched style of the same slant and zone, scaled to the size."""
    if style in types:
        return types[style], types[style].size
    near = [x for x in matches.values() if x.style in types and x.italic == italic and x.zone == zone] or \
           [x for x in matches.values() if x.style in types and x.italic == italic] or \
           [x for x in matches.values() if x.style in types]
    if not near:
        raise ValueError("no installed font matched the scan's type")
    n = max(near, key=lambda x: x.instances)
    t = types[n.style]
    return t, round(t.size * (size_class or 1.0) / (n.size_class or 1.0), 2)


def _facsimile_block(block: Block, dpi: float, style_for) -> tuple[list[_Para], tuple[float, float, float, float]]:
    """A block's paragraphs, line for line, and its frame (page points)."""
    lines = block.lines
    words = [w for line in lines for w in line.words]
    styles = [w.style for w in words if w.style >= 0]
    main = max(set(styles), key=styles.count) if styles else -1
    italic_main = sum(w.italic for w in words) > len(words) / 2
    zone = block.kind if block.kind in ("head", "foot") else ""
    main_type, main_size = style_for(main, italic_main, zone, block.size_class)
    pitch = _pitch(lines)
    leading = _pt(pitch, dpi) if pitch else round(1.2 * main_size, 2)
    x0, y0, x1, y1 = _block_frame(block, dpi, leading)
    left = min(p.left for p in block.paragraphs)

    def typeset(w):
        return style_for(w.style if w.style >= 0 else main, w.italic, zone, block.size_class)

    # Each line set in its replacement fonts must fit its measure, or it would wrap and push
    # the block's last lines out of the frame: narrow the block's type just enough.
    worst = 0.0
    for p in block.paragraphs:
        for k, line in enumerate(p.lines):
            start = _pt(p.lines[0].box[0] if k == 0 else p.left, dpi)
            natural = 0.0
            for j, w in enumerate(line.words):
                t, size = typeset(w)
                text = w.text + (" " if j < len(line.words) - 1 else "")
                natural += (_text_width(t.face, text, size) or 0.0) * t.hscale / 100
            worst = max(worst, natural / max(1.0, x1 - start))
    fit = min(1.0, 0.97 / worst) if worst > 0 else 1.0

    paras = []
    for p in block.paragraphs:
        runs: list[M.Run] = []
        for k, line in enumerate(p.lines):
            for j, w in enumerate(line.words):
                t, size = typeset(w)
                attrs = {}
                if w.bold:  # set in Review: the replacement font's bold (italic)
                    attrs["FontStyle"] = "Bold Italic" if w.italic else "Bold"
                if w.smallcaps:
                    attrs["Capitalization"] = "SmallCaps"
                if size != t.size:
                    attrs["PointSize"] = size
                if fit < 1.0:
                    attrs["HorizontalScale"] = round(t.hscale * fit, 1)
                sup = w.superscript if len(w.superscript) == len(w.text) else [False] * len(w.text)
                cut = next((i for i, s in enumerate(sup) if s), len(w.text))
                gap = " " if j < len(line.words) - 1 else (LINE_BREAK if k < len(p.lines) - 1 else "")
                if cut:
                    runs.append(M.Run(w.text[:cut] + (gap if cut == len(w.text) else ""), t.name, dict(attrs)))
                if cut < len(w.text):
                    runs.append(M.Run(w.text[cut:], t.name, {**attrs, "Position": "Superscript"}))
                    if gap:
                        runs.append(M.Run(gap, t.name, dict(attrs)))
        attrs = {"Justification": JUSTIFY[p.align]}
        if p.align in ("left", "justify"):
            attrs["LeftIndent"] = round(_pt(p.left - left, dpi), 3)
            attrs["FirstLineIndent"] = round(_pt(p.lines[0].box[0] - p.left, dpi), 3)
        paras.append(_Para(KINDS[block.kind], _merge(runs), attrs, leading=leading))
    return paras, (x0, y0, x1, y1)


def _merge(runs: list[M.Run]) -> list[M.Run]:
    out: list[M.Run] = []
    for r in runs:
        if out and out[-1].style == r.style and out[-1].attrs == r.attrs:
            out[-1].text += r.text
        else:
            out.append(r)
    return out


def _block_frame(block: Block, dpi: float, leading_pt: float) -> tuple[float, float, float, float]:
    """The block's frame in page points: the first baseline one leading below the top,
    and a leading of room under the last (a line needs its descent inside the frame).
    Justified lines end at the widest one's end; where lines aren't justified, the frame
    is wider than the ink, so a replacement font a little wider doesn't wrap them."""
    lines = block.lines
    left = min(p.left for p in block.paragraphs)
    right = max(max(l.box[2] for l in p.lines) for p in block.paragraphs)
    width = right - left
    if any(p.align == "center" for p in block.paragraphs):
        centre = float(np.mean([(l.box[0] + l.box[2]) / 2 for l in lines]))
        half = max(l.box[2] - l.box[0] for l in lines) / 2 * 1.15
        left, right = centre - half, centre + half
    elif not all(p.align == "justify" for p in block.paragraphs):
        right += 0.12 * width
    y0 = _pt(lines[0].baseline, dpi) - leading_pt
    y1 = _pt(lines[-1].baseline, dpi) + leading_pt
    return _pt(left, dpi), y0, _pt(right, dpi), y1


def _facsimile(workspace: Path, layout: Layout, folder: Path, name: str, matches: dict, options: IdmlOptions) -> dict:
    from PIL import Image

    pages = layout.pages
    types, body = _type_styles(matches, options)
    if not types:
        raise ValueError("no installed font matched the scan's type; run the glyph analysis first")

    def style_for(style, italic, zone, size_class):
        return _nearest(style, italic, zone, size_class, matches, types)

    width = float(np.median([_pt(p.width_px, p.dpi) for p in pages]))
    height = float(np.median([_pt(p.height_px, p.dpi) for p in pages]))
    facing = all(p.name[-1:] in ("L", "R") for p in pages)
    numbers = folios(layout)
    start = None
    if numbers[0] is not None:
        start = numbers[0]
    elif facing:
        start = 2 if pages[0].name.endswith("L") else 1  # a left-hand page is a verso: even

    frames, images = [], []
    scans = workspace / "idml" / "scans"
    if options.include_scan:
        scans.mkdir(parents=True, exist_ok=True)
    for i, page in enumerate(pages):
        if options.include_scan:
            jpg = scans / f"{page.name}.jpg"
            with Image.open(workspace / "pages" / f"{page.name}.png") as im:
                im.convert("L").save(jpg, "JPEG", quality=85, dpi=(page.dpi, page.dpi))
            images.append(M.PlacedImage(i, 0, 0, M.Image(str(jpg), width, height), wrap=None))
        for block in page.blocks:
            paras, (x0, y0, x1, y1) = _facsimile_block(block, page.dpi, style_for)
            frames.append(_Frame(i, x0, y0, x1 - x0, y1 - y0, paras))

    para_attrs = {"Hyphenation": "false", "MinimumWordSpacing": 60, "MaximumWordSpacing": 200,
                  "MinimumGlyphScaling": 97, "MaximumGlyphScaling": 103}
    body = body or next(iter(types.values()))
    doc = M.Document(
        width=width, height=height, margins={"top": 0, "bottom": 0, "inside": 0, "outside": 0}, facing=facing,
        page_count=len(pages), masters=[M.Master()],
        paragraph_styles=[M.Style(n, attrs=dict(para_attrs), font=body.face.family) for n in KINDS.values()],
        character_styles=[M.Style(t.name, attrs={"FontStyle": t.face.style, "PointSize": t.size, "HorizontalScale": t.hscale},
                                  font=t.face.family) for t in types.values()],
        sections=[M.Section(0, start=start)], name=name, images=images,
    )
    folder.mkdir(parents=True, exist_ok=True)
    idml = folder / f"{name}.idml"
    _FacsimileWriter(doc, frames, set(range(len(pages))) if options.include_scan else set()).write(str(idml))
    return {"idml": str(idml), "folder": str(folder), "pages": len(pages), "frames": len(frames),
            "styles": {t.name: f"{t.face.family} {t.face.style} {t.size:g} pt" for t in types.values()},
            "report": [f"{len(pages)} pages, {len(frames)} text frames, laid out where the scan has them."]}


# ---------------------------------------------------------------------------
# Reflow


def _reflow(workspace: Path, layout: Layout, folder: Path, name: str, options: IdmlOptions, lang: str) -> dict:
    from .converter import convert
    from .docx import DocxOptions, write_docx

    docx = workspace / "idml" / f"{name}.docx"
    made = write_docx(workspace, layout, docx, DocxOptions(options.page_markers, options.check_below, lang))
    result = convert(docx, folder.parent, name, options.profile)
    kept = Path(result["folder"]) / docx.name
    shutil.copyfile(docx, kept)  # the manuscript beside the IDML, for Word or another run
    notes = []
    if made["notes_at_end"]:
        notes.append(f"{made['notes_at_end']} notes whose reference wasn't found in the text are at the end under "
                     "\"Notes\" (the OCR often misses small reference figures); move them in InDesign.")
    if made["check_ocr"]:
        notes.append(f"{made['check_ocr']} words the OCR was unsure of are in the \"Check OCR\" character style.")
    return {"idml": result["idml"], "folder": result["folder"], "docx": str(kept),
            "pages": None, "footnotes": made["footnotes"],
            "report": notes + list(result.get("report", [])), "alerts": list(result.get("alerts", []))}


# ---------------------------------------------------------------------------


def export_idml(workspace: Path, out_dir: Path, name: str, options: IdmlOptions | None = None,
                layout: Layout | None = None, matches: dict | None = None, lang: str = "eng") -> dict:
    """Write the workspace's text as an IDML export folder, `<out_dir>/<name>/<name>.idml`,
    reflowed (by docx-to-idml) or in facsimile. Returns the IDML's and folder's paths and
    report lines for the user."""
    from .layout import build_layout

    options = options or IdmlOptions()
    workspace, out_dir = Path(workspace), Path(out_dir)
    layout = layout or build_layout(workspace)
    if not layout.pages or not any(p.blocks for p in layout.pages):
        raise ValueError("no OCR text to export; run OCR first")
    folder = out_dir / name
    if options.mode == "facsimile":
        if matches is None:
            from .fontmatch import match_styles

            matches = match_styles(workspace, layout)
        out = _facsimile(workspace, layout, folder, name, matches, options)
    else:
        out = _reflow(workspace, layout, folder, name, options, lang)
    return {"mode": options.mode, **out}


__all__ = ["Face", "IdmlOptions", "export_idml"]
