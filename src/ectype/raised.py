"""Raised characters read again: footnote numbers and verse parts set small above the line
("present¹", "Isa. 1, 3ᵃ", a note starting "¹ Though"), which a model not trained on them reads
as "!", "?", "%" or ">".

After a page is read, a run of up to three characters at a word's end (before any closing
punctuation), or a lone mark of one or two starting a note, is taken as raised when the bottom
of its ink stands well above the line's baseline (Tesseract's character boxes are too rough for
that: the box of a raised "ᵃ" can reach down to the line). Its image is read again by itself,
enlarged, with only figures and the letters a-h allowed, and the reading stored as superscript
characters (¹, ᵃ). Characters already read as superscripts are left alone. Tiny type reads
unreliably, differently at different enlargements: each run is read four ways (enlarged three
and five times, as it is and sharpened to black and white), and a reading is taken only when
two agree. On Driver's Treatise this reads its notes' marks and most verse parts (13ᵇ, 547ᵈ);
some stay wrong (171ᵃ read 171⁴), for Review.
"""

from __future__ import annotations

import numpy as np

SUPER = dict(zip("0123456789abcdefgh", "⁰¹²³⁴⁵⁶⁷⁸⁹ᵃᵇᶜᵈᵉᶠᵍʰ"))
ALLOWED = "".join(SUPER)
CLOSING = set(",.;:)]")
NOT_REFERENCES = set("'\"‘’“”*†‡°-‐–—+=~^")  # raised too (or above the line), but characters of their own
RAISED = 0.3  # the ink's bottom this many x-heights above the baseline


def _latin(text: str) -> bool:
    """No letters of another script (a Greek or Hebrew word's marks are its own)."""
    import unicodedata

    return not any(c.isalpha() and "LATIN" not in unicodedata.name(c, "") for c in text if c not in SUPER.values())


def x_height(line) -> float | None:
    """The line's x-height: Tesseract's line size less its ascenders and descenders."""
    if line.x_size and line.ascenders is not None and line.descenders is not None:
        xh = line.x_size - line.ascenders - line.descenders
        if xh > 4:
            return float(xh)
    return None


def _pieces(gray: np.ndarray, x0: int, x1: int, top: int, bottom: int) -> list[tuple[float, int]]:
    """The separate pieces of ink in a region of the page: (centre x, bottom y) of each."""
    import cv2

    h, w = gray.shape
    x0, x1, top, bottom = max(0, x0), min(w, x1), max(0, top), min(h, bottom)
    if x1 <= x0 or bottom <= top:
        return []
    ink = (gray[top:bottom, x0:x1] < 128).astype(np.uint8)
    n, _, stats, cents = cv2.connectedComponentsWithStats(ink, connectivity=8)
    return [(x0 + float(cents[i][0]), top + int(stats[i][1] + stats[i][3]) - 1) for i in range(1, n) if stats[i][4] >= 3]


def raised_runs(gray: np.ndarray, page):
    """(line, word, first char, end char) of each run of raised characters to read again. A
    character is raised when every piece of ink centred in its box ends well above the baseline:
    whole pieces, since Tesseract's box can hold only part of a letter (the top of a y's arm),
    which would look raised."""
    out = []
    for li, line in enumerate(page.lines):
        xh = x_height(line)
        if xh is None:
            continue
        for wi, word in enumerate(line.words):
            chars = word.chars
            if not chars or "".join(c.text for c in chars) != word.text or not _latin(word.text):
                continue
            pieces = _pieces(gray, word.box[0] - 2, word.box[2] + 2, line.box[1] - int(0.3 * xh),
                             int(line.baseline_y(word.box[2]) + 0.6 * xh))
            end = len(chars)
            while end > 0 and chars[end - 1].text in CLOSING:
                end -= 1
            k = end
            while k > 0:
                c = chars[k - 1]
                if c.text in NOT_REFERENCES or c.text in SUPER.values() or c.text.isalpha() and c.text not in SUPER:
                    break  # a letter is raised only as a verse's part (a-h)
                baseline = line.baseline_y((c.box[0] + c.box[2]) / 2)
                mine = [bottom for x, bottom in pieces if c.box[0] <= x <= c.box[2]]
                if not mine or max(mine) > baseline - RAISED * xh:
                    break
                k -= 1
            if k == end or end - k > 3:
                continue
            if any(c.text.isalpha() for c in chars[k:end]) and not (k > 0 and chars[k - 1].text.isdigit()):
                continue  # a raised letter is a verse's part, after its number (3ᵃ)
            if k == 0 and (end > 2 or wi > 0):
                continue  # a whole word raised: only a note's own mark, one or two at the line's start
            out.append((li, wi, k, end))
    return out


VARIANTS = ((3, False), (3, True), (5, False), (5, True))  # enlargement, sharpened to black and white


def enlarged(crop: np.ndarray, factor: int, sharp: bool, pad: int = 40) -> np.ndarray:
    """A small piece of the page enlarged, perhaps sharpened, and set in white space."""
    from PIL import Image

    a = np.asarray(Image.fromarray(crop).resize((crop.shape[1] * factor, crop.shape[0] * factor), Image.LANCZOS))
    if sharp:
        a = np.where(a < 150, 0, 255).astype(np.uint8)
    return np.pad(a, pad, constant_values=255)


def voted(crop: np.ndarray, read_image) -> str:
    """The reading at least two of the VARIANTS agree on (`read_image(image, factor)` -> text), or ""."""
    from collections import Counter

    readings: Counter = Counter()
    for factor, sharp in VARIANTS:  # until two agree: mostly the first two
        text = "".join(read_image(enlarged(crop, factor, sharp), factor).split())
        if text:
            readings[text] += 1
            if readings[text] >= 2:
                return text
    return ""


def reread_raised(gray: np.ndarray, page, read) -> int:
    """Read each raised run again (`read(image)` -> text, with only ALLOWED characters) and store
    it as superscripts. Returns how many runs were changed."""
    from .ocr import Char

    changed = 0
    for li, wi, k, end in raised_runs(gray, page):
        line, word = page.lines[li], page.lines[li].words[wi]
        xh = x_height(line)
        run = word.chars[k:end]
        x0, x1 = run[0].box[0], run[-1].box[2]
        baseline = line.baseline_y((x0 + x1) / 2)
        pad = int(0.3 * xh)
        y0, y1 = max(0, line.box[1] - pad), int(baseline - 0.1 * xh)
        crop = gray[y0:y1, max(0, x0 - pad // 2):x1 + pad // 2]
        if crop.size == 0:
            continue
        text = "".join(ch for ch in read(crop) if not ch.isspace())
        if not text or len(text) > 3 or any(ch not in SUPER for ch in text) or k == 0 and len(text) != len(run):
            continue  # a note's own mark keeps its length
        sup = "".join(SUPER[ch] for ch in text)
        if len(sup) == len(run):
            for c, t in zip(run, sup):
                c.text = t
        else:
            step = (x1 - x0) / len(sup)
            run = [Char(t, [round(x0 + n * step), run[0].box[1], round(x0 + (n + 1) * step), run[0].box[3]], run[0].conf)
                   for n, t in enumerate(sup)]
            word.chars = word.chars[:k] + run + word.chars[end:]
        word.text = "".join(c.text for c in word.chars)
        changed += 1
    return changed
