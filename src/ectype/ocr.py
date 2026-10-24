"""OCR engines behind one interface, producing words, lines, baselines and characters.

Each engine returns an `OcrPage` in the page's pixel coordinates. Tesseract is
implemented through its hOCR output with per-character boxes; Kraken is the
planned second engine (Greek, Hebrew, Rashi, Syriac, Arabic) and reports itself
unavailable until it is installed. Results are written as `<page>.ocr.json`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import report


@dataclass
class Char:
    text: str
    box: list[int]  # x0, y0, x1, y1 (x1, y1 exclusive)
    conf: float


@dataclass
class Word:
    text: str
    box: list[int]
    conf: float
    chars: list[Char] = field(default_factory=list)
    font_size_pt: float | None = None


@dataclass
class Line:
    box: list[int]
    baseline: list[float]  # [slope, offset]: y = box[3] + offset + slope * (x - box[0])
    x_size: float | None
    ascenders: float | None
    descenders: float | None
    words: list[Word] = field(default_factory=list)

    def baseline_y(self, x: float) -> float:
        slope, offset = self.baseline
        return self.box[3] + offset + slope * (x - self.box[0])


@dataclass
class OcrPage:
    engine: str
    lang: str
    size_px: list[int]
    dpi: float
    lines: list[Line] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)  # words removed as noise, kept for review

    def words(self):
        for line in self.lines:
            yield from line.words

    def text(self) -> str:
        return "\n".join(" ".join(w.text for w in line.words) for line in self.lines)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "OcrPage":
        lines = []
        for ld in data["lines"]:
            words = [Word(**{**wd, "chars": [Char(**c) for c in wd["chars"]]}) for wd in ld["words"]]
            lines.append(Line(**{**ld, "words": words}))
        return cls(**{**data, "lines": lines})


def drop_margin_noise(page: OcrPage, max_conf: float = 70.0) -> OcrPage:
    """Remove low-confidence words lying wholly outside the text column (gutter shadow, edge specks).

    The column is the span of confident words; running heads and folios sit inside it.
    """
    confident = [w for w in page.words() if w.conf >= 85 and len(w.text) >= 3]
    if len(confident) < 10:
        return page
    left = float(np.percentile([w.box[0] for w in confident], 2))
    right = float(np.percentile([w.box[2] for w in confident], 98))
    sizes = [line.x_size for line in page.lines if line.x_size]
    slack = 1.5 * (float(np.median(sizes)) if sizes else 30.0)
    kept_lines = []
    for line in page.lines:
        kept = []
        for w in line.words:
            outside = w.box[2] < left - slack or w.box[0] > right + slack
            if outside and w.conf < max_conf:
                page.dropped.append({"text": w.text, "box": w.box, "conf": w.conf, "reason": "outside text column"})
            else:
                kept.append(w)
        line.words = kept
        if kept:
            kept_lines.append(line)
    page.lines = kept_lines
    return page


class EngineUnavailable(RuntimeError):
    pass


class TesseractEngine:
    name = "tesseract"

    def __init__(self, psm: int = 3, oem: int = 1):
        if not shutil.which("tesseract"):
            raise EngineUnavailable("tesseract not found; install it (e.g. apt install tesseract-ocr)")
        self.psm, self.oem = psm, oem

    def recognize(self, gray: np.ndarray, dpi: float = 300, lang: str = "eng") -> OcrPage:
        with tempfile.TemporaryDirectory(prefix="ectype-tess-") as tmp:
            src = Path(tmp) / "page.png"
            Image.fromarray(gray).save(src)
            subprocess.run(
                ["tesseract", str(src), str(Path(tmp) / "out"), "-l", lang, "--dpi", str(round(dpi)),
                 "--psm", str(self.psm), "--oem", str(self.oem),
                 "-c", "hocr_char_boxes=1", "-c", "hocr_font_info=1", "hocr"],
                check=True, capture_output=True,
            )
            hocr = (Path(tmp) / "out.hocr").read_text(encoding="utf-8")
        page = parse_hocr(hocr, engine=self.name, lang=lang, dpi=dpi)
        page.size_px = [gray.shape[1], gray.shape[0]]
        return page


class KrakenEngine:
    name = "kraken"

    def __init__(self, model: str | None = None):
        try:
            import kraken  # noqa: F401
        except ImportError as exc:
            raise EngineUnavailable("Kraken is not installed (pip install kraken) or its models are unavailable") from exc
        raise EngineUnavailable("The Kraken engine is planned for Phase 5b and not implemented yet")


ENGINES = {"tesseract": TesseractEngine, "kraken": KrakenEngine}


def get_engine(name: str = "tesseract", **kwargs):
    try:
        return ENGINES[name](**kwargs)
    except KeyError:
        raise EngineUnavailable(f"unknown OCR engine {name!r}; choose from {', '.join(ENGINES)}") from None


# ---------------------------------------------------------------------------
# hOCR


def _title(element) -> dict[str, list[str]]:
    props = {}
    for part in (element.get("title") or "").split(";"):
        bits = part.split()
        if bits:
            props[bits[0]] = bits[1:]
    return props


def _box(props: dict, key: str = "bbox") -> list[int]:
    # Tesseract's right and bottom edges are already exclusive (checked against synthetic truth).
    x0, y0, x1, y1 = (int(v) for v in props[key][:4])
    return [x0, y0, x1, y1]


def parse_hocr(hocr: str, engine: str = "tesseract", lang: str = "eng", dpi: float = 300) -> OcrPage:
    """Parse hOCR with ocrx_cinfo character boxes (Tesseract's hocr_char_boxes)."""
    # Tesseract's hOCR is XHTML; drop the doctype so ElementTree needs no network or DTD.
    hocr = re.sub(r"<!DOCTYPE[^>]*>", "", hocr)
    root = ET.fromstring(hocr)
    ns = {"h": "http://www.w3.org/1999/xhtml"}
    page = OcrPage(engine=engine, lang=lang, size_px=[0, 0], dpi=dpi)
    page_el = root.find(".//h:div[@class='ocr_page']", ns)
    if page_el is not None:
        x0, y0, x1, y1 = _box(_title(page_el))
        page.size_px = [x1 - x0, y1 - y0]
    for line_el in root.iter("{http://www.w3.org/1999/xhtml}span"):
        cls = line_el.get("class", "")
        if cls not in ("ocr_line", "ocr_header", "ocr_caption", "ocr_textfloat"):
            continue
        props = _title(line_el)
        baseline = [float(v) for v in props.get("baseline", ["0", "0"])]
        line = Line(
            box=_box(props),
            baseline=baseline,
            x_size=float(props["x_size"][0]) if "x_size" in props else None,
            ascenders=float(props["x_ascenders"][0]) if "x_ascenders" in props else None,
            descenders=float(props["x_descenders"][0]) if "x_descenders" in props else None,
        )
        for word_el in line_el.findall("h:span[@class='ocrx_word']", ns):
            wprops = _title(word_el)
            chars = []
            for c_el in word_el.findall("h:span[@class='ocrx_cinfo']", ns):
                cprops = _title(c_el)
                text = "".join(c_el.itertext())
                if not text or "x_bboxes" not in cprops:
                    continue
                chars.append(Char(text=text, box=_box(cprops, "x_bboxes"), conf=round(float(cprops.get("x_conf", ["0"])[0]), 2)))
            text = "".join(c.text for c in chars) if chars else "".join(word_el.itertext()).strip()
            if not text.strip():
                continue
            line.words.append(Word(
                text=text,
                box=_box(wprops),
                conf=float(wprops.get("x_wconf", ["0"])[0]),
                chars=chars,
                font_size_pt=float(wprops["x_fsize"][0]) if "x_fsize" in wprops else None,
            ))
        if line.words:
            page.lines.append(line)
    return page


# ---------------------------------------------------------------------------
# Driver


def ocr_pages(pages_dir: Path, out_dir: Path, engine: str = "tesseract", lang: str = "eng",
              low_conf: float = 70.0) -> dict:
    """OCR every page PNG from a preprocess run; write .ocr.json, overlays and a report."""
    pages_dir, out_dir = Path(pages_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    eng = get_engine(engine)
    rows = []
    for png in sorted(pages_dir.glob("*.png")):
        image = Image.open(png)
        dpi = float(image.info.get("dpi", (300,))[0] or 300)
        gray = np.asarray(image.convert("L"))
        page = drop_margin_noise(eng.recognize(gray, dpi=dpi, lang=lang))
        report.write_json(out_dir / f"{png.stem}.ocr.json", page.to_json())
        Image.fromarray(draw_overlay(gray, page, low_conf)).save(out_dir / f"{png.stem}.overlay.png")
        words = list(page.words())
        low = [w for w in words if w.conf < low_conf]
        rows.append({
            "page": png.stem, "lines": len(page.lines), "words": len(words),
            "chars": sum(len(w.chars) for w in words),
            "mean_conf": round(float(np.mean([w.conf for w in words])), 1) if words else None,
            "low_conf_words": [w.text for w in low],
            "dropped": len(page.dropped),
        })
    summary = {"engine": engine, "lang": lang, "low_conf_threshold": low_conf, "pages": rows}
    report.write_json(out_dir / "ocr.json", summary)
    _write_html(summary, out_dir / "index.html")
    return summary


def draw_overlay(gray: np.ndarray, page: OcrPage, low_conf: float = 70.0) -> np.ndarray:
    """Page with baselines (green), word boxes (blue; red if low confidence) and character boxes (grey)."""
    view = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    view = (255 - (255 - view) * 0.7).astype(np.uint8)
    for line in page.lines:
        x0, x1 = line.box[0], line.box[2]
        cv2.line(view, (x0, round(line.baseline_y(x0))), (x1, round(line.baseline_y(x1))), (40, 170, 70), 1)
        for word in line.words:
            for c in word.chars:
                cv2.rectangle(view, (c.box[0], c.box[1]), (c.box[2] - 1, c.box[3] - 1), (170, 170, 170), 1)
            colour = (220, 40, 40) if word.conf < low_conf else (40, 90, 220)
            cv2.rectangle(view, (word.box[0], word.box[1]), (word.box[2] - 1, word.box[3] - 1), colour, 2)
    return view


def _write_html(s: dict, path: Path) -> None:
    body = [f"<p class=\"muted\">Engine {report.esc(s['engine'])} · language {report.esc(s['lang'])}. "
            f"Overlays: green baselines, blue word boxes, red words below {s['low_conf_threshold']:g} confidence, grey character boxes.</p>"]
    body.append(report.table(
        ["Page", "Lines", "Words", "Chars", "Mean conf", "Dropped as margin noise", "Low-confidence words"],
        [[r["page"], r["lines"], r["words"], r["chars"], r["mean_conf"], r["dropped"], " ".join(r["low_conf_words"][:30])] for r in s["pages"]],
        numeric={1, 2, 3, 4, 5},
    ))
    body.append("<div class=\"row\">")
    for r in s["pages"]:
        body.append(f"<figure><a href=\"{r['page']}.overlay.png\"><img loading=\"lazy\" src=\"{r['page']}.overlay.png\" alt=\"\" style=\"max-height:520px\"></a>"
                    f"<figcaption>{r['page']}</figcaption></figure>")
    body.append("</div>")
    report.write_page(path, "OCR", "\n".join(body))
