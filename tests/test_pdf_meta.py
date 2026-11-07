"""The page image choices (JPEG, lossless, black and white) and the document's language,
title and metadata, in every mode that writes a PDF."""

from __future__ import annotations

import json
import shutil
import zlib

import numpy as np
import pikepdf
import pytest
from PIL import Image

import ectype
from ectype.cluster import cluster_workspace
from ectype.fontbuild import build_workspace_fonts
from ectype.ocr import ocr_pages
from ectype.pdfwrite import PageSpec, bcp47, workspace_meta, write_pdf
from ectype.project import Project, Settings
from ectype.segment import segment_workspace
from ectype.substitute import substitute_workspace
from ectype.synth import Degrade, find_font, make_page
from ectype.textlayer import textlayer_workspace
from ectype.traced import traced_workspace

TITLE = "Treatise: תורה"


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    root = tmp_path_factory.mktemp("meta")
    (root / "pages").mkdir()
    for i, seed in enumerate((3, 4)):
        name = f"s{i + 1:02d}"
        make_page(root / "truth" / name, seed=seed,
                  degrade=Degrade(skew_degrees=0.3, blur_sigma=0.6, noise_sigma=6, speckles=40, jpeg_quality=85))
        shutil.copy(root / "truth" / name / "scan.png", root / "pages" / f"{name}.png")
    ocr_pages(root / "pages", root / "ocr")
    segment_workspace(root)
    cluster_workspace(root)
    build_workspace_fonts(root, family="Ectype Test")
    (root / "project.json").write_text(json.dumps({"name": TITLE, "lang": "eng+grc", "page_image": "gray"}),
                                       encoding="utf-8", newline="\n")
    return root


_OPEN = []  # the PDFs stay open while their objects are used


def _images(path):
    pdf = pikepdf.open(path)
    _OPEN.append(pdf)
    return [pg.Resources.XObject.Im0 for pg in pdf.pages]


def _check_meta(path, title=TITLE, lang="en"):
    pdf = pikepdf.open(path)
    assert str(pdf.Root.Lang) == lang
    assert str(pdf.docinfo["/Title"]) == title
    assert str(pdf.docinfo["/Producer"]) == f"Ectype OCR {ectype.__version__}"
    assert str(pdf.docinfo["/Creator"]) == "Ectype OCR"
    assert pdf.Root.ViewerPreferences.DisplayDocTitle is True
    assert "/MarkInfo" not in pdf.Root
    with pdf.open_metadata() as meta:
        assert meta["dc:title"] == title
        assert list(meta["dc:language"]) == [lang]
        assert meta["xmp:CreatorTool"] == "Ectype OCR"
        assert meta["pdf:Producer"] == f"Ectype OCR {ectype.__version__}"


def test_lossless_background_is_the_same_pixels(tmp_path):
    rng = np.random.default_rng(1)
    gray = rng.integers(0, 256, (120, 90), dtype=np.uint8)
    for lossless in (False, True):
        write_pdf(tmp_path / "t.pdf", [PageSpec(90, 120, 300, background=gray, background_dpi=0, lossless=lossless)], [])
        im = _images(tmp_path / "t.pdf")[0]
        if lossless:
            assert im.Filter == "/FlateDecode"
            assert np.array_equal(np.frombuffer(zlib.decompress(im.read_raw_bytes()), np.uint8).reshape(120, 90), gray)
            assert np.array_equal(np.asarray(pikepdf.PdfImage(im).as_pil_image()), gray)
        else:
            assert im.Filter == "/DCTDecode"


def test_textlayer_modes_and_size(book):
    sizes = {}
    for image in ("gray", "lossless", "bw"):
        s = textlayer_workspace(book, out_name=f"tl-{image}", image=image)
        path = book / "textlayer" / s["pdf"]
        sizes[image] = path.stat().st_size
        filt = str(_images(path)[0].Filter)
        assert filt == {"gray": "/DCTDecode", "lossless": "/FlateDecode", "bw": "/CCITTFaxDecode"}[image]
        _check_meta(path)
    src = np.asarray(Image.open(book / "pages" / "s01.png").convert("L"))
    got = np.asarray(pikepdf.PdfImage(_images(book / "textlayer" / "tl-lossless.pdf")[0]).as_pil_image())
    assert np.array_equal(got, src)
    assert sizes["gray"] < sizes["lossless"]
    print("sizes", sizes)


def test_traced_and_substitute_modes(book):
    for image, filt in (("gray", "/DCTDecode"), ("lossless", "/FlateDecode")):
        traced_workspace(book, out_name=f"t-{image}", image=image)
        substitute_workspace(book, find_font(None), out_name=f"s-{image}", image=image)
        for mode, name in (("traced", f"t-{image}"), ("substitute", f"s-{image}")):
            path = book / mode / f"{name}.pdf"
            assert str(_images(path)[0].Filter) == filt
            _check_meta(path)
        _check_meta(book / "traced" / "text-only.pdf")
    assert (book / "traced" / "t-lossless.pdf").stat().st_size > (book / "traced" / "t-gray.pdf").stat().st_size


def test_manuscript_uses_its_own_languages(tmp_path):
    p = Project(tmp_path / "book.ectype")
    (p.workspace / "pages").mkdir(parents=True)
    Image.new("L", (200, 100), 255).save(p.workspace / "pages" / "p001.png", dpi=(300, 300))
    p.set_script("manuscript")
    p.settings.ms_languages = ["syr", "heb"]
    p.settings.lang = "eng"
    p.save_settings()
    s = textlayer_workspace(p.workspace, image="lossless")
    path = p.workspace / "textlayer" / s["pdf"]
    assert str(_images(path)[0].Filter) == "/FlateDecode"
    _check_meta(path, title="book.ectype", lang="syr")  # no name or scan: the folder's name


def test_title_language_fallbacks(tmp_path):
    codes = ("eng", "heb", "grc", "ell", "lat", "syr", "ara", "xxx", None, "eng+grc")
    assert [bcp47(c) for c in codes] == ["en", "he", "grc", "el", "la", "syr", "ar", None, None, "en"]
    ws = tmp_path / "w.ectype"
    ws.mkdir()
    assert workspace_meta(ws) == ("w.ectype", None)
    (ws / "project.json").write_text(json.dumps({"source": str(tmp_path / "Scan One.pdf"), "lang": "grc"}), encoding="utf-8")
    assert workspace_meta(ws) == ("Scan One", "grc")
    assert workspace_meta(ws, "chosen")[0] == "chosen"


def test_old_project_json_loads(tmp_path):
    ws = tmp_path / "old.ectype"
    ws.mkdir()
    (ws / "project.json").write_text(json.dumps({"mode": "text", "lang": "eng"}), encoding="utf-8")
    assert Project(ws).settings.page_image == "gray"
    assert Settings(page_image="lossless").page_image == "lossless"
