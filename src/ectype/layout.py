"""The page layout of a workspace as text blocks, paragraphs, lines and styled words.

What the IDML export builds on. From the OCR, the segmentation and the glyph
clusters, each page becomes blocks:
- running heads and feet (the cluster stage's zones),
- headings (lines set larger than the text),
- notes (smaller lines, below the text),
- and text.

Blocks hold paragraphs and paragraphs hold lines. Each word carries its glyph
style (from the clusters), slant, and which characters are superscripts
(footnote references).

Blocks: consecutive lines (top to bottom) of one zone and size that overlap
horizontally, with a baseline pitch not well over the page's usual one.
Paragraphs: a new one starts at a line indented from the block's left edge, or
after a line that stops well short of the right edge. A paragraph's alignment is
justified when its lines (but the last) fill the block's width, centred when
both margins vary together, otherwise flush left.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .ocr import OcrPage
from .segment import Segmentation, fit_baselines, line_x_heights


@dataclass
class LWord:
    text: str
    box: list[int]
    italic: bool = False
    style: int = -1  # glyph style from the clusters, -1 if unknown
    superscript: list[bool] = field(default_factory=list)  # per character of text


@dataclass
class LLine:
    words: list[LWord]
    box: list[int]
    baseline: float  # page y of the baseline at the line's left edge
    slope: float
    xh: float  # x-height, px

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Paragraph:
    lines: list[LLine]
    align: str = "left"  # "justify", "left", "center", "right"
    indent: float = 0.0  # first-line indent, px
    left: float = 0.0  # left edge of its lines (but an indented first), px
    right: float = 0.0


@dataclass
class Block:
    kind: str  # "text", "heading", "note", "head" or "foot"
    size_class: float
    paragraphs: list[Paragraph]
    box: list[float]  # x0, y0, x1, y1 over its lines

    @property
    def lines(self) -> list[LLine]:
        return [line for p in self.paragraphs for line in p.lines]


@dataclass
class LPage:
    name: str
    width_px: int
    height_px: int
    dpi: float
    blocks: list[Block]
    xh: float  # the page's text x-height, px


@dataclass
class Layout:
    pages: list[LPage]
    xh: float  # the document's text x-height, px
    styles: dict[int, dict]  # glyph style -> {"italic", "zone", "size_class", "instances"}

    def text_dpi(self) -> float:
        return float(np.median([p.dpi for p in self.pages])) if self.pages else 300.0


def _word_styles(workspace: Path) -> tuple[dict[tuple[str, int], tuple[int, bool, str, float]], dict[int, dict]]:
    """Glyph unit -> (style, italic, zone, size class), and a summary per style."""
    path = workspace / "cluster" / "clusters.json"
    units: dict[tuple[str, int], tuple[int, bool, str, float]] = {}
    styles: dict[int, dict] = {}
    if not path.is_file():
        return units, styles
    data = json.loads(path.read_text(encoding="utf-8"))
    for g in data["glyphs"]:
        info = (g["style"], g["italic"], g.get("zone", ""), g["size_class"])
        for page, unit in g["instances"]:
            units[(page, unit)] = info
        st = styles.setdefault(g["style"], {"italic": Counter(), "zone": Counter(), "size_class": Counter(), "instances": 0})
        st["italic"][g["italic"]] += g["count"]
        st["zone"][g.get("zone", "")] += g["count"]
        st["size_class"][g["size_class"]] += g["count"]
        st["instances"] += g["count"]
    for st in styles.values():
        for key in ("italic", "zone", "size_class"):
            st[key] = st[key].most_common(1)[0][0]
    return units, styles


def _majority(values: list, default):
    return Counter(values).most_common(1)[0][0] if values else default


def build_layout(workspace: Path) -> Layout:
    """The layout of every page with OCR, from <workspace>/ocr, segment and cluster."""
    from PIL import Image

    from . import report
    from .cluster import running_lines

    workspace = Path(workspace)
    unit_info, styles = _word_styles(workspace)
    loaded = []
    for png in sorted((workspace / "pages").glob("*.png")):
        ocr_path = workspace / "ocr" / f"{png.stem}.ocr.json"
        if not ocr_path.is_file():
            continue
        ocr = OcrPage.from_json(json.loads(ocr_path.read_text(encoding="utf-8")))
        seg_path = workspace / "segment" / f"{png.stem}.seg.json"
        seg = Segmentation.from_json(json.loads(seg_path.read_text(encoding="utf-8"))) if seg_path.is_file() else None
        with Image.open(png) as im:
            dpi = report.image_dpi(im)
            size = im.size
        loaded.append((png.stem, size, dpi, ocr, seg))
    zones = running_lines([ocr for _, _, _, ocr, _ in loaded])

    page_xhs = []
    for _, _, _, ocr, seg in loaded:
        if seg is not None and seg.units:
            page_xhs.append(line_x_heights(seg)[1])
        else:
            page_xhs.append(float(np.median([l.x_size for l in ocr.lines if l.x_size] or [40.0])) * 0.48)
    doc_xh = float(np.median(page_xhs)) if page_xhs else 20.0

    pages = []
    for (name, size, dpi, ocr, seg), zone_of, page_xh in zip(loaded, zones, page_xhs):
        base = fit_baselines(seg, ocr) if seg is not None else {}
        xhs = line_x_heights(seg)[0] if seg is not None and seg.units else {}
        units_by_word: dict[tuple[int, int], list] = defaultdict(list)
        if seg is not None:
            for u in seg.units:
                units_by_word[(u.line, u.word)].append(u)
        lines: list[tuple[LLine, str, float]] = []
        for li, line in enumerate(ocr.lines):
            if not line.words:
                continue
            if li in base:
                b = base[li][1]
                a = base[li][0] + b * line.box[0]  # baseline at the line's left edge
            else:
                a, b = line.baseline_y(line.box[0]), line.baseline[0]
            xh = xhs.get(li, page_xh)
            words = []
            classes = []
            for wi, w in enumerate(line.words):
                units = units_by_word.get((li, wi), [])
                infos = [unit_info[(name, u.id)] for u in units if (name, u.id) in unit_info]
                letters = [i for i, u in zip(infos, units) if u.text.isalpha()] or infos
                italic = _majority([i[1] for i in letters], False)
                style = _majority([i[0] for i in letters], -1)
                classes += [i[3] for i in infos]
                sup = _superscripts(w, units, a + b * (w.box[0] - line.box[0]), xh)
                words.append(LWord(w.text, list(w.box), italic, style, sup))
            size_class = _majority(classes, None)
            if size_class is None:
                size_class = round(xh / doc_xh, 1) if abs(xh / doc_xh - 1) >= 0.12 else 1.0
            lines.append((LLine(words, list(line.box), a, b, xh), zone_of.get(li, ""), size_class))
        blocks = _blocks(lines, page_xh)
        pages.append(LPage(name, size[0], size[1], dpi, blocks, page_xh))
    return Layout(pages, doc_xh, styles)


def _superscripts(word, units, baseline: float, xh: float) -> list[bool]:
    """Per character of the word's text: set as a superscript. Small figures or letters
    whose ink sits well above the baseline, at the end of a word ("Inspiration.²"):
    footnote references, mostly."""
    starts, k = [], 0
    for c in word.chars:  # OCR characters may hold more than one code point
        starts.append((k, k + len(c.text)))
        k += len(c.text)
    sup = [False] * len(word.text)
    for u in units:
        if not u.box or not u.text.isalnum():
            continue
        x0, y0, x1, y1 = u.box
        if baseline - y1 > 0.3 * xh and (y1 - y0) < 1.1 * xh:
            for c in u.chars:
                if c < len(starts):
                    for i in range(*starts[c]):
                        if i < len(sup):
                            sup[i] = True
    end = len(sup)
    while end > 0 and sup[end - 1]:
        end -= 1
    if end == 0:  # the whole word is raised: not a reference
        return [False] * len(sup)
    return [False] * end + [True] * (len(sup) - end)


def _kind(zone: str, size_class: float) -> str:
    if zone:
        return zone
    if size_class >= 1.15:
        return "heading"
    if size_class <= 0.85:
        return "note"
    return "text"


def _blocks(lines: list[tuple[LLine, str, float]], page_xh: float) -> list[Block]:
    # Lines are compared by baseline pitch: gaps between line boxes vary with whether
    # the lines have descenders and ascenders.
    lines = sorted(lines, key=lambda t: t[0].baseline)
    pitches = [b[0].baseline - a[0].baseline for a, b in zip(lines, lines[1:]) if b[0].box[1] > a[0].box[3]]
    usual = float(np.median(pitches)) if pitches else 2.5 * page_xh
    groups: list[list[tuple[LLine, str, float]]] = []
    for item in lines:
        line, zone, sc = item
        if groups:
            prev, pzone, psc = groups[-1][-1]
            pitch = line.baseline - prev.baseline
            overlap = min(line.box[2], prev.box[2]) - max(line.box[0], prev.box[0])
            narrower = min(line.box[2] - line.box[0], prev.box[2] - prev.box[0])
            same = (zone == pzone and _kind(zone, sc) == _kind(pzone, psc) and overlap > 0.3 * narrower
                    and pitch < 1.45 * usual * max(1.0, sc))
            if same:
                groups[-1].append(item)
                continue
        groups.append([item])
    blocks = []
    for group in groups:
        sc = _majority([g[2] for g in group], 1.0)
        kind = _kind(group[0][1], sc)
        blines = [g[0] for g in group]
        blocks.append(Block(kind, sc, _paragraphs(blines), [min(l.box[0] for l in blines), min(l.box[1] for l in blines),
                                                             max(l.box[2] for l in blines), max(l.box[3] for l in blines)]))
    return blocks


def _paragraphs(lines: list[LLine]) -> list[Paragraph]:
    xh = float(np.median([l.xh for l in lines]))
    lefts = [l.box[0] for l in lines]
    rights = [l.box[2] for l in lines]
    left = float(np.percentile(lefts, 10)) if len(lines) >= 3 else min(lefts)
    right = float(np.percentile(rights, 90)) if len(lines) >= 3 else max(rights)
    indent_min, short = 0.8 * xh, 2.5 * xh
    paras: list[list[LLine]] = []
    for k, line in enumerate(lines):
        new = not paras
        if not new:
            prev = lines[k - 1]
            new = line.box[0] - left > indent_min or right - prev.box[2] > short
        if new:
            paras.append([line])
        else:
            paras[-1].append(line)
    out = []
    for p in paras:
        first = p[0].box[0] - left
        body = p[1:] or p
        p_left = float(np.median([l.box[0] for l in body]))
        p_right = float(np.median([l.box[2] for l in body]))
        full = [l for l in p[:-1] if right - l.box[2] <= xh and l.box[0] - left <= indent_min + (first if l is p[0] else 0)]
        if len(p) >= 2 and len(full) >= max(1, (len(p) - 1) // 2):
            align = "justify"
        else:
            centres = [(l.box[0] + l.box[2]) / 2 for l in p]
            mid = (left + right) / 2
            if all(abs(c - mid) < 1.5 * xh for c in centres) and all(l.box[0] - left > 2 * xh for l in p):
                align = "center"
            elif all(right - l.box[2] < xh for l in p) and any(l.box[0] - left > 4 * xh for l in p):
                align = "right"
            else:
                align = "left"
        indent = max(0.0, p[0].box[0] - p_left) if len(p) >= 2 else 0.0
        out.append(Paragraph(p, align, indent, p_left if len(p) >= 2 else p[0].box[0], p_right))
    return out


HYPHENS = ("-", "‐", "¬")


def join_lines(lines: list[LLine], vocabulary: set[str] | None = None) -> list[LWord]:
    """A paragraph's words in reading order, with line-end hyphens removed where they
    only split a word ("in-" + "spiration"). The hyphen stays when the hyphenated form
    occurs inside a line elsewhere in the document, or when the joined word doesn't
    occur but both halves do ("well-" + "known")."""
    out: list[LWord] = []
    carry: LWord | None = None
    for line in lines:
        for k, w in enumerate(line.words):
            w = LWord(w.text, list(w.box), w.italic, w.style, list(w.superscript))
            if carry is not None and k == 0 and w.text[:1].islower():
                head = carry.text[:-1]
                tail = re.split(r"\W", w.text, maxsplit=1)[0]
                keep = False
                if vocabulary is not None:
                    keep = (f"{head}-{tail}".lower() in vocabulary
                            or ((head + tail).lower() not in vocabulary and head.lower() in vocabulary and tail.lower() in vocabulary))
                text = (carry.text if keep else head) + w.text
                sup = (carry.superscript if keep else carry.superscript[:-1]) + w.superscript
                out[-1] = LWord(text, carry.box, carry.italic, carry.style, sup)
                carry = None
                continue
            carry = None
            out.append(w)
        last = out[-1] if out else None
        if last is not None and line.words and len(last.text) > 2 and last.text.endswith(HYPHENS) and last.text[-2].isalpha():
            carry = last
    return out


def vocabulary(layout: Layout) -> set[str]:
    """Lower-cased words (and hyphenated compounds) that occur inside lines."""
    vocab: set[str] = set()
    for page in layout.pages:
        for block in page.blocks:
            for line in block.lines:
                for w in line.words[:-1]:
                    vocab.update(t.lower() for t in re.findall(r"\w+(?:-\w+)*", w.text))
    return vocab


def folios(layout: Layout) -> list[int | None]:
    """Each page's printed page number: read from its running head or foot where it has one,
    and inferred for the others (an opening page has none) from the pages that do, when they
    agree on one numbering. None where nothing can be said."""
    read: dict[int, int] = {}
    for k, page in enumerate(layout.pages):
        for block in page.blocks:
            if block.kind in ("head", "foot"):
                numbers = [w.text.strip(".,") for line in block.lines for w in line.words if w.text.strip(".,").isdigit()]
                if numbers:
                    read[k] = int(numbers[0])
    if not read:
        return [None] * len(layout.pages)
    offsets = [n - k for k, n in read.items()]
    offset = max(set(offsets), key=offsets.count)
    if offsets.count(offset) < max(2, len(offsets) // 2):
        return [read.get(k) for k in range(len(layout.pages))]
    return [k + offset if k + offset > 0 else None for k in range(len(layout.pages))]
