"""Known texts for training: what generated lines are set from, and what scans of an edition
are matched against (`synthtrain.py`, `align.py`).

- "hebrew-bible": the Westminster Leningrad Codex (public domain), from the Open Scriptures
  Hebrew Bible (CC BY 4.0, github.com/openscriptures/morphhb).
- "greek-nt": the SBL Greek New Testament (CC BY 4.0, github.com/LogosBible/SBLGNT).
- "vulgate": the Clementine Vulgate (public domain), as one file of verses from
  github.com/lukesmithxyz/vul (public domain).
- "peshitta": the Peshitta Old Testament (with the deuterocanonical books), unvowelled, from
  the ETCBC's plain text (github.com/ETCBC/peshitta; the text CC BY-NC 4.0: free to use, not
  commercially, with credit to W. Th. van Peursen, G. J. Veldman, C. Sikkel, H. Vlaardingerbroek
  and D. Roorda; their programs MIT).
- "arabic": the Qurʾān (fully vowelled; Tanzil.net's "simple" text, CC BY 3.0, used
  unchanged, credit to Tanzil.net) and the Smith–Van Dyck Bible of 1865 (public domain,
  partly vowelled; eBible.org's one verse a line), their verses taken in turn.
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

import io
import re
import unicodedata
import urllib.parse
import urllib.request
import zipfile
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
VULGATE_URL = "https://raw.githubusercontent.com/lukesmithxyz/vul/master/vul.tsv"
PESHITTA_URL = "https://raw.githubusercontent.com/ETCBC/peshitta/master/plain/0.2/{book}.txt"
PESHITTA_BOOKS = ("Genesis Exodus Leviticus Numbers Deuteronomy Joshua Judges Ruth Samuel_1 Samuel_2 Kings_1 Kings_2 "
                  "Chronicles_1 Chronicles_2 Ezra Nehemia Esther Job Psalms Proverbs Ecclesiastes Song_of_Songs Isaiah "
                  "Jeremiah Lamentations Ezekiel Daniel Hosea Joel Amos Obadiah Jonah Micah Nahum Habakkuk Zephaniah "
                  "Haggai Zechariah Malachi Tobit_A Judith Wisdom_of_Solomon Sirach Baruch Maccabees_1_A "
                  "Maccabees_2").split()
# Tanzil's "simple" text, unchanged with its notice, as kept in a public repository (its 2019
# download, SHA-1 16d8abb3...): reachable where tanzil.net's own form isn't, and testable.
QURAN_URL = ("https://raw.githubusercontent.com/rizaumami/quran-epub/d96dab765d712634ba17d1550cd5d1dc15c25b36/"
             "src/quran/quran-simple.txt")
TANZIL_URL = "https://tanzil.net/pub/download/download.php"  # a form: the text chosen, the terms agreed to
TANZIL_FORM = {"quranType": "simple", "outType": "txt-2", "agree": "true"}  # txt-2: sura|aya|text a line
VAN_DYCK_URL = "https://ebible.org/Scriptures/arb-vd_vpl.zip"  # "GEN 1:1 text", a verse a line
SOURCES = {"hebrew-bible": "Hebrew Bible (Westminster Leningrad Codex)", "greek-nt": "Greek New Testament (SBLGNT)",
           "vulgate": "Latin Vulgate (Clementine)", "peshitta": "Peshitta (Old Testament, ETCBC)",
           "quran": "Qurʾān (Tanzil.net)", "van-dyck": "Arabic Bible (Smith–Van Dyck)"}
LANG = {"hebrew-bible": "heb", "greek-nt": "grc", "vulgate": "lat", "peshitta": "syr", "arabic": "ara"}

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


def _peshitta_book(txt: str) -> list[str]:
    """Verses of one book of the ETCBC's plain Peshitta: "Chapter n" headings left out, each
    verse a line starting with its number, long verses wrapped onto the lines after."""
    out: list[str] = []
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("Chapter"):
            continue
        m = re.match(r"^(\d+)\s+(.*)$", line)
        if m:
            out.append(m.group(2))
        elif out:
            out[-1] += " " + line
    return [re.sub(r"\s+", " ", v).strip() for v in out if v.strip()]


def _quran(txt: str) -> list[str]:
    """Verses of Tanzil's txt-2 text ("sura|aya|text"); its closing notice (# lines) left out
    (`_tanzil_notice` keeps it)."""
    out = []
    for line in txt.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and not line.startswith("#") and parts[2].strip():
            out.append(parts[2].strip())
    return out


def _tanzil_notice(txt: str) -> list[str]:
    """Tanzil's copyright notice (its # lines), which its terms ask to keep with any copy."""
    return [line.rstrip() for line in txt.splitlines() if line.startswith("#")]


def _get_quran() -> tuple[list[str], list[str]]:
    """The Qurʾān's verses and Tanzil's notice: from the repository's copy, else Tanzil's form."""
    problems = []
    for fetch in (lambda: _get(QURAN_URL), _tanzil_form):
        try:
            txt = fetch()
            verses = _quran(txt)
            if len(verses) >= 6000:
                return verses, _tanzil_notice(txt)
            problems.append(f"{len(verses)} verses")
        except Exception as exc:
            problems.append(str(exc))
    raise RuntimeError("; ".join(problems))


def _tanzil_form() -> str:
    form = urllib.parse.urlencode(TANZIL_FORM).encode()
    with urllib.request.urlopen(urllib.request.Request(TANZIL_URL, data=form), timeout=120) as r:
        return r.read().decode("utf-8")


def _van_dyck(data: bytes) -> list[str]:
    """Verses of eBible.org's VPL zip: lines "GEN 1:1 text" in its one text file."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        txt = z.read(name).decode("utf-8-sig")
    out = []
    for line in txt.splitlines():
        m = re.match(r"^[0-9A-Z]{3} \d+:\d+ (.+)$", line.strip())
        if m:
            out.append(re.sub(r"\s+", " ", m.group(1)).strip())
    return out


def _arabic(say: Callable[[str], None]) -> list[str]:
    """The Qurʾān's verses and the Van Dyck Bible's, taken in turn (so a sample of the first has
    both); either alone when the other can't be downloaded."""
    texts, problems = [], []
    for source in ("quran", "van-dyck"):
        try:
            texts.append(_cached(source, say))
        except Exception as exc:  # the other may still come
            problems.append(f"{SOURCES[source]}: {exc}")
    if not texts:
        raise RuntimeError("Couldn't download the Arabic texts (" + "; ".join(problems) + "). Check the connection "
                           "and try again.")
    out = []
    for k in range(max(len(t) for t in texts)):
        out += [t[k] for t in texts if k < len(t)]
    return out


def _cached(source: str, say: Callable[[str], None]) -> list[str]:
    """One downloaded text's verses, from the user's folder once fetched."""
    cache = folder() / f"{source}.txt"
    if not cache.is_file():
        say(f"downloading the {SOURCES[source]}")
        notice: list[str] = []
        if source == "quran":
            verses, notice = _get_quran()
        else:
            with urllib.request.urlopen(VAN_DYCK_URL, timeout=120) as r:
                verses = _van_dyck(r.read())
        if len(verses) < 1000:
            raise RuntimeError(f"the download held {len(verses)} verses, not the whole text")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(verses + notice) + "\n", encoding="utf-8", newline="\n")
    return [v for v in cache.read_text(encoding="utf-8").splitlines() if v.strip() and not v.startswith("#")]


def _vulgate(tsv: str) -> list[str]:
    """Verses of the vul.tsv file: book, abbreviation, book number, chapter, verse, text; the
    spaces it puts before colons and semicolons (French style, as the Clementine prints them)
    kept, since books quoting it print them too."""
    out = []
    for line in tsv.splitlines():
        parts = line.split("\t")
        if len(parts) >= 6 and parts[5].strip():
            out.append(re.sub(r"\s+", " ", parts[5]).strip())
    return out


def load(source: str, progress: Callable[[str], None] | None = None, accents: bool = False) -> list[str]:
    """The passages of a known text (verses), accents left out (kept with accents=True: the
    Hebrew Bible's cantillation, meteg and paseq), downloaded once."""
    keep = (lambda v: v) if accents else without_accents
    say = progress or (lambda msg: None)
    if source == "transliteration":
        from .translit import passages

        return passages(load("hebrew-bible", progress))
    if source == "arabic":
        return _arabic(say)
    if source not in SOURCES:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"No text {source!r}: give hebrew-bible, greek-nt, vulgate, peshitta, arabic, "
                                    "or a text file.")
        return [keep(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cache = folder() / f"{source}.txt"
    if not cache.is_file() and source == "vulgate":
        say(f"downloading the {SOURCES[source]}")
        verses = _vulgate(_get(VULGATE_URL))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(verses) + "\n", encoding="utf-8", newline="\n")
    if not cache.is_file():
        verses = []
        books, url, parse = {"hebrew-bible": (HEBREW_BOOKS, HEBREW_URL, _hebrew_book),
                             "greek-nt": (GREEK_BOOKS, GREEK_URL, _greek_book),
                             "peshitta": (PESHITTA_BOOKS, PESHITTA_URL, _peshitta_book)}[source]
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
