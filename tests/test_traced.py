from __future__ import annotations

import json
import shutil

import numpy as np
import pikepdf
import pypdfium2 as pdfium
import pytest

from ectype import trace
from ectype.cluster import cluster_workspace
from ectype.fontbuild import build_workspace_fonts, solve_sidebearings
from ectype.ocr import ocr_pages
from ectype.segment import segment_workspace
from ectype.synth import Degrade, make_page
from ectype.traced import traced_workspace


def test_trace_ring_winding():
    """A ring traces to an outer contour (counter-clockwise, y up) and a hole (clockwise)."""
    yy, xx = np.mgrid[0:80, 0:80]
    r = np.hypot(xx - 40, yy - 40)
    ring = (r < 30) & (r > 15)
    contours = trace.to_font(trace.trace_bitmap(ring), 0, 70, 1.0)
    assert len(contours) == 2
    areas = sorted(trace._area(trace._polygon(c)) for c in contours)
    assert areas[0] < 0 < areas[1] and abs(areas[1]) > abs(areas[0])
    x0, y0, x1, y1 = trace.bounds(contours)
    assert abs((x1 - x0) - 60) < 3 and abs(y1 - 60) < 3  # outer radius 30, centre 30 above the baseline row


def test_sidebearings_from_gaps():
    # gap(a,b) = rsb_a + lsb_b; symmetric letters, so lsb = rsb = gap / 2.
    pairs = {"1,2": {"median": 0.1, "count": 50}, "2,1": {"median": 0.1, "count": 50},
             "1,1": {"median": 0.1, "count": 50}, "2,2": {"median": 0.1, "count": 50}}
    lsb, rsb = solve_sidebearings({1: 200, 2: 250}, pairs)
    for c in (1, 2):
        assert abs(lsb[c] - 25) < 2 and abs(rsb[c] - 25) < 2


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    root = tmp_path_factory.mktemp("traced")
    (root / "pages").mkdir()
    for i, seed in enumerate((3, 4)):
        name = f"s{i + 1:02d}"
        make_page(root / "truth" / name, seed=seed,
                  degrade=Degrade(skew_degrees=0.3, blur_sigma=0.6, noise_sigma=6, speckles=40, jpeg_quality=85))
        shutil.copy(root / "truth" / name / "scan.png", root / "pages" / f"{name}.png")
    ocr_pages(root / "pages", root / "ocr")
    segment_workspace(root)
    cluster_workspace(root)
    return root, build_workspace_fonts(root, family="Ectype Test")


def test_fonts_validate(workspace):
    _, fonts = workspace
    assert fonts["styles"]
    for st in fonts["styles"]:
        assert st["validation"] == {"ots": "pass", "fonttools": "pass"}, st


def test_traced_pdf(workspace):
    root, _ = workspace
    s = traced_workspace(root, out_name="out")
    assert s["within_1px"]["left"] >= 0.9 and s["within_1px"]["right"] >= 0.8, s["within_1px"]
    assert all(p.get("text_cer_vs_ocr", 0) < 0.01 for p in s["pages"])
    doc = pdfium.PdfDocument(str(root / "traced" / "out.pdf"))
    assert doc[0].get_textpage().search("ligatures", match_whole_word=True).get_next() is not None
    pdf = pikepdf.open(root / "traced" / "out.pdf")
    font = next(iter(pdf.pages[0].Resources.Font.values()))
    desc = font.DescendantFonts[0]
    assert desc.Subtype == "/CIDFontType0" and desc.FontDescriptor.FontFile3.Subtype == "/CIDFontType0C"
    assert b"TJ" in pdf.pages[0].Contents.read_bytes()
    info = json.loads((root / "fonts" / "style-0.json").read_text())
    assert len(info["glyphs"]) < 120, "one glyph per character and style, roughly"
