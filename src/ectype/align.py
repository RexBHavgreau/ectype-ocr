"""Training lines from scans of an edition whose text is known: no correcting by hand.

    ectype train-scans <scan.pdf> --text hebrew-bible --edition "Letteris 1866"

The scan is read as a project of its own (preprocessing and OCR, with the language's
current model). Then each line the OCR found is looked for in the known text (`texts.py`):
1. Its words are reduced to their bare letters (no points, accents or punctuation) and the
   text is searched for the place where most of them stand in the same order (`locate`):
   OCR mistakes in a few letters still leave most words recognisable.
2. The line's words are paired with the text's words there. Words at the ends that have no
   partner, typically verse and chapter numbers, are cut off the image.
3. The line is kept only if nearly all its words matched (by default 85% of the letters):
   then the known text, with its points, is the line's true text. Doubtful lines are skipped,
   not guessed, since an edition's text may differ from the known one in places.

The lines kept go into the language's training pool as the edition's "book" (`pool.py`),
a fifth kept aside for testing, with the edition's typeface fingerprint.
"""

from __future__ import annotations

import io
import json
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from . import pool, texts
from .resources import user_data_dir

MIN_SIMILARITY = 0.85
MIN_WORDS = 2
LANGS = {"hebrew-bible": "heb", "greek-nt": "grc"}


class Index:
    """The known text as one sequence of words, with an index of its word pairs."""

    def __init__(self, passages: list[str]):
        self.words: list[str] = []
        for p in passages:
            self.words += p.split()
        self.keys = [texts.base_letters(w) for w in self.words]
        self.pairs: dict[tuple[str, str], list[int]] = {}
        for i in range(len(self.keys) - 1):
            self.pairs.setdefault((self.keys[i], self.keys[i + 1]), []).append(i)
        self.singles: dict[str, list[int]] = {}
        for i, k in enumerate(self.keys):
            self.singles.setdefault(k, []).append(i)

    def locate(self, keys: list[str]) -> int | None:
        """Where in the text a line's words (bare letters) most likely start: each pair of
        neighbouring words votes for the start it implies; rare single words vote too."""
        votes: dict[int, float] = {}
        for j in range(len(keys) - 1):
            for i in self.pairs.get((keys[j], keys[j + 1]), [])[:200]:
                votes[i - j] = votes.get(i - j, 0) + 1
        for j, k in enumerate(keys):
            hits = self.singles.get(k, [])
            if 0 < len(hits) <= 5 and len(k) >= 3:
                for i in hits:
                    votes[i - j] = votes.get(i - j, 0) + 0.5
        if not votes:
            return None
        start, score = max(votes.items(), key=lambda kv: kv[1])
        return start if score >= 1 else None


def match_line(index: Index, ocr_words: list[str]) -> tuple[int, int, list[str], float] | None:
    """The run of the OCR line's words that matches the text, and the text's words for it:
    (first OCR word, last OCR word, true words, similarity), or None."""
    keys = [texts.base_letters(w) for w in ocr_words]
    if sum(1 for k in keys if k) < MIN_WORDS:
        return None
    start = index.locate(keys)
    if start is None:
        return None
    lo, hi = max(0, start - 3), min(len(index.words), start + len(keys) + 3)
    sm = SequenceMatcher(None, keys, index.keys[lo:hi], autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        return None
    first_o, last_o = blocks[0].a, blocks[-1].a + blocks[-1].size - 1
    first_t, last_t = lo + blocks[0].b, lo + blocks[-1].b + blocks[-1].size - 1
    ocr = "".join(keys[first_o:last_o + 1])
    true = "".join(index.keys[first_t:last_t + 1])
    if not true:
        return None
    similarity = SequenceMatcher(None, ocr, true, autojunk=False).ratio()
    if last_o - first_o + 1 < MIN_WORDS:
        return None
    return first_o, last_o, index.words[first_t:last_t + 1], similarity


def align_workspace(workspace: Path, source: str, edition: str, lang: str | None = None,
                    min_similarity: float = MIN_SIMILARITY, progress: Callable[[str], None] | None = None) -> dict:
    """Match a read scan's lines to the known text and add those that agree to the pool."""
    from PIL import Image

    from .families import measure
    from .ocr import OcrPage
    from .scripts import is_rtl

    say = progress or (lambda msg: None)
    lang = lang or LANGS.get(source)
    if lang is None:
        raise ValueError("Say which language the text is in (--lang).")
    index = Index(texts.load(source, say))
    kept = skipped = 0
    samples = []
    with pool.Writer(lang) as w:
        w.remove_book(edition, source="aligned")
        for path in sorted((Path(workspace) / "ocr").glob("*.ocr.json")):
            page = path.name[: -len(".ocr.json")]
            png = Path(workspace) / "pages" / f"{page}.png"
            ocr = OcrPage.from_json(json.loads(path.read_text(encoding="utf-8")))
            image = Image.open(png).convert("L")
            for li, line in enumerate(ocr.lines):
                words = [wd for wd in line.words if wd.text.strip()]
                if not words:
                    continue
                found = match_line(index, [wd.text for wd in words])
                if found is None or found[3] < min_similarity:
                    skipped += 1
                    continue
                first, last, true, _ = found
                part = words[first:last + 1]
                x0, x1 = min(wd.box[0] for wd in part), max(wd.box[2] for wd in part)
                y0, y1 = line.box[1], line.box[3]
                pad = max(4, (y1 - y0) // 4)
                crop = image.crop((max(0, x0 - 8), max(0, y0 - pad), min(image.width, x1 + 8), min(image.height, y1 + pad)))
                buf = io.BytesIO()
                crop.save(buf, format="PNG")
                text = unicodedata.normalize("NFC", " ".join(true))
                w.add(buf.getvalue(), text, edition, "line", "aligned", origin=f"aligned::{edition}::{page}-{li}")
                kept += 1
                if len(samples) < 80:
                    import numpy as np

                    samples.append(np.asarray(crop))
            say(f"{edition}: {page}: {kept} lines matched so far, {skipped} skipped")
    pool.set_book(lang, edition, source="aligned", fingerprint=measure(samples) if samples else None,
                  rtl=bool(samples) and is_rtl(" ".join(index.words[:20])))
    return {"edition": edition, "lang": lang, "kept": kept, "skipped": skipped}


def editions_dir() -> Path:
    return user_data_dir() / "editions"


def read_and_align(scan: Path, source: str, edition: str, lang: str | None = None, pages: list[int] | None = None,
                   progress: Callable[[str], None] | None = None) -> dict:
    """Read a scan of an edition (a project kept in the user's folder) and align its lines."""
    from .project import Project

    say = progress or (lambda msg: None)
    lang = lang or LANGS.get(source)
    ws = editions_dir() / f"{pool.slug(edition)}.ectype"
    project = Project(ws, Path(scan))
    project.settings.name = edition
    project.settings.mode = "text"
    project.set_languages([lang])
    for stage in ("preprocess", "ocr"):
        say(f"{edition}: {stage}")
        if stage == "preprocess":
            from .preprocess import preprocess

            preprocess(Path(scan), ws, pages=pages)
        else:
            from .ocr import ocr_pages

            ocr_pages(ws / "pages", ws / "ocr", lang=project.settings.lang)
    return align_workspace(ws, source, edition, lang, progress=say)
