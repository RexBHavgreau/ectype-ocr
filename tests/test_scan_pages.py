"""The scan's own pages under Ectype's text, and the "Pages only" output (textlayer.py).

Beaux: "adding a text layer should realistically only increase the file size by the amount of data
needed to set the text on the page". A black-and-white scan coded again as grey JPEG grew six to
seven times (the English-Greek dictionary extract in corpus/: 850 KB -> 5.7 MB); drawn from the
scan's own page it grows by the text (944 KB). These tests hold that the page drawn so is the page
preprocessing made (turned, split, straightened), that its pictures are not coded again, and that an
earlier hidden text layer is left out.
"""

from __future__ import annotations

import json

import cv2
import numpy as np
import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image

from ectype import preprocess as pre
from ectype.synth import Degrade, make_page
from ectype.textlayer import pages_workspace, textlayer_workspace

SKEWS = (0.6, -0.4)


def _spread_pdf(root, old_text: bool = False):
    """Two pages as a spread, each half skewed, stored upside down with a wrong /Rotate (as the
    corpus scans have it), optionally with an old hidden text layer."""
    halves = []
    for side, skew in zip(("left", "right"), SKEWS):
        make_page(root / side, degrade=Degrade.none(), point_size=10, page_size_pt=(396, 612))
        page = np.asarray(Image.open(root / side / "clean.png"))
        h, w = page.shape
        halves.append(cv2.warpAffine(page, cv2.getRotationMatrix2D((w / 2, h / 2), skew, 1.0), (w, h), borderValue=255))
    image = np.hstack([halves[0], np.full((halves[0].shape[0], 140), 255, np.uint8), halves[1]])
    stored = np.ascontiguousarray(np.rot90(image, 2))
    pdf = pikepdf.new()
    xobj = pikepdf.Stream(pdf, stored.tobytes())
    xobj.Type, xobj.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    xobj.Width, xobj.Height, xobj.BitsPerComponent = stored.shape[1], stored.shape[0], 8
    xobj.ColorSpace = pikepdf.Name.DeviceGray
    wp, hp = stored.shape[1] * 72 / 300, stored.shape[0] * 72 / 300
    page = pdf.add_blank_page(page_size=(wp, hp))
    page.Resources.XObject = pikepdf.Dictionary(Im0=xobj)
    content = f"q {wp} 0 0 {hp} 0 0 cm /Im0 Do Q"
    if old_text:
        page.Resources.Font = pikepdf.Dictionary(F1=pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
        content += " BT 3 Tr /F1 12 Tf 50 50 Td (OLDLAYER) Tj ET"
    page.Contents = pikepdf.Stream(pdf, content.encode())
    page.Rotate = 270
    path = root / "spread.pdf"
    pdf.save(path)
    return path


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    root = tmp_path_factory.mktemp("scanpages")
    path = _spread_pdf(root, old_text=True)
    ws = root / "ws"
    pre.preprocess(path, ws)
    return path, ws


def _render(pdf_path, index, dpi):
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return np.asarray(doc[index].render(scale=dpi / 72, grayscale=True).to_pil().convert("L"), float)
    finally:
        doc.close()


def test_the_scan_page_drawn_is_the_page_preprocessing_made(book):
    path, ws = book
    s = pages_workspace(ws, out_name="pages")
    out = ws / "pages-pdf" / "pages.pdf"
    assert s["as_scanned"] == 2 and [r["page"] for r in s["pages"]] == ["p001L", "p001R"]
    for i, name in enumerate(["p001L", "p001R"]):
        made = np.asarray(Image.open(ws / "pages" / f"{name}.png"), float)
        drawn = _render(out, i, 300)
        h, w = min(made.shape[0], drawn.shape[0]), min(made.shape[1], drawn.shape[1])
        assert abs(drawn.shape[0] - made.shape[0]) <= 1 and abs(drawn.shape[1] - made.shape[1]) <= 1
        inner = (slice(30, h - 30), slice(30, w - 30))
        a, b = drawn[:h, :w], made[:h, :w]
        # pdfium places a turned picture to within a few pixels (poppler: exactly), so the best
        # match is looked for nearby; turned the wrong way round it is nothing like.
        best = min((np.abs(np.roll(np.roll(a, dy, 0), dx, 1)[inner] - b[inner]).mean(), dx, dy)
                   for dx in range(-4, 5) for dy in range(-4, 5))
        wrong = np.abs(np.rot90(a, 2)[inner] - b[inner]).mean()
        assert best[0] < 8 and max(abs(best[1]), abs(best[2])) <= 3, f"{name}: {best}"
        assert wrong > 3 * best[0]


def test_the_pictures_are_not_coded_again_and_the_file_grows_by_the_text(book):
    path, ws = book
    s = textlayer_workspace(ws, out_name="tl", image="original")
    out = ws / "textlayer" / "tl.pdf"
    with pikepdf.open(path) as scan, pikepdf.open(out) as made:
        original = next(iter(scan.pages[0].Resources.XObject.values())).read_raw_bytes()
        images = [o for o in made.objects if isinstance(o, pikepdf.Stream) and o.get("/Subtype") == "/Image"]
        assert len(images) == 1, "the two halves share the scan's one picture"
        assert images[0].read_raw_bytes() == original
    assert s["bytes"] < path.stat().st_size + 60_000


def test_an_earlier_hidden_text_layer_is_left_out(book):
    path, ws = book
    textlayer_workspace(ws, out_name="tl2", image="original")
    doc = pdfium.PdfDocument(str(ws / "textlayer" / "tl2.pdf"))
    try:
        text = "".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    finally:
        doc.close()
    assert "OLDLAYER" not in text
    with pikepdf.open(path) as scan:  # the scan itself is never changed
        assert b"OLDLAYER" in scan.pages[0].Contents.read_bytes()


def test_a_scan_changed_since_its_pages_were_made_is_coded_as_pictures(book, tmp_path):
    path, ws = book
    import shutil

    copy = tmp_path / "ws"
    shutil.copytree(ws, copy)
    other = tmp_path / "other.pdf"
    with pikepdf.open(path) as pdf:  # the same file name, a page of another size
        pdf.pages[0].MediaBox = [0, 0, 300, 300]
        pdf.save(other)
    data = json.loads((copy / "preprocess" / "preprocess.json").read_text(encoding="utf-8"))
    data["input"] = str(other)
    (copy / "preprocess" / "preprocess.json").write_text(json.dumps(data), encoding="utf-8")
    s = pages_workspace(copy, out_name="pages")
    assert s["as_scanned"] == 0 and {r["image"] for r in s["pages"]} == {"gray"}
