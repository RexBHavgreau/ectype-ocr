"""Typeface families: telling books' type apart, so each is read with the model that suits it.

A book's type in one script gets a fingerprint, a few numbers measured on its letters:
- weight: the median stroke width against the letters' height;
- contrast: thick strokes against thin ones (a Didot or Porson Greek against a monoline one);
- density: how much of each letter's box is ink;
- width: the letters' width against their height;
- slant: the shear that makes the strokes most upright.
Fingerprints are measured the same way on line images (generated lines in a font) and on
word images cut from a scan, so a font and a book can fall in one family.

Books (and fonts) whose fingerprints lie close together form a family (`cluster`). A family
with enough training data gets a model of its own (`train.py`); a book is read with the model
of the nearest family, if one is near enough (`nearest`), else with the language's general model.
"""

from __future__ import annotations

import math

import numpy as np

# How far apart (in the fingerprint's units) two books may be and still share a family.
SAME_FAMILY = 1.0
# Each number is divided by its usual spread between typefaces, so all count about equally.
SCALE = np.array([0.025, 0.6, 0.08, 0.12, 4.0])
MAX_IMAGES = 400


def _ink(gray: np.ndarray) -> np.ndarray:
    import cv2

    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return ink > 0


def _slant(ink: np.ndarray) -> float:
    """The shear angle (degrees, positive leaning right) that makes columns of ink sharpest."""
    h, w = ink.shape
    ys, xs = np.nonzero(ink)
    if len(xs) < 20:
        return 0.0
    best, best_angle = -1.0, 0.0
    for angle in range(-20, 21):
        shifted = np.round(xs + (h - 1 - ys) * -math.tan(math.radians(angle))).astype(int)
        shifted -= shifted.min()
        counts = np.bincount(shifted)
        score = float((counts.astype(float) ** 2).sum())
        if score > best:
            best, best_angle = score, angle
    return float(best_angle)


LETTER = 48  # letters are scaled to this height (pixels) before measuring, so size doesn't count


def _letters(ink: np.ndarray):
    """Connected components that are letters (not points, accents or specks), and their height."""
    import cv2

    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    comps = stats[1:]
    comps = comps[comps[:, cv2.CC_STAT_AREA] >= 6]
    if not len(comps):
        return comps, 0.0
    # The letters' height from the larger components: in pointed Hebrew the vowel points
    # outnumber the letters, and would otherwise set the measure.
    area = comps[:, cv2.CC_STAT_AREA]
    big = comps[area >= np.percentile(area, 50)]
    mid = float(np.median(big[:, cv2.CC_STAT_HEIGHT]))
    h = comps[:, cv2.CC_STAT_HEIGHT].astype(float)
    return comps[(h > 0.5 * mid) & (h < 2.5 * mid)], mid


def measure(images: list[np.ndarray]) -> list[float] | None:
    """The fingerprint of a typeface from images of its text (grayscale lines or words)."""
    import cv2
    from skimage.morphology import skeletonize

    widths, aspects, densities, slants = [], [], [], []
    for gray in images[:MAX_IMAGES]:
        if gray.ndim != 2 or min(gray.shape) < 8:
            continue
        ink = _ink(gray)
        if ink.mean() < 0.01 or ink.mean() > 0.6:
            continue
        _, mid = _letters(ink)
        if mid < 6:
            continue
        # The same letter height for every image, so the measures don't depend on size or resolution.
        f = LETTER / mid
        big = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC if f > 1 else cv2.INTER_AREA)
        ink = _ink(big)
        keep, mid = _letters(ink)
        if not len(keep):
            continue
        aspects.extend((keep[:, cv2.CC_STAT_WIDTH] / keep[:, cv2.CC_STAT_HEIGHT]).tolist())
        densities.extend((keep[:, cv2.CC_STAT_AREA] / (keep[:, cv2.CC_STAT_WIDTH] * keep[:, cv2.CC_STAT_HEIGHT])).tolist())
        dist = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        strokes = 2 * dist[skeletonize(ink)]
        if len(strokes):
            widths.append(strokes / mid)
        slants.append(_slant(ink))
    if not widths or len(aspects) < 10:
        return None
    strokes = np.concatenate(widths)
    weight = float(np.median(strokes))
    contrast = float(np.percentile(strokes, 90) / max(np.percentile(strokes, 25), 1e-3))
    return [round(weight, 4), round(contrast, 3), round(float(np.median(densities)), 4),
            round(float(np.median(aspects)), 4), round(float(np.median(slants)), 2)]


def distance(a, b) -> float:
    return float(np.linalg.norm((np.asarray(a, float) - np.asarray(b, float)) / SCALE))


def cluster(prints: dict[str, list[float]], threshold: float = SAME_FAMILY) -> dict[str, int]:
    """Group books (name -> fingerprint) into families: name -> family number (1, 2...),
    numbered by size, largest first. Average linkage: a book joins a family when it is near
    the family as a whole, not just one of its members."""
    names = [n for n, p in prints.items() if p]
    if not names:
        return {}
    if len(names) == 1:
        return {names[0]: 1}
    from scipy.cluster.hierarchy import fcluster, linkage

    data = np.array([np.asarray(prints[n], float) / SCALE for n in names])
    labels = fcluster(linkage(data, method="average"), t=threshold, criterion="distance")
    sizes = {lab: int((labels == lab).sum()) for lab in set(labels)}
    order = sorted(sizes, key=lambda lab: (-sizes[lab], min(i for i, x in enumerate(labels) if x == lab)))
    renumber = {lab: k + 1 for k, lab in enumerate(order)}
    return {n: renumber[lab] for n, lab in zip(names, labels)}


def centroid(prints: list[list[float]]) -> list[float]:
    return [round(float(v), 4) for v in np.mean(np.asarray(prints, float), axis=0)]


def nearest(fingerprint, centroids: dict[str, list[float]], threshold: float = SAME_FAMILY) -> str | None:
    """The family whose centre is nearest the fingerprint, if near enough to belong to it."""
    if not fingerprint or not centroids:
        return None
    name, d = min(((n, distance(fingerprint, c)) for n, c in centroids.items()), key=lambda t: t[1])
    return name if d <= threshold * 1.5 else None


def book_fingerprints(workspace, codes: list[str], max_words: int = 300) -> dict[str, list[float]]:
    """A project's fingerprint for each script of its languages, from words read confidently
    (or corrected) on its pages: script -> fingerprint."""
    import json
    from pathlib import Path

    from PIL import Image

    from .ocr import OcrPage
    from .scripts import in_script, script_of

    workspace = Path(workspace)
    scripts = []
    for code in codes:
        s = script_of(code)
        if s not in scripts:
            scripts.append(s)
    crops: dict[str, list[np.ndarray]] = {s: [] for s in scripts}
    for path in sorted((workspace / "ocr").glob("*.ocr.json")):
        if all(len(v) >= max_words for v in crops.values()):
            break
        page = path.name[: -len(".ocr.json")]
        png = workspace / "pages" / f"{page}.png"
        if not png.is_file():
            continue
        ocr = OcrPage.from_json(json.loads(path.read_text(encoding="utf-8")))
        gray = None
        for w in ocr.words():
            if w.conf < 80 or len(w.text) < 3:
                continue
            script = next((s for s in scripts if in_script(w.text, s)), None)
            if script is None or len(crops[script]) >= max_words:
                continue
            if gray is None:
                gray = np.asarray(Image.open(png).convert("L"))
            x0, y0, x1, y1 = w.box
            crop = gray[max(0, y0 - 2):y1 + 2, max(0, x0 - 2):x1 + 2]
            if crop.size:
                crops[script].append(crop)
    out = {}
    for script, images in crops.items():
        if len(images) >= 20:
            fp = measure(images)
            if fp:
                out[script] = fp
    return out
