"""Plain text output: the paragraphs and lines forms, page markers, exact characters, the file's
bytes, a workspace with OCR only, the app's Export text and Plain text mode, and the batch that
makes a text file beside each scan."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unicodedata
from pathlib import Path

import pytest
from PIL import Image

from ectype import plaintext, textfiles
from ectype.layout import Block, LLine, LPage, Layout, LWord, Paragraph
from ectype.ocr import Line, OcrPage, Word

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BERESHIT = "בְּרֵאשִׁ֖ית"  # pointed, with the accent
BARA = "בָּרָ֣א"
GREEK_NFD = "ἐν ἀρχῆ"  # ἐν ἀρχῇ, letters + combining marks
GREEK_NFC = unicodedata.normalize("NFC", GREEK_NFD)
assert GREEK_NFD != GREEK_NFC


def _line(words: list[str], y: int, x0: int = 100, x1: int | None = None, rtl: bool = False) -> Line:
    """A line of words, stretched to end at x1 (a justified line) or at their natural width.
    rtl: the first word (logically first) stands at the right."""
    sizes = [14 * len(w) for w in words]
    gap = 14
    if x1 is not None:
        extra = (x1 - x0 - sum(sizes) - gap * (len(words) - 1)) / max(1, sum(sizes))
        sizes = [int(s * (1 + extra)) for s in sizes]
    spans, x = [], x0
    for s in sizes:
        spans.append((x, x + s))
        x += s + gap
    if rtl:
        total = spans[-1][1]
        spans = [(x0 + total - b, x0 + total - a) for a, b in spans]
    out = [Word(w, [a, y, b, y + 40], 90.0) for w, (a, b) in zip(words, spans)]
    left, right = min(a for a, _ in spans), max(b for _, b in spans)
    return Line([left, y, right, y + 40], [0.0, 0.0], 40.0, None, None, out)


def _text_line(text: str, y: int, kind: str = "short") -> Line:
    words = text.split(" ")
    if kind == "indent":
        return _line(words, y, 140, 1100)
    return _line(words, y, 100, 1100 if kind == "full" else None)


def _workspace(root: Path, pages: dict[str, list[Line]], size=(1200, 1800)) -> Path:
    (root / "pages").mkdir(parents=True)
    (root / "ocr").mkdir()
    for name, lines in pages.items():
        Image.new("L", size, 255).save(root / "pages" / f"{name}.png", dpi=(300, 300))
        page = OcrPage("tesseract", "eng", list(size), 300.0, lines)
        (root / "ocr" / f"{name}.ocr.json").write_text(json.dumps(page.to_json()), encoding="utf-8")
    return root


def _book(root: Path) -> Path:
    """Three pages (folios 12-14) with a running head and a folio, a hyphen split over a line end,
    a paragraph that runs on to the next page with a word split there, and notes at the foot."""
    def frame(folio: str, body: list[Line], notes: list[Line] | None = None) -> list[Line]:
        return [_line(["THE", "TREATISE"], 60, 450)] + body + (notes or []) + [_line([folio], 1650, 580)]

    p1 = frame("12", [
        _text_line("The doctrine of inspi-", 300, "indent"),
        _text_line("ration is old and many words fill", 360, "full"),
        _text_line("the page to the end of this line.", 420),
        _text_line("Second paragraph with word¹ and 3ᵃ here and", 480, "indent"),
        _text_line(f"Hebrew {BERESHIT} {BARA} then the lines go on, and", 540, "full"),
        _text_line("the last line of the page ends with a contin-", 600, "full"),
    ], [_text_line(f"¹ Compare {BERESHIT} here", 1400)])
    p2 = frame("13", [
        _text_line(f"uation of the second paragraph, with {GREEK_NFD} in it.", 300),
        _text_line(f"A third one, precomposed {GREEK_NFC} and a", 360, "indent"),
        _text_line(f"mixed line: the word {BARA} means created.", 420),
        _text_line("A final paragraph here with several words in", 480, "indent"),
        _text_line("it, to make the page look like a page of", 540, "full"),
        _text_line("a book, with enough lines to be text.", 600),
    ])
    p3 = frame("14", [_text_line("Fourth paragraph on the third page, which runs", 300, "indent"),
                _text_line("over several lines so the page is not too sparse", 360, "full"),
                _text_line("to take its place as a block of text and more", 420, "full"),
                _text_line("words follow in the next lines of it,", 480, "full"),
                _text_line("so that gaps between lines are many.", 540)],
               [_text_line("2 A second note.", 1400)])
    return _workspace(root, {"p001": p1, "p002": p2, "p003": p3})


def _read(path: Path) -> str:
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 with a byte order mark"
    assert b"\n" not in raw.replace(b"\r\n", b""), "Windows line ends only"
    return raw.decode("utf-8-sig").replace("\r\n", "\n")


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    return _book(tmp_path_factory.mktemp("plain") / "ws")


PARAGRAPHS = [
    "The doctrine of inspiration is old and many words fill the page to the end of this line.",
    f"Second paragraph with word¹ and 3ᵃ here and Hebrew {BERESHIT} {BARA} then the lines go on, and "
    "the last line of the page ends with a continuation of the second paragraph, with " + GREEK_NFD + " in it.",
    f"A third one, precomposed {GREEK_NFC} and a mixed line: the word {BARA} means created.",
    "A final paragraph here with several words in it, to make the page look like a page of a book, "
    "with enough lines to be text.",
    "Fourth paragraph on the third page, which runs over several lines so the page is not too sparse "
    "to take its place as a block of text and more words follow in the next lines of it, "
    "so that gaps between lines are many.",
]


def test_workspace_has_ocr_only(book):
    assert not (book / "segment").exists() and not (book / "cluster").exists()


def test_paragraphs(book, tmp_path):
    out = plaintext.write_text(book, tmp_path / "a.txt", page_markers=False, keep_marks=True)
    text = _read(tmp_path / "a.txt")
    notes = ["Notes", f"{{p. 12}} ¹ Compare {BERESHIT} here", "{p. 14} 2 A second note."]
    assert text == "\n\n".join(PARAGRAPHS + notes) + "\n"
    assert out["pages"] == 3 and out["paragraphs"] == 5 and out["notes"] == 2 and out["chars"] == len(text)
    assert "THE TREATISE" not in text and "inspiration" in text and "inspi-" not in text


def test_page_markers_and_kept_heads(book, tmp_path):
    plaintext.write_text(book, tmp_path / "m.txt", page_markers=True)
    text = _read(tmp_path / "m.txt")
    paras = text.split("\n\n")
    assert paras[0] == "{p. 12} " + PARAGRAPHS[0]
    assert "continuation {p. 13} of the second paragraph" in paras[1], "inside the paragraph the page breaks in"
    assert paras[4] == "{p. 14} " + PARAGRAPHS[4]
    plaintext.write_text(book, tmp_path / "h.txt", page_markers=False, keep_heads=True)
    kept = _read(tmp_path / "h.txt").split("\n\n")
    assert kept[0] == "THE TREATISE" and "12" in kept and kept.count("THE TREATISE") == 3
    assert "contin-" in "\n".join(kept), "with running heads between, a paragraph isn't joined across pages"


def test_lines_form(book, tmp_path):
    plaintext.write_text(book, tmp_path / "l.txt", form="lines", page_markers=True, keep_marks=True)
    pages = _read(tmp_path / "l.txt").split("\n\n")
    assert len(pages) == 3
    first = pages[0].split("\n")
    assert first[0] == "{p. 12}" and first[1] == "The doctrine of inspi-" and first[2] == "ration is old and many words fill"
    assert first[-1] == f"¹ Compare {BERESHIT} here", "notes where they stand, the folio left out"
    assert pages[1].split("\n")[1].startswith("uation of the second")
    plaintext.write_text(book, tmp_path / "l2.txt", form="lines", page_markers=False)
    assert _read(tmp_path / "l2.txt").split("\n\n")[2] == (
        "Fourth paragraph on the third page, which runs\nover several lines so the page is not too sparse\n"
        "to take its place as a block of text and more\nwords follow in the next lines of it,\n"
        "so that gaps between lines are many.\n2 A second note.\n")
    with pytest.raises(ValueError):
        plaintext.write_text(book, tmp_path / "x.txt", form="columns")


def test_header_line(book, tmp_path):
    plaintext.write_text(book, tmp_path / "h.txt", header=plaintext.header_line("Treatise 01.pdf", "eng+grc", "2026-11-14"))
    text = _read(tmp_path / "h.txt")
    assert text.startswith('Ectype OCR text of "Treatise 01.pdf" (eng+grc), 2026-11-14\n\n{p. 12} The doctrine')
    assert plaintext.is_ours(tmp_path / "h.txt")
    (tmp_path / "mine.txt").write_text("my notes", encoding="utf-8")
    assert not plaintext.is_ours(tmp_path / "mine.txt")


def test_exact_characters_and_order(tmp_path):
    ws = _workspace(tmp_path / "u", {"p001": [
        _line(["Genesis", "1:1", BERESHIT, BARA], 300, 100, 1100, rtl=True),  # boxes run right to left
        _line(["the", "word", BARA, "means", "created.", "and", GREEK_NFD, GREEK_NFC], 360),
        _line(["word¹", "3ᵃ", "(Dt.", "8,", "3ᵇ)"], 420),
    ]})
    plaintext.write_text(ws, tmp_path / "u.txt", page_markers=False, keep_marks=True)
    text = _read(tmp_path / "u.txt")
    body = text.strip()
    assert body.startswith(f"Genesis 1:1 {BERESHIT} {BARA}"), "the OCR's order, not the boxes'"
    assert [hex(ord(c)) for c in f"{BERESHIT} {BARA}"] == [hex(ord(c)) for c in body.split("1:1 ", 1)[1][:len(f"{BERESHIT} {BARA}")]]
    assert f"the word {BARA} means created." in body, "a Hebrew word inside English keeps its place"
    assert f"and {GREEK_NFD} {GREEK_NFC}" in body, "Greek exactly as read, decomposed and precomposed"
    assert "word¹ 3ᵃ (Dt. 8, 3ᵇ)" in body, "Unicode superscripts stay"
    assert GREEK_NFD in body and body.count(GREEK_NFC) == 1


def test_markers_with_folios_without_and_split_spreads(book, tmp_path):
    import shutil

    book = shutil.copytree(book, tmp_path / "copy")
    assert plaintext.page_marker([172, None], 0, [90, 91]) == "{p. 172 · PDF 90}"
    assert plaintext.page_marker([None, None], 1, [90, 91]) == "{PDF 91}"
    assert plaintext.page_marker([172, None], 0, None) == "{p. 172}"
    assert plaintext.page_marker([None, None], 1, [None, None]) == "{page 2}"
    # The book above with a scan record: pages 12-14 came from PDF pages 90-92.
    (book / "preprocess").mkdir(exist_ok=True)
    record = {"input": "Treatise 01.pdf", "pages": [{"name": f"p00{k}", "source_page": 89 + k} for k in (1, 2, 3)]}
    (book / "preprocess" / "preprocess.json").write_text(json.dumps(record), encoding="utf-8")
    plaintext.write_text(book, tmp_path / "pdf.txt")
    text = _read(tmp_path / "pdf.txt")
    assert text.startswith("{p. 12 · PDF 90} The doctrine") and "continuation {p. 13 · PDF 91} of" in text
    assert text.endswith("{p. 14 · PDF 92} 2 A second note.\n"), "notes carry the same marker"
    plaintext.write_text(book, tmp_path / "pdf-lines.txt", form="lines")
    assert _read(tmp_path / "pdf-lines.txt").split("\n\n")[1].startswith("{p. 13 · PDF 91}\n")
    # A two-page spread split in two: both halves are from the same PDF page; no folios known.
    ws = _workspace(tmp_path / "spread", {
        "p001L": [_text_line("Left hand page text.", 300)], "p001R": [_text_line("Right hand page text.", 300)],
        "p002L": [_text_line("Next spread left.", 300)]})
    (ws / "preprocess").mkdir()
    record = {"input": "x.pdf", "pages": [{"name": "p001L", "source_page": 5}, {"name": "p001R", "source_page": 5},
                                          {"name": "p002L", "source_page": 6}]}
    (ws / "preprocess" / "preprocess.json").write_text(json.dumps(record), encoding="utf-8")
    plaintext.write_text(ws, tmp_path / "s.txt", form="lines")
    assert _read(tmp_path / "s.txt") == ("{PDF 5}\nLeft hand page text.\n\n{PDF 5}\nRight hand page text.\n\n"
                                         "{PDF 6}\nNext spread left.\n")
    record["input"] = "x.png"  # an image scan has no PDF pages
    (ws / "preprocess" / "preprocess.json").write_text(json.dumps(record), encoding="utf-8")
    plaintext.write_text(ws, tmp_path / "s2.txt", form="lines")
    assert _read(tmp_path / "s2.txt").startswith("{page 1}\nLeft hand")


def _word(text: str) -> LWord:
    return LWord(text, [0, 0, 10, 10])


def test_headings_stand_alone():
    def block(kind, *lines):
        paras = [Paragraph([LLine([_word(w) for w in l.split()], [0, 0, 100, 10], 10, 0, 20) for l in lines])]
        return Block(kind, 1.5 if kind == "heading" else 1.0, paras, [0, 0, 100, 10])
    layout = Layout([LPage("p001", 1000, 1000, 300, [block("heading", "CHAPTER ONE"), block("text", "Plain", "words"),
                                                      block("note", "1 A note")], 20)], 20, {})
    ws = Path(".")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        plaintext.write_text(ws, Path(d) / "h.txt", layout, page_markers=False)
        assert _read(Path(d) / "h.txt") == "CHAPTER ONE\n\nPlain words\n\nNotes\n\n{page 1} 1 A note\n"


def test_gui_export_text_and_plain_text_mode(book, tmp_path):
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from PySide6.QtWidgets import QApplication

    from ectype.gui import MODES, MainWindow, TextDialog
    from ectype.project import Project

    app = QApplication.instance() or QApplication([])
    project = Project(book)
    w = MainWindow(project)
    try:
        app.processEvents()
        menus = {m.title().replace("&", ""): m for m in w.menuBar().findChildren(type(w.menuBar().addMenu("tmp")))}
        export = menus["Export"]
        assert w.actions["export_text"] in export.actions() and w.actions["export_text"].text() == "Export text…"
        assert w.actions["export_text"].shortcut().toString() == "Ctrl+T"
        assert w.actions["text_files"] in export.actions()
        assert w.actions["export_text"].isEnabled(), "the book has OCR"
        assert ("plaintext", "Plain text only (no PDF)") in MODES
        w.mode.setCurrentIndex(w.mode.count() - 1)
        assert project.settings.mode == "plaintext" and project.stages() == ["preprocess", "ocr", "plaintext"]
        assert project.update_stage() == "plaintext" and project.output_pdf() is None
        assert not w.actions["export"].isEnabled() and not w.page_image.isEnabled()
        project.run("plaintext")
        assert project.output_text() is not None and project.output_text().parent.name == "plaintext"
        d = TextDialog(project.settings)
        assert d.paragraphs.isChecked() and d.markers.isChecked() and not d.heads.isChecked()
        d.lines.setChecked(True)
        assert d.choices() == {"form": "lines", "page_markers": True, "keep_heads": False, "keep_marks": False}
        d.close(); d.deleteLater()
        w.mode.setCurrentIndex(0)
    finally:
        w.close()
        w.deleteLater()
        app.processEvents()


def test_plain_text_run_writes_beside_the_scan(tmp_path):
    from ectype.project import Project

    scan = tmp_path / "scans" / "Treatise 01.pdf"
    scan.parent.mkdir()
    scan.write_bytes(b"%PDF")
    ws = _book(tmp_path / "ws")
    project = Project(ws, scan)
    project.settings.mode = "plaintext"
    got = project.run("plaintext")
    assert got == scan.with_suffix(".txt") and got.parent == scan.parent
    assert _read(got).startswith('Ectype OCR text of "Treatise 01.pdf" (eng; points and accents removed for searching), ')
    # A text file Ectype didn't write is left alone; the text goes into the workspace.
    got.write_text("someone else's", encoding="utf-8")
    again = project.run("plaintext")
    assert got.read_text(encoding="utf-8") == "someone else's" and again == ws / "plaintext" / "Treatise 01.txt"
    assert again.is_file()


# -- the batch ------------------------------------------------------------------------------

def _pdf(path: Path, seed: int) -> Path:
    from ectype.synth import Degrade, make_page

    truth = Path(tempfile.mkdtemp()) / path.stem  # outside the folder of scans
    make_page(truth, "Inspiration is the word of God given to us. " * 6, seed=seed, point_size=10,
              page_size_pt=(432, 300),
              degrade=Degrade(skew_degrees=0.0, blur_sigma=0.3, noise_sigma=2, speckles=0, jpeg_quality=None))
    Image.open(truth / "scan.png").convert("L").save(path, "PDF", resolution=300)
    return path


def test_batch_makes_a_companion_per_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("OMP_THREAD_LIMIT", "1")
    work = tmp_path / "userdata" / "work"
    monkeypatch.setattr(textfiles, "work_root", lambda: work)
    scans = tmp_path / "scans"
    (scans / "sub").mkdir(parents=True)
    _pdf(scans / "Alpha.pdf", 1)
    _pdf(scans / "sub" / "Beta.pdf", 2)
    (scans / "Bad.pdf").write_bytes(b"this is not a pdf")  # sorts before the others: must not stop them
    (scans / "Mine.pdf").write_bytes(b"x")
    (scans / "Mine.txt").write_text("my own notes", encoding="utf-8")
    (scans / "Done.pdf").write_bytes(b"x")
    (scans / "Done.txt").write_bytes(("﻿" + plaintext.header_line("Done.pdf", "eng") + "\n\nold").encode("utf-8"))
    seen = []
    started = time.time()
    result = textfiles.make_text_files([scans], lang="eng", recursive=True, progress=lambda s, m: seen.append((s, m)))
    took = time.time() - started
    names = lambda items: sorted(Path(s).name for s, *_ in items)  # noqa: E731
    assert names(result["done"]) == ["Alpha.pdf", "Beta.pdf"] and names(result["failed"]) == ["Bad.pdf"]
    assert dict((s.name, why) for s, why in result["skipped"]) == {
        "Mine.pdf": "a text file of that name already exists", "Done.pdf": "already has a text file"}
    for txt in (scans / "Alpha.txt", scans / "sub" / "Beta.txt"):
        text = _read(txt)
        assert text.startswith(f'Ectype OCR text of "{txt.stem}.pdf" (eng; points and accents removed for searching), ') and "{PDF 1}" in text and len(text) > 200
    assert not (scans / "Bad.txt").exists() and (scans / "Mine.txt").read_text(encoding="utf-8") == "my own notes"
    assert not work.exists() or not list(work.iterdir()), "no work folder remains"
    assert not list(scans.rglob("*.ectype")), "the scans' folder holds no workspaces"
    assert any(s == "fraction" for s, _ in seen) and seen[-1][0] == "fraction" and seen[-1][1] == "1.0000"
    # Again: everything with a text file is skipped; --redo remakes only Ectype's own.
    again = textfiles.make_text_files([scans], recursive=True)
    assert not again["done"] and names(again["skipped"]) == ["Alpha.pdf", "Beta.pdf", "Done.pdf", "Mine.pdf"]
    first = (scans / "Alpha.txt").read_bytes()
    redo = textfiles.make_text_files([scans / "Alpha.pdf", scans / "Mine.pdf"], redo=True, keep=True)
    assert names(redo["done"]) == ["Alpha.pdf"] and names(redo["skipped"]) == ["Mine.pdf"]
    assert (scans / "Alpha.txt").read_bytes() == first
    assert len(list(work.iterdir())) == 1, "--keep keeps the work folder"
    print(f"two-PDF batch took {took:.1f} s")


def test_stopped_book_leaves_no_text_file(tmp_path, monkeypatch):
    from ectype import stopping

    monkeypatch.setenv("OMP_THREAD_LIMIT", "1")
    work = tmp_path / "work"
    monkeypatch.setattr(textfiles, "work_root", lambda: work)
    scans = tmp_path / "scans"
    scans.mkdir()
    _pdf(scans / "Alpha.pdf", 3)
    flag = stopping.StopFlag()
    with stopping.active(flag):
        with pytest.raises(stopping.Stopped):
            textfiles.make_text_files([scans], progress=lambda s, m: flag.request() if s == "books" else None)
    assert not list(scans.glob("*.txt")) and not (work.exists() and list(work.iterdir()))


def test_text_files_dialog_and_cli(tmp_path, monkeypatch, capsys):
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from PySide6.QtWidgets import QApplication

    from ectype import cli
    from ectype.gui import TextFilesDialog

    app = QApplication.instance() or QApplication([])
    d = TextFilesDialog("English, Greek (Ancient)")
    assert not d.go.isEnabled() and not d.recursive.isChecked() and not d.redo.isChecked()
    d.folder.setText(str(tmp_path))
    assert d.go.isEnabled()
    d.close(); d.deleteLater(); app.processEvents()
    monkeypatch.setattr(textfiles, "work_root", lambda: tmp_path / "work")
    (tmp_path / "Mine.pdf").write_bytes(b"x")
    (tmp_path / "Mine.txt").write_text("mine", encoding="utf-8")
    assert cli.main(["text-files", str(tmp_path), "--lang", "eng"]) == 0
    assert "skipped Mine.pdf: a text file of that name already exists" in capsys.readouterr().out


def test_search_form():
    sf = plaintext.search_form
    assert sf("בְּרֵאשִׁ֖ית בָּרָ֣א") == "בראשית ברא"
    assert sf("בְּנֵֽי־יִשְׂרָאֵ֑ל") == "בני ישראל"
    assert sf("\ufb2a") == "ש" and sf("\ufb2a") == "\u05e9"
    for text in ("ἐν ἀρχῇ ἦν ὁ λόγος", unicodedata.normalize("NFD", "ἐν ἀρχῇ ἦν ὁ λόγος")):
        assert [hex(ord(c)) for c in sf(text)] == [hex(ord(c)) for c in "εν αρχη ην ο λογος"]
    assert sf("λόγος") == "λογος" and sf("ἡμέρας") == "ημερας", "the final sigma stays"
    assert sf("ܒܪܺܫܺܝܬ") == "ܒܪܫܝܬ"
    assert sf("مُحَمَّد") == "محمد"
    translit = "qūdšāʾ ʾabbāʾ, ḥesed, p\u0304, é, word¹ 3ᵃ"
    assert sf(translit) == translit and sf("p\u0304") == "p\u0304"
    assert sf("the verb בָּרָא (cf. κτίζω)") == "the verb ברא (cf. κτιζω)"
    assert sf("אֵת׃ ׀ ׆ ׳") == "את׃ ׀ ׆ ׳", "sof pasuq, paseq, nun hafukha, geresh stay"


def test_marks_removed_by_default_and_kept_on_request(tmp_path):
    ws = _workspace(tmp_path / "m", {"p001": [_text_line(f"In {BERESHIT} {BARA} and {GREEK_NFD} with ḥesed¹", 300)]})
    plaintext.write_text(ws, tmp_path / "s.txt", page_markers=False)
    assert _read(tmp_path / "s.txt").strip() == "In בראשית ברא and εν αρχη with ḥesed¹"
    plaintext.write_text(ws, tmp_path / "k.txt", page_markers=False, keep_marks=True)
    assert [hex(ord(c)) for c in _read(tmp_path / "k.txt").strip()] == [hex(ord(c)) for c in f"In {BERESHIT} {BARA} and {GREEK_NFD} with ḥesed¹"]
    assert plaintext.header_line("T.pdf", "eng+grc+heb", "2026-11-14", keep_marks=False) == (
        'Ectype OCR text of "T.pdf" (eng+grc+heb; points and accents removed for searching), 2026-11-14')


def test_search_form_drops_script_marks_with_no_letter_before_them():
    """The Treatise's OCR read a word as its points first ("ִּמם" after a space): marks that
    belong only to Hebrew (or Syriac, Arabic, Greek's perispomeni and iota subscript) go
    wherever they stand; marks shared with Latin stay after a Latin letter."""
    from ectype.plaintext import search_form

    assert search_form("So after \u05b4\u05bcמם P") == "So after מם P"
    assert search_form("a\u0342 e\u0301") == "a é"


def test_cli_text_of_a_scan_as_beaux_typed_it(tmp_path, capsys, monkeypatch):
    """Beaux (0.9.0): `ectype text "<book>.pdf" --page-markers` was refused (text took only a
    project folder, and markers are the default), and text-files filled the console with
    "fraction: 0.0013" lines. Now `text` reads a scan with no project as text-files does, the
    flag is accepted, and progress is a percentage on one line (none when not a terminal)."""
    from ectype import cli

    monkeypatch.setenv("OMP_THREAD_LIMIT", "1")
    monkeypatch.setattr(textfiles, "work_root", lambda: tmp_path / "work")
    pdf = _pdf(tmp_path / "Book.pdf", 1)
    assert cli.main(["text", str(pdf), "--page-markers"]) == 0
    out = capsys.readouterr().out
    assert "fraction" not in out and "Reading in: English (eng)" in out
    assert (tmp_path / "Book.txt").is_file() and plaintext.is_ours(tmp_path / "Book.txt")


def test_cli_refuses_a_language_not_installed(tmp_path, capsys):
    from ectype import cli

    pdf = _pdf(tmp_path / "Libro.pdf", 2)
    assert cli.main(["text-files", str(pdf), "--lang", "eng+xyz"]) == 2
    out = capsys.readouterr().out
    assert "Not installed:" in out and "ectype languages --add xyz" in out
    assert not (tmp_path / "Libro.txt").exists()


def test_a_tesseract_failure_is_said_in_plain_words():
    """Beaux (0.9.0): a book read with --lang spa, Spanish not installed, failed with Tesseract's
    whole command line. Its own reason is said instead, with the command to add the language."""
    from ectype.ocr import tesseract_failure

    said = tesseract_failure("Error opening data file /x/spa.traineddata\n"
                             "Failed loading language 'spa'\nTesseract couldn't load any languages!\n", "spa")
    assert said.startswith("Tesseract has no reading data for Spanish (spa)") and "ectype languages --add spa" in said
    assert tesseract_failure("Error in pixReadStream\nImage file cannot be read\n", "eng") == \
        "Tesseract failed: Error in pixReadStream Image file cannot be read"


def test_a_step_command_given_a_scan_says_what_to_use(tmp_path, capsys, monkeypatch):
    """Beaux (0.9.0): `ectype ocr "<book>.pdf" --lang spa` ended in a Python traceback. A step
    command given a scan says what to use instead; any other failure is one line and a file."""
    from ectype import cli

    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    pdf = tmp_path / "Libro.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert cli.main(["ocr", str(pdf), "--lang", "spa"]) == 2
    out = capsys.readouterr().out
    assert "works on a book's project folder" in out and f'ectype text "{pdf}" --lang spa' in out
    assert "Traceback" not in out
    assert cli.main(["ocr", str(tmp_path / "missing")]) == 2
    assert "No such folder" in capsys.readouterr().out
    (tmp_path / "empty.ectype").mkdir()
    monkeypatch.setattr(cli, "cmd_ocr", lambda args: (_ for _ in ()).throw(RuntimeError("something broke")))
    assert cli.main(["ocr", str(tmp_path / "empty.ectype")]) == 1
    out = capsys.readouterr().out
    assert out.startswith("Ectype stopped: something broke") and "Traceback" not in out
    assert (tmp_path / "data" / "logs" / "last-command-error.txt").is_file()


def test_cli_run_puts_the_pdf_beside_the_scan(tmp_path, capsys, monkeypatch):
    """Beaux: "The OCR'd PDF should just go next to the current one." `ectype run` keeps its
    project folder beside the scan (as the app does) and copies the PDF there as "<name> - OCR.pdf";
    the scan is untouched, a file of that name Ectype didn't make is kept, a later run replaces
    Ectype's own. The Plain text mode runs from the command line too."""
    from ectype import cli
    from ectype.project import Project, made_by_ectype

    monkeypatch.setenv("OMP_THREAD_LIMIT", "1")
    pdf = _pdf(tmp_path / "Libro.pdf", 3)
    original = pdf.read_bytes()
    assert cli.main(["run", str(pdf), "--mode", "text"]) == 0
    out = tmp_path / "Libro - OCR.pdf"
    assert out.is_file() and made_by_ectype(out) and pdf.read_bytes() == original
    assert f"PDF: {out}" in capsys.readouterr().out
    assert Project(tmp_path / "Libro.ectype").settings.mode == "text"
    assert cli.main(["run", str(pdf), "--mode", "text"]) == 0  # Ectype's own copy is replaced
    assert not (tmp_path / "Libro - OCR (2).pdf").exists()
    out.write_bytes(b"%PDF-1.4 someone else's")  # not Ectype's: kept
    assert cli.main(["run", str(pdf), "--mode", "text"]) == 0
    assert out.read_bytes() == b"%PDF-1.4 someone else's" and made_by_ectype(tmp_path / "Libro - OCR (2).pdf")
    assert cli.main(["run", str(pdf), "--mode", "plaintext"]) == 0
    assert plaintext.is_ours(tmp_path / "Libro.txt")


def test_images_are_read_and_written_whatever_letters_the_path_holds(tmp_path):
    """Beaux (0.9.0, Windows): "Tratado de la Oración" stopped traced mode: cv2.imread can't open
    a path with letters outside the old code page and returned None. Every image goes through
    imagefiles, which opens the file in Python; no cv2.imread/imwrite is left in the program."""
    import re

    import numpy as np

    from ectype import imagefiles

    labels = (np.arange(60, dtype=np.uint16).reshape(6, 10) * 1000)
    path = tmp_path / "Tratado de la Oración ܐ" / "p001.labels.png"
    path.parent.mkdir()
    assert imagefiles.imwrite(path, labels)
    back = imagefiles.imread(path)
    assert back.dtype == np.uint16 and (back == labels).all()
    assert imagefiles.imread(tmp_path / "missing.png") is None
    src = Path(imagefiles.__file__).parent
    left = [f.name for f in src.glob("*.py") if f.name != "imagefiles.py"
            and re.search(r"cv2\.im(read|write)\(", f.read_text(encoding="utf-8"))]
    assert left == []
