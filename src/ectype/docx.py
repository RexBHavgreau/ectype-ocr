"""The OCR text as a Word document, for docx-to-idml to lay out (Reflow).

docx-to-idml (https://github.com/RexBHavgreau/docx-to-idml) turns a book manuscript in
Word into an InDesign document with masters, running heads, front matter and real footnotes.
Ectype writes the reading text from the page layout as the manuscript it expects
(docs/ENGINE.md there, "Preparing the .docx"):

- headings as Heading 1 (the largest on its page: a chapter title), Heading 2 and 3, and an
  author line under a chapter title as Author;
- body paragraphs as Normal, set-off passages (quotations, verse) as Quote, centred or
  right-aligned lines kept so;
- italics as italic runs, raised figures as superscripts;
- notes as Word footnotes where their reference was found in the text (a superscript figure
  on the same page matching the note's number); the others gathered at the end under "Notes";
- words the OCR was unsure of in the character style "Check OCR", and optionally the scan's
  page numbers as "{p. 172}" in "Page Marker", where each page begins (inside a paragraph
  when the page breaks in one), so both can be found in InDesign by style.

Running heads and folios are left out; paragraphs split by a page break are joined again and
line-end hyphens removed where they only split a word (layout.join_lines). Word's own .docx
structure is written by hand (as docx-to-idml's tests do), so no Word library is needed.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from .layout import Block, Layout, LWord, folios, join_lines, vocabulary

W_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
CHECK_STYLE, MARKER_STYLE = "CheckOCR", "PageMarker"

STYLES = {
    "Normal": '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>',
    "Heading1": '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
                '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:outlineLvl w:val="0"/></w:pPr></w:style>',
    "Heading2": '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
                '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:outlineLvl w:val="1"/></w:pPr></w:style>',
    "Heading3": '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/>'
                '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:outlineLvl w:val="2"/></w:pPr></w:style>',
    "Author": '<w:style w:type="paragraph" w:customStyle="1" w:styleId="Author"><w:name w:val="Author"/>'
              '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/></w:style>',
    "Quote": '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/>'
             '<w:pPr><w:ind w:left="720" w:right="720"/></w:pPr></w:style>',
    "FootnoteText": '<w:style w:type="paragraph" w:styleId="FootnoteText"><w:name w:val="footnote text"/>'
                    '<w:basedOn w:val="Normal"/><w:rPr><w:sz w:val="20"/></w:rPr></w:style>',
    "FootnoteReference": '<w:style w:type="character" w:styleId="FootnoteReference"><w:name w:val="footnote reference"/>'
                         '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>',
    CHECK_STYLE: f'<w:style w:type="character" w:customStyle="1" w:styleId="{CHECK_STYLE}"><w:name w:val="Check OCR"/></w:style>',
    MARKER_STYLE: f'<w:style w:type="character" w:customStyle="1" w:styleId="{MARKER_STYLE}"><w:name w:val="Page Marker"/></w:style>',
}

# Tesseract language -> Word language tag, for the document's default language.
WORD_LANG = {"eng": "en-US", "deu": "de-DE", "fra": "fr-FR", "spa": "es-ES", "ita": "it-IT", "nld": "nl-NL",
             "por": "pt-PT", "lat": "la", "ell": "el-GR", "grc": "el-GR", "heb": "he-IL", "rus": "ru-RU"}


@dataclass
class DocxOptions:
    page_markers: bool = False  # "{p. 172}" at the start of each scanned page's text
    check_below: float = 70.0  # words read with less OCR confidence get "Check OCR" (0: none)
    lang: str = "eng"


@dataclass
class _Para:
    style: str
    words: list[LWord]
    align: str = "left"
    markers: dict[int, str] = field(default_factory=dict)  # word index -> page marker before it
    notes: dict[int, list[LWord]] = field(default_factory=dict)  # word index -> footnote words


def _run(text: str, italic=False, superscript=False, style: str | None = None, bold=False, smallcaps=False) -> str:
    rpr = ""
    if style:
        rpr += f'<w:rStyle w:val="{style}"/>'
    if bold:
        rpr += "<w:b/>"
    if italic:
        rpr += "<w:i/>"
    if smallcaps:
        rpr += "<w:smallCaps/>"
    if superscript:
        rpr += '<w:vertAlign w:val="superscript"/>'
    rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _words_xml(words: list[LWord], check: set[int], notes: dict[int, int] | None = None,
               markers: dict[int, str] | None = None) -> str:
    """Runs for the words: italics, superscript tails, "Check OCR", footnote references (word
    index -> footnote id) in place of a word's superscript tail, and page markers before words."""
    out = []
    for k, w in enumerate(words):
        if markers and k in markers:
            out.append(_run(markers[k], style=MARKER_STYLE) + _run(" "))
        sup = w.superscript if len(w.superscript) == len(w.text) else [False] * len(w.text)
        cut = next((i for i, s in enumerate(sup) if s), len(w.text))
        style = CHECK_STYLE if k in check else None
        if cut:
            out.append(_run(w.text[:cut], w.italic, False, style, w.bold, w.smallcaps))
        if notes and k in notes:
            out.append(f'<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteReference w:id="{notes[k]}"/></w:r>')
        elif cut < len(w.text):
            out.append(_run(w.text[cut:], w.italic, True, style, w.bold))
        if k < len(words) - 1:
            nxt = words[k + 1]
            out.append(_run(" ", w.italic and nxt.italic, bold=w.bold and nxt.bold, smallcaps=w.smallcaps and nxt.smallcaps))
    if markers and len(words) in markers:
        out.append(_run(" ") + _run(markers[len(words)], style=MARKER_STYLE))
    return "".join(out)


def _para_xml(style: str, body: str, align: str = "left") -> str:
    ppr = f'<w:pStyle w:val="{style}"/>' if style != "Normal" else ""
    if align in ("center", "right"):
        ppr += f'<w:jc w:val="{align}"/>'
    return f"<w:p>{'<w:pPr>' + ppr + '</w:pPr>' if ppr else ''}{body}</w:p>"


def _note_number(words: list[LWord]) -> tuple[int | None, list[LWord]]:
    """A note's printed number and its text without it ("7 William Neil" -> 7, "William Neil")."""
    if not words:
        return None, words
    m = re.match(r"^(\d{1,3})(.*)$", words[0].text)
    if not m:
        return None, words
    rest = [LWord(m.group(2), words[0].box, words[0].italic, words[0].style, bold=words[0].bold,
                  smallcaps=words[0].smallcaps)] if m.group(2) else []
    return int(m.group(1)), rest + words[1:]


def _low_confidence(workspace: Path, layout: Layout, below: float) -> dict[str, set[tuple]]:
    """Per page, the (left, top) of words read with confidence under `below` and not corrected."""
    import json

    out: dict[str, set[tuple]] = {}
    if below <= 0:
        return out
    for page in layout.pages:
        path = workspace / "ocr" / f"{page.name}.ocr.json"
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        fixed = set()
        log = workspace / "corrections" / f"{page.name}.json"
        if log.is_file():
            fixed = {(e["line"], e["word"]) for e in json.loads(log.read_text(encoding="utf-8"))}
        out[page.name] = {(w["box"][0], w["box"][1]) for li, line in enumerate(data["lines"])
                          for wi, w in enumerate(line["words"]) if w["conf"] < below and (li, wi) not in fixed}
    return out


def write_docx(workspace: Path, layout: Layout, dest: Path, options: DocxOptions | None = None) -> dict:
    """Write the reading text of the layout as a .docx. Returns counts for the report."""
    options = options or DocxOptions()
    vocab = vocabulary(layout)
    low = _low_confidence(Path(workspace), layout, options.check_below)
    sizes = [b.size_class for p in layout.pages for b in p.blocks if b.kind == "heading"]
    big = sorted(set(sizes), reverse=True)

    numbers = folios(layout)
    paras: list[_Para] = []
    footnotes: list[list[LWord]] = []  # footnote id - 1 -> words
    unmatched: list[list[LWord]] = []
    open_para = False  # the last paragraph may continue on the next page
    checked: dict[int, set[int]] = {}  # paragraph index -> word indexes for "Check OCR"
    for n, page in enumerate(layout.pages):
        text_left = min((b.box[0] for b in page.blocks if b.kind == "text"), default=0)
        marker = None
        if options.page_markers:
            marker = f"{{p. {numbers[n]}}}" if numbers[n] else f"{{page {n + 1}}}"
        page_notes = []
        page_start = len(paras)
        for block in page.blocks:
            if block.kind in ("head", "foot"):
                continue
            if block.kind == "note":
                for p in block.paragraphs:
                    page_notes.append(join_lines(p.lines, vocab))
                continue
            set_off = block.kind == "text" and block.box[0] - text_left > 2 * page.xh
            for k, p in enumerate(block.paragraphs):
                words = join_lines(p.lines, vocab)
                if not words:
                    continue
                flagged = {i for i, w in enumerate(words) if (w.box[0], w.box[1]) in low.get(page.name, ())}
                continues = (k == 0 and open_para and paras and block.kind == "text" and not set_off
                             and paras[-1].style == "Normal" and p.indent < 0.5 * page.xh
                             and p.lines[0].box[0] - block.box[0] < 0.8 * page.xh)
                if continues:
                    prev = paras[-1]
                    base = len(prev.words)
                    merged = _join(prev.words, words, vocab)
                    start = base - 1 if merged else base  # where this page's words begin
                    checked.setdefault(len(paras) - 1, set()).update(start + i for i in flagged)
                    if marker:
                        prev.markers[base] = marker  # after a word mended across the break
                        marker = None
                else:
                    style = "Normal"
                    if block.kind == "heading":
                        style = _heading_style(block, big)
                    elif set_off:
                        style = "Quote"
                    elif paras and paras[-1].style == "Heading1" and _is_author(words):
                        style = "Author"
                    paras.append(_Para(style, words, p.align if style != "Normal" else "left"))
                    checked[len(paras) - 1] = flagged
                    if marker:
                        paras[-1].markers[0], marker = marker, None
                last = p.lines[-1]
                open_para = (block.kind == "text" and not set_off and p.align == "justify"
                             and block.box[2] - last.box[2] <= page.xh)
        # Notes: a footnote where the text has a superscript figure of the note's number on
        # this page; the rest are gathered at the end.
        refs = {}
        for pi in range(page_start, len(paras)):
            for wi, w in enumerate(paras[pi].words):
                tail = "".join(c for c, s in zip(w.text, w.superscript) if s)
                if tail.isdigit():
                    refs.setdefault(int(tail), (pi, wi))
        for note in page_notes:
            number, text = _note_number(note)
            if number is not None and number in refs and refs[number][1] not in paras[refs[number][0]].notes:
                pi, wi = refs.pop(number)
                paras[pi].notes[wi] = text
            else:
                unmatched.append(note)

    body = []
    for pi, p in enumerate(paras):
        ids = {}
        for wi, words in sorted(p.notes.items()):
            footnotes.append(words)
            ids[wi] = len(footnotes)
        xml = _words_xml(p.words, checked.get(pi, set()), ids, p.markers)
        body.append(_para_xml(p.style, xml, p.align))
    if unmatched:
        body.append(_para_xml("Heading2", _run("Notes")))
        body += [_para_xml("Normal", _words_xml(words, set())) for words in unmatched]

    _package(dest, body, footnotes, WORD_LANG.get(options.lang.split("+")[0], "en-US"))
    return {"docx": str(dest), "paragraphs": len(paras), "footnotes": len(footnotes), "notes_at_end": len(unmatched),
            "check_ocr": sum(len(v) for v in checked.values())}


def _heading_style(block: Block, sizes: list[float]) -> str:
    rank = sizes.index(block.size_class) if block.size_class in sizes else len(sizes)
    return "Heading1" if block.size_class >= 1.8 and rank == 0 else "Heading2" if rank <= 1 else "Heading3"


def _is_author(words: list[LWord]) -> bool:
    letters = "".join(c for w in words for c in w.text if c.isalpha())
    return 0 < len(words) <= 6 and letters.isupper()


def _join(prev: list[LWord], words: list[LWord], vocab: set[str]) -> bool:
    """Continue a paragraph across a page break, mending a word split there (True if so)."""
    if prev and words:
        tail, head = prev[-1], words[0]
        if (tail.text.endswith(("-", "‐", "¬")) and len(tail.text) > 2 and tail.text[-2].isalpha()
                and head.text[:1].islower() and f"{tail.text[:-1]}-{head.text}".lower() not in vocab):
            prev[-1] = LWord(tail.text[:-1] + head.text, tail.box, tail.italic, tail.style,
                             tail.superscript[:-1] + head.superscript, tail.bold, tail.smallcaps)
            prev.extend(words[1:])
            return True
    prev.extend(words)
    return False


def _package(dest: Path, body: list[str], footnotes: list[list[LWord]], lang: str) -> None:
    styles = (f'<w:styles {W_NS}><w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="24"/><w:lang w:val="{lang}"/>'
              f'</w:rPr></w:rPrDefault></w:docDefaults>{"".join(STYLES.values())}</w:styles>')
    sect = ('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" '
            'w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')
    document = f'<w:document {W_NS}><w:body>{"".join(body)}{sect}</w:body></w:document>'
    notes = ['<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>',
             '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>']
    for i, words in enumerate(footnotes, start=1):
        ref = '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>' + _run(" ")
        notes.append(f'<w:footnote w:id="{i}">{_para_xml("FootnoteText", ref + _words_xml(words, set()))}</w:footnote>')
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
            '</Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="word/document.xml"/></Relationships>'),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{rel}/styles" Target="styles.xml"/>'
            f'<Relationship Id="rId2" Type="{rel}/footnotes" Target="footnotes.xml"/></Relationships>'),
        "word/document.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + document,
        "word/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + styles,
        "word/footnotes.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:footnotes {W_NS}>{"".join(notes)}</w:footnotes>',
    }
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text.encode("utf-8"))


