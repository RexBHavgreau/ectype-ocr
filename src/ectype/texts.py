"""Known texts for training: what generated lines are set from, and what scans of an edition
are matched against (`synthtrain.py`, `align.py`).

- "hebrew-bible": the Westminster Leningrad Codex (public domain), from the Open Scriptures
  Hebrew Bible (CC BY 4.0, github.com/openscriptures/morphhb).
- "greek-nt": the SBL Greek New Testament (CC BY 4.0, github.com/LogosBible/SBLGNT).
- a file: any UTF-8 text, one passage a line (e.g. the Westcott-Hort text, or the text of
  the edition that was scanned).

Accents are left out: Hebrew cantillation marks (and meteg, paseq and the upper and lower
dots) are removed, keeping consonants, vowel points, dagesh, shin and sin dots, maqaf and
sof pasuq; Greek keeps its accents and breathings, which are part of its spelling.
Downloads are kept in the user's folder.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

from .resources import user_data_dir

HEBREW_BOOKS = ("Gen Exod Lev Num Deut Josh Judg Ruth 1Sam 2Sam 1Kgs 2Kgs 1Chr 2Chr Ezra Neh Esth Job Ps Prov "
                "Eccl Song Isa Jer Lam Ezek Dan Hos Joel Amos Obad Jonah Mic Nah Hab Zeph Hag Zech Mal").split()
HEBREW_URL = "https://raw.githubusercontent.com/openscriptures/morphhb/master/wlc/{book}.xml"
GREEK_BOOKS = ("Matt Mark Luke John Acts Rom 1Cor 2Cor Gal Eph Phil Col 1Thess 2Thess 1Tim 2Tim Titus Phlm Heb "
               "Jas 1Pet 2Pet 1John 2John 3John Jude Rev").split()
GREEK_URL = "https://raw.githubusercontent.com/LogosBible/SBLGNT/master/data/sblgnt/text/{book}.txt"
SOURCES = {"hebrew-bible": "Hebrew Bible (Westminster Leningrad Codex)", "greek-nt": "Greek New Testament (SBLGNT)"}
LANG = {"hebrew-bible": "heb", "greek-nt": "grc"}

# Hebrew marks left out: the accents (U+0591-U+05AF), meteg, paseq, the upper and lower dots, rafe.
HEBREW_ACCENTS = re.compile("[֑-ֽֿ֯׀ׅׄ]")
# The SBLGNT's critical signs.
GREEK_SIGNS = re.compile("[⸀-⸏†‡ʼ]")


def folder() -> Path:
    return user_data_dir() / "texts"


def without_accents(text: str) -> str:
    """Hebrew without its cantillation (see the module docstring); other text unchanged."""
    return unicodedata.normalize("NFC", HEBREW_ACCENTS.sub("", unicodedata.normalize("NFD", text)))


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8")


def _hebrew_book(xml: str) -> list[str]:
    """Verses of one book of the OSHB: words with their morpheme slashes removed, maqaf joining
    words, sof pasuq at the end; the open and closed paragraph marks (פ, ס) left out."""
    ns = "{http://www.bibletechnologies.net/2003/OSIS/namespace}"
    out = []
    for verse in ET.fromstring(xml).iter(f"{ns}verse"):
        text = ""
        for el in verse:
            tag = el.tag.replace(ns, "")
            if tag == "w":
                text += (el.text or "").replace("/", "") + " "
            elif tag == "seg" and el.get("type") == "x-maqqef":
                text = text.rstrip() + (el.text or "־")
            elif tag == "seg" and el.get("type") == "x-sof-pasuq":
                text = text.rstrip() + (el.text or "׃") + " "
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append(text)
    return out


def _greek_book(txt: str) -> list[str]:
    out = []
    for line in txt.splitlines():
        if "\t" in line:
            text = GREEK_SIGNS.sub("", line.split("\t", 1)[1])
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                out.append(text)
    return out


def load(source: str, progress: Callable[[str], None] | None = None) -> list[str]:
    """The passages of a known text (verses), accents left out, downloaded once."""
    say = progress or (lambda msg: None)
    if source not in SOURCES:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"No text {source!r}: give hebrew-bible, greek-nt, or a text file.")
        return [without_accents(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cache = folder() / f"{source}.txt"
    if not cache.is_file():
        verses = []
        books, url, parse = ((HEBREW_BOOKS, HEBREW_URL, _hebrew_book) if source == "hebrew-bible"
                             else (GREEK_BOOKS, GREEK_URL, _greek_book))
        for k, book in enumerate(books, 1):
            say(f"downloading the {SOURCES[source]}: {book} ({k} of {len(books)})")
            verses += parse(_get(url.format(book=book)))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(verses) + "\n", encoding="utf-8")
    return [without_accents(v) for v in cache.read_text(encoding="utf-8").splitlines() if v.strip()]


def base_letters(word: str) -> str:
    """A word reduced for matching: its letters only, without points, accents, punctuation or
    case, and final forms as ordinary ones (σ for ς; Hebrew final letters as they are)."""
    out = []
    for c in unicodedata.normalize("NFD", word):
        if unicodedata.combining(c) or not c.isalpha():
            continue
        out.append(c.lower())
    return "".join(out).replace("ς", "σ")
