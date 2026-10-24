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
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

from . import report
from .ocr import OcrPage
from .preprocess import binarize


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
            for ch, cl in zip(word.chars, clustered[k]):
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
        expected = [widths.get(c.text, fallback) for c in chars]
        groups, skipped = _align(chars, clusters, line.x_size or x_size, speck, expected)
        groups = _join_dotless(groups, chars, clusters, stats)
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


def _clusters(comps: list[int], stats: np.ndarray, x_size: float, speck: int) -> list[dict]:
    """Group a word's components into left-to-right ink clusters.

    A smaller piece stacked above or below a larger one (the dot of an i or j, a colon,
    an accent) joins the one partner it overlaps most horizontally, so a dot can't chain
    an i to a neighbouring t. Tiny fragments within a pixel or two of a letter (thin
    serifs broken off by binarization) join that letter.
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
    reach = max(2, round(0.06 * x_size))
    group_size: dict[int, int] = {}
    for i in range(len(comps)):
        group_size[ds.find(i)] = group_size.get(ds.find(i), 0) + 1
    for a in range(len(comps)):
        if area[a] >= 3 * speck or group_size[ds.find(a)] > 1:
            continue  # only lone fragments; a stacked i-dot is already placed
        xa, ya, wa, ha, _ = stats[comps[a]]
        best, best_gap = None, reach + 1
        for b in range(len(comps)):
            if b == a or area[b] < 3 * speck:
                continue
            xb, yb, wb, hb, _ = stats[comps[b]]
            gap = max(max(xb - (xa + wa), xa - (xb + wb), 0), max(yb - (ya + ha), ya - (yb + hb), 0))
            if gap < best_gap:
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
    characters, with at most 1 - `min_precision` of its labelled ink belonging to others.
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
        for t in tids:
            verdict[t] = "ok" if ok else ("missing ink" if recall < min_recall else "takes other ink")

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


def segment_workspace(workspace: Path, truth_root: Path | None = None) -> dict:
    """Segment every page in <workspace>/pages using <workspace>/ocr; write <workspace>/segment."""
    workspace = Path(workspace)
    out = workspace / "segment"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for png in sorted((workspace / "pages").glob("*.png")):
        ocr_path = workspace / "ocr" / f"{png.stem}.ocr.json"
        if not ocr_path.is_file():
            continue
        import json

        ocr = OcrPage.from_json(json.loads(ocr_path.read_text(encoding="utf-8")))
        gray = np.asarray(Image.open(png).convert("L"))
        seg, labels = segment_page(gray, ocr)
        report.write_json(out / f"{png.stem}.seg.json", seg.to_json())
        cv2.imwrite(str(out / f"{png.stem}.labels.png"), labels.astype(np.uint16 if labels.max() < 65536 else np.uint32))
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
        rows.append(row)
    evals = [r["eval"] for r in rows if "eval" in r]
    summary = {"pages": rows}
    if evals:
        total = sum(e["truth_chars"] for e in evals)
        summary["eval"] = {"truth_chars": total, "owned": sum(e["owned"] for e in evals),
                           "owned_share": round(sum(e["owned"] for e in evals) / max(1, total), 4)}
    report.write_json(out / "segment.json", summary)
    _write_html(summary, out / "index.html")
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
