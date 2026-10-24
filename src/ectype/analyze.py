"""Dump the structure of any PDF: fonts, glyph sheets, page structure, images.

Built to take apart Acrobat ClearScan output (synthesized Type0/CFF fonts over an
erased background image) and to describe our own output the same way, so the
two can be compared. Writes `analysis.json`, an `index.html` summary, extracted
font programs and SVG glyph sheets under `fonts/`, and images under `images/`.
"""

from __future__ import annotations

import io
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
from fontTools.cffLib import CFFFontSet
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

from . import report

# Keys the PDF spec defines for font dictionaries, CIDFont dictionaries and font
# descriptors. Anything else is reported as a private key (Acrobat adds /Bezier).
_KNOWN_FONT_KEYS = {
    "/Type", "/Subtype", "/BaseFont", "/Name", "/FirstChar", "/LastChar", "/Widths",
    "/FontDescriptor", "/Encoding", "/ToUnicode", "/DescendantFonts", "/FontBBox",
    "/FontMatrix", "/CharProcs", "/Resources", "/CIDSystemInfo", "/DW", "/W", "/DW2",
    "/W2", "/CIDToGIDMap",
}
_KNOWN_DESCRIPTOR_KEYS = {
    "/Type", "/FontName", "/FontFamily", "/FontStretch", "/FontWeight", "/Flags",
    "/FontBBox", "/ItalicAngle", "/Ascent", "/Descent", "/Leading", "/CapHeight",
    "/XHeight", "/StemV", "/StemH", "/AvgWidth", "/MaxWidth", "/MissingWidth",
    "/FontFile", "/FontFile2", "/FontFile3", "/CharSet", "/Style", "/Lang", "/FD", "/CIDSet",
}

_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


# ---------------------------------------------------------------------------
# ToUnicode CMaps


def parse_tounicode(data: bytes) -> dict[int, str]:
    """Map character codes to Unicode strings from a ToUnicode CMap stream."""
    text = data.decode("latin-1")
    mapping: dict[int, str] = {}

    def utf16(hexstr: str) -> str:
        raw = bytes.fromhex(hexstr)
        if len(raw) % 2:
            raw = b"\x00" + raw
        return raw.decode("utf-16-be", errors="replace")

    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            mapping[int(src, 16)] = utf16(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for lo, hi, rest in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block
        ):
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            if rest.startswith("["):
                for i, dst in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", rest)):
                    if lo_i + i <= hi_i:
                        mapping[lo_i + i] = utf16(dst)
            else:
                start = bytes.fromhex(rest[1:-1])
                # Only the last byte increments across a range (PDF 32000 9.10.3).
                for i in range(hi_i - lo_i + 1):
                    dst = start[:-1] + bytes([(start[-1] + i) & 0xFF]) if start else b""
                    mapping[lo_i + i] = utf16(dst.hex())
    return mapping


# ---------------------------------------------------------------------------
# Embedded font programs


@dataclass
class FontProgram:
    """Glyph outlines from an embedded font, keyed by glyph id."""

    kind: str
    units_per_em: float
    glyph_names: list[str]
    draw: object  # callable(glyph_name, pen)
    cid_keyed: bool = False

    def cid_to_gid(self, cid: int) -> int | None:
        if self.cid_keyed:
            name = f"cid{cid:05d}"
            try:
                return self.glyph_names.index(name)
            except ValueError:
                return None
        return cid if cid < len(self.glyph_names) else None


def load_font_program(data: bytes, kind: str) -> FontProgram | None:
    """Load a FontFile2 (TrueType), FontFile3 (bare CFF) or OpenType program."""
    if kind in ("FontFile2", "OpenType"):
        tt = TTFont(io.BytesIO(data), lazy=True)
        glyph_set = tt.getGlyphSet()
        return FontProgram(
            kind=kind,
            units_per_em=tt["head"].unitsPerEm,
            glyph_names=tt.getGlyphOrder(),
            draw=lambda name, pen: glyph_set[name].draw(pen),
        )
    if kind in ("Type1C", "CIDFontType0C"):
        cff = CFFFontSet()
        cff.decompile(io.BytesIO(data), otFont=TTFont())
        top = cff[cff.fontNames[0]]
        char_strings = top.CharStrings
        matrix = getattr(top, "FontMatrix", [0.001])
        return FontProgram(
            kind=kind,
            units_per_em=round(1 / matrix[0]) if matrix[0] else 1000,
            glyph_names=list(top.charset),
            draw=lambda name, pen: char_strings[name].draw(pen),
            cid_keyed=hasattr(top, "ROS"),
        )
    return None


def glyph_sheet_svg(
    program: FontProgram,
    labels: dict[int, str],
    title: str,
    cell: int = 72,
    columns: int = 16,
) -> str:
    """Draw every glyph in a grid, ordered by Unicode so variants sit together."""
    order = sorted(
        range(len(program.glyph_names)),
        key=lambda gid: (labels.get(gid) is None, labels.get(gid) or "", gid),
    )
    rows = math.ceil(len(order) / columns) or 1
    scale = cell * 0.55 / program.units_per_em
    baseline = cell * 0.62
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{columns * cell}" '
        f'height="{rows * cell + 24}" viewBox="0 0 {columns * cell} {rows * cell + 24}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        f'<text x="4" y="16" font-family="sans-serif" font-size="13">{report.esc(title)}</text>',
        '<g transform="translate(0,24)" font-family="sans-serif" font-size="9" fill="#555">',
    ]
    for i, gid in enumerate(order):
        x, y = (i % columns) * cell, (i // columns) * cell
        name = program.glyph_names[gid]
        pen = SVGPathPen(None)
        bounds = BoundsPen(None)
        try:
            program.draw(name, pen)
            program.draw(name, bounds)
        except Exception:  # damaged glyph; leave the cell empty
            pass
        # Centre the ink horizontally in the cell.
        dx = cell * 0.2
        if bounds.bounds:
            x0, _, x1, _ = bounds.bounds
            dx = (cell - (x1 - x0) * scale) / 2 - x0 * scale
        parts.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="none" stroke="#ddd"/>')
        path = pen.getCommands()
        if path:
            parts.append(
                f'<path transform="translate({x + dx:.1f},{y + baseline:.1f}) scale({scale:.5f},{-scale:.5f})" '
                f'd="{path}" fill="#000"/>'
            )
        label = labels.get(gid)
        text = f"{gid}"
        if label is not None:
            shown = "".join(f"U+{ord(c):04X}" if not c.isprintable() or c == "�" else c for c in label)
            text += f" {shown}"
        parts.append(f'<text x="{x + 3}" y="{y + cell - 4}">{report.esc(text)}</text>')
    parts.append("</g></svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Fonts


@dataclass
class FontInfo:
    key: str
    base_font: str
    subtype: str
    synthesized: bool
    encoding: str | None = None
    descendant_subtype: str | None = None
    cid_system_info: str | None = None
    font_file: str | None = None
    font_file_subtype: str | None = None
    font_file_bytes: int = 0
    program_id: str | None = None
    glyph_count: int | None = None
    units_per_em: float | None = None
    has_tounicode: bool = False
    mapped_codes: int = 0
    fffd_codes: int = 0
    ligature_glyphs: list[str] = field(default_factory=list)
    variants: dict[str, int] = field(default_factory=dict)
    private_keys: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    resource_names: list[str] = field(default_factory=list)
    glyphs_shown: int = 0
    files: dict[str, str] = field(default_factory=dict)


def _name(obj) -> str | None:
    return str(obj) if obj is not None else None


def _font_file(descriptor) -> tuple[str | None, pikepdf.Stream | None]:
    if descriptor is None:
        return None, None
    for key in ("/FontFile", "/FontFile2", "/FontFile3"):
        if key in descriptor:
            return key[1:], descriptor[key]
    return None, None


def describe_font(
    key: str, font: pikepdf.Dictionary, out_dir: Path | None, seen_programs: dict[str, FontInfo] | None = None
) -> FontInfo:
    """Describe one font dictionary. Font programs shared between dictionaries
    (Acrobat writes one Type0 dictionary per page around a shared program) are
    parsed and written out once, via seen_programs."""
    seen_programs = {} if seen_programs is None else seen_programs
    base = str(font.get("/BaseFont", font.get("/Name", "(unnamed)")))
    info = FontInfo(
        key=key,
        base_font=base,
        subtype=str(font.get("/Subtype")),
        synthesized=_SUBSET_PREFIX.sub("", base.lstrip("/")).startswith("*"),
        encoding=_name(font.get("/Encoding")) if not isinstance(font.get("/Encoding"), pikepdf.Dictionary) else "(dict)",
    )
    private = {k for k in font.keys() if k not in _KNOWN_FONT_KEYS}

    descendant = None
    if "/DescendantFonts" in font:
        descendant = font.DescendantFonts[0]
        info.descendant_subtype = str(descendant.get("/Subtype"))
        csi = descendant.get("/CIDSystemInfo")
        if csi is not None:
            info.cid_system_info = f"{csi.get('/Registry')}-{csi.get('/Ordering')}-{csi.get('/Supplement')}"
        private |= {k for k in descendant.keys() if k not in _KNOWN_FONT_KEYS}

    descriptor = (descendant or font).get("/FontDescriptor")
    if descriptor is not None:
        private |= {k for k in descriptor.keys() if k not in _KNOWN_DESCRIPTOR_KEYS}
    info.private_keys = sorted(private)

    file_key, stream = _font_file(descriptor)
    program = None
    shared = None
    if stream is not None:
        info.font_file = file_key
        sub = stream.get("/Subtype")
        info.font_file_subtype = str(sub)[1:] if sub is not None else None
        info.font_file_bytes = len(stream.read_raw_bytes())
        info.program_id = f"{stream.objgen[0]}-{stream.objgen[1]}" if stream.is_indirect else key
        shared = seen_programs.get(info.program_id)
    if shared is not None:
        info.glyph_count, info.units_per_em = shared.glyph_count, shared.units_per_em
        info.files = dict(shared.files)
    elif stream is not None:
        data = stream.read_bytes()
        kind = info.font_file_subtype if file_key == "FontFile3" else file_key
        try:
            program = load_font_program(data, kind)
        except Exception as exc:  # report, don't crash on one bad font
            info.files["error"] = f"could not parse font program: {exc}"
        if program is not None:
            info.glyph_count = len(program.glyph_names)
            info.units_per_em = program.units_per_em
        if out_dir is not None:
            ext = {"FontFile": "pfb", "FontFile2": "ttf", "Type1C": "cff", "CIDFontType0C": "cff", "OpenType": "otf"}
            path = out_dir / f"{_safe(key)}.{ext.get(kind, 'bin')}"
            path.write_bytes(data)
            info.files["program"] = path.name
    elif info.subtype == "/Type3":
        info.glyph_count = len(font.get("/CharProcs", {}))

    to_unicode: dict[int, str] = {}
    if "/ToUnicode" in font and isinstance(font.ToUnicode, pikepdf.Stream):
        info.has_tounicode = True
        to_unicode = parse_tounicode(font.ToUnicode.read_bytes())
        info.mapped_codes = len(to_unicode)
        info.fffd_codes = sum(1 for v in to_unicode.values() if "�" in v)
        counts = Counter(to_unicode.values())
        info.variants = {
            u: n for u, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n > 1
        }
        info.ligature_glyphs = sorted({u for u in to_unicode.values() if len(u) > 1 and "�" not in u})

    if program is not None and out_dir is not None:
        labels: dict[int, str] = {}
        if info.subtype == "/Type0":
            for cid, text in to_unicode.items():
                gid = program.cid_to_gid(cid)
                if gid is not None:
                    labels[gid] = text
        svg = glyph_sheet_svg(program, labels, f"{info.base_font.lstrip('/')} ({info.glyph_count} glyphs)")
        sheet = out_dir / f"{_safe(key)}-glyphs.svg"
        sheet.write_text(svg, encoding="utf-8")
        info.files["glyph_sheet"] = sheet.name
    if info.program_id is not None and shared is None:
        seen_programs[info.program_id] = info
    return info


def _filters(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, pikepdf.Array):
        return " ".join(str(v).lstrip("/") for v in value)
    return str(value).lstrip("/")


def font_programs(fonts: list[FontInfo]) -> list[dict]:
    """Group font dictionaries by the embedded program they share."""
    groups: dict[str, list[FontInfo]] = {}
    for f in fonts:
        if f.program_id is not None:
            groups.setdefault(f.program_id, []).append(f)
    out = []
    for pid, members in groups.items():
        first = members[0]
        out.append({
            "id": pid,
            "base_fonts": sorted({_SUBSET_PREFIX.sub("", m.base_font.lstrip("/")) for m in members}),
            "kind": " ".join(x for x in (first.font_file, first.font_file_subtype) if x),
            "bytes": first.font_file_bytes,
            "glyph_count": first.glyph_count,
            "font_dicts": [m.key for m in members],
            "pages": sorted({p for m in members for p in m.pages}),
            "glyphs_shown": sum(m.glyphs_shown for m in members),
            "fffd_codes": first.fffd_codes,
            "variants": dict(first.variants),
            "ligature_glyphs": first.ligature_glyphs,
            "files": first.files,
        })
    return sorted(out, key=lambda p: (-p["glyphs_shown"], p["id"]))


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "font"


# ---------------------------------------------------------------------------
# Content streams


def _mul(m: tuple, n: tuple) -> tuple:
    """Multiply two PDF matrices [a b c d e f] (m applied first)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


@dataclass
class PageStats:
    number: int
    media_box: list[float]
    rotate: int = 0
    content_bytes: int = 0
    operators: Counter = field(default_factory=Counter)
    render_modes: Counter = field(default_factory=Counter)
    fonts_used: Counter = field(default_factory=Counter)
    nonzero_tc: int = 0
    text_shows: int = 0
    glyphs_shown: int = 0
    images: list[dict] = field(default_factory=list)
    forms: int = 0
    inline_images: int = 0
    marked_content: Counter = field(default_factory=Counter)


_TEXT_SHOW = {"Tj", "TJ", "'", '"'}
_PATH_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}


def _walk(stream_owner, resources, ctm, stats: PageStats, font_lookup, depth=0) -> None:
    if depth > 8:
        return
    stack = []
    font_key = None
    font_is_cid = False
    render_mode = 0
    xobjects = resources.get("/XObject", {}) if resources is not None else {}
    fonts = resources.get("/Font", {}) if resources is not None else {}
    for operands, op in pikepdf.parse_content_stream(stream_owner):
        name = str(op)
        stats.operators[name] += 1
        if name == "q":
            stack.append((ctm, font_key, font_is_cid, render_mode))
        elif name == "Q" and stack:
            ctm, font_key, font_is_cid, render_mode = stack.pop()
        elif name == "cm":
            ctm = _mul(tuple(float(v) for v in operands), ctm)
        elif name == "Tf":
            res_name = str(operands[0])
            font = fonts.get(res_name)
            font_key = font_lookup(font, res_name) if font is not None else res_name
            font_is_cid = font is not None and font.get("/Subtype") == "/Type0"
        elif name == "Tr":
            render_mode = int(operands[0])
        elif name == "Tc":
            if float(operands[0]) != 0:
                stats.nonzero_tc += 1
        elif name in _TEXT_SHOW:
            stats.text_shows += 1
            stats.render_modes[render_mode] += 1
            nbytes = 0
            for operand in operands:
                if isinstance(operand, pikepdf.String):
                    nbytes += len(bytes(operand))
                elif isinstance(operand, pikepdf.Array):
                    nbytes += sum(len(bytes(s)) for s in operand if isinstance(s, pikepdf.String))
            glyphs = nbytes // 2 if font_is_cid else nbytes
            stats.glyphs_shown += glyphs
            stats.fonts_used[font_key] += glyphs
        elif name in ("BDC", "BMC"):
            tag = str(operands[0])
            if len(operands) > 1 and isinstance(operands[1], pikepdf.Dictionary) and "/ActualText" in operands[1]:
                tag += " +ActualText"
            stats.marked_content[tag] += 1
        elif name == "INLINE IMAGE":
            stats.inline_images += 1
        elif name == "Do":
            xobj = xobjects.get(str(operands[0]))
            if xobj is None:
                continue
            subtype = xobj.get("/Subtype")
            if subtype == "/Image":
                width_pt = math.hypot(ctm[0], ctm[1])
                height_pt = math.hypot(ctm[2], ctm[3])
                w, h = int(xobj.get("/Width", 0)), int(xobj.get("/Height", 0))
                stats.images.append({
                    "name": str(operands[0]),
                    "objgen": list(xobj.objgen),
                    "width": w,
                    "height": h,
                    "bits": int(xobj.get("/BitsPerComponent", 0)) if "/BitsPerComponent" in xobj else None,
                    "colorspace": str(xobj.get("/ColorSpace")) if not isinstance(xobj.get("/ColorSpace"), pikepdf.Array) else "(array)",
                    "filter": _filters(xobj.get("/Filter")),
                    "image_mask": bool(xobj.get("/ImageMask", False)),
                    "smask": "/SMask" in xobj,
                    "placed_pt": [round(width_pt, 2), round(height_pt, 2)],
                    "dpi": [
                        round(w / (width_pt / 72), 1) if width_pt else None,
                        round(h / (height_pt / 72), 1) if height_pt else None,
                    ],
                    "stream_bytes": len(xobj.read_raw_bytes()),
                })
            elif subtype == "/Form":
                stats.forms += 1
                matrix = tuple(float(v) for v in xobj.get("/Matrix", [1, 0, 0, 1, 0, 0]))
                _walk(xobj, xobj.get("/Resources", resources), _mul(matrix, ctm), stats, font_lookup, depth + 1)


# ---------------------------------------------------------------------------
# Driver


def _collect_fonts(resources, found: dict, depth=0) -> None:
    """Find font dictionaries in a resource dictionary and its form XObjects."""
    if resources is None or depth > 8:
        return
    for res_name, font in resources.get("/Font", {}).items():
        found.setdefault(font.objgen if font.is_indirect else id(font), (font, res_name))
    for _, xobj in resources.get("/XObject", {}).items():
        if xobj.get("/Subtype") == "/Form" and "/Resources" in xobj:
            _collect_fonts(xobj.Resources, found, depth + 1)


def analyze_pdf(pdf_path: Path, out_dir: Path, images: bool = True, glyph_sheets: bool = True) -> dict:
    """Analyze one PDF and write the report folder. Returns the JSON summary."""
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    font_dir = out_dir / "fonts"
    image_dir = out_dir / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    if glyph_sheets:
        font_dir.mkdir(exist_ok=True)
    if images:
        image_dir.mkdir(exist_ok=True)

    pdf = pikepdf.open(pdf_path)
    fonts: dict[object, FontInfo] = {}
    seen_programs: dict[str, FontInfo] = {}
    font_keys: dict[object, str] = {}

    def font_key_for(font, res_name: str) -> str:
        ident = font.objgen if font.is_indirect else id(font)
        if ident not in font_keys:
            base = _SUBSET_PREFIX.sub("", str(font.get("/BaseFont", res_name)).lstrip("/"))
            suffix = f"-{font.objgen[0]}" if font.is_indirect else f"-{len(font_keys)}"
            font_keys[ident] = base + suffix
        return font_keys[ident]

    pages: list[PageStats] = []
    images_seen: dict[tuple, str] = {}
    for number, page in enumerate(pdf.pages, start=1):
        box = [float(v) for v in page.mediabox]
        stats = PageStats(number=number, media_box=box, rotate=int(page.obj.get("/Rotate", 0)))
        contents = page.obj.get("/Contents")
        if isinstance(contents, pikepdf.Array):
            stats.content_bytes = sum(len(c.read_raw_bytes()) for c in contents)
        elif contents is not None:
            stats.content_bytes = len(contents.read_raw_bytes())

        found: dict = {}
        _collect_fonts(page.obj.get("/Resources"), found)
        for ident, (font, res_name) in found.items():
            key = font_key_for(font, str(res_name))
            if ident not in fonts:
                fonts[ident] = describe_font(key, font, font_dir if glyph_sheets else None, seen_programs)
            info = fonts[ident]
            if number not in info.pages:
                info.pages.append(number)
            if str(res_name) not in info.resource_names:
                info.resource_names.append(str(res_name))

        _walk(page, page.obj.get("/Resources"), (1, 0, 0, 1, 0, 0), stats, font_key_for)

        if images:
            for image in stats.images:
                ident = tuple(image["objgen"])
                if ident in images_seen and ident != (0, 0):
                    image["file"] = images_seen[ident]
                    continue
                filename = f"p{number:03d}-{_safe(image['name'])}.png"
                try:
                    xobj = page.obj.Resources.XObject[image["name"]]
                    pil = pikepdf.PdfImage(xobj).as_pil_image()
                    pil.save(image_dir / filename)
                    thumb = pil.copy()
                    thumb.thumbnail((640, 640))
                    thumb.save(image_dir / f"thumb-{filename}")
                    image["file"] = filename
                    images_seen[ident] = filename
                except Exception as exc:
                    image["error"] = f"could not extract: {exc}"
        pages.append(stats)

    by_key = {info.key: info for info in fonts.values()}
    for stats in pages:
        for key, n in stats.fonts_used.items():
            if key in by_key:
                by_key[key].glyphs_shown += n

    file_size = pdf_path.stat().st_size
    programs = font_programs(list(fonts.values()))
    font_bytes = sum(p["bytes"] for p in programs)
    unique_images = {tuple(i["objgen"]): i["stream_bytes"] for p in pages for i in p.images}
    image_bytes = sum(unique_images.values())
    content_bytes = sum(p.content_bytes for p in pages)

    summary = {
        "file": pdf_path.name,
        "file_bytes": file_size,
        "pdf_version": pdf.pdf_version,
        "page_count": len(pdf.pages),
        "docinfo": {str(k): str(v) for k, v in pdf.docinfo.items()},
        "size_breakdown": {
            "fonts": font_bytes,
            "images": image_bytes,
            "content_streams": content_bytes,
            "other": file_size - font_bytes - image_bytes - content_bytes,
            "per_page": round(file_size / max(len(pdf.pages), 1)),
        },
        "font_count": len(fonts),
        "synthesized_font_count": sum(f.synthesized for f in fonts.values()),
        "font_program_count": len(programs),
        "font_programs": programs,
        "fonts": [vars(f) for f in sorted(fonts.values(), key=lambda f: f.key)],
        "pages": [
            {
                "number": p.number,
                "media_box": p.media_box,
                "rotate": p.rotate,
                "content_bytes": p.content_bytes,
                "text_shows": p.text_shows,
                "glyphs_shown": p.glyphs_shown,
                "render_modes": {str(k): v for k, v in sorted(p.render_modes.items())},
                "positioning_ops": {op: p.operators[op] for op in ("Td", "TD", "Tm", "T*") if p.operators[op]},
                "nonzero_tc": p.nonzero_tc,
                "fonts_used": dict(p.fonts_used.most_common()),
                "marked_content": dict(p.marked_content),
                "images": p.images,
                "inline_images": p.inline_images,
                "forms": p.forms,
                "path_paints": sum(p.operators[op] for op in _PATH_PAINT),
            }
            for p in pages
        ],
    }
    pdf.close()
    report.write_json(out_dir / "analysis.json", summary)
    write_html(summary, out_dir / "index.html")
    return summary


# ---------------------------------------------------------------------------
# HTML


def write_html(summary: dict, path: Path) -> None:
    hb = report.human_bytes
    sizes = summary["size_breakdown"]
    body = [
        f"<p class=\"muted\">{report.esc(summary['file'])} · PDF {report.esc(summary['pdf_version'])} · "
        f"{summary['page_count']} pages · {hb(summary['file_bytes'])}</p>",
    ]
    producer = summary["docinfo"].get("/Producer")
    if producer:
        body.append(f"<p>Producer: <code>{report.esc(producer)}</code></p>")

    body.append("<h2>File size</h2>")
    body.append(report.table(
        ["Part", "Bytes", "Size", "Share"],
        [[k, v, hb(v), f"{100 * v / max(summary['file_bytes'], 1):.1f}%"]
         for k, v in sizes.items() if k != "per_page"]
        + [["per page", sizes["per_page"], hb(sizes["per_page"]), ""]],
        numeric={1, 2, 3},
    ))

    fonts = summary["fonts"]
    programs = summary["font_programs"]
    body.append(f"<h2>Fonts ({summary['font_program_count']} embedded programs, {len(fonts)} font dictionaries, "
                f"{summary['synthesized_font_count']} synthesized)</h2>")
    if programs:
        body.append(report.table(
            ["Program", "Name", "Kind", "Glyphs", "U+FFFD", "Most repeated", "Ligatures", "Pages", "Dicts",
             "Glyphs shown", "Bytes"],
            [[
                p["id"],
                ", ".join(p["base_fonts"]),
                p["kind"],
                p["glyph_count"] if p["glyph_count"] is not None else "",
                p["fffd_codes"],
                ", ".join(f"{u!r}×{n}" for u, n in list(p["variants"].items())[:4]),
                " ".join(p["ligature_glyphs"][:8]),
                _ranges(p["pages"]),
                len(p["font_dicts"]),
                p["glyphs_shown"],
                p["bytes"],
            ] for p in programs],
            numeric={3, 4, 8, 9, 10},
        ))
    if fonts:
        body.append("<details><summary>Font dictionaries</summary>")
        body.append(report.table(
            ["Font", "Type", "Program", "Glyphs", "ToUnicode", "U+FFFD", "Most repeated", "Ligatures",
             "Private keys", "Pages", "Glyphs shown", "Bytes"],
            [[
                f["key"],
                f["subtype"].lstrip("/") + (f" / {f['descendant_subtype'].lstrip('/')}" if f["descendant_subtype"] else ""),
                " ".join(x for x in (f["font_file"], f["font_file_subtype"]) if x) or "not embedded",
                f["glyph_count"] if f["glyph_count"] is not None else "",
                f["mapped_codes"] if f["has_tounicode"] else "no",
                f["fffd_codes"],
                ", ".join(f"{u!r}×{n}" for u, n in list(f["variants"].items())[:4]),
                " ".join(f["ligature_glyphs"][:8]),
                " ".join(f["private_keys"]),
                _ranges(f["pages"]),
                f["glyphs_shown"],
                f["font_file_bytes"],
            ] for f in fonts],
            numeric={3, 4, 5, 10, 11},
        ))
        body.append("</details>")
    sheets = [p for p in programs if "glyph_sheet" in p["files"]]
    if sheets:
        body.append("<h3>Glyph sheets</h3><p class=\"muted\">Ordered by Unicode, so repeated variants of one character sit together. Labels: glyph id and mapped text.</p>")
        for p in sheets:
            body.append(f"<p><a href=\"fonts/{p['files']['glyph_sheet']}\">{report.esc(', '.join(p['base_fonts']))}</a> "
                        f"<span class=\"muted\">program {report.esc(p['id'])}</span></p>")
            body.append(f"<img class=\"sheet\" loading=\"lazy\" src=\"fonts/{p['files']['glyph_sheet']}\" alt=\"glyph sheet\">")

    body.append("<h2>Pages</h2>")
    body.append(report.table(
        ["Page", "Size (pt)", "Rotate", "Images", "Image dpi", "Text shows", "Glyphs", "Render modes",
         "Positioning", "Nonzero Tc", "Fonts", "ActualText", "Content bytes"],
        [[
            p["number"],
            f"{p['media_box'][2] - p['media_box'][0]:.0f} × {p['media_box'][3] - p['media_box'][1]:.0f}",
            p["rotate"],
            len(p["images"]) + p["inline_images"],
            ", ".join(f"{i['width']}×{i['height']} @ {i['dpi'][0]:.0f}" for i in p["images"] if i["dpi"][0]),
            p["text_shows"],
            p["glyphs_shown"],
            " ".join(f"{k}:{v}" for k, v in p["render_modes"].items()),
            " ".join(f"{k}:{v}" for k, v in p["positioning_ops"].items()),
            p["nonzero_tc"],
            len(p["fonts_used"]),
            sum(v for k, v in p["marked_content"].items() if "ActualText" in k),
            p["content_bytes"],
        ] for p in summary["pages"]],
        numeric={0, 2, 3, 5, 6, 9, 10, 11, 12},
    ))

    thumbs = [(p["number"], i) for p in summary["pages"] for i in p["images"] if "file" in i]
    if thumbs:
        body.append("<h2>Images</h2><div class=\"row\">")
        seen = set()
        for number, image in thumbs:
            if image["file"] in seen:
                continue
            seen.add(image["file"])
            body.append(
                f"<figure><a href=\"images/{image['file']}\"><img loading=\"lazy\" src=\"images/thumb-{image['file']}\" alt=\"\"></a>"
                f"<figcaption>p{number} {report.esc(image['name'])} · {image['width']}×{image['height']} · "
                f"{report.esc(image['colorspace'])} · {report.esc(image['filter'])} · {hb(image['stream_bytes'])}</figcaption></figure>"
            )
        body.append("</div>")
    report.write_page(path, f"Analysis: {summary['file']}", "\n".join(body))


def _ranges(numbers: list[int]) -> str:
    out, start, prev = [], None, None
    for n in sorted(numbers):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}" if start == prev else f"{start}–{prev}")
            start = prev = n
    if start is not None:
        out.append(f"{start}" if start == prev else f"{start}–{prev}")
    return ", ".join(out)

