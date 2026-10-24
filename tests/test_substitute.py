from __future__ import annotations

import json
import shutil

import numpy as np
import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image

from ectype.ocr import get_engine, ocr_pages
from ectype.pdfwrite import GlyphRun, PageSpec, subset_font, write_pdf
from ectype.segment import segment_workspace
from ectype.substitute import substitute_workspace
from ectype.synth import Degrade, find_font, make_page


def test_write_pdf_text_is_extractable(tmp_path):
    font, char_cid, metrics = subset_font(find_font(), "Hello, wörld fi")
    assert "ö" in char_cid
    runs = [GlyphRun("F1", 50, 100, 200, [char_cid[c] for c in "Hello,"], 0.0),
            GlyphRun("F1", 50, 330, 200, [char_cid[c] for c in "wörld"], 2.0, 1.05, actual_text="wörld")]
    page = PageSpec(1000, 400, 300, background=np.full((400, 1000), 230, np.uint8), runs=runs, fill_gray=0.1)
    write_pdf(tmp_path / "t.pdf", [page], [font])
    doc = pdfium.PdfDocument(str(tmp_path / "t.pdf"))
    text = doc[0].get_textpage().get_text_bounded()
    assert "Hello," in text and "wörld" in text
    pdf = pikepdf.open(tmp_path / "t.pdf")
    f = pdf.pages[0].Resources.Font.F1
    assert f.Subtype == "/Type0" and f.Encoding == "/Identity-H" and "/ToUnicode" in f
    content = pdf.pages[0].Contents.read_bytes()
    assert b"105 Tz" in content and b"/ActualText" in content


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    root = tmp_path_factory.mktemp("sub")
    truth = make_page(root / "truth" / "s01", degrade=Degrade(skew_degrees=0.4, blur_sigma=0.5, noise_sigma=4, speckles=10, jpeg_quality=90))
    (root / "pages").mkdir()
    shutil.copy(root / "truth" / "s01" / "scan.png", root / "pages" / "s01.png")
    ocr_pages(root / "pages", root / "ocr")
    segment_workspace(root, truth_root=root / "truth")
    return root, truth


def test_substitute_same_font_lands_on_the_ink(workspace):
    root, truth = workspace
    # A real scan is never perfectly square to the pixel grid; with zero skew every letter
    # edge falls on the same pixel phase and binarized heights quantize a few percent low.
    s = substitute_workspace(root, truth["font"]["path"], out_name="out")
    page = s["pages"][0]
    assert abs(page["size_pt"] - truth["point_size"]) / truth["point_size"] < 0.03
    assert s["within_1px"]["left"] >= 0.97 and s["within_1px"]["right"] >= 0.95
    assert page["text_cer_vs_ocr"] < 0.005
    doc = pdfium.PdfDocument(str(root / "substitute" / "out.pdf"))
    assert doc[0].get_textpage().search("ligatures", match_whole_word=True).get_next() is not None
    background = np.asarray(Image.open(root / "substitute" / "s01.background.png"))
    assert (background < 100).mean() < 0.002, "text is erased from the background"
