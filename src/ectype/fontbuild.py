"""Phase 4: build one CID-keyed CFF font per style from the traced composites.

Each glyph cluster becomes a glyph whose CID is its rank by frequency (as Acrobat
orders its glyphs), and whose ToUnicode entry is its text, so ligature units map
to several characters. Glyph outlines come from tracing the composite; x-height is
fixed at 500 units of a 1000-unit em. Sidebearings are solved by least squares from
the ink gaps measured between neighbouring glyphs in words (gap = right sidebearing
of the first + left sidebearing of the second), with a weak prior toward equal
sides. Fonts are written as OpenType (validated with OTS, the sanitizer browsers
use) and embedded in PDFs as bare CID-keyed CFF, like Acrobat's.
"""

from __future__ import annotations

import io
import json
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from fontTools.cffLib import FDArrayIndex, FDSelect, FontDict
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from PIL import Image
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import lsqr

from . import report, trace
from .pdfwrite import EmbeddedFont, save_font

UNITS_PER_EM = 1000
XH_UNITS = 500  # x-height in font units: the font's "size" is twice the x-height
DEFAULT_SIDE = 0.06 * XH_UNITS


@dataclass
class BuiltGlyph:
    cid: int
    cluster: int
    text: str
    count: int
    advance: float
    lsb: float
    rsb: float
    ink: tuple[float, float, float, float] | None  # in font units, after the lsb shift


def solve_sidebearings(widths: dict[int, float], pairs: dict[str, dict]) -> tuple[dict[int, float], dict[int, float]]:
    """Left and right sidebearings (font units) per cluster from observed pair gaps."""
    ids = sorted(widths)
    index = {c: k for k, c in enumerate(ids)}
    n = len(ids)
    rows = []
    for key, obs in pairs.items():
        a, b = (int(v) for v in key.split(","))
        if a in index and b in index:
            rows.append((index[a], index[b], obs["median"] * XH_UNITS, np.sqrt(obs["count"])))
    m = len(rows) + 2 * n
    A = lil_matrix((m, 2 * n))  # unknowns: lsb_0..lsb_n-1, rsb_0..rsb_n-1
    y = np.zeros(m)
    for r, (i, j, gap, w) in enumerate(rows):
        A[r, n + i] = w  # rsb of the first
        A[r, j] = w  # lsb of the second
        y[r] = w * gap
    base = len(rows)
    for k in range(n):
        A[base + k, k] = 0.5  # lsb ~ rsb
        A[base + k, n + k] = -0.5
        A[base + n + k, k] = 0.05  # weak anchor so unobserved glyphs get a typical side
        A[base + n + k, n + k] = 0.05
        y[base + n + k] = 0.05 * 2 * DEFAULT_SIDE
    x = lsqr(A.tocsr(), y, atol=1e-10, btol=1e-10)[0]
    lo, hi = -0.3 * XH_UNITS, 0.6 * XH_UNITS
    lsb = {c: float(np.clip(x[index[c]], lo, hi)) for c in ids}
    rsb = {c: float(np.clip(x[n + index[c]], lo, hi)) for c in ids}
    return lsb, rsb


def _cid_cff(fb: FontBuilder, name: str, n: int) -> bytes:
    """Turn the font's CFF table into a CID-keyed one (ROS Adobe-Identity-0); return it bare."""
    cff = fb.font["CFF "].cff
    top = cff.topDictIndex[0]
    fd = FontDict()
    fd.Private = top.Private
    fd.FontName = name
    fdarray = FDArrayIndex()
    fdarray.append(fd)
    top.FDArray = fdarray
    select = FDSelect()
    select.format = 3
    select.gidArray = [0] * n
    top.FDSelect = select
    top.ROS = ("Adobe", "Identity", 0)
    top.CIDCount = n
    del top.Private
    for cs in top.CharStrings.values():
        cs.private = fd.Private
    out = io.BytesIO()
    cff.compile(out, fb.font)
    return out.getvalue()


def build_style_font(style: int, glyphs: list[dict], composites: Path, pairs: dict, key: str,
                     name: str, threshold: float = 0.45) -> tuple[EmbeddedFont, dict[int, int], list[BuiltGlyph], bytes]:
    """Trace a style's glyphs and build its font. Returns the font to embed, a map from
    cluster id to CID, the glyph table, and the OpenType file."""
    glyphs = sorted(glyphs, key=lambda g: -g["count"])
    traced, widths = {}, {}
    for g in glyphs:
        comp = 1 - np.asarray(Image.open(composites / g["composite"]), np.float32) / 255
        ink = comp >= threshold
        canvas = g["canvas"]
        scale = XH_UNITS / canvas["x_height"]
        if ink.any():
            contours = trace.trace_bitmap(ink)
            cols = np.nonzero(ink.any(axis=0))[0]
            contours = trace.to_font(contours, float(cols[0]), canvas["baseline_row"], scale,
                                     min_area=(0.04 * XH_UNITS) ** 2)
        else:
            contours = []
        box = trace.bounds(contours)
        traced[g["id"]] = (contours, box)
        widths[g["id"]] = (box[2] - box[0]) if box else 0.25 * XH_UNITS
    lsb, rsb = solve_sidebearings(widths, pairs)

    order = [".notdef", "cid00001"] + [f"cid{k + 2:05d}" for k in range(len(glyphs))]
    charstrings, metrics, built = {}, {}, []
    pen = T2CharStringPen(500, None)
    charstrings[".notdef"] = pen.getCharString()
    metrics[".notdef"] = (500, 0)
    space = round(0.5 * XH_UNITS)
    charstrings["cid00001"] = T2CharStringPen(space, None).getCharString()
    metrics["cid00001"] = (space, 0)
    cid_of = {}
    to_unicode = {1: " "}
    for k, g in enumerate(glyphs):
        cid = k + 2
        contours, box = traced[g["id"]]
        left = lsb[g["id"]]
        dx = left - (box[0] if box else 0.0)
        advance = max(1, round((left + widths[g["id"]] + rsb[g["id"]])))
        p = T2CharStringPen(advance, None)
        trace.draw(contours, p, dx)
        charstrings[order[cid]] = p.getCharString()
        metrics[order[cid]] = (advance, round(left))
        cid_of[g["id"]] = cid
        to_unicode[cid] = g["text"]
        ink = (box[0] + dx, box[1], box[2] + dx, box[3]) if box else None
        built.append(BuiltGlyph(cid, g["id"], g["text"], g["count"], advance, round(left, 1), round(rsb[g["id"]], 1),
                                tuple(round(v, 1) for v in ink) if ink else None))

    ps_name = name.replace(" ", "")
    fb = FontBuilder(UNITS_PER_EM, isTTF=False)
    fb.setupGlyphOrder(order)
    cmap = {}
    for b in built:  # the most frequent glyph of each single character gets its code point
        if len(b.text) == 1 and ord(b.text) not in cmap:
            cmap[ord(b.text)] = order[b.cid]
    cmap[0x20] = "cid00001"
    fb.setupCharacterMap(cmap)
    fb.setupCFF(ps_name, {"FullName": name}, charstrings, {})
    fb.setupHorizontalMetrics(metrics)
    inks = [b.ink for b in built if b.ink]
    ascent = round(max([i[3] for i in inks] + [2 * XH_UNITS]))
    descent = round(min([i[1] for i in inks] + [-0.5 * XH_UNITS]))
    fb.setupHorizontalHeader(ascent=ascent, descent=descent)
    fb.setupNameTable({"familyName": name, "styleName": "Regular"})
    caps = [b.ink[3] for b in built if b.ink and b.text in "HIEFLT"]
    fb.setupOS2(sTypoAscender=ascent, sTypoDescender=descent, usWinAscent=ascent, usWinDescent=-descent,
                sxHeight=XH_UNITS, sCapHeight=round(np.median(caps)) if caps else round(1.4 * XH_UNITS))
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)
    otf = buf.getvalue()
    bare = _cid_cff(fb, ps_name, len(order))
    xs = [i[0] for i in inks] + [0]
    embedded = EmbeddedFont(
        key=key, name=ps_name, program=bare, kind="cid-cff",
        widths={k: float(metrics[order[k]][0]) for k in range(len(order))},
        to_unicode=to_unicode, ascent=ascent, descent=descent,
        cap_height=round(np.median(caps)) if caps else 1.4 * XH_UNITS,
        bbox=(min(xs), descent, max([i[2] for i in inks] + [500]), ascent), flags=4,
    )
    return embedded, cid_of, built, otf


def validate_otf(path: Path) -> dict:
    """OTS (OpenType Sanitizer) verdict, plus a fontTools round trip."""
    from fontTools.ttLib import TTFont

    result = {"ots": None, "fonttools": None}
    try:
        import ots

        with tempfile.TemporaryDirectory() as tmp:
            r = ots.sanitize(str(path), str(Path(tmp) / "out.otf"), capture_output=True)
        result["ots"] = "pass" if r.returncode == 0 else (r.stderr.decode(errors="replace").strip()[-300:] or f"exit {r.returncode}")
    except ImportError:
        result["ots"] = "not installed"
    try:
        from fontTools.pens.boundsPen import BoundsPen

        font = TTFont(str(path))
        glyph_set = font.getGlyphSet()
        for name in font.getGlyphOrder():  # decompile and draw every glyph
            glyph_set[name].draw(BoundsPen(glyph_set))
        buf = io.BytesIO()
        font.save(buf)
        TTFont(io.BytesIO(buf.getvalue())).getGlyphSet()
        result["fonttools"] = "pass"
    except Exception as exc:  # report, don't hide
        result["fonttools"] = f"fail: {exc}"
    return result


def build_workspace_fonts(workspace: Path, family: str | None = None, min_instances: int = 1,
                          threshold: float = 0.45) -> dict:
    """Build every style's font from <workspace>/cluster into <workspace>/fonts."""
    workspace = Path(workspace)
    cl = json.loads((workspace / "cluster" / "clusters.json").read_text(encoding="utf-8"))
    out = workspace / "fonts"
    out.mkdir(parents=True, exist_ok=True)
    family = family or f"Ectype {workspace.name}"
    by_style = defaultdict(list)
    for g in cl["glyphs"]:
        if g["count"] >= min_instances:
            by_style[g["style"]].append(g)
    styles = []
    for style, gs in sorted(by_style.items()):
        name = f"{family} S{style}"
        font, cid_of, built, otf = build_style_font(style, gs, workspace / "cluster" / "composites", cl["pairs"],
                                                   key=f"T{style}", name=name, threshold=threshold)
        path = out / f"style-{style}.otf"
        path.write_bytes(otf)
        save_font(font, out / f"style-{style}")
        check = validate_otf(path)
        report.write_json(out / f"style-{style}.json", {
            "style": style, "name": font.name, "cid_of_cluster": {str(k): v for k, v in cid_of.items()},
            "glyphs": [asdict(b) for b in built], "validation": check,
        })
        styles.append({"style": style, "name": font.name, "glyphs": len(built), "otf_bytes": len(otf),
                       "cff_bytes": len(font.program), "validation": check})
    summary = {"family": family, "threshold": threshold, "styles": styles}
    report.write_json(out / "fonts.json", summary)
    _write_html(summary, out)
    return summary


def _write_html(s: dict, out: Path) -> None:
    from .analyze import glyph_sheet_svg, load_font_program

    body = [f"<p class=\"muted\">One CID-keyed CFF font per style, traced from the averaged composites. "
            "Validation: OTS (the OpenType sanitizer browsers use) and a fontTools round trip.</p>"]
    body.append(report.table(
        ["Style", "Font", "Glyphs", "CFF bytes", "OTS", "fontTools"],
        [[st["style"], st["name"], st["glyphs"], st["cff_bytes"], st["validation"]["ots"], st["validation"]["fonttools"]]
         for st in s["styles"]],
        numeric={0, 2, 3},
    ))
    for st in s["styles"]:
        data = (out / f"style-{st['style']}.otf").read_bytes()
        program = load_font_program(data, "OpenType")
        info = json.loads((out / f"style-{st['style']}.json").read_text(encoding="utf-8"))
        labels = {b["cid"]: b["text"] for b in info["glyphs"]}
        svg = glyph_sheet_svg(program, labels, f"{st['name']} ({st['glyphs']} glyphs)")
        sheet = out / f"style-{st['style']}-glyphs.svg"
        sheet.write_text(svg, encoding="utf-8")
        body.append(f"<h2>Style {st['style']}</h2><img class=\"sheet\" loading=\"lazy\" src=\"{sheet.name}\" alt=\"glyphs of style {st['style']}\">")
    report.write_page(out / "index.html", "Fonts", "\n".join(body))
