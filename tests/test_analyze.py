from __future__ import annotations

import json

from ectype.analyze import analyze_pdf, parse_tounicode


def test_parse_tounicode_bfchar_and_ranges():
    cmap = b"""
    2 beginbfchar
    <0001> <0069>
    <0002> <00660069>
    endbfchar
    2 beginbfrange
    <0010> <0012> <0041>
    <0020> <0021> [<0061> <D835DC00>]
    endbfrange
    """
    m = parse_tounicode(cmap)
    assert m[1] == "i"
    assert m[2] == "fi"
    assert [m[0x10], m[0x11], m[0x12]] == ["A", "B", "C"]
    assert m[0x20] == "a"
    assert m[0x21] == "\U0001D400"  # surrogate pair decodes to one character


def test_analyze_clearscan_like(clearscan_like_pdf, tmp_path):
    out = tmp_path / "report"
    s = analyze_pdf(clearscan_like_pdf, out)

    assert s["page_count"] == 2
    assert s["font_count"] == 1, "the font shared by both pages is counted once"
    assert s["synthesized_font_count"] == 1

    f = s["fonts"][0]
    assert f["subtype"] == "/Type0"
    assert f["descendant_subtype"] == "/CIDFontType0"
    assert (f["font_file"], f["font_file_subtype"]) == ("FontFile3", "OpenType")
    assert f["glyph_count"] == 6  # .notdef + five
    assert f["mapped_codes"] == 5
    assert f["fffd_codes"] == 1
    assert f["variants"] == {"i": 2}
    assert f["ligature_glyphs"] == ["fi"]
    assert f["private_keys"] == ["/Bezier"]
    assert f["pages"] == [1, 2]
    assert f["glyphs_shown"] == 2 * (2 + 1 + 3)

    p = s["pages"][0]
    assert p["text_shows"] == 3
    assert p["glyphs_shown"] == 6
    assert p["render_modes"] == {"0": 3}
    assert p["positioning_ops"] == {"Td": 3}
    assert p["nonzero_tc"] == 1
    assert p["marked_content"] == {"/Span +ActualText": 1}
    image = p["images"][0]
    assert image["dpi"] == [300.0, 300.0]
    assert image["file"] == "p001-Im0.png"
    assert s["pages"][1]["images"][0]["file"] == "p001-Im0.png", "shared image is extracted once"

    assert (out / "analysis.json").is_file()
    assert json.loads((out / "analysis.json").read_text())["page_count"] == 2
    sheet = (out / "fonts" / f["files"]["glyph_sheet"]).read_text()
    assert sheet.count("<path") == 6
    assert "U+FFFD" in sheet
    assert (out / "index.html").read_text().count("<img") >= 2


def test_analyze_scan_only(tmp_path):
    from ectype.synth import Degrade, make_page

    make_page(tmp_path / "page", degrade=Degrade.none())
    s = analyze_pdf(tmp_path / "page" / "scan.pdf", tmp_path / "report", images=False)
    assert s["font_count"] == 0
    p = s["pages"][0]
    assert p["text_shows"] == 0
    assert p["images"][0]["dpi"] == [300.0, 300.0]
    assert s["size_breakdown"]["images"] > 0.9 * s["file_bytes"]
