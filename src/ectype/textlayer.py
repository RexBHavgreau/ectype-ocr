"""Text-layer mode: the scan as it is, with the OCR text laid invisibly over it.

The quickest output, and all an IDML export needs: no segmentation, no glyph
grouping, no font synthesis. Each page is the preprocessed (upright, deskewed)
page image, with every OCR word set in invisible text (render mode 3) over its
scanned position, so the PDF is searchable and its text copies out.

The text is set in a "glyphless" font made on the fly: one empty glyph per
character, all of one width, mapped to Unicode by the ToUnicode table. Any script
the OCR reads (Greek, Hebrew, ...) is covered without a font that has it. Each
word is scaled (Tz) to its scanned width, and sized so a selection covers the line.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image

from . import report
from .ocr import OcrPage
from .pdfwrite import EmbeddedFont, GlyphRun, PageSpec, write_pdf
from .preprocess import binarize
from .scripts import is_rtl

UPM = 1000
ADVANCE = 500
ASCENT, DESCENT = 800, -200


def glyphless_font(chars: list[str], key: str = "F1") -> tuple[EmbeddedFont, dict[str, int]]:
    """A TrueType font with one empty glyph per character (CID = glyph index)."""
    order = [".notdef"] + [f"g{i}" for i in range(1, len(chars) + 1)]
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({})  # characters reach the PDF through ToUnicode, not the font's cmap
    empty = TTGlyphPen(None).glyph()
    fb.setupGlyf({g: empty for g in order})
    fb.setupHorizontalMetrics({g: (ADVANCE, 0) for g in order})
    fb.setupHorizontalHeader(ascent=ASCENT, descent=DESCENT)
    fb.setupNameTable({"familyName": "EctypeGlyphless", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=ASCENT, sTypoDescender=DESCENT, usWinAscent=ASCENT, usWinDescent=-DESCENT)
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)
    cid = {c: i for i, c in enumerate(chars, start=1)}
    font = EmbeddedFont(
        key=key, name="EctypeGlyphless", program=buf.getvalue(), kind="truetype",
        widths={i: ADVANCE * 1000 / UPM for i in range(len(order))}, to_unicode={i: c for c, i in cid.items()},
        ascent=ASCENT, descent=DESCENT, cap_height=700, bbox=(0, DESCENT, ADVANCE, ASCENT),
    )
    return font, cid


def place_words(ocr: OcrPage, cid: dict[str, int], font_key: str = "F1") -> list[GlyphRun]:
    """One invisible run per word: on its line's baseline, as wide as its box, sized so
    the font's ascent reaches the top of the line."""
    runs = []
    for line in ocr.lines:
        for wi, word in enumerate(line.words):
            text = word.text
            cids = [cid[c] for c in text if c in cid]
            if not cids:
                continue
            x0, _, x1, _ = word.box
            baseline = line.baseline_y((x0 + x1) / 2)
            size = max(4.0, (baseline - line.box[1]) * UPM / ASCENT)
            natural = len(cids) * ADVANCE / UPM * size
            scale = max(0.05, (x1 - x0) / natural)
            actual = None
            if is_rtl(text):
                # Right to left: glyphs in visual order, the text in reading order.
                cids = cids[::-1]
                actual = text + (" " if wi < len(line.words) - 1 else "")
            if wi < len(line.words) - 1 and " " in cid:
                cids.append(cid[" "])  # keeps words apart when the text is copied out
            runs.append(GlyphRun(font_key, size, x0, baseline, cids, horizontal_scale=scale, invisible=True,
                                 actual_text=actual))
    return runs


def textlayer_workspace(workspace: Path, out_name: str | None = None, image: str = "gray", image_quality: int = 60,
                        on_page=None) -> dict:
    """Build <workspace>/textlayer/<name>.pdf from the page images and OCR. `image` is
    "gray" (JPEG at the page's resolution) or "bw" (black and white, CCITT G4: about a
    tenth of the size, for pages of plain text)."""
    workspace = Path(workspace)
    out = workspace / "textlayer"
    out.mkdir(parents=True, exist_ok=True)
    loaded = []
    chars: set[str] = {" "}
    pngs = sorted((workspace / "pages").glob("*.png"))
    for n, png in enumerate(pngs, 1):
        if on_page:
            on_page(n, len(pngs))
        page_image = Image.open(png)
        ocr_path = workspace / "ocr" / f"{png.stem}.ocr.json"
        ocr = OcrPage.from_json(json.loads(ocr_path.read_text(encoding="utf-8"))) if ocr_path.is_file() else None
        if ocr:
            chars.update(c for w in ocr.words() for c in w.text)
        loaded.append((png.stem, np.asarray(page_image.convert("L")), report.image_dpi(page_image), ocr))
    font, cid = glyphless_font(sorted(chars))
    pages, rows = [], []
    for name, gray, dpi, ocr in loaded:
        runs = place_words(ocr, cid, font.key) if ocr else []
        h, w = gray.shape
        if image == "bw":
            pages.append(PageSpec(w, h, dpi, bilevel=binarize(gray), runs=runs))
        else:
            pages.append(PageSpec(w, h, dpi, background=gray, background_dpi=0, background_quality=image_quality, runs=runs))
        rows.append({"page": name, "words": len(runs)})
    pdf_name = (out_name or workspace.name) + ".pdf"
    write_pdf(out / pdf_name, pages, [font])
    summary = {"pdf": pdf_name, "image": image, "bytes": (out / pdf_name).stat().st_size, "words": sum(r["words"] for r in rows),
               "pages": rows}
    report.write_json(out / "textlayer.json", summary)
    return summary
