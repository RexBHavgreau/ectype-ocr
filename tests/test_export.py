from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path
from xml.dom import minidom

import pypdfium2 as pdfium
import pytest

from ectype import languages
from ectype.cluster import cluster_workspace
from ectype.idml import IdmlOptions, export_idml
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
    assert '<?aid style="50" type="document"' in design, "InDesign recognises a package by this line"
    for src in re.findall(r'src="([^"]+)"', design):
        assert src in z.namelist(), src
    stories = "".join(z.read(n).decode() for n in z.namelist() if n.startswith("Stories/"))
    return z, design, re.sub(r"<[^>]+>", " ", stories)


def _validate(path):
    """docx-to-idml's checker: package structure, links between parts, and Adobe's schema
    (jing). Runs where docx-to-idml's source is present (ECTYPE_DOCX2IDML_SOURCE), as in CI."""
    import os
    import subprocess
    import sys

    source = os.environ.get("ECTYPE_DOCX2IDML_SOURCE")
    if not source or not Path(source, "tools", "idml_check.py").is_file():
        return
    out = subprocess.run([sys.executable, str(Path(source, "tools", "idml_check.py")), str(path)],
                         capture_output=True, text=True)
    assert out.returncode == 0 and ": OK" in out.stdout, out.stdout + out.stderr


def test_facsimile(analysed, tmp_path):
    ws, layout = analysed
    ocr_words = [w.text for p in layout.pages for b in p.blocks for l in b.lines for w in l.words]
    out = export_idml(ws, tmp_path, "book", IdmlOptions(mode="facsimile"), layout)
    assert Path(out["idml"]) == tmp_path / "book" / "book.idml"
    z, design, text = _check_package(out["idml"])
    assert out["pages"] == 2 and out["frames"] >= 2
    assert all(w in text for w in ocr_words[:50])
    assert "\u2028" in text, "lines kept as forced line breaks"
    assert (tmp_path / "book" / "Links" / "p001.jpg").is_file()
    assert 'Name="Scan"' in design and 'Printable="false"' in design
    spread = z.read(next(n for n in z.namelist() if n.startswith("Spreads/"))).decode()
    assert 'LinkResourceURI="file:Links/p001.jpg"' in spread, "linked relative to the IDML"
    assert 'PagesPerDocument="1"' in z.read("Resources/Preferences.xml").decode()
    styles = z.read("Resources/Styles.xml").decode()
    assert 'CharacterStyle/Text"' in styles, "a character style per type style of the scan"
    _validate(out["idml"])


def test_reflow_manuscript(analysed, tmp_path):
    from ectype.docx import DocxOptions, write_docx

    ws, layout = analysed
    made = write_docx(ws, layout, tmp_path / "book.docx", DocxOptions(page_markers=True))
    z = zipfile.ZipFile(made["docx"])
    assert {"[Content_Types].xml", "word/document.xml", "word/styles.xml", "word/footnotes.xml"} <= set(z.namelist())
    document = z.read("word/document.xml").decode()
    text = re.sub(r"<[^>]+>", "", document)
    words = [w.text.rstrip("-") for p in layout.pages for b in p.blocks if b.kind == "text" for l in b.lines for w in l.words]
    assert sum(w in text for w in words) >= 0.95 * len(words)
    assert "{p. " in text or "{page " in text, "page markers"
    assert 'w:rStyle w:val="PageMarker"' in document
    styles = z.read("word/styles.xml").decode()
    assert 'w:name w:val="Check OCR"' in styles and 'w:name w:val="Page Marker"' in styles


def test_reflow_through_docx_to_idml(analysed, tmp_path):
    from ectype import converter

    if converter.find_program() is None:
        pytest.skip("docx-to-idml isn't installed (CI runs it from source)")
    ws, layout = analysed
    out = export_idml(ws, tmp_path, "book", IdmlOptions(mode="reflow"), layout)
    assert Path(out["idml"]).is_file() and Path(out["docx"]).is_file()
    assert Path(out["idml"]).parent == Path(out["folder"])
    _check_package(out["idml"])
    _validate(out["idml"])
    assert out["report"], "docx-to-idml's report is passed on"


def test_converter_missing(tmp_path, monkeypatch):
    from ectype import converter

    monkeypatch.delenv("ECTYPE_DOCX2IDML", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert converter.find_program() is None
    with pytest.raises(converter.ConverterMissing, match="docx-to-idml"):
        converter.convert(tmp_path / "x.docx", tmp_path, "x")
    (tmp_path / "docx-to-idml").mkdir()
    exe = tmp_path / "docx-to-idml-0.8.0.exe"
    exe.write_text("")
    (tmp_path / "docx-to-idml" / "program.json").write_text(json.dumps({"exe": str(exe), "version": "0.8.0", "api": 1}))
    program = converter.find_program()
    assert program.command == [str(exe)] and program.version == "0.8.0"


def test_project_prepares_idml_once(analysed):
    ws, _ = analysed
    project = Project(ws)
    stages = []
    prepared = project.prepare_idml(lambda stage, msg: stages.append(stage))
    assert "segment" not in stages and "cluster" not in stages, "fresh results are reused"
    assert prepared["layout"].pages and prepared["matches"]
    assert (ws / "cluster" / "font-matches.json").is_file()


def test_a_font_that_fails_to_draw_is_passed_over(monkeypatch):
    """A damaged installed font that opens but fails when a letter is drawn (FreeType's
    "invalid reference") is skipped by font matching, not the end of the IDML export."""
    import numpy as np

    from ectype import fontmatch
    from ectype.synth import find_font

    face = fontmatch.FontFace("Broken", "Regular", str(find_font("FreeSerif")), False, 400, 1000, 450)

    def broken(font, char, xh):
        raise OSError("invalid reference")

    monkeypatch.setattr(fontmatch, "_glyph_feature", broken)
    assert fontmatch._distance(face, {"a": (10, np.zeros(10), 30.0, 1.0)}) is None


def test_unicode_superscripts_become_superscript_formatting():
    """¹ and ᵃ read by a model trained on them go to Word and InDesign as plain characters set
    as superscripts, so a note's number matches its reference."""
    from ectype.docx import _note_number
    from ectype.layout import LWord, plain_superscripts

    assert plain_superscripts("present¹:", [False] * 9) == ("present1:", [False] * 7 + [True, False])
    assert plain_superscripts("3ᵃ", [False, False]) == ("3a", [False, True])
    text, sup = plain_superscripts("¹", [False])
    assert _note_number([LWord(text, [0, 0, 1, 1], False, 0, sup), LWord("Though", [0, 0, 1, 1], False, 0)])[0] == 1
