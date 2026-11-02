"""Type sizes, headings and notes in the page layout (layout.py, cluster.relative_size,
segment.line_x_heights), from Driver's Treatise exported to IDML: five pages scanned at about
690 dpi among 300-dpi ones came out as 41 "chapter titles", and most notes weren't notes."""

from __future__ import annotations

import zipfile

from ectype.cluster import relative_size, text_xh
from ectype.docx import DocxOptions, write_docx
from ectype.layout import Block, Layout, LLine, LPage, LWord, Paragraph, _blocks, _mark_notes, _superscripts
from ectype.segment import Segmentation, Unit, line_x_heights


def test_sizes_against_the_page_not_pixels():
    doc_pt = 19 * 72 / 300  # the book's text, in points
    # A 690-dpi page: its text is 44 pixels high, the same size in print.
    assert relative_size(44, 44, 72 / 690, doc_pt) == 1.0
    # A 300-dpi page scanned a little smaller (16 pixels for the same type): its text is text.
    assert relative_size(16, 16, 72 / 300, doc_pt) == 1.0
    assert abs(relative_size(24, 16, 72 / 300, doc_pt) - 1.5) < 1e-9, "a heading on it"
    # A title page whose text is all large: against the book's.
    assert abs(relative_size(38, 38, 72 / 300, doc_pt) - 2.0) < 1e-9
    # Notes don't pass for a page's text, however many.
    assert text_xh([19, 19, 15, 15, 15, 15, 15, 19, 19, 19], 15) == 19


def _unit(i, line, text, box):
    return Unit(i, line, 0, text, [i], [i], box, 100, 90.0, "single", box[3])


def test_a_line_in_capitals_is_sized_by_its_capitals():
    units, k = [], 0
    for li in range(3):  # body text: x-height letters 20 pixels high, capitals 28
        for j in range(12):
            y = 100 + 60 * li
            units.append(_unit(k, li, "o" if j % 2 else "T", [10 * j, y - (20 if j % 2 else 28), 10 * j + 8, y]))
            k += 1
    for j, c in enumerate("ATREATISE"):  # a title in capitals 57 pixels high
        units.append(_unit(k, 3, c, [12 * j, 400, 12 * j + 10, 457]))
        k += 1
    for j, c in enumerate("ON"):  # a small word in capitals between a title's lines
        units.append(_unit(k, 4, c, [12 * j, 500, 12 * j + 10, 520]))
        k += 1
    per, page = line_x_heights(Segmentation([800, 800], k, units))
    assert page == 20
    assert abs(per[3] - 57 / 1.42) < 0.5, "the title's capitals, not the page's capital T"
    assert per[4] == 20, "never smaller than the text: not taken for a note"


def _line(text, y, xh=20.0, x0=100, width=800):
    words = [LWord(t, [x0 + 60 * i, y - 20, x0 + 60 * i + 50, y]) for i, t in enumerate(text.split())]
    return LLine(words, [x0, y - 28, x0 + width, y + 6], float(y), 0.0, xh)


def test_a_heading_has_letters_and_does_not_begin_mid_sentence():
    lines = [(_line("A TREATISE", 100), "", 1.5), (_line("text of the book runs on here", 200), "", 1.0),
             (_line("invests it with a character", 400), "", 1.2), (_line("4841.", 600), "", 1.3)]
    kinds = [b.kind for b in _blocks(lines, 20.0)]
    assert kinds[0] == "heading" and set(kinds[1:]) == {"text"}


def test_notes_found_by_their_place_and_number():
    body = [(_line(f"line {i} of the text", 200 + 50 * i), "", 1.0) for i in range(20)]
    notes = [(_line("1 The form in found thrice", 1400), "", 1.0), (_line("16 is hardly old it appears", 1450), "", 1.0)]
    numbered = [(_line("1. The Hebrew language in", 1400), "", 1.0)]
    blocks = _mark_notes(_blocks(body + notes, 20.0), 1950, 20.0)
    assert [b.kind for b in blocks] == ["text", "note"], "below a gap, beginning with a note's number"
    blocks = _mark_notes(_blocks(body + numbered, 20.0), 1950, 20.0)
    assert [b.kind for b in blocks] == ["text", "text"], "a numbered paragraph is text"


def test_a_raised_reference_standing_apart():
    word = LWord("1", [500, 80, 510, 92])
    unit = _unit(0, 0, "1", [500, 78, 510, 90])
    assert _superscripts(type("W", (), {"chars": [type("C", (), {"text": "1"})()], "text": "1"})(), [unit], 100, 20) == [True]
    other = _unit(1, 0, "w", [500, 78, 510, 90])
    w = type("W", (), {"chars": [type("C", (), {"text": "wo"})()], "text": "wo"})()
    assert _superscripts(w, [other], 100, 20) == [False, False], "a whole raised word isn't a reference"
    assert word.text == "1"


def _note_page(text_words, notes):
    lines = [LLine([LWord(t, [100 + 60 * i, 180, 150 + 60 * i, 200], superscript=s)
                    for i, (t, s) in enumerate(text_words)], [100, 172, 900, 206], 200.0, 0.0, 20.0)]
    blocks = [Block("text", 1.0, [Paragraph(lines, "justify")], [100, 172, 900, 206])]
    for k, words in enumerate(notes):
        nl = LLine([LWord(t, [100 + 60 * i, 1400 + 50 * k, 150 + 60 * i, 1420 + 50 * k]) for i, t in enumerate(words)],
                   [100, 1392 + 50 * k, 900, 1426 + 50 * k], 1420.0 + 50 * k, 0.0, 16.0)
        blocks.append(Block("note", 0.8, [Paragraph([nl])], nl.box))
    return LPage("p001", 1200, 1950, 300.0, blocks, 20.0)


def test_notes_matched_in_order_when_their_marks_were_misread(tmp_path):
    """The Treatise's references are often read as "*" or "!", so the notes' numbers can't be
    matched; when a page has as many raised marks as notes, they go in order."""
    text = [("fundamental*", [False] * 11 + [True]), ("character", [False] * 9), ("moods!", [False] * 5 + [True])]
    page = _note_page(text, [["1", "These", "words"], ["2", "It", "will", "appear"]])
    out = write_docx(tmp_path, Layout([page], 20.0, {}), tmp_path / "a.docx", DocxOptions(False, 0, "eng"))
    assert out["footnotes"] == 2 and out["notes_at_end"] == 0
    with zipfile.ZipFile(tmp_path / "a.docx") as z:
        doc = z.read("word/document.xml").decode()
    assert doc.count("footnoteReference") == 2 and "*" not in doc
    # One mark for two notes: nothing guessed.
    page = _note_page(text[:2], [["1", "These", "words"], ["2", "It", "will", "appear"]])
    out = write_docx(tmp_path, Layout([page], 20.0, {}), tmp_path / "b.docx", DocxOptions(False, 0, "eng"))
    assert out["footnotes"] == 0 and out["notes_at_end"] == 2
