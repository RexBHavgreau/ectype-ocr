"""`ectype strip-text`: a PDF's hidden (OCR) text layer taken out, the page left looking the same.
These tests were written with the task (TASKS.md T-014) and are skipped until `ectype.striptext` exists."""
from pathlib import Path

import numpy as np
import pikepdf
import pypdfium2 as pdfium
import pytest

striptext = pytest.importorskip("ectype.striptext")

HIDDEN = b"BT /F1 12 Tf 3 Tr 20 100 Td (hidden words) Tj ET"
SHOWN = b"BT /F1 12 Tf 0 Tr 20 60 Td (shown words) Tj ET"
BOX = b"0 0 0 rg 20 20 60 10 re f"


def make(path: Path, *contents: bytes, form: bytes | None = None) -> Path:
    """A PDF with one page per content stream (Helvetica as /F1); `form` adds a form XObject /Fm0
    holding that content, drawn by the first page."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
    for n, content in enumerate(contents):
        resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        if form is not None and n == 0:
            xobj = pdf.make_stream(form, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 200, 200],
                                   Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)))
            resources.XObject = pikepdf.Dictionary(Fm0=xobj)
            content += b" /Fm0 Do"
        page = pdf.add_blank_page(page_size=(200, 200))
        page.Resources = resources
        page.Contents = pdf.make_stream(content)
    pdf.save(path)
    return path


def text(path: Path, page: int = 0) -> str:
    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[page].get_textpage().get_text_range().strip()
    finally:
        doc.close()


def picture(path: Path, page: int = 0) -> np.ndarray:
    doc = pdfium.PdfDocument(str(path))
    try:
        return np.asarray(doc[page].render(scale=2, grayscale=True).to_pil())
    finally:
        doc.close()


def test_hidden_text_goes_and_the_page_looks_the_same(tmp_path):
    src = make(tmp_path / "book.pdf", HIDDEN + b" " + BOX)
    before = src.read_bytes()
    assert text(src) == "hidden words"
    out = striptext.strip_text(src)
    assert out["output"] == tmp_path / "book - no text.pdf" and out["output"].is_file()
    assert (out["pages"], out["pages_changed"], out["removed"], out["visible_left"]) == (1, 1, 1, 0)
    assert text(out["output"]) == ""
    assert np.array_equal(picture(src), picture(out["output"])), "the page looks the same"
    assert src.read_bytes() == before, "the PDF given is never changed"


def test_visible_text_stays_unless_asked(tmp_path):
    src = make(tmp_path / "book.pdf", HIDDEN + b" " + SHOWN)
    out = striptext.strip_text(src)
    assert (out["removed"], out["visible_left"]) == (1, 1)
    assert text(out["output"]) == "shown words"
    assert np.array_equal(picture(src), picture(out["output"]))
    everything = striptext.strip_text(src, tmp_path / "bare.pdf", everything=True)
    assert everything["output"] == tmp_path / "bare.pdf" and (everything["removed"], everything["visible_left"]) == (2, 0)
    assert text(tmp_path / "bare.pdf") == ""


def test_the_text_mode_ends_with_its_group(tmp_path):
    # "3 Tr" inside q ... Q no longer holds after Q: the second text is visible and stays
    src = make(tmp_path / "book.pdf", b"q 3 Tr BT /F1 12 Tf 20 100 Td (hidden words) Tj ET Q BT /F1 12 Tf 20 60 Td (shown words) Tj ET")
    out = striptext.strip_text(src)
    assert (out["removed"], out["visible_left"]) == (1, 1) and text(out["output"]) == "shown words"


def test_every_way_of_showing_text(tmp_path):
    src = make(tmp_path / "book.pdf", b"BT /F1 12 Tf 3 Tr 14 TL 20 150 Td (one) Tj [(tw) -20 (o)] TJ (three) ' 1 0 (four) \" ET")
    assert text(src)
    out = striptext.strip_text(src)
    assert out["removed"] == 4 and text(out["output"]) == ""


def test_a_text_block_with_both_kinds_is_left_whole(tmp_path):
    # the visible words' place depends on the hidden ones before them in the same BT ... ET
    mixed = b"BT /F1 12 Tf 20 100 Td 3 Tr (hidden ) Tj 0 Tr (shown) Tj ET"
    src = make(tmp_path / "book.pdf", mixed + b" " + HIDDEN)
    out = striptext.strip_text(src)
    assert (out["removed"], out["mixed"]) == (1, 1)
    assert "shown" in text(out["output"]) and "hidden" in text(out["output"]) and "words" not in text(out["output"])
    assert np.array_equal(picture(src), picture(out["output"]))


def test_text_shown_outside_a_text_block(tmp_path):
    # Not allowed in a PDF, but some OCR programs write text after ET with no new BT, and viewers
    # still read it (found in a real scan): the hidden kind goes there too.
    src = make(tmp_path / "book.pdf", b"BT /F1 12 Tf 3 Tr 20 100 Td (hidden words) Tj ET (more hidden) Tj ET " + BOX)
    assert "more hidden" in text(src)
    out = striptext.strip_text(src)
    assert out["removed"] == 2 and text(out["output"]) == ""
    assert np.array_equal(picture(src), picture(out["output"]))


def test_text_inside_a_form(tmp_path):
    src = make(tmp_path / "book.pdf", BOX, form=HIDDEN)
    assert text(src) == "hidden words"
    out = striptext.strip_text(src)
    assert out["removed"] == 1 and text(out["output"]) == ""


def test_nothing_to_remove_writes_nothing(tmp_path):
    src = make(tmp_path / "book.pdf", SHOWN, BOX)
    out = striptext.strip_text(src)
    assert out["output"] is None and (out["pages"], out["pages_changed"], out["removed"], out["visible_left"]) == (2, 0, 0, 1)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["book.pdf"]


def test_only_the_pages_with_hidden_text_change(tmp_path):
    src = make(tmp_path / "book.pdf", HIDDEN, BOX, HIDDEN + b" " + BOX)
    out = striptext.strip_text(src)
    assert (out["pages"], out["pages_changed"], out["removed"]) == (3, 2, 2)
    assert [text(out["output"], n) for n in range(3)] == ["", "", ""]


def test_names_and_what_is_never_overwritten(tmp_path):
    src = make(tmp_path / "book.pdf", HIDDEN)
    first = striptext.strip_text(src)["output"]
    second = striptext.strip_text(src)["output"]
    assert (first.name, second.name) == ("book - no text.pdf", "book - no text (2).pdf")
    with pytest.raises(FileExistsError):
        striptext.strip_text(src, first)
    with pytest.raises(ValueError):
        striptext.strip_text(src, src)
    assert not list(tmp_path.glob("*.part"))


def test_the_command(tmp_path, capsys):
    from ectype import cli

    one, two = make(tmp_path / "one.pdf", HIDDEN + b" " + SHOWN), make(tmp_path / "two.pdf", BOX)
    assert cli.main(["strip-text", str(one), str(two)]) == 0
    said = capsys.readouterr().out
    assert (tmp_path / "one - no text.pdf").is_file() and not (tmp_path / "two - no text.pdf").exists()
    assert "one - no text.pdf" in said and "no hidden text" in said and "--all" in said
    assert cli.main(["strip-text", str(one), "-o", str(tmp_path / "out.pdf"), "--all"]) == 0
    assert text(tmp_path / "out.pdf") == ""
    assert cli.main(["strip-text", str(tmp_path / "missing.pdf")]) != 0
    assert "Traceback" not in capsys.readouterr().out
