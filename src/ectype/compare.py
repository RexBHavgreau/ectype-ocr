"""Compare two renderings of the same pages: ours against Acrobat's, or either against the scan.

For each page: side-by-side renders, a difference overlay (red = ink only in A,
blue = ink only in B, dark = both), SSIM, and differing-ink share. For the
documents: file size, bytes per page, and text-extraction accuracy as character
error rate (CER) against ground truth when it is given.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from rapidfuzz.distance import Levenshtein
from skimage.metrics import structural_similarity

from . import report

IMAGE_SUFFIXES = {".png", ".tif", ".tiff", ".jpg", ".jpeg", ".bmp"}


@dataclass
class Source:
    """A PDF or a single page image, rendered at a fixed resolution."""

    path: Path
    label: str

    @property
    def is_pdf(self) -> bool:
        return self.path.suffix.lower() == ".pdf"

    def page_count(self) -> int:
        if not self.is_pdf:
            return 1
        doc = pdfium.PdfDocument(str(self.path))
        try:
            return len(doc)
        finally:
            doc.close()

    def render(self, index: int, dpi: float) -> np.ndarray:
        """Grayscale render of one page (0-based), honouring /Rotate."""
        if not self.is_pdf:
            image = Image.open(self.path)
            src_dpi = image.info.get("dpi", (dpi, dpi))[0] or dpi
            gray = np.asarray(image.convert("L"))
            if abs(src_dpi - dpi) > 0.5:
                scale = dpi / src_dpi
                gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            return gray
        doc = pdfium.PdfDocument(str(self.path))
        try:
            # pdfium rounds the bitmap size up, so 432 pt at 300 dpi (1800.0000001 px) would
            # become 1801 px and resample the page; a hair under the scale keeps it exact.
            bitmap = doc[index].render(scale=dpi / 72 * (1 - 1e-9), grayscale=True)
            return np.asarray(bitmap.to_pil().convert("L")).copy()
        finally:
            doc.close()

    def texts(self) -> list[str]:
        if not self.is_pdf:
            return [""]
        doc = pdfium.PdfDocument(str(self.path))
        try:
            return [doc[i].get_textpage().get_text_bounded() for i in range(len(doc))]
        finally:
            doc.close()


def normalize_text(text: str) -> str:
    """Collapse whitespace only. No Unicode normalization (it breaks some Hebrew mark orders)."""
    return re.sub(r"\s+", " ", text.replace("\r", "")).strip()


def cer(hypothesis: str, reference: str) -> float | None:
    """Character error rate of hypothesis against reference, after whitespace normalization."""
    ref, hyp = normalize_text(reference), normalize_text(hypothesis)
    if not ref:
        return None
    return Levenshtein.distance(hyp, ref) / len(ref)


def load_truth(path: Path) -> list[str]:
    """Ground-truth text per page: a synth truth.json, or a .txt with pages split by form feeds."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "text" in data:
            return [data["text"]]
        if isinstance(data, list):
            return [p["text"] if isinstance(p, dict) else str(p) for p in data]
        raise ValueError(f"{path}: expected a truth.json with a 'text' field or a list of pages")
    return path.read_text(encoding="utf-8").split("\f")


def ink_mask(gray: np.ndarray) -> np.ndarray:
    """Ink pixels by Otsu threshold; blank pages give an empty mask."""
    if gray.max() - gray.min() < 32:
        return np.zeros(gray.shape, bool)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary == 0


def overlay(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """RGB difference overlay: dark where both have ink, red for A only, blue for B only."""
    ink_a, ink_b = ink_mask(a), ink_mask(b)
    out = np.full(a.shape + (3,), 255, np.uint8)
    out[ink_a & ink_b] = (40, 40, 40)
    out[ink_a & ~ink_b] = (220, 40, 40)
    out[ink_b & ~ink_a] = (30, 100, 230)
    return out


def match_size(img: np.ndarray, shape: tuple[int, int]) -> tuple[np.ndarray, bool]:
    """Fit img to shape. Off-by-a-pixel renders are cropped or padded; anything else is resampled."""
    if img.shape == shape:
        return img, False
    dh, dw = shape[0] - img.shape[0], shape[1] - img.shape[1]
    if abs(dh) <= 2 and abs(dw) <= 2:
        out = np.full(shape, 255, np.uint8)
        h, w = min(shape[0], img.shape[0]), min(shape[1], img.shape[1])
        out[:h, :w] = img[:h, :w]
        return out, False
    return cv2.resize(img, (shape[1], shape[0]), interpolation=cv2.INTER_AREA), True


def page_metrics(a: np.ndarray, b: np.ndarray) -> dict:
    ink_a, ink_b = ink_mask(a), ink_mask(b)
    union = ink_a | ink_b
    return {
        "ssim": round(float(structural_similarity(a, b, data_range=255)), 4),
        "mean_abs_diff": round(float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()), 3),
        # Share of inked pixels (in either) that disagree: 0 = identical ink, 1 = no overlap.
        "ink_disagreement": round(float((ink_a ^ ink_b).sum() / union.sum()), 4) if union.any() else 0.0,
    }


def parse_pages(spec: str | None, count: int) -> list[int]:
    """'1-3,5' -> [0, 1, 2, 4] (1-based in, 0-based out), clipped to count."""
    if not spec:
        return list(range(count))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            pages.extend(range(int(lo), int(hi) + 1))
        elif part:
            pages.append(int(part))
    return [p - 1 for p in pages if 1 <= p <= count]


def compare(
    a_path: Path,
    b_path: Path,
    out_dir: Path,
    dpi: float = 150,
    pages: str | None = None,
    truth: Path | None = None,
    labels: tuple[str, str] = ("A", "B"),
) -> dict:
    a, b = Source(Path(a_path), labels[0]), Source(Path(b_path), labels[1])
    out_dir = Path(out_dir)
    img_dir = out_dir / "pages"
    img_dir.mkdir(parents=True, exist_ok=True)

    count_a, count_b = a.page_count(), b.page_count()
    indices = parse_pages(pages, min(count_a, count_b))
    truth_pages = load_truth(truth) if truth else None
    texts_a, texts_b = a.texts(), b.texts()

    rows = []
    for i in indices:
        t0 = time.perf_counter()
        ra, rb = a.render(i, dpi), b.render(i, dpi)
        rb, resized = match_size(rb, ra.shape)
        metrics = page_metrics(ra, rb)
        stem = f"p{i + 1:03d}"
        Image.fromarray(ra).save(img_dir / f"{stem}-a.png")
        Image.fromarray(rb).save(img_dir / f"{stem}-b.png")
        Image.fromarray(overlay(ra, rb)).save(img_dir / f"{stem}-overlay.png")
        row = {
            "page": i + 1,
            "size_px": [ra.shape[1], ra.shape[0]],
            "b_resized": resized,
            **metrics,
            "chars_a": len(normalize_text(texts_a[i])) if i < len(texts_a) else 0,
            "chars_b": len(normalize_text(texts_b[i])) if i < len(texts_b) else 0,
            "seconds": round(time.perf_counter() - t0, 2),
        }
        if truth_pages is not None and i < len(truth_pages):
            row["cer_a"] = cer(texts_a[i] if i < len(texts_a) else "", truth_pages[i])
            row["cer_b"] = cer(texts_b[i] if i < len(texts_b) else "", truth_pages[i])
        rows.append(row)

    def doc_info(src: Source, count: int) -> dict:
        size = src.path.stat().st_size
        return {"file": str(src.path), "label": src.label, "pages": count, "bytes": size,
                "bytes_per_page": round(size / max(count, 1))}

    def mean(key: str) -> float | None:
        values = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(values) / len(values), 4) if values else None

    summary = {
        "dpi": dpi,
        "a": doc_info(a, count_a),
        "b": doc_info(b, count_b),
        "truth": str(truth) if truth else None,
        "pages_compared": len(rows),
        "page_count_mismatch": count_a != count_b,
        "mean": {k: mean(k) for k in ("ssim", "ink_disagreement", "mean_abs_diff", "cer_a", "cer_b")},
        "pages": rows,
    }
    report.write_json(out_dir / "compare.json", summary)
    _write_html(summary, out_dir / "index.html")
    return summary


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _write_html(s: dict, path: Path) -> None:
    hb = report.human_bytes
    la, lb = s["a"]["label"], s["b"]["label"]
    body = [
        f"<p class=\"muted\">Rendered at {s['dpi']:g} dpi. Overlay: <b style=\"color:#dc2828\">red</b> = ink only in {report.esc(la)}, "
        f"<b style=\"color:#1e64e6\">blue</b> = ink only in {report.esc(lb)}, dark = both.</p>",
        "<h2>Documents</h2>",
        report.table(
            ["", "File", "Pages", "Size", "Per page", "Mean CER vs truth"],
            [
                [la, Path(s["a"]["file"]).name, s["a"]["pages"], hb(s["a"]["bytes"]), hb(s["a"]["bytes_per_page"]), _fmt(s["mean"]["cer_a"])],
                [lb, Path(s["b"]["file"]).name, s["b"]["pages"], hb(s["b"]["bytes"]), hb(s["b"]["bytes_per_page"]), _fmt(s["mean"]["cer_b"])],
            ],
            numeric={2, 3, 4, 5},
        ),
    ]
    if s["page_count_mismatch"]:
        body.append("<p><b>Page counts differ;</b> only the pages both documents have are compared.</p>")
    if s["truth"] is None:
        body.append("<p class=\"muted\">No ground truth given, so text accuracy is not measured (pass --truth).</p>")
    body.append(
        f"<p>Mean SSIM {_fmt(s['mean']['ssim'])} · mean ink disagreement {_fmt(s['mean']['ink_disagreement'])}. "
        "Position accuracy per word needs OCR word boxes and arrives with Phase 1.</p>"
    )
    body.append("<h2>Pages</h2>")
    body.append(report.table(
        ["Page", "SSIM", "Ink disagreement", "Mean |diff|", f"Chars {la}", f"Chars {lb}", f"CER {la}", f"CER {lb}", "Note"],
        [[r["page"], _fmt(r["ssim"]), _fmt(r["ink_disagreement"]), _fmt(r["mean_abs_diff"]), r["chars_a"], r["chars_b"],
          _fmt(r.get("cer_a")), _fmt(r.get("cer_b")), "B resized to A" if r["b_resized"] else ""] for r in s["pages"]],
        numeric={0, 1, 2, 3, 4, 5, 6, 7},
    ))
    for r in s["pages"]:
        stem = f"pages/p{r['page']:03d}"
        body.append(f"<h3>Page {r['page']}</h3><div class=\"row\">")
        for suffix, caption in (("a", la), ("b", lb), ("overlay", "Overlay")):
            body.append(
                f"<figure><a href=\"{stem}-{suffix}.png\"><img loading=\"lazy\" src=\"{stem}-{suffix}.png\" alt=\"\"></a>"
                f"<figcaption>{report.esc(caption)}</figcaption></figure>"
            )
        body.append("</div>")
    report.write_page(path, f"Compare: {la} vs {lb}", "\n".join(body))
