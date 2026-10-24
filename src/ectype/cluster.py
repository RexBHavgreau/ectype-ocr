"""Phase 3: group every instance of each glyph by style, and average each group.

1. Collect: every glyph unit with ink becomes an instance, its ink cut out of the
   page (neighbours masked off) as coverage (0 = paper, 1 = ink), positioned by its
   line's fitted baseline and its own ink centroid, and scaled by the line's
   x-height, so size classes (body, footnotes, headings) are kept apart.
2. Cluster: within each (text, size class), instances are grouped by shape with
   average-linkage clustering on correlation distance; tiny groups join their
   nearest large one when close enough, else stay as rare glyphs.
3. Styles: clusters whose letters occur together in words are joined, so the
   roman a, e, n... form one style and the italic ones another.
4. Average: each cluster's instances are upsampled 4x, aligned on the baseline and
   centroid, refined by phase correlation against the running mean, and averaged.
   The composite is grayscale coverage; thresholding it at one half gives the glyph.

Pair gaps between neighbouring glyphs (for advance widths and sidebearings in
Phase 4) are recorded per cluster pair.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

from . import report
from .ocr import OcrPage
from .segment import Segmentation, fit_baselines, line_x_heights

UPSAMPLE = 4
FEATURE_ROWS = 24
ABOVE, BELOW = 1.9, 0.75  # canvas extent above/below the baseline, in x-heights


@dataclass
class Instance:
    page: str
    unit: int
    text: str
    line: int
    word: int
    box: list[int]
    baseline: float  # page y of the baseline under this glyph
    centroid_x: float  # page x of the ink centroid
    xh: float  # x-height of its line, px
    size_class: float
    italic: bool
    conf: float
    crop: np.ndarray = field(repr=False)  # coverage, float32, 0..1
    crop_origin: tuple[int, int] = (0, 0)  # page x, y of crop[0, 0]
    cluster: int = -1


@dataclass
class Glyph:
    id: int
    text: str
    size_class: float
    italic: bool
    style: int
    count: int
    xh: float  # median line x-height of its instances, px
    composite: str  # file name under composites/
    canvas: dict  # composite geometry at UPSAMPLE: baseline_row, centroid_col, width, height
    ink: list[float] | None  # composite ink box after thresholding, in x-heights from (centroid, baseline)
    instances: list[list] = field(default_factory=list)  # [page, unit]


def _size_class(ratio: float) -> float:
    return 1.0 if abs(ratio - 1) < 0.12 else round(round(ratio / 0.1) * 0.1, 1)


def collect(workspace: Path) -> list[Instance]:
    """Instances of every glyph unit with ink, across all pages of a workspace."""
    workspace = Path(workspace)
    instances: list[Instance] = []
    page_data = []
    for png in sorted((workspace / "pages").glob("*.png")):
        seg_path = workspace / "segment" / f"{png.stem}.seg.json"
        ocr_path = workspace / "ocr" / f"{png.stem}.ocr.json"
        if not seg_path.is_file() or not ocr_path.is_file():
            continue
        seg = Segmentation.from_json(json.loads(seg_path.read_text(encoding="utf-8")))
        if not seg.units:
            continue
        ocr = OcrPage.from_json(json.loads(ocr_path.read_text(encoding="utf-8")))
        page_data.append((png, seg, ocr))
    if not page_data:
        return instances
    doc_xh = float(np.median([line_x_heights(seg)[1] for _, seg, _ in page_data]))

    kernel = np.ones((3, 3), np.uint8)
    for png, seg, ocr in page_data:
        gray = np.asarray(Image.open(png).convert("L"))
        labels = cv2.imread(str(workspace / "segment" / f"{png.stem}.labels.png"), cv2.IMREAD_UNCHANGED).astype(np.int32)
        paper = float(np.percentile(gray, 90))
        text = labels > 0
        ink_level = float(np.percentile(gray[text], 5)) if text.any() else 0.0
        coverage = np.clip((paper - gray.astype(np.float32)) / max(1.0, paper - ink_level), 0, 1)
        base = fit_baselines(seg, ocr)
        xhs, page_xh = line_x_heights(seg)
        italic = word_italics(seg, labels)
        h, w = gray.shape
        for u in seg.units:
            if not u.box or not u.components:
                continue
            x0, y0, x1, y1 = u.box
            xa, ya, xb, yb = max(0, x0 - 3), max(0, y0 - 3), min(w, x1 + 3), min(h, y1 + 3)
            mask = cv2.dilate(np.isin(labels[ya:yb, xa:xb], u.components).astype(np.uint8), kernel, iterations=2)
            crop = coverage[ya:yb, xa:xb] * mask
            total = float(crop.sum())
            if total <= 0:
                continue
            cx = xa + float((crop.sum(axis=0) * np.arange(crop.shape[1])).sum()) / total + 0.5
            a, b = base.get(u.line, (y1, 0.0))
            xh = xhs.get(u.line, page_xh)
            instances.append(Instance(
                page=png.stem, unit=u.id, text=u.text, line=u.line, word=u.word, box=list(u.box),
                baseline=a + b * cx, centroid_x=cx, xh=xh, size_class=_size_class(xh / doc_xh),
                italic=italic.get((u.line, u.word), False), conf=u.conf, crop=crop.astype(np.float32), crop_origin=(xa, ya),
            ))
    return instances


def _feature(inst: Instance, half_width: float) -> np.ndarray:
    """The instance on a canvas scaled to its x-height: rows from ABOVE x-heights over
    the baseline to BELOW under it, columns centred on the ink centroid."""
    s = FEATURE_ROWS / ((ABOVE + BELOW) * inst.xh)
    cols = max(4, int(round(2 * half_width * inst.xh * s)))
    ox, oy = inst.crop_origin
    m = np.array([[s, 0, (ox - (inst.centroid_x - half_width * inst.xh)) * s],
                  [0, s, (oy - (inst.baseline - ABOVE * inst.xh)) * s]], np.float32)
    src = cv2.GaussianBlur(inst.crop, (0, 0), max(0.3, 0.5 / s))
    return cv2.warpAffine(src, m, (cols, FEATURE_ROWS), flags=cv2.INTER_LINEAR).ravel()


def cluster_instances(instances: list[Instance], threshold: float = 0.25, min_size: int = 3,
                      max_linkage: int = 3000, seed: int = 0) -> dict[tuple, list[list[int]]]:
    """Group instances by (text, size class), then by shape. Returns the groups as lists of
    indices into `instances` and sets each instance's `cluster`."""
    rng = np.random.default_rng(seed)
    by_key: dict[tuple, list[int]] = defaultdict(list)
    for i, inst in enumerate(instances):
        by_key[(inst.text, inst.size_class, inst.italic)].append(i)
    groups: dict[tuple, list[list[int]]] = {}
    next_id = 0
    for key, idx in sorted(by_key.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])):
        widths = [(instances[i].box[2] - instances[i].box[0]) / instances[i].xh for i in idx]
        half = float(np.percentile(widths, 95)) / 2 + 0.25
        feats = np.stack([_feature(instances[i], half) for i in idx])
        if len(idx) == 1:
            labels = np.array([1])
        else:
            sample = np.arange(len(idx)) if len(idx) <= max_linkage else rng.choice(len(idx), max_linkage, replace=False)
            # A feature with no variance (ink off the canvas) has no correlation: treat as far.
            d = np.nan_to_num(pdist(feats[sample], metric="correlation"), nan=1.0, posinf=1.0)
            z = linkage(d, method="average")
            sample_labels = fcluster(z, t=threshold, criterion="distance")
            labels = np.zeros(len(idx), int)
            labels[sample] = sample_labels
            if len(sample) < len(idx):
                cents = {c: feats[sample][sample_labels == c].mean(axis=0) for c in np.unique(sample_labels)}
                rest = np.setdiff1d(np.arange(len(idx)), sample)
                for r in rest:
                    labels[r] = min(cents, key=lambda c: _corr_dist(feats[r], cents[c]))
        # Small groups join the nearest large group if close enough; otherwise stay rare.
        members = {c: np.nonzero(labels == c)[0] for c in np.unique(labels)}
        big = {c: m for c, m in members.items() if len(m) >= min_size}
        if big:
            cents = {c: feats[m].mean(axis=0) for c, m in big.items()}
            for c, m in members.items():
                if c in big:
                    continue
                cent = feats[m].mean(axis=0)
                best = min(cents, key=lambda k: _corr_dist(cent, cents[k]))
                if _corr_dist(cent, cents[best]) < 1.6 * threshold:
                    labels[m] = best
        out = []
        for c in sorted(np.unique(labels), key=lambda c: -(labels == c).sum()):
            members_idx = [idx[k] for k in np.nonzero(labels == c)[0]]
            for i in members_idx:
                instances[i].cluster = next_id
            out.append(members_idx)
            next_id += 1
        groups[key] = out
    return groups


def _corr_dist(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return 1.0 - float(a @ b) / d if d > 0 else 1.0


def assign_styles(instances: list[Instance], min_links: int = 2) -> dict[int, int]:
    """Join letter clusters that occur together in words into styles, only within one
    size class and slant (so a stray link can't merge italic into roman). Clusters of
    punctuation and digits, and letters that never share a word with another (the J of
    "J."), join the style they most often share a word or line with."""
    info = {inst.cluster: (inst.size_class, inst.italic, inst.text.isalpha()) for inst in instances}
    count: dict[int, int] = defaultdict(int)
    for inst in instances:
        count[inst.cluster] += 1
    parent = {c: c for c in info}

    def find(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    links: dict[tuple[int, int], int] = defaultdict(int)
    by_word: dict[tuple, list[int]] = defaultdict(list)
    by_line: dict[tuple, list[int]] = defaultdict(list)
    for inst in instances:
        by_word[(inst.page, inst.line, inst.word)].append(inst.cluster)
        by_line[(inst.page, inst.line)].append(inst.cluster)
    for clusters in by_word.values():
        cs = sorted({c for c in clusters if info[c][2]})
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                links[(cs[i], cs[j])] += 1
    linked = set()
    for (a, b), n in links.items():
        # Rare letters (a chapter title's) can't co-occur twice; they need only as many
        # shared words as they have instances.
        if n >= min(min_links, count[a], count[b]) and info[a][:2] == info[b][:2]:
            parent[find(a)] = find(b)
            linked.update((a, b))

    # Punctuation and digits: the letter style they share words with most, else lines.
    for c, (sc, it, alpha) in info.items():
        if alpha and c in linked:
            continue  # letters linked through words already have a style; the rest vote
        votes: dict[int, int] = defaultdict(int)
        for same_slant in (True, False):
            for scope in (by_word, by_line):
                for clusters in scope.values():
                    if c in clusters:
                        for o in clusters:
                            if o != c and o in linked and info[o][0] == sc and (info[o][1] == it or not same_slant):
                                votes[find(o)] += 1
                if votes:
                    break
            if votes:
                break
        if votes:
            parent[find(c)] = max(votes, key=votes.get)

    sizes: dict[int, int] = defaultdict(int)
    for inst in instances:
        sizes[find(inst.cluster)] += 1
    order = {r: k for k, r in enumerate(sorted(sizes, key=lambda r: -sizes[r]))}
    return {c: order[find(c)] for c in info}


def word_slant(mask: np.ndarray, baseline_row: float) -> float:
    """Slant of a word's strokes in degrees (italic positive): the horizontal shear
    that makes the column profile of its ink sharpest."""
    h, w = mask.shape
    m = mask.astype(np.float32)
    best, best_score = 0.0, -1.0
    for angle in range(-6, 27, 2):
        t = math.tan(math.radians(angle))
        shear = np.array([[1, t, -t * baseline_row], [0, 1, 0]], np.float32)
        out_w = w + int(abs(t) * h) + 2
        if t < 0:
            shear[0, 2] += abs(t) * h
        cols = cv2.warpAffine(m, shear, (out_w, h)).sum(axis=0)
        score = float((cols ** 2).sum())
        if score > best_score:
            best, best_score = float(angle), score
    return best


def word_italics(seg: Segmentation, labels: np.ndarray, low: float = 7.0, high: float = 18.0) -> dict[tuple[int, int], bool]:
    """Italic flag per (line, word) from the word's measured slant. Book italics lean
    about 8-15 degrees (the Young chapter's measure 8-12); steeper readings come from
    words made mostly of diagonals ("very", "way": 22-24), so only low..high counts.
    Words of one or two letters are too short to measure and follow their neighbours."""
    by_word: dict[tuple[int, int], list] = defaultdict(list)
    for u in seg.units:
        if u.box and u.components:
            by_word[(u.line, u.word)].append(u)
    slant: dict[tuple[int, int], float | None] = {}
    for key, units in by_word.items():
        letters = [u for u in units if u.text.isalnum()]
        if sum(len(u.text) for u in letters) < 3:
            # Too short to measure. Short words with letters follow their neighbours;
            # anything else (a lone figure, a folio) stays upright.
            slant[key] = None if any(u.text.isalpha() for u in letters) else 0.0
            continue
        x0 = min(u.box[0] for u in letters)
        y0 = min(u.box[1] for u in letters)
        x1 = max(u.box[2] for u in letters)
        y1 = max(u.box[3] for u in letters)
        comps = [c for u in letters for c in u.components]
        mask = np.isin(labels[y0:y1, x0:x1], comps)
        slant[key] = word_slant(mask, float(np.median([u.baseline_y for u in letters])) - y0)
    flags: dict[tuple[int, int], bool] = {}
    for (li, wi), a in slant.items():
        if a is not None:
            flags[(li, wi)] = low <= a <= high
    for (li, wi), a in slant.items():
        if a is None:
            prev = next((flags[(li, k)] for k in range(wi - 1, -1, -1) if (li, k) in flags), None)
            nxt = next((flags[(li, k)] for k in range(wi + 1, wi + 40) if (li, k) in flags), None)
            known = [v for v in (prev, nxt) if v is not None]
            flags[(li, wi)] = known[0] if known and all(v == known[0] for v in known) else False
    return flags


def average(members: list[Instance], iterations: int = 2, max_instances: int = 400, seed: int = 0) -> tuple[np.ndarray, dict]:
    """Composite of a cluster at UPSAMPLE: instances placed by baseline and centroid,
    then each refined by phase correlation against the running mean."""
    rng = np.random.default_rng(seed)
    if len(members) > max_instances:
        members = [members[i] for i in rng.choice(len(members), max_instances, replace=False)]
    xh = float(np.median([m.xh for m in members]))
    half = max((m.box[2] - m.box[0]) for m in members) / 2 + 0.4 * xh
    above = max(m.baseline - m.box[1] for m in members) + 0.3 * xh
    below = max(m.box[3] - m.baseline for m in members) + 0.3 * xh
    W = int(math.ceil(2 * half * UPSAMPLE))
    H = int(math.ceil((above + below) * UPSAMPLE))
    centroid_col, baseline_row = half * UPSAMPLE, above * UPSAMPLE

    def place(m: Instance, dx: float = 0.0, dy: float = 0.0) -> np.ndarray:
        ox, oy = m.crop_origin
        s = UPSAMPLE * xh / m.xh  # normalise each instance to the cluster's x-height
        t = np.array([[s, 0, centroid_col - (m.centroid_x - ox) * s + dx],
                      [0, s, baseline_row - (m.baseline - oy) * s + dy]], np.float32)
        return cv2.warpAffine(m.crop, t, (W, H), flags=cv2.INTER_CUBIC)

    shifts = [(0.0, 0.0)] * len(members)
    placed = [place(m) for m in members]
    mean = np.mean(placed, axis=0)
    limit = 1.5 * UPSAMPLE
    window = cv2.createHanningWindow((W, H), cv2.CV_32F)
    for _ in range(iterations):
        new = []
        for k, m in enumerate(members):
            (sx, sy), _ = cv2.phaseCorrelate(placed[k].astype(np.float32), mean.astype(np.float32), window)
            sx, sy = float(np.clip(sx, -limit, limit)), float(np.clip(sy, -limit, limit))
            dx, dy = shifts[k][0] + sx, shifts[k][1] + sy
            shifts[k] = (dx, dy)
            new.append(place(m, dx, dy))
        placed = new
        mean = np.mean(placed, axis=0)
    composite = np.clip(mean, 0, 1)
    return composite, {"baseline_row": baseline_row, "centroid_col": centroid_col, "width": W, "height": H,
                       "x_height": xh * UPSAMPLE, "instances_used": len(members),
                       "mean_shift_px": round(float(np.mean([math.hypot(*s) for s in shifts])) / UPSAMPLE, 3)}


def composite_feature(composite: np.ndarray, canvas: dict, half: float = 1.0) -> np.ndarray:
    """A composite on the same normalised canvas as instance features (x-height units)."""
    u = canvas["x_height"]
    s = FEATURE_ROWS / ((ABOVE + BELOW) * u)
    cols = max(4, int(round(2 * half * u * s)))
    m = np.array([[s, 0, -(canvas["centroid_col"] - half * u) * s],
                  [0, s, -(canvas["baseline_row"] - ABOVE * u) * s]], np.float32)
    src = cv2.GaussianBlur(composite.astype(np.float32), (0, 0), max(0.3, 0.5 / s))
    return cv2.warpAffine(src, m, (cols, FEATURE_ROWS), flags=cv2.INTER_LINEAR).ravel()


def find_suspects(glyphs: list, features: dict[int, np.ndarray], min_size: int = 3) -> list[dict]:
    """Glyphs, other than a character's main one in its style, whose shape matches another
    character's main glyph better than their own: likely OCR misreadings (right shape,
    wrong text), such as "Tbid." for "Ibid." or a comma read as a period."""
    main: dict[tuple[int, str], object] = {}
    for g in glyphs:
        key = (g.style, g.text)
        if key not in main or g.count > main[key].count:
            main[key] = g
    out = []
    for g in glyphs:
        own = main.get((g.style, g.text))
        if own is None or (own.id == g.id and g.count >= min_size):
            continue
        # A character's only glyph, if rare, is checked against the others too.
        own_d = _corr_dist(features[g.id], features[own.id]) if own.id != g.id else 1.0
        best, best_d = None, own_d
        for (st, text), other in main.items():
            if st != g.style or text == g.text or other.count < min_size:
                continue
            d = _corr_dist(features[g.id], features[other.id])
            if d < best_d:
                best, best_d = other, d
        if best is not None and best_d < 0.15:  # looser cutoffs flagged many sound words on the Young chapter
            out.append({"glyph": g.id, "text": g.text, "looks_like": best.text, "distance": round(best_d, 3),
                        "count": g.count, "instances": g.instances})
    return out


def pair_gaps(instances: list[Instance]) -> dict[str, dict]:
    """Ink gaps between neighbouring glyphs in a word, per cluster pair, in x-heights."""
    by_word: dict[tuple, list[Instance]] = defaultdict(list)
    for inst in instances:
        by_word[(inst.page, inst.line, inst.word)].append(inst)
    gaps: dict[tuple[int, int], list[float]] = defaultdict(list)
    for word in by_word.values():
        word.sort(key=lambda i: i.box[0])
        for a, b in zip(word, word[1:]):
            gaps[(a.cluster, b.cluster)].append((b.box[0] - a.box[2]) / a.xh)
    return {f"{a},{b}": {"median": round(float(np.median(v)), 4), "count": len(v)} for (a, b), v in gaps.items()}


def cluster_workspace(workspace: Path, threshold: float = 0.25) -> dict:
    workspace = Path(workspace)
    out = workspace / "cluster"
    comp_dir = out / "composites"
    comp_dir.mkdir(parents=True, exist_ok=True)
    instances = collect(workspace)
    groups = cluster_instances(instances, threshold=threshold)
    style_of = assign_styles(instances)
    by_cluster: dict[int, list[Instance]] = defaultdict(list)
    for inst in instances:
        by_cluster[inst.cluster].append(inst)

    glyphs: list[Glyph] = []
    feats: dict[int, np.ndarray] = {}
    for (text, size_class, is_italic), clusters in groups.items():
        for members_idx in clusters:
            cid = instances[members_idx[0]].cluster
            members = by_cluster[cid]
            composite, canvas = average(members)
            feats[cid] = composite_feature(composite, canvas)
            name = f"g{cid:05d}.png"
            Image.fromarray((255 - composite * 255).astype(np.uint8)).save(comp_dir / name)
            ink = composite >= 0.5
            if ink.any():
                ys, xs = np.nonzero(ink)
                u = canvas["x_height"]
                ink_box = [round((xs.min() - canvas["centroid_col"]) / u, 4), round((canvas["baseline_row"] - ys.max() - 1) / u, 4),
                           round((xs.max() + 1 - canvas["centroid_col"]) / u, 4), round((canvas["baseline_row"] - ys.min()) / u, 4)]
            else:
                ink_box = None
            glyphs.append(Glyph(
                id=cid, text=text, size_class=size_class, italic=is_italic, style=style_of[cid], count=len(members),
                xh=round(float(np.median([m.xh for m in members])), 2), composite=name, canvas=canvas, ink=ink_box,
                instances=[[m.page, m.unit] for m in members],
            ))

    summary = {
        "threshold": threshold,
        "instances": len(instances),
        "glyphs": [vars(g) for g in sorted(glyphs, key=lambda g: (g.style, g.size_class, g.text, -g.count))],
        "styles": _style_summary(glyphs),
        "suspects": find_suspects(glyphs, feats),
        "pairs": pair_gaps(instances),
    }
    report.write_json(out / "clusters.json", summary)
    _write_sheets(summary, out)
    return summary


def _style_summary(glyphs: list[Glyph]) -> list[dict]:
    styles = defaultdict(list)
    for g in glyphs:
        styles[g.style].append(g)
    out = []
    for s, gs in sorted(styles.items()):
        per_key = defaultdict(int)
        per_key_big = defaultdict(int)
        for g in gs:
            per_key[(g.text, g.size_class)] += 1
            if g.count >= 3:
                per_key_big[(g.text, g.size_class)] += 1
        out.append({
            "style": s,
            "glyphs": len(gs),
            "instances": sum(g.count for g in gs),
            "size_classes": sorted({g.size_class for g in gs}),
            "italic": sorted({g.italic for g in gs}),
            "duplicates": {f"{t} @{sc}": n for (t, sc), n in sorted(per_key.items()) if n > 1},
            "duplicates_with_3_plus": {f"{t} @{sc}": n for (t, sc), n in sorted(per_key_big.items()) if n > 1},
            "sample": "".join(sorted({g.text for g in gs if len(g.text) == 1 and g.text.isalpha()}))[:60],
        })
    return out


def _write_sheets(s: dict, out: Path, cell: int = 72, columns: int = 16) -> None:
    by_style = defaultdict(list)
    for g in s["glyphs"]:
        by_style[g["style"]].append(g)
    body = [f"<p class=\"muted\">{s['instances']} glyph instances → {len(s['glyphs'])} averaged glyphs in {len(s['styles'])} styles "
            f"(shape threshold {s['threshold']}). Each cell is one composite: the average of all its instances, at 4× resolution; "
            "the number is how many instances it averages. Check that each style has one glyph per character and that italic and "
            "roman are in different styles.</p>"]
    body.append(report.table(
        ["Style", "Glyphs", "Instances", "Size classes", "Slant", "Letters", "Characters with more than one glyph of 3+ instances"],
        [[st["style"], st["glyphs"], st["instances"], " ".join(map(str, st["size_classes"])),
          "/".join("italic" if i else "upright" for i in st["italic"]), st["sample"],
          ", ".join(f"{k}×{n}" for k, n in list(st["duplicates_with_3_plus"].items())[:25])] for st in s["styles"]],
        numeric={0, 1, 2},
    ))
    if s["suspects"]:
        body.append("<h2>Probable OCR misreadings</h2><p class=\"muted\">Small glyph groups whose shape matches another "
                    "character's glyph better than their own text's. Check these words in the OCR report.</p>")
        body.append(report.table(
            ["Glyph", "OCR text", "Looks like", "Distance", "Instances", "Where (page, unit)"],
            [[x["glyph"], x["text"], x["looks_like"], x["distance"], x["count"],
              " ".join(f"{p}#{u}" for p, u in x["instances"][:6])] for x in s["suspects"]],
            numeric={0, 3, 4},
        ))
    for style, gs in sorted(by_style.items()):
        rows = math.ceil(len(gs) / columns)
        sheet = Image.new("L", (columns * cell, rows * cell), 255)
        draw = ImageDraw.Draw(sheet)
        for k, g in enumerate(gs):
            img = Image.open(out / "composites" / g["composite"])
            img.thumbnail((cell - 6, cell - 18))
            x, y = (k % columns) * cell, (k // columns) * cell
            sheet.paste(img, (x + (cell - img.width) // 2, y + 2))
            draw.text((x + 3, y + cell - 13), f"{g['text']} {g['count']}", fill=90)
            draw.rectangle((x, y, x + cell - 1, y + cell - 1), outline=215)
        name = f"style-{style}.png"
        sheet.save(out / name)
        body.append(f"<h2>Style {style}</h2><img class=\"sheet\" src=\"{name}\" alt=\"glyph sheet for style {style}\">")
    report.write_page(out / "index.html", "Glyph clusters", "\n".join(body))
