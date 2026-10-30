"""Segmentation: tie each OCR character to the ink that forms it.

Connected components of the binarized page are assigned to OCR words by overlap,
then to characters by how many of their pixels fall in each character's columns.
A component that substantially covers several characters (touching letters, a
printed ligature) makes those characters one glyph unit with a multi-character
string; several components on one character (the dot of an i, a broken e, an
accent) all go to that character. The result is a list of glyph units, each a
Unicode string plus its component ids, which later stages cluster and trace.

`evaluate` scores a segmentation against a synthetic page's character label map.
"""

from __future__ import annotations

import difflib
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

from . import report
from .ocr import OcrPage
from .preprocess import binarize
from .scripts import is_rtl

X_LETTERS = set("acemnorsuvwxz")
# Old-style figures (Driver's Treatise) hang 3, 4, 5, 7 and 9 below the line; 0, 1, 2, 6 and 8
# sit on it in either style.
NO_DESCENDER = set("abcdefhiklmnorstuvwxzABCDEFGHIKLMNOPRSTUVWXYZ01268!?%&")
MAX_SLOPE_GAP = 0.01  # a fitted slope this far from Tesseract's line slope is not trusted


@dataclass
class Unit:
    id: int
    line: int
    word: int
    text: str
    chars: list[int]  # character indices within the word
    components: list[int]
    box: list[int] | None  # ink box, x0 y0 x1 y1 (exclusive); None when no ink was found
    ink_px: int
    conf: float
    kind: str  # "single", "multi" (one blob for several characters) or "no ink"
    baseline_y: float


@dataclass
class Segmentation:
    size_px: list[int]
    components: int
    units: list[Unit] = field(default_factory=list)
    unassigned: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Segmentation":
        return cls(size_px=data["size_px"], components=data["components"],
                   units=[Unit(**u) for u in data["units"]], unassigned=data["unassigned"])


class _DisjointSet:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def segment_page(gray: np.ndarray, ocr: OcrPage) -> tuple[Segmentation, np.ndarray]:
    """Segment one page. Returns the segmentation and the component label image."""
    ink = binarize(gray)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    slices = ndimage.find_objects(labels)
    seg = Segmentation(size_px=[gray.shape[1], gray.shape[0]], components=n - 1)

    words = [(li, wi, w) for li, line in enumerate(ocr.lines) for wi, w in enumerate(line.words)]
    if not words:
        seg.unassigned = [_component_info(c, stats, "no OCR text on page") for c in range(1, n)]
        return seg, labels
    wbox = np.array([w.box for _, _, w in words], float)
    sizes = [line.x_size for line in ocr.lines if line.x_size]
    x_size = float(np.median(sizes)) if sizes else 30.0
    speck = max(3, round(0.008 * x_size * x_size))

    # Component -> word, by pixels inside each overlapping word box.
    by_word: dict[int, list[int]] = {}
    for c in range(1, n):
        x, y, w, h, area = stats[c]
        hits = np.nonzero((wbox[:, 0] < x + w) & (wbox[:, 2] > x) & (wbox[:, 1] < y + h) & (wbox[:, 3] > y))[0]
        if len(hits) == 0:
            reason = "speck" if area < speck else ("large non-text" if max(w, h) > 4 * x_size else "outside words")
            seg.unassigned.append(_component_info(c, stats, reason))
            continue
        mask = labels[slices[c - 1]] == c
        best, best_px = -1, 0
        for k in hits:
            bx0, by0, bx1, by1 = wbox[k]
            sub = mask[max(0, int(by0) - y):max(0, int(by1) - y), max(0, int(bx0) - x):max(0, int(bx1) - x)]
            px = int(sub.sum())
            if px > best_px:
                best, best_px = k, px
        if best < 0 or best_px < 0.3 * area:
            seg.unassigned.append(_component_info(c, stats, "mostly outside words"))
            continue
        by_word.setdefault(int(best), []).append(c)

    # Cluster every word, then learn each character's typical ink width (in x-heights)
    # from words whose cluster count matches their character count.
    clustered = {}
    samples: dict[str, list[float]] = {}
    for k, (li, wi, word) in enumerate(words):
        xs = ocr.lines[li].x_size or x_size
        clustered[k] = _clusters(by_word.get(k, []), stats, xs, speck)
        if word.chars and len(word.chars) == len(clustered[k]):
            for ch, cl in zip(_visual(word.chars), clustered[k]):
                samples.setdefault(ch.text, []).append((cl["x1"] - cl["x0"]) / xs)
    widths = {t: float(np.median(v)) for t, v in samples.items() if len(v) >= 2}
    fallback = float(np.median([w for v in samples.values() for w in v])) if samples else 0.5

    next_id = 0
    for k, (li, wi, word) in enumerate(words):
        line = ocr.lines[li]
        chars = word.chars
        if not chars:
            continue
        clusters = clustered[k]
        # Ink clusters run left to right; a right-to-left word's characters are aligned to
        # them in visual order and mapped back to their places in the text.
        order = sorted(range(len(chars)), key=lambda i: chars[i].box[0]) if is_rtl(word.text) else list(range(len(chars)))
        visual = [chars[i] for i in order]
        # Marks floating above the letters (an italic i's dot leaning over the next letter) are
        # set aside, so they aren't taken for letters, then given to the letters that carry one.
        clusters, floating = _float_marks(clusters, visual, stats, line.x_size or x_size)
        expected = [widths.get(c.text, fallback) for c in visual]
        groups, skipped = _align(visual, clusters, line.x_size or x_size, speck, expected)
        skipped = skipped + list(range(len(clusters), len(clusters) + len(floating)))
        clusters = clusters + floating
        groups = _join_dotless(groups, visual, clusters, stats)
        groups, skipped = _join_marks(groups, skipped, visual, clusters, stats, line.x_size or x_size)
        groups = [(sorted(order[i] for i in idx), cl) for idx, cl in groups]
        for j in skipped:
            for c in clusters[j]["components"]:
                seg.unassigned.append(_component_info(c, stats, "speck in word" if clusters[j]["area"] < 3 * speck else "extra ink in word"))
        for idx, cl in groups:
            cs = sorted(c for j in cl for c in clusters[j]["components"])
            if cs:
                box = [int(min(stats[c][0] for c in cs)), int(min(stats[c][1] for c in cs)),
                       int(max(stats[c][0] + stats[c][2] for c in cs)), int(max(stats[c][1] + stats[c][3] for c in cs))]
                ink_px = int(sum(stats[c][4] for c in cs))
            else:
                box, ink_px = None, 0
            kind = "no ink" if not cs else ("multi" if len(idx) > 1 else "single")
            x_mid = (box[0] + box[2]) / 2 if box else (chars[idx[0]].box[0] + chars[idx[-1]].box[2]) / 2
            seg.units.append(Unit(
                id=next_id, line=li, word=wi, text="".join(chars[i].text for i in idx), chars=list(idx),
                components=[int(c) for c in cs], box=box, ink_px=ink_px,
                conf=round(float(min(chars[i].conf for i in idx)), 2), kind=kind,
                baseline_y=round(line.baseline_y(x_mid), 2),
            ))
            next_id += 1
    return seg, labels


def _visual(chars):
    """A word's characters left to right on the page (right-to-left words come in text order)."""
    return sorted(chars, key=lambda c: c.box[0]) if is_rtl("".join(c.text for c in chars)) else chars


def _clusters(comps: list[int], stats: np.ndarray, x_size: float, speck: int) -> list[dict]:
    """Group a word's components into left-to-right ink clusters.

    A smaller piece stacked above or below a larger one (the dot of an i or j, a colon,
    an accent) joins the one partner it overlaps most horizontally, so a dot can't chain
    an i to a neighbouring t. A small piece inside a letter's box (a broken-off s
    terminal) joins that letter, and slivers smaller than any punctuation mark within
    two pixels of one join it too; a hyphen or period beside a letter does not.
    """
    comps = sorted(comps, key=lambda c: stats[c][0])
    ds = _DisjointSet(len(comps))
    area = [int(stats[c][4]) for c in comps]
    for a in range(len(comps)):
        xa, ya, wa, ha, _ = stats[comps[a]]
        best, best_ov = None, 0.6
        for b in range(len(comps)):
            if b == a or area[b] < area[a]:
                continue
            xb, yb, wb, hb, _ = stats[comps[b]]
            x_overlap = min(xa + wa, xb + wb) - max(xa, xb)
            y_overlap = min(ya + ha, yb + hb) - max(ya, yb)
            ov = x_overlap / max(1, min(wa, wb))
            if y_overlap <= 0.3 * min(ha, hb) and ov >= best_ov:
                best, best_ov = b, ov
        if best is not None:
            ds.union(a, best)
    reach = 2  # a broken serif sits within a pixel or two of its letter
    group_size: dict[int, int] = {}
    for i in range(len(comps)):
        group_size[ds.find(i)] = group_size.get(ds.find(i), 0) + 1
    for a in range(len(comps)):
        if area[a] >= 3 * speck or group_size[ds.find(a)] > 1:
            continue  # lone small pieces only; a stacked i-dot is already placed
        xa, ya, wa, ha, _ = stats[comps[a]]
        best, best_gap = None, None
        for b in range(len(comps)):
            if b == a or area[b] < 3 * speck:
                continue
            xb, yb, wb, hb, _ = stats[comps[b]]
            dx = max(xb - (xa + wa), xa - (xb + wb))
            dy = max(yb - (ya + ha), ya - (yb + hb))
            if dx < 0 and dy < 0:
                gap = -1  # inside the letter's box: part of it (the tail of an s)
            elif area[a] < 1.6 * speck:
                gap = max(dx, dy, 0)  # beside it: only slivers smaller than any punctuation
            else:
                continue
            if gap <= reach and (best_gap is None or gap < best_gap):
                best, best_gap = b, gap
        if best is not None:
            ds.union(a, best)
    groups: dict[int, list[int]] = {}
    for i, c in enumerate(comps):
        groups.setdefault(ds.find(i), []).append(c)
    out = []
    for cs in groups.values():
        out.append({
            "components": cs,
            "x0": int(min(stats[c][0] for c in cs)),
            "x1": int(max(stats[c][0] + stats[c][2] for c in cs)),
            "area": int(sum(stats[c][4] for c in cs)),
        })
    return sorted(out, key=lambda g: (g["x0"] + g["x1"]) / 2)


def _join_dotless(groups, chars, clusters: list[dict], stats: np.ndarray):
    """An i or j that came out without its dot, under a neighbour that reaches over it
    (the f of "fi" fused with the dot), joins that neighbour: the unit becomes "fi"."""
    out = []
    for idx, cl in groups:
        if (out and len(idx) == 1 and chars[idx[0]].text in "ij" and len(cl) == 1
                and len(clusters[cl[0]]["components"]) == 1 and out[-1][1]):
            prev_idx, prev_cl = out[-1]
            me = clusters[cl[0]]
            stem = stats[me["components"][0]]
            reach = max(clusters[j]["x1"] for j in prev_cl)
            prev_top = min(stats[c][1] for j in prev_cl for c in clusters[j]["components"])
            if reach > me["x0"] and prev_top < stem[1] - 0.15 * stem[3]:
                out[-1] = (prev_idx + idx, prev_cl + cl)
                continue
        out.append((idx, cl))
    return out


RAISED = set("'\"‘’“”‚„`´^ʹʺʻʼʽʾʿˈ¹²³⁴⁵⁶⁷⁸⁹⁰ᵃᵇᶜᵈᵉᶠ*†‡")


def _float_marks(clusters: list[dict], chars, stats: np.ndarray, x_size: float):
    """The word's clusters without the small ones floating wholly above its letters' tops, and
    those set aside: at most one for each letter that carries a mark (i, j, é...), and none in a
    word with raised characters of its own (quotes, apostrophes, ʾ ʿ, superscripts), which also
    float above the letters and must stay theirs."""
    text = "".join(c.text for c in chars)
    wanting = sum(_wants_mark(c.text) for c in chars)
    if not wanting or any(c in RAISED or unicodedata.category(c) == "Lm" for c in text) or len(clusters) < 3:
        return clusters, []

    def box(cl):
        cs = cl["components"]
        return (min(stats[c][1] for c in cs), max(stats[c][1] + stats[c][3] for c in cs))

    tops = sorted(box(cl)[0] for cl in clusters)
    letter_top = tops[len(tops) // 2]  # most letters reach the x-height
    areas = sorted(cl["area"] for cl in clusters)
    typical = areas[len(areas) // 2]
    floating = [k for k, cl in enumerate(clusters)
                if box(cl)[1] <= letter_top + 0.1 * x_size and cl["area"] < 0.25 * typical]
    floating = floating[:wanting]
    return [cl for k, cl in enumerate(clusters) if k not in floating], [clusters[k] for k in floating]


def _gap(x: float, span) -> float:
    """How far x lies outside the span (x0, x1); 0 inside it."""
    return 0.0 if span[0] <= x <= span[1] else min(abs(x - span[0]), abs(x - span[1]))


def _wants_mark(text: str) -> bool:
    """A letter drawn with a separate mark: i, j, or one with an accent (ä, é, ḥ, ō...)."""
    return any(c in "ij" or len(unicodedata.normalize("NFD", c)) > 1 for c in text if c.isalpha())


def _join_marks(groups, skipped: list[int], chars, clusters: list[dict], stats: np.ndarray, x_size: float):
    """A small piece the alignment left out, above or below the letters, joins the nearest letter
    that carries a mark, within half an x-height of it; one beside a letter, within its height
    and almost touching it (a broken-off arm), joins that letter. The dot of an italic i leans right, over
    the gap before the next letter, so `_clusters` (which wants it over a stem) leaves it alone;
    left out, it was lost from the traced type (Driver's Treatise: "ıs sellıng", "I gıve").
    Returns the groups and the clusters still left out."""
    def extent(cls):
        cs = [c for k in cls for c in clusters[k]["components"]]
        return (min(stats[c][0] for c in cs), min(stats[c][1] for c in cs),
                max(stats[c][0] + stats[c][2] for c in cs), max(stats[c][1] + stats[c][3] for c in cs))

    def extent_of(cls):
        x0, _, x1, _ = extent(cls)
        return x0, x1

    groups = list(groups)
    # A mark joined to a letter that has none (an italic i's dot leaning over the m of "imp"):
    # taken off it as a cluster of its own, for the nearest i or j without one below.
    def bare(idx, cls):
        if not cls:
            return False
        cs = [c for k in cls for c in clusters[k]["components"]]
        top = min(stats[c][1] for c in cs)
        main = max(cs, key=lambda c: stats[c][4])
        return top >= stats[main][1] - 2  # nothing above its body
    # Latin only: in Hebrew or Greek, points and breathings the reading left out are no clue.
    wanting = [g for g, (idx, cls) in enumerate(groups)
               if any(chars[i].text in "ij" for i in idx) and bare(idx, cls)]
    if wanting:
        for g, (idx, cls) in enumerate(groups):
            text = "".join(chars[i].text for i in idx)
            if not cls or _wants_mark(text) or not all("LATIN" in unicodedata.name(c, "") for c in text):
                continue
            for k in cls:
                comps = clusters[k]["components"]
                if len(comps) < 2:
                    continue
                main = max(comps, key=lambda c: stats[c][4])
                for c in list(comps):
                    x, y, w, h, area = stats[c]
                    if c == main or area > 0.4 * stats[main][4] or y + h > stats[main][1] + 0.2 * stats[main][3]:
                        continue  # not a small piece above the letter's body
                    cx = x + w / 2
                    near = min(wanting, key=lambda t: _gap(cx, extent_of(groups[t][1])))
                    if _gap(cx, extent_of(groups[near][1])) > 0.5 * x_size:
                        continue
                    comps.remove(c)
                    clusters.append({"components": [int(c)], "area": int(area), "x0": int(x), "x1": int(x + w)})
                    skipped = skipped + [len(clusters) - 1]
    left = []
    for j in skipped:
        x0, y0, x1, y1 = extent([j])
        cx = (x0 + x1) / 2
        best, best_d = None, 0.5 * x_size
        for g, (idx, cls) in enumerate(groups):
            if not cls or not _wants_mark("".join(chars[i].text for i in idx)):
                continue
            if clusters[j]["area"] > 0.4 * sum(clusters[k]["area"] for k in cls):
                continue  # not a mark: as much ink as a letter
            gx0, gy0, gx1, gy1 = extent(cls)
            h = gy1 - gy0
            if not (y1 <= gy0 + 0.2 * h or y0 >= gy1 - 0.2 * h):
                continue  # beside the letter, not above or below it
            d = _gap(cx, (gx0, gx1))
            if d <= best_d:
                best, best_d = g, d
        if best is None:
            # A piece broken off a letter, beside it (the right arm of a v): joins the letter
            # it almost touches, when it stands within the letters' height.
            for g, (idx, cls) in enumerate(groups):
                if not cls or clusters[j]["area"] > 0.5 * sum(clusters[k]["area"] for k in cls):
                    continue
                gx0, gy0, gx1, gy1 = extent(cls)
                near = max(gx0 - x1, x0 - gx1) <= 0.12 * x_size
                if near and y0 >= gy0 - 2 and y1 <= gy1 + 2 and (best is None or _gap(cx, (gx0, gx1)) < best_d):
                    best, best_d = g, _gap(cx, (gx0, gx1))
        if best is None:
            left.append(j)
        else:
            idx, cls = groups[best]
            groups[best] = (idx, sorted(cls + [j]))
    return groups, left


ALIGN = {"merge": 1.2, "split": 0.8, "noink": 2.0, "skip": 1.5, "edge": 0.25, "cap": 0.5, "place": 0.3}


def _align(chars, clusters: list[dict], x_size: float, speck: int, expected: list[float] | None = None, max_span: int = 3):
    """Monotone alignment of characters to ink clusters by dynamic programming.

    Steps: one character to one cluster; up to `max_span` characters to one cluster
    (touching letters, ligatures); one character to up to `max_span` clusters (broken
    letters, quote marks); a cluster left out (specks); a character with no ink.
    Tesseract's LSTM character boxes drift by up to a letter, so the main cost is how
    well each group's ink width fits the characters' usual widths, learned from the
    same page (`expected`, in x-heights); box position is a weak, capped tie-breaker.
    Merges, splits, skips and missing characters carry penalties of about one
    x-height each: the alignment is one-to-one unless the ink clearly says otherwise.
    Returns ([(char indices, cluster indices)], skipped cluster indices).
    """
    n, m = len(chars), len(clusters)
    if n == m:
        # As many ink clusters as characters: one-to-one. Tesseract's boxes can drift
        # by a whole letter in a run, which would otherwise fit a merge plus a split.
        return [([i], [i]) for i in range(n)], []
    merge_pen, split_pen, noink_pen = ALIGN["merge"] * x_size, ALIGN["split"] * x_size, ALIGN["noink"] * x_size
    INF = float("inf")
    cost = [[INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            here = cost[i][j]
            if here == INF:
                continue
            if j < m:  # leave cluster j out
                skip = 0.15 * x_size if clusters[j]["area"] < 3 * speck else ALIGN["skip"] * x_size
                if here + skip < cost[i][j + 1]:
                    cost[i][j + 1], back[i][j + 1] = here + skip, (i, j, "skip")
            if i < n:  # character i has no ink
                if here + noink_pen < cost[i + 1][j]:
                    cost[i + 1][j], back[i + 1][j] = here + noink_pen, (i, j, "noink")
            for a in range(1, max_span + 1):
                for b in range(1, max_span + 1):
                    if (a > 1 and b > 1) or i + a > n or j + b > m:
                        continue
                    c0, c1 = chars[i].box[0], chars[i + a - 1].box[2]
                    k0, k1 = clusters[j]["x0"], clusters[j + b - 1]["x1"]
                    centre = abs((c0 + c1) - (k0 + k1)) / 2
                    edges = abs(c0 - k0) + abs(c1 - k1)
                    # Capped, so drifting boxes can't outvote the count of ink clusters.
                    place = min(centre + ALIGN["edge"] * edges, ALIGN["cap"] * x_size)
                    if expected is not None:
                        # How well the clusters' ink width fits the characters' usual widths.
                        want = sum(expected[i:i + a]) * x_size
                        place = ALIGN["place"] * place + abs((k1 - k0) - want)
                    step = place + (a - 1) * merge_pen + (b - 1) * split_pen
                    if here + step < cost[i + a][j + b]:
                        cost[i + a][j + b], back[i + a][j + b] = here + step, (i, j, "pair")
    groups, skipped = [], []
    i, j = n, m
    while i or j:
        pi, pj, kind = back[i][j]
        if kind == "skip":
            skipped.append(pj)
        elif kind == "noink":
            groups.append((list(range(pi, i)), []))
        else:
            groups.append((list(range(pi, i)), list(range(pj, j))))
        i, j = pi, pj
    groups.reverse()
    return groups, sorted(skipped)


def _component_info(c: int, stats: np.ndarray, reason: str) -> dict:
    x, y, w, h, area = (int(v) for v in stats[c])
    return {"id": int(c), "box": [x, y, x + w, y + h], "area": area, "reason": reason}


def fit_baselines(seg: Segmentation, ocr: OcrPage) -> dict[int, tuple[float, float]]:
    """Per line, a baseline y = a + b * x fitted to the bottoms of letters without
    descenders (outliers dropped); Tesseract's baseline where there are too few. A fitted
    slope far from Tesseract's (the letters bunched at one end of the line: a footnote line
    mostly Greek) keeps Tesseract's slope, through the letters' median."""
    by_line: dict[int, list] = {}
    for u in seg.units:
        if u.box:
            by_line.setdefault(u.line, []).append(u)
    base: dict[int, tuple[float, float]] = {}
    for li, line in enumerate(ocr.lines):
        units = by_line.get(li, [])
        pts = [((u.box[0] + u.box[2]) / 2, u.box[3]) for u in units if u.text and set(u.text) <= NO_DESCENDER]
        if len(pts) >= 3:
            xs, ys = np.array(pts, float).T
            for _ in range(2):  # fit, drop outliers, refit
                b, a = np.polyfit(xs, ys, 1) if np.ptp(xs) > 0 else (0.0, float(np.median(ys)))
                resid = ys - (a + b * xs)
                keep = np.abs(resid - np.median(resid)) <= max(1.5, 2.5 * np.median(np.abs(resid - np.median(resid))))
                if keep.sum() < 3 or keep.all():
                    break
                xs, ys = xs[keep], ys[keep]
            slope = line.baseline[0]
            if abs(b - slope) > MAX_SLOPE_GAP:
                b, a = slope, float(np.median(ys - slope * xs))
            base[li] = (float(a), float(b))
        else:
            slope, offset = line.baseline
            base[li] = (line.box[3] + offset - slope * line.box[0], slope)
    return base


def line_x_heights(seg: Segmentation) -> tuple[dict[int, float], float]:
    """Per line, the median ink height of x-height letters (px), and the page median.

    A line with fewer than five x-height letters (a footnote of figures, "Ibid.") is
    measured from whatever single characters it has, each against that character's
    usual height on the page; only a line with nothing to compare takes the page value.
    """
    per: dict[int, list[int]] = {}
    by_text: dict[str, list[int]] = {}
    by_line: dict[int, list] = {}
    for u in seg.units:
        if u.box and u.kind == "single":
            h = u.box[3] - u.box[1]
            by_text.setdefault(u.text, []).append(h)
            by_line.setdefault(u.line, []).append(u)
            if u.text in X_LETTERS:
                per.setdefault(u.line, []).append(h)
    every = [h for v in per.values() for h in v]
    page = float(np.median(every)) if every else 20.0
    typical = {t: float(np.median(v)) for t, v in by_text.items() if len(v) >= 10}
    out = {}
    for li, units in by_line.items():
        if len(per.get(li, [])) >= 5:
            out[li] = float(np.median(per[li]))
            continue
        ratios = [(u.box[3] - u.box[1]) / typical[u.text] for u in units if u.text in typical and typical[u.text] > 0]
        out[li] = page * float(np.median(ratios)) if len(ratios) >= 2 else page
    return out, page

# ---------------------------------------------------------------------------
# Review overlay

_PALETTE = np.array([(31, 119, 180), (44, 160, 44), (148, 103, 189), (23, 190, 207), (140, 86, 75), (188, 189, 34)], np.uint8)


def draw_overlay(gray: np.ndarray, seg: Segmentation, labels: np.ndarray) -> np.ndarray:
    """Ink coloured by glyph unit (cycling colours); multi-character units in magenta with a box;
    unassigned ink in red; characters with no ink as red boxes."""
    view = np.full(gray.shape + (3,), 255, np.uint8)
    faint = gray < 200
    view[faint] = (225, 225, 225)
    lut = np.zeros((seg.components + 1, 3), np.uint8)
    lut[:] = (230, 30, 30)  # unassigned
    lut[0] = (255, 255, 255)
    for u in seg.units:
        colour = (210, 40, 200) if u.kind == "multi" else _PALETTE[u.id % len(_PALETTE)]
        for c in u.components:
            lut[c] = colour
    ink = labels > 0
    view[ink] = lut[labels[ink]]
    for u in seg.units:
        if u.kind == "multi" and u.box:
            cv2.rectangle(view, (u.box[0] - 2, u.box[1] - 2), (u.box[2] + 1, u.box[3] + 1), (210, 40, 200), 1)
    return view


def no_ink_boxes(seg: Segmentation, ocr: OcrPage) -> list[list[int]]:
    boxes = []
    for u in seg.units:
        if u.kind == "no ink":
            word = ocr.lines[u.line].words[u.word]
            xs = [word.chars[i].box for i in u.chars]
            boxes.append([min(b[0] for b in xs), min(b[1] for b in xs), max(b[2] for b in xs), max(b[3] for b in xs)])
    return boxes


# ---------------------------------------------------------------------------
# Evaluation against synthetic truth


def evaluate(seg: Segmentation, ocr: OcrPage, labels: np.ndarray, gray: np.ndarray, truth: dict,
             truth_labels: np.ndarray, min_recall: float = 0.9, min_precision: float = 0.95) -> dict:
    """Share of truth characters that own the right ink.

    OCR characters are matched to truth characters by aligning the two character
    sequences. A truth character owns the right ink when it was matched and the glyph
    unit holding it covers at least `min_recall` of the (binarized) ink of its truth
    characters, with at most 1 - `min_precision` of its labelled ink belonging to others,
    and, for a unit of several characters, their ink really touches.
    """
    truth_chars = [c for line in truth["lines"] for w in line["words"] for c in w["chars"]]
    ocr_chars = []  # (unit id, text)
    unit_of: dict[tuple[int, int, int], int] = {}
    for u in seg.units:
        for i in u.chars:
            unit_of[(u.line, u.word, i)] = u.id
    for li, line in enumerate(ocr.lines):
        for wi, w in enumerate(line.words):
            for ci, c in enumerate(w.chars):
                ocr_chars.append((unit_of.get((li, wi, ci)), c.text))

    matcher = difflib.SequenceMatcher(None, [t for _, t in ocr_chars], [c["text"] for c in truth_chars], autojunk=False)
    truth_for_ocr: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            truth_for_ocr[block.a + k] = block.b + k

    units = {u.id: u for u in seg.units}
    unit_truth: dict[int, set[int]] = {}
    unit_unmatched: set[int] = set()
    for i, (uid, _) in enumerate(ocr_chars):
        if uid is None:
            continue
        if i in truth_for_ocr:
            unit_truth.setdefault(uid, set()).add(truth_chars[truth_for_ocr[i]]["id"])
        else:
            unit_unmatched.add(uid)

    ink = binarize(gray)
    truth_slices = ndimage.find_objects(truth_labels)
    comp_slices = ndimage.find_objects(labels)
    verdict: dict[int, str] = {}
    for uid, tids in unit_truth.items():
        u = units[uid]
        if uid in unit_unmatched:
            for t in tids:
                verdict[t] = "unit text partly wrong"
            continue
        if not u.components:
            for t in tids:
                verdict[t] = "no ink found"
            continue
        sl = [s for s in [truth_slices[t - 1] for t in tids if t - 1 < len(truth_slices)] if s is not None]
        sl += [comp_slices[c - 1] for c in u.components]
        y0 = min(s[0].start for s in sl); y1 = max(s[0].stop for s in sl)
        x0 = min(s[1].start for s in sl); x1 = max(s[1].stop for s in sl)
        tl = truth_labels[y0:y1, x0:x1]
        mine = np.isin(labels[y0:y1, x0:x1], u.components)
        theirs = np.isin(tl, list(tids)) & ink[y0:y1, x0:x1]
        recall = (mine & theirs).sum() / max(1, theirs.sum())
        labelled = mine & (tl > 0)
        wrong = (labelled & ~np.isin(tl, list(tids))).sum()
        precision = 1 - wrong / max(1, labelled.sum())
        ok = recall >= min_recall and precision >= min_precision
        outcome = "ok" if ok else ("missing ink" if recall < min_recall else "takes other ink")
        if ok and len(tids) > 1:
            # One unit for several characters is right only if their ink really touches:
            # some component must hold ink of more than one of them.
            cl = labels[y0:y1, x0:x1]
            joined = any(len(set(np.unique(tl[(cl == c) & (tl > 0)])) & tids) > 1 for c in u.components)
            if not joined:
                outcome = "separate glyphs merged"
        for t in tids:
            verdict[t] = outcome

    counts: dict[str, int] = {}
    for c in truth_chars:
        v = verdict.get(c["id"], "not recognized")
        counts[v] = counts.get(v, 0) + 1
    total = len(truth_chars)
    return {
        "truth_chars": total,
        "owned": counts.get("ok", 0),
        "owned_share": round(counts.get("ok", 0) / max(1, total), 4),
        "outcomes": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "units": len(seg.units),
        "multi_units": sum(u.kind == "multi" for u in seg.units),
    }


# ---------------------------------------------------------------------------
# Driver


def _segment_one(png: Path, workspace: Path, truth_root: Path | None, force: bool) -> dict | None:
    import json

    from .ocr import _fresh

    out = workspace / "segment"
    ocr_path = workspace / "ocr" / f"{png.stem}.ocr.json"
    if not ocr_path.is_file():
        return None
    ocr = OcrPage.from_json(json.loads(ocr_path.read_text(encoding="utf-8")))
    seg_path, labels_path = out / f"{png.stem}.seg.json", out / f"{png.stem}.labels.png"
    gray = np.asarray(Image.open(png).convert("L"))
    if not force and _fresh(seg_path, png, ocr_path) and labels_path.is_file():
        seg = Segmentation.from_json(json.loads(seg_path.read_text(encoding="utf-8")))
        labels = cv2.imread(str(labels_path), cv2.IMREAD_UNCHANGED).astype(np.int32)
    else:
        seg, labels = segment_page(gray, ocr)
        report.write_json(seg_path, seg.to_json())
        cv2.imwrite(str(labels_path), labels.astype(np.uint16 if labels.max() < 65536 else np.uint32))
        view = draw_overlay(gray, seg, labels)
        for b in no_ink_boxes(seg, ocr):
            cv2.rectangle(view, (b[0], b[1]), (b[2] - 1, b[3] - 1), (230, 30, 30), 2)
        Image.fromarray(view).save(out / f"{png.stem}.overlay.png")
    row = {
        "page": png.stem,
        "units": len(seg.units),
        "multi": [u.text for u in seg.units if u.kind == "multi"],
        "no_ink": [u.text for u in seg.units if u.kind == "no ink"],
        "unassigned": {r: sum(1 for x in seg.unassigned if x["reason"] == r) for r in sorted({x["reason"] for x in seg.unassigned})},
    }
    truth_dir = (truth_root / png.stem) if truth_root else None
    if truth_dir and (truth_dir / "truth.json").is_file():
        from .synth import scan_labels

        truth = json.loads((truth_dir / "truth.json").read_text(encoding="utf-8"))
        row["eval"] = evaluate(seg, ocr, labels, gray, truth, scan_labels(truth_dir, truth))
    return row


def segment_workspace(workspace: Path, truth_root: Path | None = None, jobs: int | None = None, force: bool = False,
                      on_page=None) -> dict:
    """Segment every page in <workspace>/pages using <workspace>/ocr; write <workspace>/segment.
    Pages run in parallel processes; a page whose result is newer than its inputs is reused."""
    import os
    from concurrent.futures import ProcessPoolExecutor

    workspace = Path(workspace)
    (workspace / "segment").mkdir(parents=True, exist_ok=True)
    pngs = sorted((workspace / "pages").glob("*.png"))
    jobs = max(1, jobs or (os.cpu_count() or 1))
    rows = []
    if jobs == 1 or len(pngs) < 2:
        for p in pngs:
            rows.append(_segment_one(p, workspace, truth_root, force))
            if on_page:
                on_page(len(rows), len(pngs))
    else:
        import multiprocessing

        # spawn, not fork: forking a process that has threads (OpenCV, BLAS) can deadlock.
        with ProcessPoolExecutor(max_workers=min(jobs, len(pngs)), mp_context=multiprocessing.get_context("spawn")) as pool:
            for row in pool.map(_segment_one, pngs, [workspace] * len(pngs), [truth_root] * len(pngs), [force] * len(pngs)):
                rows.append(row)
                if on_page:
                    on_page(len(rows), len(pngs))
    rows = [r for r in rows if r is not None]
    evals = [r["eval"] for r in rows if "eval" in r]
    summary = {"pages": rows}
    if evals:
        total = sum(e["truth_chars"] for e in evals)
        summary["eval"] = {"truth_chars": total, "owned": sum(e["owned"] for e in evals),
                           "owned_share": round(sum(e["owned"] for e in evals) / max(1, total), 4)}
    report.write_json(workspace / "segment" / "segment.json", summary)
    _write_html(summary, workspace / "segment" / "index.html")
    return summary


def _write_html(s: dict, path: Path) -> None:
    body = ["<p class=\"muted\">Overlay: ink coloured by glyph unit; <b style=\"color:#d228c8\">magenta boxes</b> are one blob "
            "standing for several characters (touching letters or ligatures); <b style=\"color:#e61e1e\">red ink</b> belongs to no "
            "character (specks, shadows, figures); <b style=\"color:#e61e1e\">red boxes</b> are characters with no ink found. "
            "Check that each letter is one colour and no two letters share a colour unless boxed.</p>"]
    if "eval" in s:
        e = s["eval"]
        body.append(f"<p><b>Against synthetic truth:</b> {e['owned']} of {e['truth_chars']} characters own the right ink "
                    f"({100 * e['owned_share']:.2f}%; Phase 1 target 98%).</p>")
    body.append(report.table(
        ["Page", "Units", "Multi-character units", "No-ink characters", "Unassigned components", "Owned (synthetic)", "Outcomes"],
        [[r["page"], r["units"], " ".join(r["multi"][:20]) + (" …" if len(r["multi"]) > 20 else ""),
          " ".join(r["no_ink"][:20]), ", ".join(f"{k}: {v}" for k, v in r["unassigned"].items()),
          f"{100 * r['eval']['owned_share']:.2f}%" if "eval" in r else "—",
          ", ".join(f"{k}: {v}" for k, v in r["eval"]["outcomes"].items()) if "eval" in r else ""] for r in s["pages"]],
        numeric={1, 5},
    ))
    body.append("<div class=\"row\">")
    for r in s["pages"]:
        body.append(f"<figure><a href=\"{r['page']}.overlay.png\"><img loading=\"lazy\" src=\"{r['page']}.overlay.png\" alt=\"\" style=\"max-height:520px\"></a>"
                    f"<figcaption>{r['page']}</figcaption></figure>")
    body.append("</div>")
    report.write_page(path, "Segmentation", "\n".join(body))
