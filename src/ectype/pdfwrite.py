"""Write pages of positioned glyph runs over background images, with embedded fonts.

Used by both output modes: substitute mode passes a subset of a real typeface,
traced mode passes the fonts synthesized from the scan. Fonts are embedded once
per document as Type0 / Identity-H with a ToUnicode CMap, so text is searchable
and copies out. Coordinates arrive in scan pixels with a top-left origin and are
converted to PDF points here, in one place.
"""

from __future__ import annotations

import io
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pikepdf
from fontTools.ttLib import TTFont
from PIL import Image


@dataclass
class EmbeddedFont:
    """A font program ready to embed, addressed by CID (= glyph index)."""

    key: str  # resource name, e.g. "F1"
    name: str  # BaseFont
    program: bytes  # TrueType (glyf) or OpenType (CFF) file, or bare CID-keyed CFF
    kind: str  # "truetype", "opentype-cff" or "cid-cff"
    widths: dict[int, float]  # CID -> advance in 1/1000 em
    to_unicode: dict[int, str]  # CID -> text
    ascent: float = 800
    descent: float = -200
    cap_height: float = 700
    bbox: tuple[float, float, float, float] = (0, -200, 1000, 800)
    italic_angle: float = 0
    flags: int = 4  # symbolic


@dataclass
class GlyphRun:
    """One word: glyphs set from a baseline origin, in scan pixels."""

    font: str
    size_px: float
    x: float
    y: float  # baseline
    cids: list[int]
    char_spacing_px: float = 0.0  # Tc, before horizontal scaling (as PDF applies it)
    horizontal_scale: float = 1.0  # Tz / 100
    actual_text: str | None = None
    adjust: list[float] | None = None  # TJ numbers (1/1000 em) before glyphs 1..n-1, to place each glyph exactly
    invisible: bool = False  # render mode 3: searchable text over an image fallback


@dataclass
class PageSpec:
    width_px: int
    height_px: int
    dpi: float
    background: np.ndarray | None = None  # grayscale, full page
    background_dpi: float = 150
    background_quality: int = 75
    runs: list[GlyphRun] = field(default_factory=list)
    fill_gray: float = 0.0  # text colour, 0 = black
    invisible: bool = False  # render mode 3 (for image fallback comparisons)


def _tounicode(mapping: dict[int, str]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
        "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange",
    ]
    items = sorted(mapping.items())
    for start in range(0, len(items), 100):
        chunk = items[start:start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for cid, text in chunk:
            lines.append(f"<{cid:04X}> <{text.encode('utf-16-be').hex().upper()}>")
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode("ascii")


def _w_array(widths: dict[int, float]) -> list:
    out: list = []
    run_start, run = None, []
    for cid in sorted(widths):
        if run_start is not None and cid == run_start + len(run):
            run.append(round(widths[cid], 1))
        else:
            if run_start is not None:
                out += [run_start, run]
            run_start, run = cid, [round(widths[cid], 1)]
    if run_start is not None:
        out += [run_start, run]
    return out


def _embed_font(pdf: pikepdf.Pdf, f: EmbeddedFont) -> pikepdf.Object:
    stream = pikepdf.Stream(pdf, f.program)
    if f.kind == "truetype":
        file_key, subtype = "/FontFile2", "/CIDFontType2"
        stream.Length1 = len(f.program)
    elif f.kind == "opentype-cff":
        file_key, subtype = "/FontFile3", "/CIDFontType0"
        stream.Subtype = pikepdf.Name.OpenType
    else:
        file_key, subtype = "/FontFile3", "/CIDFontType0"
        stream.Subtype = pikepdf.Name.CIDFontType0C
    descriptor = pdf.make_indirect(pikepdf.Dictionary({
        "/Type": pikepdf.Name.FontDescriptor,
        "/FontName": pikepdf.Name("/" + f.name),
        "/Flags": f.flags,
        "/FontBBox": [round(v) for v in f.bbox],
        "/ItalicAngle": f.italic_angle,
        "/Ascent": round(f.ascent),
        "/Descent": round(f.descent),
        "/CapHeight": round(f.cap_height),
        "/StemV": 80,
        file_key: stream,
    }))
    cidfont = pikepdf.Dictionary({
        "/Type": pikepdf.Name.Font,
        "/Subtype": pikepdf.Name(subtype),
        "/BaseFont": pikepdf.Name("/" + f.name),
        "/CIDSystemInfo": pikepdf.Dictionary({"/Registry": "Adobe", "/Ordering": "Identity", "/Supplement": 0}),
        "/FontDescriptor": descriptor,
        "/W": _w_array(f.widths),
        "/DW": 1000,
    })
    if subtype == "/CIDFontType2":
        cidfont["/CIDToGIDMap"] = pikepdf.Name.Identity
    return pdf.make_indirect(pikepdf.Dictionary({
        "/Type": pikepdf.Name.Font,
        "/Subtype": pikepdf.Name.Type0,
        "/BaseFont": pikepdf.Name("/" + f.name),
        "/Encoding": pikepdf.Name("/Identity-H"),
        "/DescendantFonts": [pdf.make_indirect(cidfont)],
        "/ToUnicode": pikepdf.Stream(pdf, _tounicode(f.to_unicode)),
    }))


def _fmt(v: float) -> str:
    text = f"{v:.3f}".rstrip("0").rstrip(".")
    return text if text not in ("-0", "") else "0"


def _background_xobject(pdf: pikepdf.Pdf, page: PageSpec) -> pikepdf.Stream:
    img = page.background
    if page.background_dpi and page.background_dpi < page.dpi:
        scale = page.background_dpi / page.dpi
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG", quality=page.background_quality, optimize=True)
    xobj = pikepdf.Stream(pdf, buf.getvalue())
    xobj.Type, xobj.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    xobj.Width, xobj.Height = img.shape[1], img.shape[0]
    xobj.ColorSpace, xobj.BitsPerComponent, xobj.Filter = pikepdf.Name.DeviceGray, 8, pikepdf.Name.DCTDecode
    return xobj


def write_pdf(path: Path, pages: list[PageSpec], fonts: list[EmbeddedFont], producer: str = "Ectype OCR") -> None:
    pdf = pikepdf.new()
    font_objs = {f.key: _embed_font(pdf, f) for f in fonts}
    for page in pages:
        k = 72.0 / page.dpi
        wpt, hpt = page.width_px * k, page.height_px * k
        pdf_page = pdf.add_blank_page(page_size=(wpt, hpt))
        resources = pikepdf.Dictionary()
        ops: list[str] = []
        if page.background is not None:
            resources.XObject = pikepdf.Dictionary(Im0=_background_xobject(pdf, page))
            ops.append(f"q {_fmt(wpt)} 0 0 {_fmt(hpt)} 0 0 cm /Im0 Do Q")
        used = sorted({r.font for r in page.runs})
        if used:
            resources.Font = pikepdf.Dictionary({f"/{key}": font_objs[key] for key in used})
            ops += ["BT", f"{_fmt(page.fill_gray)} g"]
            last_tr = None
            current = (None, None)
            last_tc = 0.0
            last_tz = 1.0
            x0 = y0 = 0.0
            for run in page.runs:
                size_pt = run.size_px * k
                if (run.font, round(size_pt, 3)) != current:
                    ops.append(f"/{run.font} {_fmt(size_pt)} Tf")
                    current = (run.font, round(size_pt, 3))
                if abs(run.horizontal_scale - last_tz) > 1e-5:
                    ops.append(f"{_fmt(100 * run.horizontal_scale)} Tz")
                    last_tz = run.horizontal_scale
                tc = run.char_spacing_px * k
                if abs(tc - last_tc) > 1e-4:
                    ops.append(f"{_fmt(tc)} Tc")
                    last_tc = tc
                tr = 3 if (run.invisible or page.invisible) else 0
                if tr != last_tr:
                    ops.append(f"{tr} Tr")
                    last_tr = tr
                x, y = run.x * k, hpt - run.y * k
                ops.append(f"{_fmt(x - x0)} {_fmt(y - y0)} Td")
                x0, y0 = x, y
                if run.adjust:
                    parts = [f"<{run.cids[0]:04X}>"]
                    for cid, a in zip(run.cids[1:], run.adjust):
                        if abs(a) >= 0.05:
                            parts.append(_fmt(a))
                        parts.append(f"<{cid:04X}>")
                    show = "[" + " ".join(parts) + "] TJ"
                else:
                    show = "<" + "".join(f"{cid:04X}" for cid in run.cids) + "> Tj"
                if run.actual_text is not None:
                    actual = run.actual_text.encode("utf-16-be").hex().upper()
                    ops.append(f"/Span << /ActualText <FEFF{actual}> >> BDC {show} EMC")
                else:
                    ops.append(show)
            ops.append("ET")
        pdf_page.Resources = resources
        pdf_page.Contents = pikepdf.Stream(pdf, zlib.compress("\n".join(ops).encode("latin-1")))
        pdf_page.Contents.Filter = pikepdf.Name.FlateDecode
    pdf.docinfo["/Producer"] = producer
    pdf.save(path, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)


# ---------------------------------------------------------------------------
# Real fonts (substitute mode)


@dataclass
class FontMetrics:
    """What placement needs from a font, in font units."""

    units_per_em: int
    x_height: float
    advance: dict[int, float]  # gid -> advance
    ink: dict[int, tuple[float, float, float, float] | None]  # gid -> (xMin, yMin, xMax, yMax)


def subset_font(path: Path, text: str, key: str = "F1") -> tuple[EmbeddedFont, dict[str, int], FontMetrics]:
    """Subset a TrueType/OpenType font to the characters in text.

    Returns the font to embed, a map from character to CID (glyph index in the
    subset; characters the font lacks are absent) and the metrics placement needs.
    """
    from fontTools import subset
    from fontTools.pens.boundsPen import BoundsPen

    font = TTFont(str(path))
    cmap = font.getBestCmap()
    wanted = {ord(c) for c in set(text) if ord(c) in cmap} | {0x20}
    options = subset.Options()
    options.layout_features = []
    options.drop_tables += ["GSUB", "GPOS", "GDEF", "kern", "DSIG"]
    options.notdef_outline = True
    options.name_IDs = ["*"]
    options.hinting = False
    sub = subset.Subsetter(options)
    sub.populate(unicodes=wanted)
    sub.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    program = buf.getvalue()
    font = TTFont(io.BytesIO(program))

    upem = font["head"].unitsPerEm
    scale = 1000.0 / upem
    cmap = font.getBestCmap()
    glyph_set = font.getGlyphSet()
    order = font.getGlyphOrder()
    gid_of = {name: i for i, name in enumerate(order)}
    char_cid = {chr(u): gid_of[name] for u, name in cmap.items()}
    advance, ink = {}, {}
    for name, gid in gid_of.items():
        advance[gid] = font["hmtx"][name][0]
        pen = BoundsPen(glyph_set)
        glyph_set[name].draw(pen)
        ink[gid] = pen.bounds
    os2 = font["OS/2"] if "OS/2" in font else None
    x_height = getattr(os2, "sxHeight", 0) or 0
    if not x_height and "x" in char_cid and ink[char_cid["x"]]:
        x_height = ink[char_cid["x"]][3]
    hhea = font["hhea"]
    name = font["name"].getDebugName(6) or Path(path).stem
    embedded = EmbeddedFont(
        key=key,
        name="".join(ch for ch in name if ch.isalnum() or ch in "-_") or "Font",
        program=program,
        kind="truetype" if "glyf" in font else "opentype-cff",
        widths={gid: adv * scale for gid, adv in advance.items()},
        to_unicode={cid: ch for ch, cid in char_cid.items()},
        ascent=hhea.ascent * scale,
        descent=hhea.descent * scale,
        cap_height=(getattr(os2, "sCapHeight", 0) or 0.7 * upem) * scale,
        bbox=tuple(v * scale for v in (font["head"].xMin, font["head"].yMin, font["head"].xMax, font["head"].yMax)),
        italic_angle=font["post"].italicAngle,
        flags=32,  # nonsymbolic
    )
    return embedded, char_cid, FontMetrics(upem, float(x_height or 0.5 * upem), advance, ink)


def save_font(font: EmbeddedFont, path: Path) -> None:
    """Write an EmbeddedFont as <path>.cff (the program) and <path>.font.json (the rest)."""
    from dataclasses import asdict
    import json

    path = Path(path)
    path.with_suffix(".cff").write_bytes(font.program)
    meta = {k: v for k, v in asdict(font).items() if k != "program"}
    meta["widths"] = {str(k): v for k, v in font.widths.items()}
    meta["to_unicode"] = {str(k): v for k, v in font.to_unicode.items()}
    path.with_suffix(".font.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def load_font(path: Path) -> EmbeddedFont:
    import json

    path = Path(path)
    meta = json.loads(path.with_suffix(".font.json").read_text(encoding="utf-8"))
    meta["widths"] = {int(k): v for k, v in meta["widths"].items()}
    meta["to_unicode"] = {int(k): v for k, v in meta["to_unicode"].items()}
    meta["bbox"] = tuple(meta["bbox"])
    return EmbeddedFont(program=path.with_suffix(".cff").read_bytes(), **meta)
