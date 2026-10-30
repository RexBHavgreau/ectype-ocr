"""Known texts for training: what generated lines are set from, and what scans of an edition
are matched against (`synthtrain.py`, `align.py`).

- "hebrew-bible": the Westminster Leningrad Codex (public domain), from the Open Scriptures
  Hebrew Bible (CC BY 4.0, github.com/openscriptures/morphhb).
- "greek-nt": the SBL Greek New Testament (CC BY 4.0, github.com/LogosBible/SBLGNT).
- "transliteration": English with transliterated words (Hebrew made from the Hebrew Bible,
  other Semitic languages, IPA, European accents), generated (`translit.py`).
- a file: any UTF-8 text, one passage a line (e.g. the Westcott-Hort text, or the text of
  the edition that was scanned).

Accents are left out: Hebrew cantillation marks (and meteg, paseq and the upper and lower
dots) are removed, keeping consonants, vowel points, dagesh, shin and sin dots, maqaf and
sof pasuq; Greek keeps its accents and breathings, which are part of its spelling. Lines set
for training ask for them (`load(accents=True)`): grammars and editions print Hebrew with
its accents, or with ֫ over a stress before the last syllable (`grammar_style`).
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


def load(source: str, progress: Callable[[str], None] | None = None, accents: bool = False) -> list[str]:
    """The passages of a known text (verses), accents left out (kept with accents=True: the
    Hebrew Bible's cantillation, meteg and paseq), downloaded once."""
    keep = (lambda v: v) if accents else without_accents
    say = progress or (lambda msg: None)
    if source == "transliteration":
        from .translit import passages

        return passages(load("hebrew-bible", progress))
    if source not in SOURCES:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"No text {source!r}: give hebrew-bible, greek-nt, or a text file.")
        return [keep(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cache = folder() / f"{source}.txt"
    if not cache.is_file():
        verses = []
        books, url, parse = ((HEBREW_BOOKS, HEBREW_URL, _hebrew_book) if source == "hebrew-bible"
                             else (GREEK_BOOKS, GREEK_URL, _greek_book))
        for k, book in enumerate(books, 1):
            say(f"downloading the {SOURCES[source]}: {book} ({k} of {len(books)})")
            verses += parse(_get(url.format(book=book)))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(verses) + "\n", encoding="utf-8", newline="\n")
    return [keep(v) for v in cache.read_text(encoding="utf-8").splitlines() if v.strip()]


STRESS = "\u05ab"  # ole: grammars (Driver, Gesenius) set it over a syllable stressed before the last
# Accents that stand on a word's first or last letter whatever its stress: no guide to it.
PLACED = set("\u0592\u0599\u059a\u05a0\u05a9\u05ad\u05ae")
FULL_VOWELS = set("\u05b1\u05b2\u05b3\u05b4\u05b5\u05b6\u05b7\u05b8\u05b9\u05ba\u05bb\u05c7")


def grammar_style(text: str) -> str:
    """Accented Hebrew as grammars print it: points without the cantillation, and ֫ over the
    stressed syllable of a word stressed before its last (milʿel), found from where the Codex's
    accent stands (the last in the word; silluq, the meteg before sof pasuq, too)."""
    return " ".join("־".join(_stressed(part) for part in word.split("־")) for word in text.split(" "))


def _syllables(units: list[str]) -> list[int]:
    """Indices of the letters (each with its marks) that carry a syllable's vowel: a full vowel,
    or a vav with holam or dagesh after an unvowelled letter (holam male, shureq); a furtive
    patah under a final guttural is none."""
    out = []
    for n, u in enumerate(units):
        vowel = any(c in FULL_VOWELS for c in u[1:])
        if u[0] == "\u05d5" and n and ("\u05bc" in u or "\u05b9" in u) and not any(c in FULL_VOWELS for c in units[n - 1][1:]):
            vowel = True
        if n == len(units) - 1 and n and u[0] in "\u05d7\u05e2\u05d4" and "\u05b7" in u and out and out[-1] == n - 1:
            vowel = False  # furtive patah: rûaḥ
        if vowel:
            out.append(n)
    return out


def _letters(text: str) -> list[str]:
    units: list[str] = []
    for c in unicodedata.normalize("NFD", text):
        if "\u05d0" <= c <= "\u05ea" or not units:
            units.append(c)
        else:
            units[-1] += c
    return units


def _stressed(word: str) -> str:
    units = _letters(word)
    plain = _letters(without_accents(word))
    final = word.rstrip().endswith("\u05c3")
    accented = [n for n, u in enumerate(units) if any(("\u0591" <= c <= "\u05af" and c not in PLACED)
                                                       or (c == "\u05bd" and final) for c in u[1:])]
    for mark in PLACED:  # an accent of the first or last letter written twice: the first marks the stress
        twice = [n for n, u in enumerate(units) if mark in u[1:]]
        if len(twice) > 1:
            accented = accented or twice[:1]
    if not accented or len(plain) != len(units):
        return unicodedata.normalize("NFC", "".join(plain))
    at = accented[-1]
    nuclei = _syllables(units)
    mine = next((n for n in nuclei if n >= at), None)  # the stressed syllable's vowel (on a vav after it, say)
    if mine is None or not any(n > mine for n in nuclei):
        return unicodedata.normalize("NFC", "".join(plain))  # stressed on the last syllable: left unmarked
    plain[at] += STRESS
    return unicodedata.normalize("NFC", "".join(plain))


def base_letters(word: str) -> str:
    """A word reduced for matching: its letters only, without points, accents, punctuation or
    case, and final forms as ordinary ones (σ for ς; Hebrew final letters as they are)."""
    out = []
    for c in unicodedata.normalize("NFD", word):
        if unicodedata.combining(c) or not c.isalpha():
            continue
        out.append(c.lower())
    return "".join(out).replace("ς", "σ")
