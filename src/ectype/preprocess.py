"""Preprocessing: orient, split spreads, deskew. One grayscale PNG per book page.

Orientation comes from the image (Tesseract OSD), never from the PDF's /Rotate,
which scanners get wrong (the Young scan's is off by 90°). Two-page spreads are
split at the gutter, found from where the text stops and the fold shadow is
darkest, so off-centre folds split correctly. Each half is deskewed on its own.

Every output page gets a JSON sidecar with the transforms applied, including a
2x3 matrix from source-image pixels to output pixels, so later stages can map
anything back to the scan. A per-page overrides file (also written by the review
report and, later, the GUI) can force orientation, the split position, or skew:

    {"3": {"rotate": 180, "split": 1642, "skew": 0.25}, "7": {"split": false}}

Keys are 1-based source pages. "rotate" is clockwise degrees applied to the
stored image; "split" is an x position in the oriented image, or false.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pikepdf
import pypdfium2 as pdfium
import pytesseract
from PIL import Image

from . import report

IMAGE_SUFFIXES = {".png", ".tif", ".tiff", ".jpg", ".jpeg", ".bmp"}
SPREAD_ASPECT = 1.15  # width / height above which an upright page is treated as a spread


# ---------------------------------------------------------------------------
# Loading


@dataclass
class SourcePage:
    number: int  # 1-based
    gray: np.ndarray
    dpi: float
    origin: str  # "embedded image", "rendered", or "image file"


def load_pages(path: Path, dpi: float = 300, pages: list[int] | None = None):
    """Yield source pages. A PDF page that is one full-page image yields that image
    at its native resolution; anything else is rendered (without /Rotate) at dpi."""
    path = Path(path)
    if path.suffix.lower() in IMAGE_SUFFIXES:
        image = Image.open(path)
        yield SourcePage(1, np.asarray(image.convert("L")).copy(), report.image_dpi(image, dpi), "image file")
        return
    pdf = pikepdf.open(path)
    doc = pdfium.PdfDocument(str(path))
    try:
        for index, page in enumerate(pdf.pages):
            number = index + 1
            if pages and number not in pages:
                continue
            native = _single_page_image(page)
            if native is not None:
                gray, width_pt = native
                yield SourcePage(number, gray, gray.shape[1] / (width_pt / 72), "embedded image")
                continue
            p = doc[index]
            bitmap = p.render(scale=dpi / 72 * (1 - 1e-6), rotation=(360 - p.get_rotation()) % 360, grayscale=True)
            yield SourcePage(number, np.asarray(bitmap.to_pil().convert("L")).copy(), dpi, "rendered")
    finally:
        doc.close()
        pdf.close()


def page_count(path: Path, pages: list[int] | None = None) -> int:
    """How many source pages load_pages will yield."""
    if Path(path).suffix.lower() in IMAGE_SUFFIXES:
        return 1
    with pikepdf.open(path) as pdf:
        n = len(pdf.pages)
    return len([p for p in pages if 1 <= p <= n]) if pages else n


def _single_page_image(page: pikepdf.Page) -> tuple[np.ndarray, float] | None:
    """The page's only image, if the page is nothing but one image covering it."""
    resources = page.obj.get("/Resources")
    if resources is None or "/Font" in resources:
        return None
    xobjects = resources.get("/XObject", {})
    images = [x for _, x in xobjects.items() if x.get("/Subtype") == "/Image"]
    if len(images) != 1 or len(xobjects) != 1:
        return None
    try:
        gray = np.asarray(pikepdf.PdfImage(images[0]).as_pil_image().convert("L")).copy()
    except Exception:
        return None
    box = [float(v) for v in page.mediabox]
    width_pt, height_pt = box[2] - box[0], box[3] - box[1]
    # Covers the page if its aspect ratio matches the unrotated MediaBox.
    if abs(np.log((gray.shape[1] / gray.shape[0]) / (width_pt / height_pt))) > 0.03:
        return None
    return gray, width_pt


# ---------------------------------------------------------------------------
# Measurements


def detect_orientation(gray: np.ndarray, max_side: int = 1700) -> tuple[int, float]:
    """Clockwise rotation (0/90/180/270) that makes the text upright, and Tesseract's confidence."""
    scale = min(1.0, max_side / max(gray.shape))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    from .resources import configure_pytesseract

    configure_pytesseract()
    try:
        osd = pytesseract.image_to_osd(Image.fromarray(small), config="--psm 0 -c min_characters_to_try=30",
                                       output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError:
        return 0, 0.0
    # OSD's "rotate" is how far the image is turned counter-clockwise; undo it clockwise.
    return int(osd["rotate"]) % 360, float(osd["orientation_conf"])


def turn(gray: np.ndarray, clockwise: int) -> np.ndarray:
    return np.ascontiguousarray(np.rot90(gray, -(clockwise // 90) % 4))


def turn_matrix(clockwise: int, w: int, h: int) -> np.ndarray:
    """3x3 matrix mapping pixel coordinates of a w x h image to the image turned clockwise."""
    k = (clockwise // 90) % 4
    return {
        0: np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], float),
        1: np.array([[0, -1, h - 1], [1, 0, 0], [0, 0, 1]], float),
        2: np.array([[-1, 0, w - 1], [0, -1, h - 1], [0, 0, 1]], float),
        3: np.array([[0, 1, 0], [-1, 0, w - 1], [0, 0, 1]], float),
    }[k]


def binarize(gray: np.ndarray) -> np.ndarray:
    """Ink mask (True = ink) by Otsu on a lightly blurred image."""
    _, binary = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary == 0


def text_mask(ink: np.ndarray) -> np.ndarray:
    """Ink in letter-sized connected components only (drops shadows, rules, specks, figures)."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    if n <= 1:
        return np.zeros_like(ink)
    h = stats[1:, cv2.CC_STAT_HEIGHT]
    w = stats[1:, cv2.CC_STAT_WIDTH]
    median_h = float(np.median(h[h >= 4])) if (h >= 4).any() else 10.0
    keep = (h >= 0.3 * median_h) & (h <= 3.0 * median_h) & (w <= 4.0 * median_h)
    return np.concatenate([[False], keep])[labels]


def estimate_skew(gray: np.ndarray, max_degrees: float = 3.0) -> tuple[float, float]:
    """Skew in degrees (counter-clockwise positive, as cv2) that would level the text lines,
    by maximising the sharpness of the row profile. Returns (angle, score gain over 0°)."""
    scale = min(1.0, 1200 / gray.shape[1])
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    mask = text_mask(binarize(small)).astype(np.float32)
    if mask.sum() < 500:
        return 0.0, 0.0
    h, w = mask.shape
    centre = (w / 2, h / 2)

    def score(angle: float) -> float:
        rotated = cv2.warpAffine(mask, cv2.getRotationMatrix2D(centre, angle, 1.0), (w, h))
        rows = rotated.sum(axis=1)
        return float(np.sum(np.diff(rows) ** 2))

    coarse = np.arange(-max_degrees, max_degrees + 1e-9, 0.2)
    scores = [score(a) for a in coarse]
    best = float(coarse[int(np.argmax(scores))])
    fine = np.arange(best - 0.2, best + 0.2 + 1e-9, 0.02)
    fine_scores = [score(a) for a in fine]
    angle = float(fine[int(np.argmax(fine_scores))])
    base = score(0.0)
    return round(angle, 2), round(max(fine_scores) / base, 3) if base else 0.0


@dataclass
class Gutter:
    x: int
    confidence: float  # 0..1: how empty of text the gutter band is relative to the text columns
    band: tuple[int, int]  # the text-free band the split falls in
    method: str


def find_gutter(gray: np.ndarray, search: tuple[float, float] = (0.3, 0.7)) -> Gutter:
    """Split position of a spread: inside the widest text-free vertical band near the
    middle, at its darkest column (the fold shadow) when there is one, else its centre."""
    h, w = gray.shape
    scale = min(1.0, 1600 / w)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    sh, sw = small.shape
    text = text_mask(binarize(small))
    density = text.sum(axis=0).astype(float)
    density = np.convolve(density, np.ones(5) / 5, mode="same")
    lo, hi = int(search[0] * sw), int(search[1] * sw)
    peak = np.percentile(density[: sw // 2], 90) + np.percentile(density[sw // 2:], 90)
    empty = density < max(0.03 * peak / 2, 0.5)

    best = None  # widest empty run overlapping the search window, preferring the centre
    x = lo
    while x < hi:
        if empty[x]:
            start = x
            while start > 0 and empty[start - 1]:
                start -= 1
            end = x
            while end < sw - 1 and empty[end + 1]:
                end += 1
            width = end - start + 1
            centre_bias = 1 - abs((start + end) / 2 - sw / 2) / (sw / 2)
            key = width * (0.5 + centre_bias)
            if best is None or key > best[0]:
                best = (key, start, end)
            x = end + 1
        else:
            x += 1

    if best is None:
        # No text-free band: cut at the least texty column near the middle.
        xs = int(lo + np.argmin(density[lo:hi]))
        return Gutter(round(xs / scale), 0.0, (round(xs / scale), round(xs / scale)), "minimum text density")

    _, start, end = best
    columns = small[:, start:end + 1].astype(float)
    profile = np.convolve(columns.mean(axis=0), np.ones(3) / 3, mode="same") if end - start >= 2 else columns.mean(axis=0)
    paper = float(np.percentile(small, 90))
    darkest = int(np.argmin(profile))
    if paper - profile[darkest] > 25 and 1 <= darkest <= len(profile) - 2:
        xs, method = start + darkest, "fold shadow"
    else:
        xs, method = (start + end) // 2, "centre of text-free band"
    inside = density[start:end + 1].mean() if end >= start else 0.0
    confidence = float(np.clip(1 - inside / (peak / 2 + 1e-9) * 10, 0, 1)) * min(1.0, (end - start + 1) / (0.02 * sw))
    if method != "fold shadow" and (end - start + 1) > 0.25 * sw:
        # One side is blank and there's no fold shadow: the band says little about the fold.
        confidence = min(confidence, 0.2)
        method = "blank side, no fold shadow"
    return Gutter(round(xs / scale), round(confidence, 3), (round(start / scale), round(end / scale)), method)


# ---------------------------------------------------------------------------
# Driver


@dataclass
class OutputPage:
    name: str
    source_page: int
    side: str  # "left", "right", or "full"
    file: str
    size_px: list[int]
    dpi: float
    rotate: int
    orientation_confidence: float
    split_x: int | None
    gutter: dict | None
    skew_degrees: float
    skew_gain: float
    transform: list[list[float]]  # 2x3, source-image pixels -> output pixels
    overridden: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def preprocess(
    input_path: Path,
    out_dir: Path,
    keep_spreads: bool = False,
    overrides: dict | None = None,
    pages: list[int] | None = None,
    deskew: bool = True,
    dpi: float = 300,
    on_page=None,
) -> dict:
    """Orient, split and deskew every source page into <out_dir>/pages. `on_page(done, total)`
    is called as pages are done (two passes, so total is twice the source pages)."""
    out_dir = Path(out_dir)
    page_dir = out_dir / "pages"
    report_dir = out_dir / "preprocess"
    view_dir = report_dir / "review"
    page_dir.mkdir(parents=True, exist_ok=True)
    view_dir.mkdir(parents=True, exist_ok=True)
    overrides = {str(k): v for k, v in (overrides or {}).items()}

    # Pass 1: orientation and gutter for every page, so weak gutters can borrow
    # the document's typical fold position. Images are reloaded in pass 2 rather
    # than held, to keep memory flat on long books.
    measured: dict[int, dict] = {}
    total = 2 * page_count(Path(input_path), pages)
    for src in load_pages(Path(input_path), dpi=dpi, pages=pages):
        ov = overrides.get(str(src.number), {})
        if "rotate" in ov:
            rotate, conf, overridden = int(ov["rotate"]) % 360, 1.0, ["rotate"]
        else:
            rotate, conf = detect_orientation(src.gray)
            overridden = []
        oriented = turn(src.gray, rotate)
        oh, ow = oriented.shape
        is_spread = ow / oh > SPREAD_ASPECT
        gutter = find_gutter(oriented) if is_spread and not keep_spreads and "split" not in ov else None
        measured[src.number] = {"rotate": rotate, "conf": conf, "overridden": overridden,
                                "spread": is_spread, "width": ow, "gutter": gutter}
        if on_page:
            on_page(len(measured), total)
    confident = [m["gutter"].x / m["width"] for m in measured.values() if m["gutter"] and m["gutter"].confidence >= 0.5]
    typical = float(np.median(confident)) if confident else None

    sources = []
    outputs: list[OutputPage] = []
    for src in load_pages(Path(input_path), dpi=dpi, pages=pages):
        ov = overrides.get(str(src.number), {})
        m = measured[src.number]
        rotate, conf, overridden, gutter, is_spread = m["rotate"], m["conf"], list(m["overridden"]), m["gutter"], m["spread"]
        h0, w0 = src.gray.shape
        oriented = turn(src.gray, rotate)
        base = turn_matrix(rotate, w0, h0)
        oh, ow = oriented.shape
        flags = [] if conf >= 2 or overridden else ["low orientation confidence"]

        split_x = None
        if "split" in ov:
            overridden.append("split")
            split_x = int(ov["split"]) if ov["split"] not in (False, None) else None
        elif gutter is not None:
            split_x = gutter.x
            if gutter.confidence < 0.5:
                if typical is not None:
                    split_x = round(typical * ow)
                    gutter.method += "; split at document median"
                    flags.append("gutter from document median")
                else:
                    flags.append("uncertain gutter")

        parts = [("full", 0, ow)] if split_x is None else [("left", 0, split_x), ("right", split_x, ow)]
        source_view = _source_view(oriented, split_x, gutter)
        Image.fromarray(source_view).save(view_dir / f"src-{src.number:03d}.png")
        sources.append({
            "page": src.number, "origin": src.origin, "size_px": [w0, h0], "dpi": round(src.dpi, 1),
            "rotate": rotate, "orientation_confidence": round(conf, 2), "spread": is_spread,
            "split_x": split_x, "gutter": vars(gutter) if gutter else None,
            "overridden": overridden, "flags": flags, "view": f"review/src-{src.number:03d}.png",
        })

        for side, x0, x1 in parts:
            piece = oriented[:, x0:x1]
            crop = np.array([[1, 0, -x0], [0, 1, 0], [0, 0, 1]], float)
            skew, gain = 0.0, 0.0
            if "skew" in ov:
                skew = float(ov["skew"])
            elif deskew:
                skew, gain = estimate_skew(piece)
            ph, pw = piece.shape
            rot = np.vstack([cv2.getRotationMatrix2D((pw / 2, ph / 2), skew, 1.0), [0, 0, 1]])
            if skew:
                paper = int(np.percentile(piece, 90))
                piece = cv2.warpAffine(piece, rot[:2], (pw, ph), flags=cv2.INTER_LINEAR, borderValue=paper)
            matrix = (rot @ crop @ base)[:2]
            page_flags = list(flags)
            if text_mask(binarize(piece)).mean() < 0.0005:
                page_flags.append("blank")
            suffix = {"full": "", "left": "L", "right": "R"}[side]
            name = f"p{src.number:03d}{suffix}"
            _save_if_changed(page_dir / f"{name}.png", piece, src.dpi)
            page = OutputPage(
                name=name, source_page=src.number, side=side, file=f"pages/{name}.png",
                size_px=[pw, ph], dpi=round(src.dpi, 1), rotate=rotate, orientation_confidence=round(conf, 2),
                split_x=split_x, gutter=vars(gutter) if gutter else None,
                skew_degrees=skew, skew_gain=gain, transform=np.round(matrix, 6).tolist(),
                overridden=overridden + (["skew"] if "skew" in ov else []), flags=page_flags,
            )
            report.write_json(page_dir / f"{name}.json", vars(page))
            outputs.append(page)
        if on_page:
            on_page(len(measured) + len(sources), total)

    # Pages a source page no longer produces (a spread now kept whole, or the reverse) go.
    produced = {p.name for p in outputs}
    redone = {p.source_page for p in outputs}
    for stale in page_dir.glob("p*.png"):
        num = stale.stem[1:4]
        if stale.stem not in produced and num.isdigit() and int(num) in redone:
            stale.unlink()
            stale.with_suffix(".json").unlink(missing_ok=True)

    summary = {
        "input": str(input_path),
        "keep_spreads": keep_spreads,
        "sources": sources,
        "pages": [vars(p) for p in outputs],
    }
    report.write_json(report_dir / "preprocess.json", summary)
    _write_html(summary, report_dir / "index.html")
    return summary


def _save_if_changed(path: Path, image: np.ndarray, dpi: float) -> None:
    """Write a page image unless an identical one is already there, so later stages'
    caches (keyed on file times) stay valid when only some pages were redone."""
    if path.is_file():
        try:
            old = np.asarray(Image.open(path).convert("L"))
            if old.shape == image.shape and np.array_equal(old, image):
                return
        except OSError:
            pass
    Image.fromarray(image).save(path, dpi=(dpi, dpi))


def load_overrides(path: Path | None) -> dict:
    if path is None or not Path(path).is_file():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _source_view(oriented: np.ndarray, split_x: int | None, gutter) -> np.ndarray:
    """Thumbnail of the oriented source with the text-free band and the split marked."""
    scale = 700 / max(oriented.shape)
    small = cv2.resize(oriented, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    view = cv2.cvtColor(small, cv2.COLOR_GRAY2RGB)
    if gutter is not None:
        a, b = (round(v * scale) for v in gutter.band)
        band = view[:, a:b + 1].astype(np.int16)
        band[..., 2] = np.minimum(band[..., 2] + 60, 255)
        view[:, a:b + 1] = band.astype(np.uint8)
    if split_x is not None:
        x = round(split_x * scale)
        view[:, max(x - 1, 0):x + 1] = (220, 40, 40)
    return view


def _write_html(s: dict, path: Path) -> None:
    by_source: dict[int, list[dict]] = {}
    for p in s["pages"]:
        by_source.setdefault(p["source_page"], []).append(p)
    flagged = [src["page"] for src in s["sources"] if src["flags"]]
    body = [
        f"<p class=\"muted\">{report.esc(Path(s['input']).name)} · {len(s['sources'])} source pages → "
        f"{len(s['pages'])} output pages{' · spreads kept' if s['keep_spreads'] else ''}. "
        "Red line: split. Blue band: text-free gutter. To correct a page, add it to an overrides file "
        "(<code>{\"3\": {\"split\": 1642}}</code>, <code>{\"rotate\": 180}</code>, <code>{\"skew\": 0.2}</code>, "
        "<code>{\"split\": false}</code>) and pass <code>--overrides</code>.</p>",
    ]
    if flagged:
        body.append(f"<p><b>Check pages:</b> {', '.join(map(str, flagged))}</p>")
    body.append(report.table(
        ["Source", "Origin", "dpi", "Turned", "OSD conf", "Split at", "Gutter method", "Gutter conf", "Output", "Skew", "Flags"],
        [[src["page"], src["origin"], src["dpi"], f"{src['rotate']}°", src["orientation_confidence"],
          src["split_x"] if src["split_x"] is not None else "—",
          src["gutter"]["method"] if src["gutter"] else ("override" if "split" in src["overridden"] else "—"),
          src["gutter"]["confidence"] if src["gutter"] else "—",
          " ".join(p["name"] for p in by_source.get(src["page"], [])),
          " ".join(f"{p['skew_degrees']:+.2f}°" for p in by_source.get(src["page"], [])),
          ", ".join(src["flags"] + [f"override: {o}" for o in src["overridden"]])]
         for src in s["sources"]],
        numeric={0, 2, 4, 5, 7},
    ))
    for src in s["sources"]:
        body.append(f"<h3>Source page {src['page']}</h3><div class=\"row\">")
        body.append(f"<figure><img loading=\"lazy\" src=\"{src['view']}\" alt=\"\"><figcaption>oriented source</figcaption></figure>")
        for p in by_source.get(src["page"], []):
            body.append(f"<figure><a href=\"../{p['file']}\"><img loading=\"lazy\" src=\"../{p['file']}\" alt=\"\" style=\"max-height:420px\"></a>"
                        f"<figcaption>{p['name']} · {p['size_px'][0]}×{p['size_px'][1]} · skew {p['skew_degrees']:+.2f}°</figcaption></figure>")
        body.append("</div>")
    report.write_page(path, "Preprocess", "\n".join(body))
