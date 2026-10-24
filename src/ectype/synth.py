"""Synthetic test pages: known text in a known font, rendered and degraded like a scan.

With the font and text known exactly, every later stage can be checked against
ground truth: character identity, ink boxes, baselines and spacing. Each page
writes `clean.png` (the undegraded render), `scan.png` and `scan.pdf` (the
degraded page as an image-only PDF), `truth.txt`, and `truth.json` with lines,
words and characters in clean-page pixel coordinates plus the affine transform
that maps them onto `scan.png`.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import pikepdf
from PIL import Image, ImageDraw, ImageFont

from . import report

DEFAULT_TEXT = """\
Of the many questions which the reader of an old book must ask, the first is whether the page in hand says what its author wrote. A careful scan preserves the shape of every letter, but the text it carries cannot be searched, copied, or quoted until each figure on the page has been identified and tied to the ink that forms it.

The present page was set for that purpose. Its words were chosen to exercise the common ligatures (fi, fl, ff, ffi, ffl), the figures 0123456789, capitals from A to Z, and the marks of punctuation: commas, semicolons; colons: question marks? exclamation points! quotation marks, both "double" and 'single', parentheses (like these), brackets [and these], hyphens, en dashes such as 1871–1872, and em dashes—like this one.

A justified page stretches the space between words so that every full line meets the right margin. The stretch varies from line to line, which is precisely why each word must be placed at its own measured position rather than at a fixed advance from the one before it. Quick zephyrs blow, vexing daft Jim; the five boxing wizards jump quickly; sphinx of black quartz, judge my vow.

Sixty-two pages later, the same reader may find that the fourth chapter quotes the first, and will want to search for the passage. That search succeeds only if the text behind the glyphs is right, and the glyphs succeed only if they look like the type that was actually printed.
"""

FONT_CANDIDATES = [
    "EB Garamond",
    "Liberation Serif",
    "DejaVu Serif",
    "FreeSerif",
]


@dataclass
class Degrade:
    """How to make a clean render look like a scan. All defaults are mild."""

    paper_gray: int = 238
    ink_gray: int = 18
    skew_degrees: float = 0.4
    ink_spread_px: int = 0
    blur_sigma: float = 0.6
    noise_sigma: float = 6.0
    speckles: int = 40
    jpeg_quality: int | None = 85

    @classmethod
    def none(cls) -> "Degrade":
        return cls(255, 0, 0.0, 0, 0.0, 0.0, 0, None)


def find_font(family: str | None = None) -> Path:
    """Resolve a font family name or file path to a font file on this system."""
    if family and Path(family).is_file():
        return Path(family)
    candidates = [family] if family else FONT_CANDIDATES
    if shutil.which("fc-match"):
        for name in candidates:
            out = subprocess.run(
                ["fc-match", "-f", "%{family}\n%{file}", f"{name}:style=Regular"],
                capture_output=True, text=True, check=False,
            ).stdout.splitlines()
            if len(out) == 2 and name.lower() in out[0].lower():
                return Path(out[1])
    raise FileNotFoundError(
        f"No font found for {family or ' / '.join(FONT_CANDIDATES)}; pass a font file with --font"
    )


def _layout(text: str, font: ImageFont.FreeTypeFont, width_px: float, indent_px: float, space_px: float):
    """Break paragraphs into justified lines. Yields (words, x positions, is_last_line)."""
    for p_index, paragraph in enumerate(text.strip().split("\n\n")):
        words = paragraph.split()
        line: list[str] = []
        indent = indent_px if p_index else 0.0
        for word in words:
            trial = line + [word]
            natural = sum(font.getlength(w) for w in trial) + space_px * (len(trial) - 1)
            if line and natural > width_px - indent:
                yield line, _justify(line, font, width_px - indent, space_px, indent, last=False), False
                line, indent = [word], 0.0
            else:
                line = trial
        if line:
            yield line, _justify(line, font, width_px - indent, space_px, indent, last=True), True


def _justify(words, font, width, space, indent, last):
    widths = [font.getlength(w) for w in words]
    gap = space
    if not last and len(words) > 1:
        gap = (width - sum(widths)) / (len(words) - 1)
    xs, x = [], indent
    for w in widths:
        xs.append(x)
        x += w + gap
    return xs


def make_page(
    out_dir: Path,
    text: str = DEFAULT_TEXT,
    font_path: Path | None = None,
    dpi: int = 300,
    point_size: float = 11.0,
    page_size_pt: tuple[float, float] = (432.0, 648.0),
    margin_pt: float = 54.0,
    leading: float = 1.32,
    degrade: Degrade | None = None,
    seed: int = 0,
) -> dict:
    """Render one synthetic page and its ground truth into out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    degrade = degrade if degrade is not None else Degrade()
    font_path = Path(font_path) if font_path else find_font()
    px = dpi / 72.0
    size_px = point_size * px
    font = ImageFont.truetype(str(font_path), size=round(size_px), layout_engine=ImageFont.Layout.BASIC)
    page_w, page_h = round(page_size_pt[0] * px), round(page_size_pt[1] * px)
    margin = margin_pt * px
    line_step = point_size * leading * px
    space = font.getlength(" ")

    image = Image.new("L", (page_w, page_h), 255)
    draw = ImageDraw.Draw(image)
    ascent, _ = font.getmetrics()
    baseline = margin + ascent
    lines = []
    for words, xs, last in _layout(text, font, page_w - 2 * margin, 1.5 * size_px, space):
        if baseline > page_h - margin:
            raise ValueError("Text does not fit on one page; shorten it or reduce --size")
        line_words = []
        for word, x in zip(words, xs):
            ox = margin + x
            draw.text((ox, baseline), word, font=font, fill=0, anchor="ls")
            chars = []
            for i, ch in enumerate(word):
                cx = ox + font.getlength(word[:i])
                l, t, r, b = font.getbbox(ch, anchor="ls")
                chars.append({
                    "text": ch,
                    "origin": [round(cx, 2), round(baseline, 2)],
                    "advance": round(font.getlength(ch), 2),
                    "box": [round(cx + l, 1), round(baseline + t, 1), round(cx + r, 1), round(baseline + b, 1)],
                })
            l, t, r, b = font.getbbox(word, anchor="ls")
            line_words.append({
                "text": word,
                "origin": [round(ox, 2), round(baseline, 2)],
                "box": [round(ox + l, 1), round(baseline + t, 1), round(ox + r, 1), round(baseline + b, 1)],
                "chars": chars,
            })
        lines.append({
            "text": " ".join(words),
            "baseline": round(baseline, 2),
            "last_in_paragraph": last,
            "words": line_words,
        })
        baseline += line_step * (1.5 if last else 1.0)

    clean = np.asarray(image)
    scan, transform = _degrade(clean, degrade, seed)
    Image.fromarray(clean).save(out_dir / "clean.png", dpi=(dpi, dpi))
    Image.fromarray(scan).save(out_dir / "scan.png", dpi=(dpi, dpi))
    _write_image_pdf(out_dir / "scan.pdf", scan, dpi, degrade.jpeg_quality)

    truth_text = _truth_text(lines)
    (out_dir / "truth.txt").write_text(truth_text, encoding="utf-8")
    font_info = {
        "path": str(font_path),
        "family": font.getname()[0],
        "style": font.getname()[1],
        "sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
        "layout": "basic (no kerning, no ligatures)",
    }
    truth = {
        "generator": "ectype synth",
        "font": font_info,
        "dpi": dpi,
        "point_size": point_size,
        "page_size_pt": list(page_size_pt),
        "size_px": [page_w, page_h],
        "seed": seed,
        "degradation": asdict(degrade),
        "transform": transform.round(6).tolist(),
        "text": truth_text,
        "lines": lines,
    }
    report.write_json(out_dir / "truth.json", truth)
    return truth


def _truth_text(lines: list[dict]) -> str:
    out = []
    for line in lines:
        out.append(line["text"])
        if line["last_in_paragraph"]:
            out.append("")
    return "\n".join(out).strip() + "\n"


def _degrade(clean: np.ndarray, d: Degrade, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Apply scan-like damage. Returns the image and the 2x3 clean-to-scan transform."""
    rng = np.random.default_rng(seed)
    img = clean.astype(np.float32)
    h, w = img.shape
    if d.ink_spread_px > 0:
        k = np.ones((2 * d.ink_spread_px + 1,) * 2, np.uint8)
        img = cv2.erode(img, k)  # dark ink grows
    transform = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    if d.skew_degrees:
        transform = cv2.getRotationMatrix2D((w / 2, h / 2), d.skew_degrees, 1.0)
        img = cv2.warpAffine(img, transform, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    if d.blur_sigma > 0:
        img = cv2.GaussianBlur(img, (0, 0), d.blur_sigma)
    # Paper is rarely pure white, nor ink pure black.
    img = d.paper_gray - (d.paper_gray - d.ink_gray) * (1 - img / 255.0)
    if d.noise_sigma > 0:
        img += rng.normal(0, d.noise_sigma, img.shape)
    for _ in range(d.speckles):
        y, x = rng.integers(0, h), rng.integers(0, w)
        r = rng.uniform(0.8, 2.5)
        cv2.circle(img, (int(x), int(y)), max(1, round(r)), float(rng.uniform(20, 90)), -1, lineType=cv2.LINE_AA)
    img = np.clip(img, 0, 255).astype(np.uint8)
    if d.jpeg_quality:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, d.jpeg_quality])
        img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    return img, transform


def _write_image_pdf(path: Path, gray: np.ndarray, dpi: int, jpeg_quality: int | None) -> None:
    """Wrap a grayscale page image in a one-page PDF at its true physical size."""
    h, w = gray.shape
    pdf = pikepdf.new()
    if jpeg_quality:
        buf = io.BytesIO()
        Image.fromarray(gray).save(buf, format="JPEG", quality=jpeg_quality)
        data, filt = buf.getvalue(), pikepdf.Name.DCTDecode
    else:
        data, filt = zlib.compress(gray.tobytes()), pikepdf.Name.FlateDecode
    image = pikepdf.Stream(pdf, data)
    image.Type, image.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    image.Width, image.Height = w, h
    image.ColorSpace, image.BitsPerComponent, image.Filter = pikepdf.Name.DeviceGray, 8, filt
    wp, hp = w * 72 / dpi, h * 72 / dpi
    page = pdf.add_blank_page(page_size=(wp, hp))
    page.Resources.XObject = pikepdf.Dictionary(Im0=image)
    page.Contents = pikepdf.Stream(pdf, f"q {wp:.4f} 0 0 {hp:.4f} 0 0 cm /Im0 Do Q".encode())
    pdf.docinfo["/Producer"] = "ectype synth"
    pdf.save(path)
