from __future__ import annotations

import re
import shutil
import zipfile
from xml.dom import minidom

import pypdfium2 as pdfium
import pytest

from ectype import languages
from ectype.cluster import cluster_workspace
from ectype.idml import Face, IdmlOptions, export_idml
from ectype.layout import build_layout, join_lines
from ectype.ocr import ocr_pages
from ectype.project import Project
from ectype.segment import segment_workspace
from ectype.synth import Degrade, make_page
from ectype.textlayer import textlayer_workspace


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    root = tmp_path_factory.mktemp("export")
    (root / "pages").mkdir()
    for i, seed in enumerate((3, 4)):
        name = f"p{i + 1:03d}"
        make_page(root / "truth" / name, seed=seed, degrade=Degrade(skew_degrees=0.2, blur_sigma=0.5, noise_sigma=4,
                                                                   speckles=20, jpeg_quality=90))
        shutil.copy(root / "truth" / name / "scan.png", root / "pages" / f"{name}.png")
    ocr_pages(root / "pages", root / "ocr")
    return root


def _words(text: str) -> list[str]:
    return text.split()


def test_text_layer(workspace):
    from ectype.ocr import OcrPage
    import json

    for image in ("gray", "bw"):
        s = textlayer_workspace(workspace, out_name=f"tl-{image}", image=image)
        doc = pdfium.PdfDocument(str(workspace / "textlayer" / s["pdf"]))
        ocr = OcrPage.from_json(json.loads((workspace / "ocr" / "p001.ocr.json").read_text(encoding="utf-8")))
        assert _words(doc[0].get_textpage().get_text_range()) == _words(ocr.text())
        # The words sit over their scanned position.
        first = ocr.lines[0].words[0]
        box = doc[0].get_textpage().get_charbox(0)
        k = 72 / ocr.dpi
        assert abs(box[0] - first.box[0] * k) < 2
    gray = (workspace / "textlayer" / "tl-gray.pdf").stat().st_size
    bw = (workspace / "textlayer" / "tl-bw.pdf").stat().st_size
    assert bw < gray / 3


def test_language_codes_and_download(tmp_path, monkeypatch):
    assert languages.split("eng+grc+eng+") == ["eng", "grc"]
    assert languages.join([]) == "eng"
    assert languages.name("grc") == "Greek (Ancient)"
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    source = tmp_path / "server"
    source.mkdir()
    (source / "lat.traineddata").write_bytes(b"x" * 1000)
    seen = []
    done = languages.download(["lat"], lambda code, got, total: seen.append((code, got, total)),
                              url=(source.as_uri() + "/{code}.traineddata"))
    assert done == ["lat"] and seen[-1] == ("lat", 1000, 1000)
    assert "lat" in languages.installed() and "eng" in languages.installed(), "the shipped languages are copied beside it"
    assert not list((tmp_path / "data" / "tessdata").glob("*.part"))


@pytest.fixture(scope="module")
def analysed(workspace):
    segment_workspace(workspace)
    cluster_workspace(workspace)
    return workspace, build_layout(workspace)


def test_layout(analysed):
    ws, layout = analysed
    assert [p.name for p in layout.pages] == ["p001", "p002"]
    for page in layout.pages:
        text = [b for b in page.blocks if b.kind == "text"]
        assert text and sum(len(b.lines) for b in text) >= 0.9 * sum(len(b.lines) for b in page.blocks)
        assert any(p.align == "justify" for b in text for p in b.paragraphs)


def test_dehyphenation():
    from ectype.layout import LLine, LWord

    def line(*words):
        return LLine([LWord(w, [0, 0, 1, 1], superscript=[False] * len(w)) for w in words], [0, 0, 1, 1], 0, 0, 10)

    lines = [line("the", "in-"), line("spiration", "of"), line("well-"), line("known", "words")]
    words = [w.text for w in join_lines(lines, {"the", "of", "well-known", "words", "well", "known"})]
    assert words == ["the", "inspiration", "of", "well-known", "words"]


def _check_package(path):
    z = zipfile.ZipFile(path)
    first = z.infolist()[0]
    assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
    assert z.read("mimetype") == b"application/vnd.adobe.indesign-idml-package"
    for name in z.namelist():
        if name.endswith(".xml"):
            minidom.parseString(z.read(name))
    design = z.read("designmap.xml").decode()
    for src in re.findall(r'src="([^"]+)"', design):
        assert src in z.namelist(), src
    stories = "".join(z.read(n).decode() for n in z.namelist() if n.startswith("Stories/"))
    return z, design, re.sub(r"<[^>]+>", " ", stories)


def test_idml_reflow_and_facsimile(analysed, tmp_path):
    ws, layout = analysed
    ocr_words = [w.text for p in layout.pages for b in p.blocks for l in b.lines for w in l.words]

    out = export_idml(ws, tmp_path / "book-reflow.idml", IdmlOptions(mode="reflow", body=Face("Liberation Serif")), layout)
    z, design, text = _check_package(out["idml"])
    assert out["pages"] >= 2
    body = [w.rstrip("-") for w in ocr_words]
    found = sum(1 for w in body if w in text)
    assert found >= 0.9 * len(body)
    assert design.count("idPkg:Spread") == out["pages"]

    out = export_idml(ws, tmp_path / "book-facsimile.idml", IdmlOptions(mode="facsimile"), layout)
    z, design, text = _check_package(out["idml"])
    assert out["pages"] == 2 and out["stories"] >= 2
    assert all(w in text for w in ocr_words[:50])
    assert " " in text, "lines kept as forced line breaks"
    assert (tmp_path / "book-facsimile Links" / "p001.jpg").is_file()
    spread = z.read(next(n for n in z.namelist() if n.startswith("Spreads/"))).decode()
    assert 'Name="Scan"' in spread and "LinkResourceURI" in spread


def test_project_prepares_idml_once(analysed):
    ws, _ = analysed
    project = Project(ws)
    stages = []
    prepared = project.prepare_idml(lambda stage, msg: stages.append(stage))
    assert "segment" not in stages and "cluster" not in stages, "fresh results are reused"
    assert prepared["layout"].pages and prepared["matches"]
    assert (ws / "cluster" / "font-matches.json").is_file()
