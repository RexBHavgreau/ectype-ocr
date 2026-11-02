"""Substitute mode: the scanned words, set in a real typeface at their scanned positions.

Per line, the x-height (from the ink of x-height letters) sets the type size and
a baseline is fitted to the bottoms of letters without descenders. Per word, the
origin puts the first glyph's ink on the word's scanned left ink edge, and a
per-word character spacing (Tc) stretches the glyphs so the last one's ink ends
on the scanned right edge. Text goes over the background with the text erased.

`measure_overlay` renders the text layer alone and checks, word by word, how far
its ink lands from the scan's: the Phase 2 target is about 1 px at word ends.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from . import report
from .background import erase_text, smooth_paper
from .ocr import OcrPage
from .pdfwrite import FontMetrics, GlyphRun, PageSpec, subset_font, write_pdf
from .preprocess import binarize
from .scripts import is_rtl
from .segment import X_LETTERS, Segmentation, fit_baselines


@dataclass
class PlacedWord:
    line: int
    word: int
    text: str
    ink: list[int]  # scanned ink box x0 y0 x1 y1
    run: GlyphRun
    missing: str  # characters the font lacks


TC_MAX, TC_MIN = 0.06, -0.04  # spacing between a word's letters, in ems, at most


def _trimmed_mean(values: list[float]) -> float:
    """Mean of the middle half: heights are whole pixels, so a median snaps to a few values."""
    v = np.sort(np.asarray(values, float))
    k = len(v) // 4
    return float(v[k:len(v) - k].mean()) if len(v) > 3 else float(v.mean())


def _line_fits(seg: Segmentation, ocr: OcrPage, metrics: FontMetrics, char_cid: dict[str, int],
               growth: float) -> tuple[dict[int, float], dict[int, tuple[float, float]], float]:
    """Per line: the em size in px that makes the font's letters as tall as the scanned
    ones (each scanned x-height letter against the same letter in the font, so round
    letters' overshoot cancels), and the baseline as (a, b) for y = a + b * x."""
    by_line: dict[int, list] = {}
    for u in seg.units:
        if u.box:
            by_line.setdefault(u.line, []).append(u)
    font_h = {}
    for ch in X_LETTERS:
        g = char_cid.get(ch)
        if g is not None and metrics.ink.get(g):
            font_h[ch] = metrics.ink[g][3] - metrics.ink[g][1]
    em: dict[int, float] = {}
    counts: dict[int, int] = {}
    all_em = []
    for li, units in by_line.items():
        ratios = [(u.box[3] - u.box[1] - 2 * growth) * metrics.units_per_em / font_h[u.text]
                  for u in units if u.kind == "single" and u.text in font_h]
        counts[li] = len(ratios)
        if len(ratios) >= 3:
            em[li] = _trimmed_mean(ratios)
            all_em += ratios
    base = fit_baselines(seg, ocr)
    # The page's text size from its lines' sizes, the larger ones: averaged with the notes'
    # it fell between the two, and lines of the text were set in two sizes (the Treatise p. 14).
    line_ems = [em[li] for li in em if counts[li] >= 5]
    page_em = (float(np.percentile(line_ems, 70)) if len(line_ems) >= 3
               else _trimmed_mean(all_em) if all_em else 40.0)
    for li in by_line:
        # Lines take the page's size unless clearly set in another (footnotes, headings):
        # a per-line estimate carries a few percent of noise, which Tc would turn into
        # visible letterspacing.
        if li not in em or counts[li] < 5 or abs(em[li] / page_em - 1) < 0.12:
            em[li] = page_em
    # A full line alone in another size between two lines of one size takes theirs: its
    # measurement was off (the Treatise: one text line, or a footnote's last line, set
    # larger than the lines around it). A heading is short and keeps its size.
    order = sorted(em)
    for k in range(1, len(order) - 1):
        before, li, after = em[order[k - 1]], em[order[k]], em[order[k + 1]]
        chars = sum(len(w.text) for w in ocr.lines[order[k]].words) if order[k] < len(ocr.lines) else 0
        if abs(before / after - 1) < 0.05 and abs(li / before - 1) >= 0.05 and chars >= 25:
            em[order[k]] = before
    return em, base, page_em


# Fonts for the scripts the chosen font may lack, best first (any installed font with the
# letters is taken after these). The chosen font itself is used when it has them.
SCRIPT_FONTS = {
    "Hebrew": ["SBL Hebrew", "Ezra SIL", "Taamey Frank CLM", "Frank Ruehl CLM", "Times New Roman", "David",
               "Noto Serif Hebrew", "FreeSerif", "Liberation Serif"],
    "Syriac": ["Estrangelo Edessa", "Serto Jerusalem", "East Syriac Adiabene", "Noto Sans Syriac",
               "Noto Sans Syriac Estrangela", "FreeSans"],
    "Greek": ["Gentium Plus", "GFS Didot", "SBL Greek", "Times New Roman", "Noto Serif", "FreeSerif",
              "Liberation Serif"],
    "Arabic": ["Scheherazade New", "Amiri", "Noto Naskh Arabic", "Times New Roman", "FreeSerif"],
}
# Letters standing on the line without ascender or descender, to size a script's font by the
# scan (as x-height letters size the Latin).
FLAT = {"Hebrew": set("בגדהחטכמסעפצרשת"), "Syriac": set("ܒܓܕܗܘܙܚܛܝܟܡܢܣܥܦܨܩܪܫܬ"),
        "Arabic": set("بتثسشصضطظفقكم")}


@dataclass
class Face:
    """A font of the set, subset and ready: its resource key, metrics and character map."""
    key: str
    path: Path
    embedded: object
    metrics: FontMetrics
    cid: dict[str, int]


def _script(text: str) -> str:
    """The script of most of a word's letters ("Latin" for none)."""
    import unicodedata

    counts: dict[str, int] = {}
    for c in text:
        if c.isalpha():
            name = unicodedata.name(c, "")
            script = next((s for s in ("HEBREW", "SYRIAC", "GREEK", "ARABIC") if name.startswith(s)), "LATIN")
            counts[script] = counts.get(script, 0) + 1
    return max(counts, key=counts.get).title() if counts else "Latin"


def _visual(text: str) -> str:
    """A right-to-left word's characters in the order they stand on the page, left to right:
    its letters reversed, each with its points and accents after it (a font's marks are drawn
    over the letter just set)."""
    import unicodedata

    clusters: list[str] = []
    for c in text:
        if clusters and unicodedata.combining(c):
            clusters[-1] += c
        else:
            clusters.append(c)
    return "".join(reversed(clusters))


def choose_faces(font: Path, texts: dict[str, str], pool=None) -> dict[str, Path]:
    """The fonts a substitute PDF needs: "roman" (the chosen font), its "italic", "bold" and
    "bold italic" faces when installed, and for each script the roman lacks (texts: script ->
    its words' text), an installed font with its letters: SCRIPT_FONTS first, else the one
    with the most of them."""
    from .synthtrain import FontInfo, covers, fonts_in, system_font_folders

    pool = pool if pool is not None else fonts_in(Path(font).parent, *system_font_folders())
    pool = [f for f in pool if Path(f.path).suffix.lower() != ".ttc"]  # subset_font takes single fonts
    roman = next((f for f in pool if Path(f.path).resolve() == Path(font).resolve()), None) or FontInfo(Path(font), Path(font).stem)
    out = {"roman": Path(font)}
    style = (roman.style or "").lower()
    weight = "bold" if "bold" in style else ""
    for key, want in (("italic", {"italic", "oblique", f"{weight} italic".strip()}),
                      ("bold", {"bold"}), ("bold italic", {"bold italic", "bold oblique"})):
        if key == "italic" and weight:
            want = {"bold italic", "bold oblique"}
        face = next((f for f in pool if roman.family and f.family.lower() == roman.family.lower()
                     and f.style.lower() in want), None)
        if face is not None:
            out[key] = Path(face.path)
    import unicodedata

    for script, text in texts.items():
        letters = {c for c in text if (c.isalpha() and _script(c) == script) or unicodedata.combining(c)}
        if not letters or covers(roman, letters) >= letters:
            continue
        def score(f):
            return len(covers(f, letters))
        listed = [f for name in SCRIPT_FONTS.get(script, []) for f in pool
                  if f.family.lower() == name.lower() and f.style.lower() in ("regular", "book", "roman", "")]
        best = next((f for f in listed if score(f) >= 0.95 * len(letters)), None)
        if best is None:
            ranked = sorted(pool, key=score, reverse=True)
            best = ranked[0] if ranked and score(ranked[0]) >= 0.8 * len(letters) else None
        if best is not None:
            out[script] = Path(best.path)
    return out


def place_words(seg: Segmentation, ocr: OcrPage, metrics: FontMetrics, char_cid: dict[str, int],
                font_key: str = "F1", space: bool = True, growth: float = 0.0,
                faces: dict[str, Face] | None = None, choose=None) -> list[PlacedWord]:
    """Place each OCR word over its scanned ink.

    `growth` shrinks scanned ink boxes by that many px per edge. On synthetic pages
    Otsu edges sit on the glyph outline (growth 0); very blurred scans binarize about
    half a pixel fat, which makes the type a few percent large. Estimating growth per
    page from letter heights proved unstable, so it stays a knob.
    """
    em, base, page_em = _line_fits(seg, ocr, metrics, char_cid, growth)
    faces = faces or {"roman": Face(font_key, Path(), None, metrics, char_cid)}
    units_by_word: dict[tuple[int, int], list] = {}
    for u in seg.units:
        units_by_word.setdefault((u.line, u.word), []).append(u)
    scale = _script_scales(seg, ocr, faces, page_em, metrics)
    measured = []
    for li, line in enumerate(ocr.lines):
        for wi, word in enumerate(line.words):
            units = [u for u in units_by_word.get((li, wi), []) if u.box]
            ink = ([min(u.box[0] for u in units), min(u.box[1] for u in units),
                    max(u.box[2] for u in units), max(u.box[3] for u in units)] if units else list(word.box))
            chars = [c.text for c in word.chars] or list(word.text)
            text = "".join(chars)
            which = choose(li, wi, text) if choose else "roman"
            face = faces.get(which) or faces["roman"]
            fm, cid = face.metrics, face.cid
            visual = _visual(text) if is_rtl(text) else text
            gids = [cid[c] for c in visual if c in cid]
            missing = "".join(c for c in text if c not in cid)
            if not gids:
                continue
            size = em.get(li, page_em) * scale.get(which, 1.0)
            s = size / fm.units_per_em
            first = fm.ink[gids[0]]
            last = fm.ink[gids[-1]]
            first_left = (first[0] if first else 0.0) * s
            natural_right = sum(fm.advance[g] for g in gids[:-1]) * s + (last[2] if last else fm.advance[gids[-1]]) * s
            span = natural_right - first_left
            left, right = ink[0] + growth, ink[2] - growth
            measured.append((li, wi, line, text, ink, gids, missing, size, first_left, span, left, right, face))

    # One horizontal scale for the page brings the font's letter widths to the scan's,
    # so the per-word spacing (Tc) only absorbs what's left instead of letterspacing.
    ratios = [(m[11] - m[10]) / m[9] for m in measured if len(m[5]) >= 4 and m[9] > 0 and m[12].key == font_key]
    scale_x = float(np.clip(np.median(ratios), 0.8, 1.25)) if len(ratios) >= 10 else 1.0

    placed = []
    for li, wi, line, text, ink, gids, missing, size, first_left, span, left, right, face in measured:
        n = len(gids)
        # A word may widen or narrow its letters a little (±7% of the page scale) before
        # spacing them: substitute letters differ in width letter by letter, and slightly
        # wider letters read better than visible letterspacing.
        own = (right - left) / span if span > 0 and n >= 2 else scale_x
        word_x = float(np.clip(own, scale_x * 0.93, scale_x * 1.07))
        tc = ((right - left) / word_x - span) / (n - 1) if n > 1 else 0.0
        # Letters spread or squeezed only a little ("a s regards", "i t", the title's "ON"
        # set over itself, where the word's box was wider or narrower than the word): past
        # that, the word is set at the most spacing allowed, centred in its box.
        limit = (TC_MAX * size if tc > 0 else TC_MIN * size)
        if n > 1 and abs(tc) > abs(limit):
            tc = limit
            width = (span + (n - 1) * tc) * word_x
            left = left + ((right - left) - width) / 2
        x = left - first_left * word_x
        a, b = base.get(li, (ink[3], 0.0))
        y = a + b * (ink[0] + ink[2]) / 2 - growth
        last = wi == len(line.words) - 1
        # A right-to-left word's glyphs are in visual order (reversed when measured); its
        # text goes with them in reading order.
        actual = (text + ("" if last else " ")) if is_rtl(text) else None
        space_cid = face.cid.get(" ")
        cids = gids + ([space_cid] if space and space_cid is not None and not last else [])
        placed.append(PlacedWord(li, wi, text, ink, GlyphRun(face.key, size, x, y, cids, tc, word_x, actual_text=actual),
                                 missing))
    return placed


def _script_scales(seg: Segmentation, ocr: OcrPage, faces: dict[str, Face], page_em: float,
                   roman: FontMetrics) -> dict[str, float]:
    """For each script's font, how much larger or smaller than the line's size to set it so
    its letters stand as tall as the scan's: the scanned height of flat letters (no ascender
    or descender) against the same letters in the font, the lower readings (points below a
    letter make its ink taller). 1 without enough letters."""
    out = {}
    for script, letters in FLAT.items():
        face = faces.get(script)
        if face is None:
            continue
        ratios = []
        for u in seg.units:
            if not u.box or not u.text or u.text[0] not in letters or u.text[0] not in face.cid:
                continue
            ink = face.metrics.ink.get(face.cid[u.text[0]])
            if ink and ink[3] > ink[1]:
                ratios.append((u.box[3] - u.box[1]) * face.metrics.units_per_em / (ink[3] - ink[1]))
        if len(ratios) >= 3:
            out[script] = float(np.percentile(ratios, 30)) / page_em
    return out


def _hidden_runs(ocr: OcrPage, words: set[tuple[int, int]], cid: dict[str, int], key: str) -> list[GlyphRun]:
    """Invisible text for the words left as the scan, in the glyphless font (any script)."""
    from .textlayer import place_words as hidden_words

    runs = []
    for li, line in enumerate(ocr.lines):
        keep = [wi for wi in range(len(line.words)) if (li, wi) in words]
        if keep:
            sub = OcrPage(ocr.engine, ocr.lang, ocr.size_px, ocr.dpi,
                          [type(line)(line.box, line.baseline, line.x_size, line.ascenders, line.descenders,
                                      [line.words[wi] for wi in keep])])
            runs += hidden_words(sub, cid, key)
    return runs


def measure_overlay(rendered: np.ndarray, placed: list[PlacedWord], pad: int = 3) -> dict:
    """Word-end offsets between the rendered text layer's ink and the scanned ink, in px.

    Each word is measured in a window bounded by the midpoints of the gaps to its
    neighbours on the line, so a neighbour's ink never counts as this word's.
    """
    ink = binarize(rendered) if rendered.min() < 128 else np.zeros(rendered.shape, bool)
    by_line: dict[int, list[PlacedWord]] = {}
    for p in placed:
        by_line.setdefault(p.line, []).append(p)
    lefts, rights, tops, bottoms = [], [], [], []
    for words in by_line.values():
        words = sorted(words, key=lambda p: p.ink[0])
        for k, p in enumerate(words):
            x0, y0, x1, y1 = p.ink
            h = y1 - y0
            xa = int((words[k - 1].ink[2] + x0) // 2) if k else int(x0 - h)
            xb = int((x1 + words[k + 1].ink[0]) // 2) if k + 1 < len(words) else int(x1 + h)
            ya, yb = max(0, int(y0 - pad - h // 3)), min(ink.shape[0], int(y1 + pad + h // 3))
            xa, xb = max(0, xa), min(ink.shape[1], xb)
            region = ink[ya:yb, xa:xb]
            cols = np.nonzero(region.any(axis=0))[0]
            rows = np.nonzero(region.any(axis=1))[0]
            if len(cols) == 0:
                continue
            lefts.append(xa + cols[0] - x0)
            rights.append(xa + cols[-1] + 1 - x1)
            tops.append(ya + rows[0] - y0)
            bottoms.append(ya + rows[-1] + 1 - y1)

    def stats(v):
        a = np.abs(np.array(v, float))
        return {"median": round(float(np.median(a)), 2), "p95": round(float(np.percentile(a, 95)), 2),
                "within_1px": round(float((a <= 1).mean()), 4)} if len(a) else None

    return {"words": len(lefts), "left": stats(lefts), "right": stats(rights), "top": stats(tops), "bottom": stats(bottoms)}


def _render(path: Path, index: int, dpi: float) -> np.ndarray:
    doc = pdfium.PdfDocument(str(path))
    try:
        bitmap = doc[index].render(scale=dpi / 72 * (1 - 1e-6), grayscale=True)
        return np.asarray(bitmap.to_pil().convert("L")).copy()
    finally:
        doc.close()


def substitute_workspace(workspace: Path, font: Path, out_name: str | None = None,
                         background_dpi: float = 150, pages: list[str] | None = None, on_page=None) -> dict:
    """Build <workspace>/substitute/<name>.pdf from pages, OCR and segmentation."""
    workspace = Path(workspace)
    out = workspace / "substitute"
    out.mkdir(parents=True, exist_ok=True)
    names = [p.stem for p in sorted((workspace / "pages").glob("*.png")) if not pages or p.stem in pages]

    loaded = []
    text = ""
    for n, name in enumerate(names, 1):
        if on_page:
            on_page(n, len(names))
        gray = np.asarray(Image.open(workspace / "pages" / f"{name}.png").convert("L"))
        dpi = report.image_dpi(Image.open(workspace / "pages" / f"{name}.png"))
        ocr_path = workspace / "ocr" / f"{name}.ocr.json"
        seg_path = workspace / "segment" / f"{name}.seg.json"
        if ocr_path.is_file() and seg_path.is_file():
            ocr = OcrPage.from_json(json.loads(ocr_path.read_text(encoding="utf-8")))
            seg = Segmentation.from_json(json.loads(seg_path.read_text(encoding="utf-8")))
            labels = cv2.imread(str(workspace / "segment" / f"{name}.labels.png"), cv2.IMREAD_UNCHANGED).astype(np.int32)
            text += ocr.text()
        else:
            ocr = seg = labels = None
        loaded.append((name, gray, dpi, ocr, seg, labels))

    # Which words are italic (their slant, as Traced mode measures it, over the book; or as set
    # in Review, with bold), and the fonts: the chosen font's own faces, and fonts for the
    # scripts it lacks (Hebrew in EB Garamond).
    from .cluster import italic_limit, word_italics, word_slants
    from .review import word_styles

    slants = {name: word_slants(seg, labels) for name, gray, dpi, ocr, seg, labels in loaded if seg is not None}
    high = italic_limit([a for sl in slants.values() for a in sl.values()])
    styles: dict[str, dict] = {}
    review: dict[str, set] = {}
    for name, gray, dpi, ocr, seg, labels in loaded:
        if seg is None:
            continue
        found = {k: {"italic": v} for k, v in word_italics(None, None, high=high, slants=slants[name]).items()}
        mine = word_styles(workspace, name)
        for k, st in mine.items():
            found.setdefault(k, {}).update(st)
        styles[name] = found
        review[name] = set(mine)
    by_script: dict[str, str] = {}
    styled: dict[str, str] = {"italic": "", "bold": "", "bold italic": ""}
    for name, gray, dpi, ocr, seg, labels in loaded:
        if ocr is None:
            continue
        for li, line in enumerate(ocr.lines):
            for wi, w in enumerate(line.words):
                script = _script(w.text)
                if script != "Latin":
                    by_script[script] = by_script.get(script, "") + w.text + " "
                st = styles.get(name, {}).get((li, wi), {})
                kind = " ".join(k for k in ("bold", "italic") if st.get(k)) if st.get("bold") or st.get("italic") else ""
                if kind:
                    styled[kind] += w.text + " "
    paths = choose_faces(Path(font), by_script)
    embedded, char_cid, metrics = subset_font(Path(font), text + " ")
    faces = {"roman": Face("F1", Path(font), embedded, metrics, char_cid)}
    for n, (which, path) in enumerate((k, v) for k, v in paths.items() if k != "roman"):
        wanted = styled.get(which) or by_script.get(which) or ""
        if wanted.strip():
            emb, cid, met = subset_font(path, wanted + " .,;:()[]-'’0123456789", key=f"F{n + 3}")
            faces[which] = Face(f"F{n + 3}", path, emb, met, cid)
    from .textlayer import glyphless_font

    hidden, hidden_cid = glyphless_font(sorted(set(text) | {" "}), key="F2")

    def chooser(name: str):
        def choose(li: int, wi: int, word: str) -> str:
            has = (lambda which: which in faces and all(c in faces[which].cid for c in word))
            st = styles.get(name, {}).get((li, wi), {})
            kind = " ".join(k for k in ("bold", "italic") if st.get(k))
            if has("roman"):
                # Italic as measured only for the Latin alphabet: Greek letters lean by their
                # shape (the Treatise's upright γέγονα measured italic); set in Review, always.
                set_in_review = (li, wi) in review.get(name, set())
                if kind and _script(word) != "Latin" and not set_in_review:
                    kind = ""
                return kind if kind and has(kind) else "roman"
            script = _script(word)
            return script if has(script) else "roman"
        return choose
    pages_out, text_only, rows, all_placed = [], [], [], []
    for name, gray, dpi, ocr, seg, labels in loaded:
        h, w = gray.shape
        if ocr is None or not ocr.lines:
            pages_out.append(PageSpec(w, h, dpi, background=gray, background_dpi=background_dpi))
            text_only.append(PageSpec(w, h, dpi))
            rows.append({"page": name, "words": 0})
            all_placed.append([])
            continue
        sizes = [line.x_size for line in ocr.lines if line.x_size]
        x_size = float(np.median(sizes)) if sizes else 30.0
        # A word with a character the font lacks (Hebrew in EB Garamond) stays as the scan,
        # with its text laid invisibly over it: only the words set in the font are erased.
        everything = place_words(seg, ocr, metrics, char_cid, embedded.key, faces=faces, choose=chooser(name))
        placed = [p for p in everything if not p.missing]
        done = {(p.line, p.word) for p in placed}
        kept = {(li, wi) for li, line in enumerate(ocr.lines) for wi in range(len(line.words)) if (li, wi) not in done}
        set_units = [u for u in seg.units if (u.line, u.word) not in kept]
        background, mask = erase_text(gray, Segmentation(seg.size_px, seg.components, set_units, seg.unassigned), labels, x_size)
        background = smooth_paper(background)
        ink_gray = float(np.median(gray[mask & (gray < 128)])) / 255 if (mask & (gray < 128)).any() else 0.1
        runs = [p.run for p in placed] + _hidden_runs(ocr, kept, hidden_cid, hidden.key)
        pages_out.append(PageSpec(w, h, dpi, background=background, background_dpi=background_dpi,
                                  runs=runs, fill_gray=round(ink_gray, 3)))
        text_only.append(PageSpec(w, h, dpi, runs=runs))
        cv2.imwrite(str(out / f"{name}.background.png"), background)
        all_placed.append(placed)
        rows.append({"page": name, "words": len(placed),
                     "size_pt": round(float(np.median([p.run.size_px for p in placed])) * 72 / dpi, 2) if placed else None,
                     "missing": "".join(sorted({c for p in everything for c in p.missing})),
                     "kept_as_scan": len(kept)})

    pdf_name = (out_name or workspace.name) + ".pdf"
    fonts = [f.embedded for f in faces.values()] + [hidden]
    write_pdf(out / pdf_name, pages_out, fonts)
    write_pdf(out / "text-only.pdf", text_only, fonts)

    doc = pdfium.PdfDocument(str(out / pdf_name))
    extracted = [doc[i].get_textpage().get_text_bounded() for i in range(len(doc))]
    doc.close()
    from .compare import cer, match_size

    for i, (row, (name, gray, dpi, ocr, seg, labels)) in enumerate(zip(rows, loaded)):
        if not all_placed[i]:
            continue
        rendered = match_size(_render(out / "text-only.pdf", i, dpi), gray.shape)[0]
        row["overlay"] = measure_overlay(rendered, all_placed[i])
        row["text_cer_vs_ocr"] = round(cer(extracted[i], ocr.text()), 4)
        view = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        text_ink = binarize(rendered)
        view[text_ink] = (0.4 * view[text_ink] + 0.6 * np.array([220, 40, 40])).astype(np.uint8)
        Image.fromarray(view).save(out / f"{name}.overlay.png", compress_level=1)  # a check picture: fast, not small

    measured = [r["overlay"] for r in rows if r.get("overlay") and r["overlay"]["left"]]
    summary = {
        "font": str(font),
        "faces": {k: str(f.path) for k, f in faces.items()},
        "pdf": pdf_name,
        "bytes": (out / pdf_name).stat().st_size,
        "pages": rows,
        "within_1px": {
            side: round(float(np.average([m[side]["within_1px"] for m in measured], weights=[m["words"] for m in measured])), 4)
            for side in ("left", "right", "top", "bottom")
        } if measured else None,
    }
    report.write_json(out / "substitute.json", summary)
    _write_html(summary, out / "index.html")
    return summary


def _write_html(s: dict, path: Path) -> None:
    w1 = s["within_1px"] or {}
    body = [
        f"<p class=\"muted\">Font: {report.esc(Path(s['font']).name)} · <a href=\"{report.esc(s['pdf'])}\">{report.esc(s['pdf'])}</a> · "
        f"{report.human_bytes(s['bytes'])}. Overlays: the text layer in red over the scan; red should sit exactly on the scanned letters.</p>",
        f"<p>Words whose rendered ink ends within 1 px of the scan's: left {100 * w1.get('left', 0):.1f}%, right {100 * w1.get('right', 0):.1f}%, "
        f"top {100 * w1.get('top', 0):.1f}%, bottom {100 * w1.get('bottom', 0):.1f}%.</p>" if w1 else "",
    ]
    body.append(report.table(
        ["Page", "Words", "Size (pt)", "Left ≤1px", "Right ≤1px", "Right p95 (px)", "Bottom ≤1px", "Text CER vs OCR", "Missing glyphs"],
        [[r["page"], r["words"], r.get("size_pt") or "—",
          f"{100 * r['overlay']['left']['within_1px']:.1f}%" if r.get("overlay") and r["overlay"]["left"] else "—",
          f"{100 * r['overlay']['right']['within_1px']:.1f}%" if r.get("overlay") and r["overlay"]["right"] else "—",
          r["overlay"]["right"]["p95"] if r.get("overlay") and r["overlay"]["right"] else "—",
          f"{100 * r['overlay']['bottom']['within_1px']:.1f}%" if r.get("overlay") and r["overlay"]["bottom"] else "—",
          r.get("text_cer_vs_ocr", "—"), r.get("missing", "")] for r in s["pages"]],
        numeric={1, 2, 3, 4, 5, 6, 7},
    ))
    body.append("<div class=\"row\">")
    for r in s["pages"]:
        if r.get("overlay"):
            body.append(f"<figure><a href=\"{r['page']}.overlay.png\"><img loading=\"lazy\" src=\"{r['page']}.overlay.png\" alt=\"\" style=\"max-height:520px\"></a>"
                        f"<figcaption>{r['page']}</figcaption></figure>")
    body.append("</div>")
    report.write_page(path, "Substitute mode", "\n".join(body))
