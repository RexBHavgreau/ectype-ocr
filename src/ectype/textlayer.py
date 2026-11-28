"""Text-layer mode: the scan as it is, with the OCR text laid invisibly over it.

The quickest output, and all an IDML export needs: no segmentation, no glyph
grouping, no font synthesis. Each page is the preprocessed (upright, deskewed)
page, with every OCR word set in invisible text (render mode 3) over its
scanned position, so the PDF is searchable and its text copies out.

The page picture is, by default ("original"), the scan's own: its page is drawn
turned, cut and straightened as preprocessing measured, by a matrix, and its
pictures are copied unchanged, so the PDF is the scan's size plus the text (a
black-and-white scan coded again as grey JPEG had grown six to seven times). A
page that can't be drawn so (a scan given as image files) is coded as before.
The same pages with no text are the "Pages only" output (`pages_workspace`).

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
from .pdfwrite import EmbeddedFont, GlyphRun, PageSpec, workspace_meta, write_pdf
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


# ---------------------------------------------------------------------------
# The scan's own page under the text


def _affine(m) -> np.ndarray:
    m = np.asarray(m, float)
    return m if m.shape == (3, 3) else np.vstack([m[:2], [0, 0, 1]])


def _shift(dx: float, dy: float) -> np.ndarray:
    return np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1]], float)


def _image_placement(page) -> np.ndarray | None:
    """For a page that is one picture: the matrix its content draws the picture with (the unit
    square to page space), so the picture's pixels can be placed whichever way it was put on the
    page. None when it can't be followed."""
    import pikepdf

    try:
        names = {str(k) for k, x in page.Resources.XObject.items() if x.get("/Subtype") == "/Image"}
        ctm, saved = np.eye(3), []
        for operands, operator in pikepdf.parse_content_stream(page):
            op = str(operator)
            if op == "q":
                saved.append(ctm)
            elif op == "Q" and saved:
                ctm = saved.pop()
            elif op == "cm" and len(operands) == 6:
                a, b, c, d, e, f = (float(v) for v in operands)
                ctm = ctm @ np.array([[a, c, e], [b, d, f], [0, 0, 1]])  # cm acts before what is in force
            elif op == "Do" and operands and str(operands[0]) in names:
                return ctm
    except (pikepdf.PdfError, AttributeError, KeyError, ValueError, TypeError):
        return None
    return None


def _image_size(page) -> tuple[int, int] | None:
    try:
        images = [x for _, x in page.Resources.XObject.items() if x.get("/Subtype") == "/Image"]
        return (int(images[0].Width), int(images[0].Height)) if len(images) == 1 else None
    except (AttributeError, KeyError, ValueError, TypeError, IndexError):
        return None


def source_matrices(workspace: Path) -> tuple[Path | None, dict[str, tuple[int, tuple]]]:
    """(the scan, {output page: (its 0-based page in the scan, the PDF matrix from that page's
    space to the output page's points)}) for every page preprocessing made from a PDF it can
    follow; pages left out (and all of a scan given as image files) are coded as pictures."""
    import pikepdf

    workspace = Path(workspace)
    try:
        pre = json.loads((workspace / "preprocess" / "preprocess.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {}
    scan = Path(pre.get("input", ""))
    if scan.suffix.lower() != ".pdf" or not scan.is_file():
        return None, {}
    sources = {src["page"]: src for src in pre.get("sources", [])}
    out: dict[str, tuple[int, tuple]] = {}
    try:
        pdf = pikepdf.open(scan)
    except (pikepdf.PdfError, OSError):
        return None, {}
    with pdf:
        for p in pre.get("pages", []):
            src = sources.get(p["source_page"])
            index = p["source_page"] - 1
            if src is None or not 0 <= index < len(pdf.pages):
                continue
            page = pdf.pages[index]
            w0, h0 = src["size_px"]
            # Source pixels (top-left origin) -> the page's own space.
            if src["origin"] == "embedded image":
                placed = _image_placement(page)
                if placed is None or _image_size(page) != (w0, h0):  # not the picture measured
                    continue
                unit = np.array([[1 / w0, 0, 0], [0, -1 / h0, 1], [0, 0, 1]], float)  # pixels -> unit square
                to_page = placed @ unit
            elif src["origin"] == "rendered":
                x0, y0, x1, y1 = (float(v) for v in page.cropbox)  # what the renderer drew
                x0, x1 = min(x0, x1), max(x0, x1)
                y0, y1 = min(y0, y1), max(y0, y1)
                scale = float(src.get("dpi") or 0) / 72
                if abs((x1 - x0) * scale - w0) > 2 or abs((y1 - y0) * scale - h0) > 2:
                    continue  # the scan was changed since its pages were made
                to_page = np.array([[(x1 - x0) / w0, 0, x0], [0, -(y1 - y0) / h0, y1], [0, 0, 1]], float)
            else:
                continue
            try:
                to_pixels = np.linalg.inv(to_page)
            except np.linalg.LinAlgError:
                continue
            # preprocess's matrix works on pixel centres (OpenCV's), the page on pixel edges.
            transform = _shift(0.5, 0.5) @ _affine(p["transform"]) @ _shift(-0.5, -0.5)
            k = 72.0 / p["dpi"]
            _, ph = p["size_px"]
            to_points = np.array([[k, 0, 0], [0, -k, ph * k], [0, 0, 1]], float)
            a = to_points @ transform @ to_pixels
            if not np.isfinite(a).all():
                continue
            out[p["name"]] = (index, (a[0, 0], a[1, 0], a[0, 1], a[1, 1], a[0, 2], a[1, 2]))
    return scan, out


def _pages_pdf(workspace: Path, folder: str, out_name: str | None, image: str, image_quality: int,
               text: bool, on_page=None) -> dict:
    workspace = Path(workspace)
    out = workspace / folder
    out.mkdir(parents=True, exist_ok=True)
    scan, placed = source_matrices(workspace) if image == "original" else (None, {})
    loaded = []
    chars: set[str] = {" "}
    pngs = sorted((workspace / "pages").glob("*.png"))
    for n, png in enumerate(pngs, 1):
        if on_page:
            on_page(n, len(pngs))
        ocr = None
        ocr_path = workspace / "ocr" / f"{png.stem}.ocr.json"
        if text and ocr_path.is_file():
            ocr = OcrPage.from_json(json.loads(ocr_path.read_text(encoding="utf-8")))
            chars.update(c for w in ocr.words() for c in w.text)
        loaded.append((png, ocr))
    font, cid = glyphless_font(sorted(chars))
    pages, rows = [], []
    for png, ocr in loaded:
        runs = place_words(ocr, cid, font.key) if ocr else []
        page_image = Image.open(png)
        w, h = page_image.size
        dpi = report.image_dpi(page_image)
        if png.stem in placed:
            index, matrix = placed[png.stem]
            pages.append(PageSpec(w, h, dpi, source_page=index, source_matrix=matrix, runs=runs))
            kept = "as scanned"
        else:
            gray = np.asarray(page_image.convert("L"))
            if image == "bw":
                pages.append(PageSpec(w, h, dpi, bilevel=binarize(gray), runs=runs))
            else:
                pages.append(PageSpec(w, h, dpi, background=gray, background_dpi=0, background_quality=image_quality,
                                      lossless=image == "lossless", runs=runs))
            kept = "gray" if image == "original" else image
        rows.append({"page": png.stem, "words": len(runs), "image": kept})
    pdf_name = (out_name or workspace.name) + ".pdf"
    title, lang = workspace_meta(workspace, out_name)
    part = out / (pdf_name + ".part")
    write_pdf(part, pages, [font] if text else [], title=title, lang=lang, source=scan)
    part.replace(out / pdf_name)
    summary = {"pdf": pdf_name, "image": image, "bytes": (out / pdf_name).stat().st_size,
               "words": sum(r["words"] for r in rows), "pages": rows,
               "as_scanned": sum(1 for r in rows if r["image"] == "as scanned")}
    if scan is not None:
        summary["scan_bytes"] = scan.stat().st_size
    return summary


def textlayer_workspace(workspace: Path, out_name: str | None = None, image: str = "original", image_quality: int = 60,
                        on_page=None) -> dict:
    """Build <workspace>/textlayer/<name>.pdf from the pages and OCR. `image` is "original" (the
    scan's own page and pictures, unchanged: the smallest file, as the scan plus its text),
    "gray" (the page image as JPEG at its resolution), "lossless" (the same gray, Flate coded, no
    loss) or "bw" (black and white, CCITT G4)."""
    summary = _pages_pdf(workspace, "textlayer", out_name, image, image_quality, True, on_page)
    report.write_json(Path(workspace) / "textlayer" / "textlayer.json", summary)
    return summary


def pages_workspace(workspace: Path, out_name: str | None = None, image: str = "original", image_quality: int = 60,
                    on_page=None) -> dict:
    """"Pages only": <workspace>/pages-pdf/<name>.pdf, the preprocessed pages (split, straightened,
    turned upright, as chosen) with no OCR text."""
    summary = _pages_pdf(workspace, "pages-pdf", out_name, image, image_quality, False, on_page)
    report.write_json(Path(workspace) / "pages-pdf" / "pages.json", summary)
    return summary
