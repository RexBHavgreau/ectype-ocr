"""Shared fixtures: a tiny ClearScan-shaped PDF built from scratch."""

from __future__ import annotations

import io
import zlib
from pathlib import Path

import numpy as np
import pikepdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen

# CID -> text. Two glyphs for "i" (as Acrobat keeps variants), a ligature, an unknown.
GLYPH_TEXT = {1: "i", 2: "i", 3: "fi", 4: "o", 5: "�"}


def _box(width: int, height: int):
    pen = T2CharStringPen(width, None)
    pen.moveTo((50, 0))
    pen.lineTo((width - 50, 0))
    pen.lineTo((width - 50, height))
    pen.lineTo((50, height))
    pen.closePath()
    return pen.getCharString()


def build_cff_otf() -> bytes:
    """A five-glyph CFF-flavoured OpenType font with distinct box glyphs."""
    names = [".notdef"] + [f"g{cid}" for cid in GLYPH_TEXT]
    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({})
    charstrings = {".notdef": _box(500, 700)}
    for cid in GLYPH_TEXT:
        charstrings[f"g{cid}"] = _box(300 + 60 * cid, 400 + 50 * cid)
    fb.setupCFF("EctypeTest", {"FullName": "Ectype Test"}, charstrings, {})
    fb.setupHorizontalMetrics({n: (600, 50) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Ectype Test", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


def _tounicode(mapping: dict[int, str]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap",
        "/CMapName /Adobe-Identity-UCS def /CMapType 2 def",
        "1 begincodespacerange <0000> <FFFF> endcodespacerange",
        f"{len(mapping)} beginbfchar",
    ]
    for cid, text in mapping.items():
        lines.append(f"<{cid:04X}> <{text.encode('utf-16-be').hex().upper()}>")
    lines += ["endbfchar", "endcmap CMapName currentdict /CMap defineresource pop end end"]
    return "\n".join(lines).encode()


@pytest.fixture
def clearscan_like_pdf(tmp_path: Path) -> Path:
    pdf = pikepdf.new()
    otf = build_cff_otf()
    font_file = pikepdf.Stream(pdf, otf)
    font_file.Subtype = pikepdf.Name.OpenType
    descriptor = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.FontDescriptor, FontName=pikepdf.Name("/*Times New Roman-12345"),
        Flags=4, FontBBox=[0, -200, 1000, 800], ItalicAngle=0, Ascent=800, Descent=-200,
        CapHeight=700, StemV=80, FontFile3=font_file,
    ))
    cidfont = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.CIDFontType0,
        BaseFont=pikepdf.Name("/*Times New Roman-12345"),
        CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
        FontDescriptor=descriptor, DW=600,
    ))
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type0,
        BaseFont=pikepdf.Name("/*Times New Roman-12345"), Encoding=pikepdf.Name("/Identity-H"),
        DescendantFonts=[cidfont], ToUnicode=pikepdf.Stream(pdf, _tounicode(GLYPH_TEXT)),
    ))
    font.Bezier = 300  # Acrobat's private key

    gray = np.full((300, 200), 240, np.uint8)
    image = pikepdf.Stream(pdf, zlib.compress(gray.tobytes()))
    image.Type, image.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    image.Width, image.Height, image.BitsPerComponent = 200, 300, 8
    image.ColorSpace, image.Filter = pikepdf.Name.DeviceGray, pikepdf.Name.FlateDecode

    for _ in range(2):  # the same font on two pages
        page = pdf.add_blank_page(page_size=(144, 216))
        page.Resources.Font = pikepdf.Dictionary(T1_0=font)
        page.Resources.XObject = pikepdf.Dictionary(Im0=image)
        page.Contents = pikepdf.Stream(pdf, (
            b"q 48 0 0 72 0 0 cm /Im0 Do Q\n"  # 200 px over 48 pt = 300 dpi
            b"BT /T1_0 12 Tf 0 Tr 20 100 Td 0.25 Tc <00010003> Tj 30 0 Td 0 Tc <0004> Tj ET\n"
            b"/Span << /ActualText (fio) >> BDC BT /T1_0 12 Tf 20 80 Td <000100020005> Tj ET EMC\n"
        ))
    path = tmp_path / "clearscan-like.pdf"
    pdf.save(path)
    return path
