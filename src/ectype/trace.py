"""Trace an averaged glyph composite into closed Bezier contours.

vtracer (MIT) does the curve fitting on the thresholded 4x composite. Its SVG is
parsed into contours, specks are dropped, coordinates are moved into font units
relative to the baseline, and winding is fixed for CFF: outer contours run
counter-clockwise (y up), holes clockwise.
"""

from __future__ import annotations

import io
import re

import numpy as np
import vtracer
from PIL import Image

# A segment is ("L", end) or ("C", c1, c2, end); a contour is (start, [segments]).
Point = tuple[float, float]


def trace_bitmap(ink: np.ndarray, speckle: int = 8) -> list[tuple[Point, list]]:
    """Contours of a binary image (True = ink) in its pixel coordinates (y down)."""
    rgb = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8)).convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    svg = vtracer.convert_raw_image_to_svg(
        buf.getvalue(), img_format="png", colormode="binary", mode="spline",
        filter_speckle=speckle, corner_threshold=60, length_threshold=4.0, splice_threshold=45, path_precision=2,
    )
    contours = []
    for d, tx, ty in re.findall(r'<path d="([^"]*)"[^>]*?transform="translate\(([-\d.]+),([-\d.]+)\)"', svg):
        contours += _parse_path(d, float(tx), float(ty))
    return contours


def _parse_path(d: str, tx: float, ty: float) -> list[tuple[Point, list]]:
    tokens = re.findall(r"[MLCZmlcz]|-?\d*\.?\d+(?:e-?\d+)?", d)
    out, start, segs, cur, i, cmd = [], None, [], None, 0, None
    def num():
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                if start is not None:
                    if cur != start:
                        segs.append(("L", start))
                    out.append((start, segs))
                start, segs, cur = None, [], None
            continue
        if cmd == "M":
            if start is not None and segs:
                out.append((start, segs))
            start = cur = (num() + tx, num() + ty)
            segs = []
            cmd = "L"  # further pairs after M are lines
        elif cmd == "L":
            cur = (num() + tx, num() + ty)
            segs.append(("L", cur))
        elif cmd == "C":
            c1 = (num() + tx, num() + ty)
            c2 = (num() + tx, num() + ty)
            cur = (num() + tx, num() + ty)
            segs.append(("C", c1, c2, cur))
        else:
            raise ValueError(f"unsupported path command {cmd!r} from vtracer")
    if start is not None and segs:
        out.append((start, segs))
    return out


def _polygon(contour: tuple[Point, list], steps: int = 4) -> np.ndarray:
    start, segs = contour
    pts = [start]
    cur = start
    for seg in segs:
        if seg[0] == "L":
            pts.append(seg[1])
        else:
            c1, c2, end = seg[1], seg[2], seg[3]
            for k in range(1, steps + 1):
                t = k / steps
                mt = 1 - t
                pts.append((mt ** 3 * cur[0] + 3 * mt * mt * t * c1[0] + 3 * mt * t * t * c2[0] + t ** 3 * end[0],
                            mt ** 3 * cur[1] + 3 * mt * mt * t * c1[1] + 3 * mt * t * t * c2[1] + t ** 3 * end[1]))
        cur = seg[-1]
    return np.array(pts, float)


def _area(poly: np.ndarray) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _inside(p: Point, poly: np.ndarray) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    for k in range(n):
        x1, y1 = poly[k]
        x2, y2 = poly[(k + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _reverse(contour: tuple[Point, list]) -> tuple[Point, list]:
    start, segs = contour
    points = [start] + [s[-1] for s in segs]
    out = []
    for k in range(len(segs) - 1, -1, -1):
        seg = segs[k]
        begin = points[k]
        out.append(("L", begin) if seg[0] == "L" else ("C", seg[2], seg[1], begin))
    return points[-1], out


def to_font(contours: list[tuple[Point, list]], origin_x: float, baseline_row: float, scale: float,
            min_area: float = 0.0) -> list[tuple[Point, list]]:
    """Move contours from composite pixels (y down) to font units (y up) with x measured
    from origin_x and y from the baseline; drop specks; fix winding for CFF."""
    def tf(p):
        return ((p[0] - origin_x) * scale, (baseline_row - p[1]) * scale)

    moved = []
    for start, segs in contours:
        new = [("L", tf(s[1])) if s[0] == "L" else ("C", tf(s[1]), tf(s[2]), tf(s[3])) for s in segs]
        moved.append((tf(start), new))
    polys = [_polygon(c) for c in moved]
    keep = [k for k, p in enumerate(polys) if abs(_area(p)) >= min_area]
    moved = [moved[k] for k in keep]
    polys = [polys[k] for k in keep]
    fixed = []
    for k, (c, poly) in enumerate(zip(moved, polys)):
        depth = sum(1 for j, other in enumerate(polys) if j != k and _inside(tuple(poly[0]), other))
        ccw = _area(poly) > 0
        want_ccw = depth % 2 == 0
        fixed.append(c if ccw == want_ccw else _reverse(c))
    return fixed


def bounds(contours: list[tuple[Point, list]]) -> tuple[float, float, float, float] | None:
    if not contours:
        return None
    pts = np.vstack([_polygon(c, steps=8) for c in contours])
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def draw(contours: list[tuple[Point, list]], pen, dx: float = 0.0) -> None:
    """Draw contours into a fontTools pen, shifted right by dx, on integer coordinates."""
    def r(p):
        return (round(p[0] + dx), round(p[1]))

    for start, segs in contours:
        pen.moveTo(r(start))
        for seg in segs:
            if seg[0] == "L":
                pen.lineTo(r(seg[1]))
            else:
                pen.curveTo(r(seg[1]), r(seg[2]), r(seg[3]))
        pen.closePath()
