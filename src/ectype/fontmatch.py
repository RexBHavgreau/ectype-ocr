"""Choose installed fonts that look like the scan's, style by style.

Each glyph style from the cluster stage has averaged glyphs (composites) of its
common characters. Every installed font of the same slant draws the same
characters, scaled to the same x-height and placed by baseline and ink centroid
on the canvas the clusters use; the font whose letters correlate best with the
composites, on average, is that style's likely replacement. The comparison sees
serifs, stroke contrast, weight and letter widths.

The point size follows from the style's x-height and the font's: a replacement
with a smaller x-height is set larger, as a typesetter would. A horizontal scale
is measured too: how much wider or narrower the scanned words are than the
font sets them.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import cv2

from .cluster import ABOVE, BELOW, _corr_dist

RENDER_XH = 64  # px: candidate glyphs are drawn with this x-height
MAX_CHARS = 24
ROWS = 72  # comparison canvas height (the clusters' own 24 rows blur serifs away)
INK = 0.5  # composite coverage that counts as ink (as the fonts stage traces them)


def _feature(ink: np.ndarray, centroid_col: float, baseline_row: float, x_height: float, half: float = 1.2) -> np.ndarray:
    """A glyph's ink on a canvas scaled to its x-height, from ABOVE x-heights over the
    baseline to BELOW under it, centred on the ink centroid, blurred a little."""
    s = ROWS / ((ABOVE + BELOW) * x_height)
    cols = int(round(2 * half * x_height * s))
    m = np.array([[s, 0, -(centroid_col - half * x_height) * s],
                  [0, s, -(baseline_row - ABOVE * x_height) * s]], np.float32)
    out = cv2.warpAffine(ink.astype(np.float32), m, (cols, ROWS), flags=cv2.INTER_AREA)
    return cv2.GaussianBlur(out, (0, 0), 1.0).ravel()


@dataclass
class FontFace:
    family: str  # as layout applications list it (typographic family where the font has one)
    style: str  # e.g. "Regular", "Italic", "Bold Italic"
    path: str
    italic: bool
    weight: int
    units_per_em: int
    x_height: float  # font units

    @property
    def label(self) -> str:
        return f"{self.family} {self.style}".strip()


@dataclass
class StyleMatch:
    style: int
    italic: bool
    zone: str
    size_class: float
    instances: int
    bold: bool  # strokes clearly heavier than the body text's
    face: FontFace | None
    distance: float | None  # mean correlation distance of the face's letters to the composites
    point_size: float | None
    horizontal_scale: float  # 1.0 = as the font sets it
    runners_up: list[tuple[str, float]]  # (label, distance)

    def to_json(self) -> dict:
        d = asdict(self)
        d["face"] = asdict(self.face) if self.face else None
        return d


def _is_italic(font, style: str) -> bool:
    os2 = font["OS/2"] if "OS/2" in font else None
    if os2 is not None and os2.fsSelection & 1:
        return True
    if "post" in font and font["post"].italicAngle < -2:
        return True
    return any(k in style.lower() for k in ("italic", "oblique"))


@lru_cache(maxsize=1)
def installed_faces() -> list[FontFace]:
    """Static TrueType/OpenType faces with the Latin alphabet, from the font folders."""
    from fontTools.ttLib import TTFont

    from .resources import font_index

    faces, seen = [], set()
    for family, style, path in font_index():
        try:
            font = TTFont(str(path), lazy=True, fontNumber=0)
            if "fvar" in font:
                continue  # variable fonts: their instances aren't separate faces here
            cmap = font.getBestCmap() or {}
            if not all(ord(c) in cmap for c in "aeonstxHE"):
                continue
            upm = font["head"].unitsPerEm
            os2 = font["OS/2"] if "OS/2" in font else None
            xh = float(getattr(os2, "sxHeight", 0) or 0)
            if xh <= 0:
                glyf_name = cmap[ord("x")]
                if "glyf" in font:
                    g = font["glyf"][glyf_name]
                    xh = float(getattr(g, "yMax", 0) or 0)
                if xh <= 0:
                    xh = 0.5 * upm
            weight = int(getattr(os2, "usWeightClass", 400) or 400)
            key = (family.lower(), style.lower())
            if key in seen:
                continue
            seen.add(key)
            faces.append(FontFace(family, style, str(path), _is_italic(font, style), weight, upm, xh))
        except Exception:
            continue
    return faces


def _glyph_feature(font: ImageFont.FreeTypeFont, char: str, xh_px: float) -> np.ndarray | None:
    """A character drawn by the font on the cluster canvas (baseline, ink centroid, x-height)."""
    size = int(round(font.size))
    w, h = 4 * size, 3 * size
    img = Image.new("L", (w, h), 0)
    baseline = int(2 * size)
    ImageDraw.Draw(img).text((size, baseline), char, font=font, fill=255, anchor="ls")
    ink = np.asarray(img, np.float32) / 255.0 >= INK
    total = ink.sum()
    if total <= 0:
        return None
    cx = float((ink.sum(axis=0) * np.arange(w)).sum() / total) + 0.5
    return _feature(ink, cx, float(baseline), xh_px)


def _style_composites(workspace: Path) -> dict[int, dict]:
    """Per style: its slant, zone, size class, instance count, and the composite feature
    of each common character (its largest cluster)."""
    data = json.loads((workspace / "cluster" / "clusters.json").read_text(encoding="utf-8"))
    styles: dict[int, dict] = {}
    for g in data["glyphs"]:
        st = styles.setdefault(g["style"], {"italic": {}, "zone": {}, "size_class": {}, "instances": 0, "chars": {}})
        for key in ("italic", "zone", "size_class"):
            value = g.get(key, "")
            st[key][value] = st[key].get(value, 0) + g["count"]
        st["instances"] += g["count"]
        text = g["text"]
        if len(text) == 1 and text.isalnum() and g["count"] >= 3:
            if text not in st["chars"] or g["count"] > st["chars"][text][0]:
                st["chars"][text] = (g["count"], g)
    out = {}
    for sid, st in styles.items():
        chars = sorted(st["chars"].items(), key=lambda kv: -kv[1][0])[:MAX_CHARS]
        feats = {}
        for text, (count, g) in chars:
            img = np.asarray(Image.open(workspace / "cluster" / "composites" / g["composite"]).convert("L"), np.float32)
            ink = 1.0 - img / 255.0 >= INK
            c = g["canvas"]
            feats[text] = (count, _feature(ink, c["centroid_col"], c["baseline_row"], c["x_height"]), g["xh"],
                           float(ink.sum()) / c["x_height"] ** 2)  # ink per x-height square: stroke weight
        out[sid] = {k: max(st[k], key=st[k].get) for k in ("italic", "zone", "size_class")}
        out[sid].update(instances=st["instances"], features=feats)
    return out


def _distance(face: FontFace, feats: dict[str, tuple]) -> float | None:
    """How unlike the style's letters the face's are (None: it can't be drawn). A damaged
    font can open and then fail when a letter is drawn (FreeType's "invalid reference", one
    of Beaux's Windows fonts, stopped the IDML export): such a face is passed over."""
    from fontTools.ttLib import TTFont

    size = RENDER_XH * face.units_per_em / face.x_height
    try:
        font = ImageFont.truetype(face.path, size=max(8, int(round(size))))
        cmap = TTFont(face.path, lazy=True, fontNumber=0).getBestCmap() or {}
    except Exception:
        return None
    total, weight = 0.0, 0.0
    for text, (count, feat, *_) in feats.items():
        if ord(text) not in cmap:
            continue
        try:
            f = _glyph_feature(font, text, RENDER_XH * (int(round(size)) / size))
        except (OSError, ValueError):
            return None
        if f is None or f.shape != feat.shape:
            continue
        w = math.sqrt(count)
        total += w * _corr_dist(f, feat)
        weight += w
    return total / weight if weight else None


def _point_size(face: FontFace, xh_px: float, dpi: float) -> float:
    return round(xh_px / dpi * 72 * face.units_per_em / face.x_height, 2)


def _horizontal_scale(face: FontFace, words: list[tuple[str, float]], size_px: float) -> float:
    """Median ratio of scanned word width to the font's, for words of 4+ characters."""
    from fontTools.ttLib import TTFont

    if not words:
        return 1.0
    font = TTFont(face.path, lazy=True, fontNumber=0)
    cmap = font.getBestCmap() or {}
    hmtx = font["hmtx"]
    s = size_px / face.units_per_em
    ratios = []
    for text, width in words:
        names = [cmap.get(ord(c)) for c in text]
        if len(text) < 4 or None in names:
            continue
        adv = [hmtx[n] for n in names]
        # ink span: advances, less the first glyph's left and the last glyph's right sidebearing
        natural = sum(a for a, _ in adv) - adv[0][1]
        last_name = names[-1]
        if "glyf" in font:
            g = font["glyf"][last_name]
            if hasattr(g, "xMax"):
                natural -= adv[-1][0] - g.xMax
        if natural > 0:
            ratios.append(width / (natural * s))
    if len(ratios) < 5:
        return 1.0
    return float(np.clip(np.median(ratios), 0.8, 1.2))


def match_styles(workspace: Path, layout=None, faces: list[FontFace] | None = None,
                 progress=None) -> dict[int, StyleMatch]:
    """The likely replacement font of every glyph style, best first."""
    workspace = Path(workspace)
    styles = _style_composites(workspace)
    faces = installed_faces() if faces is None else faces
    words_by_style: dict[int, list[tuple[str, float]]] = {}
    dpi = 300.0
    if layout is not None:
        dpi = layout.text_dpi()
        for page in layout.pages:
            for block in page.blocks:
                for line in block.lines:
                    for w in line.words:
                        if w.style >= 0 and not any(w.superscript):
                            words_by_style.setdefault(w.style, []).append((w.text, w.box[2] - w.box[0]))
    # The body text (the largest upright text style) is taken to be a regular weight, and
    # matched first: other text styles (notes, italics) lean towards its family, as books
    # set them. Scanned ink spreads, so judging weight from the scan alone picks bold.
    order = sorted(styles, key=lambda sid: (styles[sid]["zone"] != "" or styles[sid]["italic"], -styles[sid]["instances"]))
    body = styles[order[0]] if order else None
    body_family = None
    out = {}
    for k, sid in enumerate(order):
        st = styles[sid]
        if progress:
            progress(f"style {sid}: {k + 1} of {len(styles)}")
        feats = st["features"]
        xh = float(np.median([v[2] for v in feats.values()])) if feats else None
        shared = [st["features"][c][3] / body["features"][c][3] for c in feats if c in body["features"]] if body else []
        bold = bool(shared) and float(np.median(shared)) > 1.25
        scored = []
        if len(feats) >= 4:
            pool = [f for f in faces if f.italic == st["italic"] and (f.weight >= 600) == bold] or \
                   [f for f in faces if f.italic == st["italic"]]
            for face in pool:
                d = _distance(face, feats)
                if d is None:
                    continue
                if body_family and st["zone"] == "" and face.family == body_family:
                    d *= 0.92
                scored.append((d, face))
        scored.sort(key=lambda t: t[0])
        if k == 0 and scored:
            body_family = scored[0][1].family
        best = scored[0][1] if scored else None
        size = _point_size(best, xh, dpi) if best and xh else None
        try:
            scale = _horizontal_scale(best, words_by_style.get(sid, []), xh * best.units_per_em / best.x_height) if best and xh else 1.0
        except Exception:
            scale = 1.0  # a font whose tables can't be read: its natural width
        out[sid] = StyleMatch(sid, st["italic"], st["zone"], st["size_class"], st["instances"], bold, best,
                              round(scored[0][0], 4) if scored else None, size, round(scale, 3),
                              [(f.label, round(d, 4)) for d, f in scored[1:6]])
    return out


def write_matches(path: Path, matches: dict[int, StyleMatch]) -> None:
    from . import report

    report.write_json(path, {str(k): m.to_json() for k, m in sorted(matches.items())})
