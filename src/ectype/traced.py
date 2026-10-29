"""Phase 5: traced mode, the ClearScan equivalent.

Every glyph unit is drawn with the glyph synthesized from its own cluster, in its
style's font, over the scan with that text erased. Glyphs are placed one by one:
each word starts at its first unit's ink, and a TJ adjustment before every later
glyph puts it exactly where that instance was printed, so kerning and any
irregular spacing in the original survive. Words the OCR is unsure of, or whose
units have no glyph, fall back to the scan image: their ink is left in the
background and their text is laid over it invisibly, so it can still be searched.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from . import report
from .background import erase_text, smooth_paper
from .fontbuild import UNITS_PER_EM, XH_UNITS, build_workspace_fonts
from .ocr import OcrPage
from .pdfwrite import GlyphRun, PageSpec, load_font, write_pdf
from .preprocess import binarize
from .scripts import is_rtl
from .segment import Segmentation, fit_baselines, line_x_heights
from .substitute import PlacedWord, _render, measure_overlay


def _load_fonts(workspace: Path):
    fonts_dir = workspace / "fonts"
    if not (fonts_dir / "fonts.json").is_file():
        build_workspace_fonts(workspace)
    summary = json.loads((fonts_dir / "fonts.json").read_text(encoding="utf-8"))
    fonts, glyph_of = {}, {}
    for st in summary["styles"]:
        style = st["style"]
        font = load_font(fonts_dir / f"style-{style}")
        fonts[font.key] = font
        info = json.loads((fonts_dir / f"style-{style}.json").read_text(encoding="utf-8"))
        table = {g["cid"]: g for g in info["glyphs"]}
        for cluster, cid in info["cid_of_cluster"].items():
            g = table[cid]
            glyph_of[int(cluster)] = (font.key, cid, g["ink"][0] if g["ink"] else 0.0, g["advance"])
    return fonts, glyph_of


def place_page(seg: Segmentation, ocr: OcrPage, cluster_of: dict[int, int], glyph_of: dict, min_word_conf: float):
    """Glyph runs for one page, the units drawn as text, and the words left to the image."""
    base = fit_baselines(seg, ocr)
    xhs, page_xh = line_x_heights(seg)
    units_by_word: dict[tuple[int, int], list] = defaultdict(list)
    for u in seg.units:
        units_by_word[(u.line, u.word)].append(u)
    runs, drawn, placed, fallback = [], [], [], []
    for li, line in enumerate(ocr.lines):
        for wi, word in enumerate(line.words):
            rtl = is_rtl(word.text)
            # Glyphs are set left to right; a right-to-left word's, in visual order, with its
            # text in reading order attached (ActualText), so it copies and searches right.
            units = sorted(units_by_word.get((li, wi), []),
                           key=lambda u: (u.box[0] if u.box else 0) if rtl else u.chars[0])
            glyphs = [glyph_of.get(cluster_of.get(u.id)) if u.box else None for u in units]
            if not units:
                continue
            ok = all(glyphs) and word.conf >= min_word_conf
            size = xhs.get(li, page_xh) * UNITS_PER_EM / XH_UNITS
            s = size / UNITS_PER_EM
            a, b = base.get(li, (units[0].box[3] if units[0].box else word.box[3], 0.0))
            ink = [min(u.box[0] for u in units if u.box), min(u.box[1] for u in units if u.box),
                   max(u.box[2] for u in units if u.box), max(u.box[3] for u in units if u.box)] if any(u.box for u in units) else list(word.box)
            y = a + b * (ink[0] + ink[2]) / 2
            if not all(glyphs):
                fallback.append({"text": word.text, "conf": word.conf, "reason": "unit without a glyph"})
                continue
            # One run per stretch of glyphs in the same font (punctuation can sit in another style).
            current = None
            word_runs = len(runs)
            for u, (key, cid, ink_left, advance) in zip(units, glyphs):
                origin = u.box[0] - ink_left * s
                if current is None or current.font != key:
                    current = GlyphRun(key, size, origin, y, [cid], adjust=[], invisible=not ok)
                    current._next = origin + advance * s  # type: ignore[attr-defined]
                    runs.append(current)
                else:
                    current.adjust.append(-(origin - current._next) / s)  # type: ignore[attr-defined]
                    current.cids.append(cid)
                    current._next = origin + advance * s  # type: ignore[attr-defined]
            if wi < len(line.words) - 1 and current is not None:
                current.cids.append(1)  # the space glyph, so copied text has word breaks
                current.adjust.append(0.0)
            if rtl and len(runs) > word_runs:
                runs[word_runs].actual_text = word.text + (" " if wi < len(line.words) - 1 else "")
                for r in runs[word_runs + 1:]:
                    r.actual_text = ""
            if ok:
                drawn += units
                placed.append(PlacedWord(li, wi, word.text, ink, current, ""))
            else:
                fallback.append({"text": word.text, "conf": word.conf, "reason": "low OCR confidence"})
    for r in runs:
        r.__dict__.pop("_next", None)
    return runs, drawn, placed, fallback


def traced_workspace(workspace: Path, out_name: str | None = None, background_dpi: float = 150,
                     min_word_conf: float = 60.0, on_page=None) -> dict:
    workspace = Path(workspace)
    out = workspace / "traced"
    out.mkdir(parents=True, exist_ok=True)
    cl = json.loads((workspace / "cluster" / "clusters.json").read_text(encoding="utf-8"))
    cluster_by_page: dict[str, dict[int, int]] = defaultdict(dict)
    for g in cl["glyphs"]:
        for page, unit in g["instances"]:
            cluster_by_page[page][unit] = g["id"]
    fonts, glyph_of = _load_fonts(workspace)

    pages_out, text_only, rows, placed_all, loaded = [], [], [], [], []
    pngs = sorted((workspace / "pages").glob("*.png"))
    for n, png in enumerate(pngs, 1):
        if on_page:
            on_page(n, len(pngs))
        name = png.stem
        image = Image.open(png)
        dpi = report.image_dpi(image)
        gray = np.asarray(image.convert("L"))
        h, w = gray.shape
        seg_path, ocr_path = workspace / "segment" / f"{name}.seg.json", workspace / "ocr" / f"{name}.ocr.json"
        if not seg_path.is_file() or not ocr_path.is_file():
            pages_out.append(PageSpec(w, h, dpi, background=gray, background_dpi=background_dpi))
            text_only.append(PageSpec(w, h, dpi))
            rows.append({"page": name, "words": 0})
            placed_all.append([])
            loaded.append((name, gray, dpi, None))
            continue
        seg = Segmentation.from_json(json.loads(seg_path.read_text(encoding="utf-8")))
        ocr = OcrPage.from_json(json.loads(ocr_path.read_text(encoding="utf-8")))
        labels = cv2.imread(str(workspace / "segment" / f"{name}.labels.png"), cv2.IMREAD_UNCHANGED).astype(np.int32)
        runs, drawn, placed, fallback = place_page(seg, ocr, cluster_by_page.get(name, {}), glyph_of, min_word_conf)
        drawn_ids = {u.id for u in drawn}
        drawn_seg = Segmentation(seg.size_px, seg.components, [u for u in seg.units if u.id in drawn_ids], seg.unassigned)
        sizes = [line.x_size for line in ocr.lines if line.x_size]
        background, mask = erase_text(gray, drawn_seg, labels, float(np.median(sizes)) if sizes else 30.0)
        background = smooth_paper(background)
        ink_gray = float(np.median(gray[mask & (gray < 128)])) / 255 if (mask & (gray < 128)).any() else 0.1
        pages_out.append(PageSpec(w, h, dpi, background=background, background_dpi=background_dpi,
                                  runs=runs, fill_gray=round(ink_gray, 3)))
        text_only.append(PageSpec(w, h, dpi, runs=[r for r in runs if not r.invisible]))
        cv2.imwrite(str(out / f"{name}.background.png"), background)
        placed_all.append(placed)
        loaded.append((name, gray, dpi, ocr))
        rows.append({"page": name, "words": len(placed), "fallback": fallback,
                     "glyphs_drawn": sum(len(r.cids) for r in runs if not r.invisible)})

    pdf_name = (out_name or workspace.name) + ".pdf"
    used = sorted({r.font for p in pages_out for r in p.runs})
    write_pdf(out / pdf_name, pages_out, [fonts[k] for k in used])
    write_pdf(out / "text-only.pdf", text_only, [fonts[k] for k in used])

    from .compare import cer, match_size

    doc = pdfium.PdfDocument(str(out / pdf_name))
    extracted = [doc[i].get_textpage().get_text_bounded() for i in range(len(doc))]
    doc.close()
    for i, (row, (name, gray, dpi, ocr)) in enumerate(zip(rows, loaded)):
        if not placed_all[i]:
            continue
        rendered = match_size(_render(out / "text-only.pdf", i, dpi), gray.shape)[0]
        row["overlay"] = measure_overlay(rendered, placed_all[i])
        row["text_cer_vs_ocr"] = round(cer(extracted[i], ocr.text()), 4)
        view = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        text_ink = binarize(rendered)
        view[text_ink] = (0.4 * view[text_ink] + 0.6 * np.array([220, 40, 40])).astype(np.uint8)
        Image.fromarray(view).save(out / f"{name}.overlay.png")

    measured = [r["overlay"] for r in rows if r.get("overlay") and r["overlay"]["left"]]
    total = (out / pdf_name).stat().st_size
    summary = {
        "pdf": pdf_name,
        "bytes": total,
        "bytes_per_page": round(total / max(1, len(rows))),
        "font_bytes": sum(len(fonts[k].program) for k in used),
        "fonts": len(used),
        "min_word_conf": min_word_conf,
        "fallback_words": sum(len(r.get("fallback", [])) for r in rows),
        "words": sum(r["words"] for r in rows),
        "pages": rows,
        "within_1px": {
            side: round(float(np.average([m[side]["within_1px"] for m in measured], weights=[m["words"] for m in measured])), 4)
            for side in ("left", "right", "top", "bottom")
        } if measured else None,
    }
    report.write_json(out / "traced.json", summary)
    _write_html(summary, out / "index.html")
    return summary


def _write_html(s: dict, path: Path) -> None:
    w1 = s["within_1px"] or {}
    body = [
        f"<p class=\"muted\"><a href=\"{report.esc(s['pdf'])}\">{report.esc(s['pdf'])}</a> · {report.human_bytes(s['bytes'])} "
        f"({report.human_bytes(s['bytes_per_page'])} per page; fonts {report.human_bytes(s['font_bytes'])} in {s['fonts']} fonts). "
        f"{s['words']} words drawn as text; {s['fallback_words']} left to the image with invisible text "
        f"(OCR confidence under {s['min_word_conf']:g}, or a unit without a glyph). Overlays: text layer in red over the scan.</p>",
        f"<p>Words whose rendered ink lands within 1 px of the scan's: left {100 * w1.get('left', 0):.1f}%, right {100 * w1.get('right', 0):.1f}%, "
        f"top {100 * w1.get('top', 0):.1f}%, bottom {100 * w1.get('bottom', 0):.1f}%.</p>" if w1 else "",
    ]
    body.append(report.table(
        ["Page", "Words", "Glyphs", "Left ≤1px", "Right ≤1px", "Top ≤1px", "Bottom ≤1px", "Text CER vs OCR", "Image fallback"],
        [[r["page"], r["words"], r.get("glyphs_drawn", 0)] +
         [f"{100 * r['overlay'][k]['within_1px']:.1f}%" if r.get("overlay") and r["overlay"][k] else "—" for k in ("left", "right", "top", "bottom")] +
         [r.get("text_cer_vs_ocr", "—"), " ".join(f"{x['text']}" for x in r.get("fallback", [])[:12])] for r in s["pages"]],
        numeric={1, 2, 3, 4, 5, 6, 7},
    ))
    body.append("<div class=\"row\">")
    for r in s["pages"]:
        if r.get("overlay"):
            body.append(f"<figure><a href=\"{r['page']}.overlay.png\"><img loading=\"lazy\" src=\"{r['page']}.overlay.png\" alt=\"\" style=\"max-height:520px\"></a>"
                        f"<figcaption>{r['page']}</figcaption></figure>")
    body.append("</div>")
    report.write_page(path, "Traced mode", "\n".join(body))
