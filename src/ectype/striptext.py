"""A PDF's hidden text layer taken out: `ectype strip-text`.

Scanners and OCR programs lay invisible text over a page's picture so the page can be searched
(text drawn in text mode 3, the operator `3 Tr`). When that text is bad, this writes a copy of
the PDF without it: the pictures and everything visible stay, the page looks the same, and a
search finds nothing. Visible text is left alone unless asked for (`everything`): in Acrobat
ClearScan and Ectype's own traced PDFs the visible type *is* the page.

The PDF given is never changed; the result is a new file beside it ("<name> - no text.pdf").
"""

from __future__ import annotations

import os
from pathlib import Path

import pikepdf

SHOWS = {"Tj", "TJ", "'", '"'}  # the operators that show text
HIDDEN = 3  # text mode 3: neither filled nor stroked, so invisible


def destination(src: Path) -> Path:
    """Beside the PDF: "<name> - no text.pdf", or "(2)", "(3)" … if that is taken."""
    n = 1
    while True:
        dest = src.with_name(f"{src.stem} - no text{'' if n == 1 else f' ({n})'}.pdf")
        if not dest.exists():
            return dest
        n += 1


def _strip(owner, everything: bool, counts: dict) -> tuple[bytes | None, int]:
    """One content stream (a page's, or a form's) without its hidden text, and how many
    text-showing operators went (None and 0 when it has none to remove). The text mode is followed through `Tr` and `q`/`Q`. A text block (BT … ET)
    loses its text-showing operators when all of them are hidden; a block with hidden and
    visible text is left whole (counted as "mixed"), since the visible words' place depends on
    the hidden ones before them."""
    mode, saved = 0, []
    out: list[tuple] = []
    block: list[tuple] | None = None  # (operands, operator, hidden?) of the text block being read
    removed = 0
    for operands, operator in pikepdf.parse_content_stream(owner):
        op = str(operator)
        if op == "q":
            saved.append(mode)
        elif op == "Q" and saved:
            mode = saved.pop()
        elif op == "Tr" and operands:
            mode = int(operands[0])
        if block is None:
            if op == "BT":
                block = [(operands, operator, False)]
            elif op in SHOWS:
                # Text shown outside any BT … ET: not allowed, but some OCR programs write it
                # and viewers still read it as text (a Patrologia Graeca scan in corpus/ has it).
                if everything or mode == HIDDEN:
                    removed += 1
                else:
                    counts["visible_left"] += 1
                    out.append((operands, operator))
            else:
                out.append((operands, operator))
            continue
        block.append((operands, operator, op in SHOWS and (everything or mode == HIDDEN)))
        if op != "ET":
            continue
        shows = [item for item in block if str(item[1]) in SHOWS]
        hidden = sum(1 for item in shows if item[2])
        if hidden and hidden < len(shows):
            counts["mixed"] += 1
            counts["visible_left"] += len(shows) - hidden
            hidden = 0
        else:
            counts["visible_left"] += len(shows) - hidden
        removed += hidden
        out += [(operands, operator) for operands, operator, gone in block if not (hidden and gone)]
        block = None
    if block is not None:  # a text block never closed: left as it is
        out += [(operands, operator) for operands, operator, _ in block]
    return (pikepdf.unparse_content_stream(out), removed) if removed else (None, 0)


def _forms(resources, seen: set):
    """The forms (pictures made of page content) a page or form draws, each once, with the
    forms inside them."""
    try:
        xobjects = resources.get("/XObject", {}) if resources is not None else {}
        names = list(xobjects.keys())
    except (AttributeError, TypeError):
        return
    for name in names:
        form = xobjects[name]
        try:
            if form.get("/Subtype") != "/Form" or form.objgen in seen:
                continue
        except (AttributeError, TypeError):
            continue
        seen.add(form.objgen)
        yield form
        yield from _forms(form.get("/Resources"), seen)


def strip_text(src, dest=None, everything: bool = False) -> dict:
    """Write a copy of the PDF `src` without its hidden text (with `everything`, without any
    text). Returns {"output": the new file, or None when there was nothing to remove (then no
    file is written), "pages", "pages_changed", "removed": text-showing operators taken out,
    "visible_left": visible ones left, "mixed": text blocks left whole for holding both kinds}.
    `dest` must not be the PDF itself (ValueError) nor a file that exists (FileExistsError)."""
    src = Path(src)
    if dest is None:
        dest = destination(src)
    else:
        dest = Path(dest)
        if dest.exists() and dest.resolve() == src.resolve():
            raise ValueError("The result can't take the place of the PDF itself; give another name.")
        if dest.exists():
            raise FileExistsError(f"{dest} is already there; give another name.")
    counts = {"mixed": 0, "visible_left": 0}
    removed = changed = 0
    seen: set = set()
    with pikepdf.open(src) as pdf:
        for page in pdf.pages:
            before = removed
            data, n = _strip(page, everything, counts)
            if data is not None:
                page.Contents = pdf.make_stream(data)
                removed += n
            for form in _forms(page.get("/Resources"), seen):
                data, n = _strip(form, everything, counts)
                if data is not None:
                    form.write(data)
                    removed += n
            if removed > before:
                changed += 1
        pages = len(pdf.pages)
        if removed:
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_suffix(dest.suffix + ".part")
            try:
                pdf.save(part)
                os.replace(part, dest)
            finally:
                part.unlink(missing_ok=True)
    return {"output": dest if removed else None, "pages": pages, "pages_changed": changed,
            "removed": removed, **counts}
