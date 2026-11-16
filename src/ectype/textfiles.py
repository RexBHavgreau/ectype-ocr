"""A text file beside each scan, for a whole folder of scans ("Make text files for a folder").

For each PDF (or page image) without a companion .txt of its name (Treatise 01.pdf ->
Treatise 01.txt), the pages are prepared and read in a temporary work folder in the user's data
folder, the text is written beside the scan, and the work folder is deleted (its page images
are large; the scans' folder must not fill with .ectype folders) unless `keep`. The text is
the plain OCR text (plaintext.py) with page markers that point into the PDF: {p. 172 · PDF 90}.

A text file is never overwritten unless Ectype wrote it (its first line says so) and `redo`
is asked. One book's failure (a damaged PDF) is reported and the others go on. Stop ends the
run at the next page; a book stopped midway leaves no text file.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Callable

from . import stopping
from .plaintext import companion, is_ours
from .preprocess import IMAGE_SUFFIXES

Progress = Callable[[str, str], None]
NEEDS: dict[str, list[str]] = {}  # scan -> languages its reading lacked (for the summary)


def work_root() -> Path:
    from .resources import user_data_dir

    return user_data_dir() / "work"


def find_scans(inputs: list[Path], recursive: bool = False) -> list[Path]:
    """The scans named or inside the folders named: PDFs and page images, in name order. Folders
    of Ectype's own work (*.ectype) are not looked into."""
    suffixes = {".pdf"} | {s.lower() for s in IMAGE_SUFFIXES}
    found: list[Path] = []
    for item in map(Path, inputs):
        if item.is_dir():
            walk = item.rglob("*") if recursive else item.glob("*")
            found += sorted(p for p in walk if p.is_file() and p.suffix.lower() in suffixes
                            and not any(part.endswith(".ectype") for part in p.relative_to(item).parts))
        elif item.is_file() and item.suffix.lower() in suffixes:
            found.append(item)
    return list(dict.fromkeys(found))


def make_text_file(scan: Path, lang: str, keep: bool = False, keep_marks: bool = False, progress: Progress | None = None,
                   fraction: Callable[[float], None] | None = None) -> Path:
    """Read one scan and write its companion text file. Raises on a failure; the work folder is
    deleted either way unless `keep`."""
    from .languages import split
    from .ocr import ocr_pages
    from .preprocess import preprocess
    from .project import Project

    progress = progress or (lambda stage, msg: None)
    fraction = fraction or (lambda f: None)
    work = work_root() / f"{scan.stem[:40]}-{uuid.uuid4().hex[:8]}.ectype"
    try:
        project = Project(work, scan)
        project.set_languages(split(lang))

        def on_page(stage: str, lo: float, hi: float):
            def call(done: int, total: int) -> None:
                stopping.check()
                fraction(lo + (hi - lo) * done / max(1, total))
                if done == total:
                    progress(stage, f"{total} pages" if stage == "ocr" else "pages prepared")
            return call

        stopping.check()
        preprocess(scan, work, keep_spreads=not project.settings.split_spreads, on_page=on_page("preprocess", 0.0, 0.3))
        ocr_pages(work / "pages", work / "ocr", lang=project.settings.lang, on_page=on_page("ocr", 0.3, 0.95))
        stopping.check()
        project.settings.name = scan.stem
        out = project.write_text(companion(scan), keep_marks=keep_marks)  # atomic: a stopped book leaves no text file
        missing = project.missing_languages()
        if missing:  # said in the summary: the book needs another language (the batch goes on)
            from .project import found_words

            progress("languages", f"{scan.name}: {found_words(missing[1])} not read; add --lang "
                                  + "+".join(dict.fromkeys(split(lang) + missing[0])) + " and --redo")
            NEEDS[str(scan)] = missing[0]
        fraction(1.0)
        return Path(out["text"])
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
            try:
                work.parent.rmdir()  # the work root too, if nothing else is in it
            except OSError:
                pass


def check_languages(lang: str) -> None:
    """Refuse at once, in plain words, a language that isn't installed (rather than failing on
    every book)."""
    from . import languages

    have = set(languages.installed())
    missing = [c for c in languages.split(lang) if c not in have]
    if missing:
        names = ", ".join(f"{languages.name(c)} ({c})" for c in missing)
        raise ValueError(f"Not installed: {names}. Add it first: ectype languages --add {' '.join(missing)}")


def make_text_files(inputs: list[Path], lang: str = "eng", recursive: bool = False, redo: bool = False,
                    keep: bool = False, progress: Progress | None = None, keep_marks: bool = False) -> dict:
    """A text file beside every scan that has none. Returns {"done": [(scan, txt)], "skipped":
    [(scan, why)], "failed": [(scan, why)]}. `progress(stage, message)`: "books" lines, the stage
    lines of each book, and ("fraction", "0.42") for the whole."""
    progress = progress or (lambda stage, msg: None)
    check_languages(lang)
    scans = find_scans(inputs, recursive)
    result: dict = {"done": [], "skipped": [], "failed": []}
    total = max(1, len(scans))
    for k, scan in enumerate(scans):
        stopping.check()
        txt = companion(scan)
        if txt.exists():
            if not is_ours(txt):
                result["skipped"].append((scan, "a text file of that name already exists"))
                continue
            if not redo:
                result["skipped"].append((scan, "already has a text file"))
                continue
        progress("books", f"{k + 1} of {len(scans)}: {scan.name}")
        try:
            path = make_text_file(scan, lang, keep, keep_marks, progress, lambda f, k=k: progress("fraction", f"{(k + f) / total:.4f}"))
        except Exception as exc:  # one damaged book must not stop the others
            why = str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__
            result["failed"].append((scan, why))
            progress("books", f"{scan.name} failed: {why}")
            continue
        result["done"].append((scan, path))
        if str(scan) in NEEDS:
            result.setdefault("needs", []).append((scan, NEEDS.pop(str(scan))))
        progress("fraction", f"{(k + 1) / total:.4f}")
    return result


def summary_text(result: dict) -> str:
    lines = [f"Done: {len(result['done'])}, skipped: {len(result['skipped'])}, failed: {len(result['failed'])}."]
    lines += [f"  made {txt}" for _, txt in result["done"]]
    for scan, codes in result.get("needs", []):
        lines.append(f"  NEEDS {'+'.join(codes)}: {scan.name} has words in a script the languages didn't read; "
                     f"make it again with --lang …+{'+'.join(codes)} --redo")
    lines += [f"  skipped {scan.name}: {why}" for scan, why in result["skipped"]]
    lines += [f"  FAILED {scan.name}: {why}" for scan, why in result["failed"]]
    return "\n".join(lines)
