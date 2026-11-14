"""The OCR text as a plain text file (UTF-8), apart from the PDF modes.

Walks the page layout the way the Word export does (docx.py: page order, a paragraph split by a
page break joined again, headings as paragraphs of their own, notes, `{p. 172}` page markers,
running heads and folios left out), so the text and the Word manuscript agree. Two forms:

- "paragraphs": the reading text, a blank line between paragraphs, lines joined with line-end
  hyphens removed where they only split a word (`layout.join_lines`); notes gathered after the
  main text under "Notes", each led by its page's marker so it can be found;
- "lines": every line as printed, page by page, notes where they stand, a blank line between pages.

The characters are the OCR's own, as corrected in Review: Unicode superscripts (¹ ᵃ) stay as
they are (`LWord.original`), nothing is normalised (the order of Hebrew points and accents and
of Greek combining marks is kept), and words stay in the order the OCR gives them, which is
the logical reading order, also for right-to-left lines. The file is UTF-8 with a byte order
mark and Windows line ends, so Word and Notepad open it as Unicode.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .layout import HYPHENS, Layout, LWord, build_layout, folios, join_lines, vocabulary

FORMS = ("paragraphs", "lines")


@dataclass
class _Para:
    kind: str  # "text", "heading", "head" (running head or folio)
    words: list[LWord]
    markers: dict[int, str] = field(default_factory=dict)  # word index -> page marker before it


HEADER = "Ectype OCR text of"  # a text file begins with this if Ectype wrote it (and may overwrite it)


def page_marker(numbers: list[int | None], n: int, pdf: list[int | None] | None = None) -> str:
    """Where a page begins, to be found in the PDF viewer: "{p. 172 · PDF 90}" with the printed page
    number (folio) where known and the scan PDF's page, "{PDF 90}" with only the PDF page. Without
    the PDF page (an image scan), as docx does: "{p. 172}", or "{page 3}" (the scan's position)."""
    folio, source = numbers[n], (pdf[n] if pdf and n < len(pdf) else None)
    if source:
        return f"{{p. {folio} · PDF {source}}}" if folio else f"{{PDF {source}}}"
    return f"{{p. {folio}}}" if folio else f"{{page {n + 1}}}"


def pdf_pages(workspace: Path, layout: Layout) -> list[int | None]:
    """Each layout page's page in the source PDF (a spread split in two: both halves carry the
    same number), from what preprocessing recorded; None where the scan isn't a PDF or unknown."""
    import json

    path = Path(workspace) / "preprocess" / "preprocess.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [None] * len(layout.pages)
    if not str(data.get("input", "")).lower().endswith(".pdf"):
        return [None] * len(layout.pages)
    by_name = {p.get("name"): p.get("source_page") for p in data.get("pages", [])}
    return [by_name.get(page.name) for page in layout.pages]


_MARKED_SCRIPTS = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F), (0x0370, 0x03FF), (0x1F00, 0x1FFF), (0x0700, 0x074F),
                   (0x0600, 0x06FF), (0x0750, 0x077F))  # Hebrew, Greek, Syriac, Arabic


def search_form(text: str) -> str:
    """The text for searching: vowel points, accents and breathings removed from Hebrew, Greek,
    Syriac and Arabic letters, the letters kept. Decomposed first (so presentation forms and
    precomposed polytonic Greek split into letter + marks); a combining mark is dropped only after
    a letter of those scripts, so transliteration (ḥ ā š p̄) keeps its marks; the maqaf becomes a
    space (two words to search); composed again at the end (é, ḥ). Everything else, superscript
    note numbers and Hebrew punctuation included, is as read."""
    out: list[str] = []
    base = 0  # code point of the last character that isn't a combining mark
    for c in unicodedata.normalize("NFD", text):
        if unicodedata.combining(c) or unicodedata.category(c) == "Mn":
            if any(lo <= base <= hi for lo, hi in _MARKED_SCRIPTS):
                continue
        else:
            base = ord(c)
            if c == "\u05be":
                c = " "
        out.append(c)
    return unicodedata.normalize("NFC", "".join(out))


def _join(prev: list[LWord], words: list[LWord], vocab: set[str]) -> bool:
    """Continue a paragraph across a page break, mending a word split there (True if so)."""
    if prev and words:
        tail, head = prev[-1], words[0]
        if (tail.text.endswith(HYPHENS) and len(tail.text) > 2 and tail.text[-2].isalpha()
                and head.text[:1].islower() and f"{tail.text[:-1]}-{head.text}".lower() not in vocab):
            raw = tail.original[:-1] + head.original
            text = tail.text[:-1] + head.text
            prev[-1] = LWord(text, tail.box, tail.italic, tail.style, tail.superscript[:-1] + head.superscript,
                             tail.bold, tail.smallcaps, raw if raw != text else "")
            prev.extend(words[1:])
            return True
    prev.extend(words)
    return False


def _render(p: _Para, shape=lambda t: t) -> str:
    out = []
    for k, w in enumerate(p.words):
        if k in p.markers:
            out.append(p.markers[k])
        out.append(shape(w.original))
    if len(p.words) in p.markers:
        out.append(p.markers[len(p.words)])
    return " ".join(out)


def _paragraphs_form(layout: Layout, pdf: list, page_markers: bool, keep_heads: bool, shape=lambda t: t) -> tuple[list[str], int, int]:
    vocab = vocabulary(layout)
    numbers = folios(layout)
    paras: list[_Para] = []
    notes: list[tuple[str, list[LWord]]] = []
    open_para = False  # the last paragraph may continue on the next page
    for n, page in enumerate(layout.pages):
        text_left = min((b.box[0] for b in page.blocks if b.kind == "text"), default=0)
        marker = page_marker(numbers, n, pdf) if page_markers else None
        for block in page.blocks:
            if block.kind in ("head", "foot"):
                if keep_heads:
                    for p in block.paragraphs:
                        words = join_lines(p.lines, vocab)
                        if words:
                            paras.append(_Para("head", words))
                            if marker:
                                paras[-1].markers[0], marker = marker, None
                continue
            if block.kind == "note":
                for p in block.paragraphs:
                    words = join_lines(p.lines, vocab)
                    if words:
                        notes.append((page_marker(numbers, n, pdf), words))
                continue
            set_off = block.kind == "text" and block.box[0] - text_left > 2 * page.xh
            for k, p in enumerate(block.paragraphs):
                words = join_lines(p.lines, vocab)
                if not words:
                    continue
                continues = (k == 0 and open_para and paras and block.kind == "text" and not set_off
                             and paras[-1].kind == "text" and p.indent < 0.5 * page.xh
                             and p.lines[0].box[0] - block.box[0] < 0.8 * page.xh)
                if continues:
                    prev = paras[-1]
                    base = len(prev.words)
                    _join(prev.words, words, vocab)
                    if marker:
                        prev.markers[base] = marker  # after a word mended across the break
                        marker = None
                else:
                    paras.append(_Para("heading" if block.kind == "heading" else "text", words))
                    if marker:
                        paras[-1].markers[0], marker = marker, None
                last = p.lines[-1]
                open_para = (block.kind == "text" and not set_off and p.align == "justify"
                             and block.box[2] - last.box[2] <= page.xh)
    chunks = [_render(p, shape) for p in paras]
    if notes:
        chunks.append("Notes")
        chunks += [f"{m} " + _render(_Para("text", words), shape) for m, words in notes]
    return chunks, len(paras), len(notes)


def _lines_form(layout: Layout, pdf: list, page_markers: bool, keep_heads: bool, shape=lambda t: t) -> tuple[list[str], int, int]:
    numbers = folios(layout)
    pages: list[str] = []
    paragraphs = notes = 0
    for n, page in enumerate(layout.pages):
        lines = [page_marker(numbers, n, pdf)] if page_markers else []
        for block in page.blocks:
            if block.kind in ("head", "foot") and not keep_heads:
                continue
            paragraphs += len(block.paragraphs)
            notes += len(block.paragraphs) if block.kind == "note" else 0
            lines += [" ".join(shape(w.original) for w in line.words) for line in block.lines if line.words]
        if lines:
            pages.append("\n".join(lines))
    return pages, paragraphs, notes


def write_text(workspace: Path, dest: Path, layout: Layout | None = None, *, form: str = "paragraphs",
               page_markers: bool = True, keep_heads: bool = False, header: str | None = None,
               keep_marks: bool = False) -> dict:
    """Write the book's OCR text to `dest` (UTF-8 with a BOM, CRLF line ends), led by the `header`
    line if given. Vowel points, accents and breathings are removed for searching (`search_form`)
    unless `keep_marks`, which keeps every character exactly as read. Returns the counts: pages, paragraphs (lines' paragraphs in the lines form),
    notes, chars."""
    if form not in FORMS:
        raise ValueError(f"unknown form {form!r}; choose from {', '.join(FORMS)}")
    layout = layout if layout is not None else build_layout(Path(workspace))
    build = _paragraphs_form if form == "paragraphs" else _lines_form
    chunks, paragraphs, notes = build(layout, pdf_pages(Path(workspace), layout), page_markers, keep_heads,
                                      (lambda t: t) if keep_marks else search_form)
    if header:
        chunks.insert(0, header)
    text = "\n\n".join(chunks) + ("\n" if chunks else "")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with open(part, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(text)
    part.replace(dest)
    return {"text": str(dest), "pages": len(layout.pages), "paragraphs": paragraphs, "notes": notes, "chars": len(text)}


def default_path(workspace: Path, name: str) -> Path:
    return Path(workspace) / "plaintext" / f"{name}.txt"


def header_line(scan: str, lang: str, today: str | None = None, keep_marks: bool = True) -> str:
    """The first line of a companion text file: 'Ectype OCR text of "Treatise 01.pdf" (eng+heb; points
    and accents removed for searching), 2026-11-14'."""
    from datetime import date

    note = "" if keep_marks else "; points and accents removed for searching"
    return f'{HEADER} "{scan}" ({lang}{note}), {today or date.today().isoformat()}'


def is_ours(path: Path) -> bool:
    """True when the text file was written by Ectype (its first line says so)."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            return f.read(len(HEADER)) == HEADER
    except OSError:
        return False


def companion(scan: Path) -> Path:
    """The text file that goes beside a scan with its name: Treatise 01.pdf -> Treatise 01.txt."""
    return Path(scan).with_suffix(".txt")
