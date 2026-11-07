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
Downloads are kept in the user's folder. Every file comes from a pinned commit and is checked
against its known fingerprint before it is used (`verify.py`, `fingerprints.json`); a kept copy
is checked again when read (`_cache_ok`) and downloaded again if it doesn't match.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

from . import verify
from .resources import user_data_dir

HEBREW_BOOKS = ("Gen Exod Lev Num Deut Josh Judg Ruth 1Sam 2Sam 1Kgs 2Kgs 1Chr 2Chr Ezra Neh Esth Job Ps Prov "
                "Eccl Song Isa Jer Lam Ezek Dan Hos Joel Amos Obad Jonah Mic Nah Hab Zeph Hag Zech Mal").split()
# Each file's address is verify.url_for(source, name): its repository at the commit pinned in verify.py.
GREEK_BOOKS = ("Matt Mark Luke John Acts Rom 1Cor 2Cor Gal Eph Phil Col 1Thess 2Thess 1Tim 2Tim Titus Phlm Heb "
               "Jas 1Pet 2Pet 1John 2John 3John Jude Rev").split()
PESHITTA_BOOKS = ("Genesis Exodus Leviticus Numbers Deuteronomy Joshua Judges Ruth Samuel_1 Samuel_2 Kings_1 Kings_2 "
                  "Chronicles_1 Chronicles_2 Ezra Nehemia Esther Job Psalms Proverbs Ecclesiastes Song_of_Songs Isaiah "
                  "Jeremiah Lamentations Ezekiel Daniel Hosea Joel Amos Obadiah Jonah Micah Nahum Habakkuk Zephaniah "
                  "Haggai Zechariah Malachi Tobit_A Judith Wisdom_of_Solomon Sirach Baruch Maccabees_1_A "
                  "Maccabees_2").split()
# Tanzil's "simple" text, unchanged with its notice, as kept in a public repository (its 2019
# download, SHA-1 16d8abb3...), at the commit pinned in verify.py. Tanzil's own form is no
# longer a fallback: a form's answer can't be checked against a known fingerprint.
QURAN_FILE = "quran-simple.txt"
# eBible.org's Smith-Van Dyck zip ("GEN 1:1 text", a verse a line). Its download is OFF: it
# couldn't be fingerprinted (ebible.org is unreachable from where this was made). To turn it
# on, download the zip once, check it is the right file, and put its {"size": ..., "sha256": ...}
# here; `_cached` then downloads it and checks it like the others.
VAN_DYCK_URL = "https://ebible.org/Scriptures/arb-vd_vpl.zip"
VAN_DYCK_FINGERPRINT: dict | None = None
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


def _get(source: str, name: str) -> str:
    """One file of a pinned source, downloaded and checked (size, git id and SHA-256) before it
    is read; nothing is kept."""
    with tempfile.TemporaryDirectory(prefix="ectype-text-") as tmp:
        path = verify.fetch(verify.url_for(source, name), Path(tmp) / name, verify.known(source, name),
                            name=f"The {SOURCES[source]} ({name})")
        return path.read_text(encoding="utf-8")


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
    """The Qurʾān's verses and Tanzil's notice, from the repository's copy (checked)."""
    txt = _get("quran", QURAN_FILE)
    verses = _quran(txt)
    if len(verses) < 6000:
        raise RuntimeError(f"the download held {len(verses)} verses, not the whole text")
    return verses, _tanzil_notice(txt)


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


def _stamp(cache: Path) -> Path:
    return cache.with_name(cache.name + ".check")


def _cache_ok(cache: Path, pin: str) -> bool:
    """Whether a kept text was made from the files of the pinned commit `pin` and is unchanged:
    its stamp names that pin and the text's SHA-256. Copies made before the stamp existed (from
    a branch's head at some other time) and damaged ones fail, and are downloaded again."""
    try:
        stamp = json.loads(_stamp(cache).read_text(encoding="utf-8"))
        return bool(pin) and stamp.get("pin") == pin and stamp.get("sha256") == hashlib.sha256(cache.read_bytes()).hexdigest()
    except (OSError, ValueError, AttributeError):
        return False


def _write_cache(cache: Path, pin: str, lines: list[str]) -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    data = ("\n".join(lines) + "\n").encode("utf-8")
    _stamp(cache).unlink(missing_ok=True)
    cache.write_bytes(data)
    _stamp(cache).write_text(json.dumps({"pin": pin, "sha256": hashlib.sha256(data).hexdigest()}) + "\n",
                             encoding="utf-8", newline="\n")


def _cached(source: str, say: Callable[[str], None]) -> list[str]:
    """One downloaded Arabic text's verses, from the user's folder once fetched (and while the
    kept copy still matches what was checked)."""
    cache = folder() / f"{source}.txt"
    if source == "quran":
        pin = verify.QURAN_COMMIT
    elif VAN_DYCK_FINGERPRINT is None:
        raise RuntimeError("its download is off until a fingerprint is recorded for it")
    else:
        pin = VAN_DYCK_FINGERPRINT.get("sha256", "")
    if not _cache_ok(cache, pin):
        say(f"downloading the {SOURCES[source]}")
        notice: list[str] = []
        if source == "quran":
            verses, notice = _get_quran()
        else:
            with tempfile.TemporaryDirectory(prefix="ectype-text-") as tmp:
                zipped = verify.fetch(VAN_DYCK_URL, Path(tmp) / "arb-vd_vpl.zip", VAN_DYCK_FINGERPRINT, timeout=120,
                                      name=f"The {SOURCES[source]}")
                verses = _van_dyck(zipped.read_bytes())
        if len(verses) < 1000:
            raise RuntimeError(f"the download held {len(verses)} verses, not the whole text")
        _write_cache(cache, pin, verses + notice)
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
    pin = verify.PINS[source][1]
    if not _cache_ok(cache, pin):
        say(f"downloading the {SOURCES[source]}")
        if source == "vulgate":
            verses = _vulgate(_get("vulgate", "vul.tsv"))
        else:
            verses = []
            books, ext, parse = {"hebrew-bible": (HEBREW_BOOKS, ".xml", _hebrew_book),
                                 "greek-nt": (GREEK_BOOKS, ".txt", _greek_book),
                                 "peshitta": (PESHITTA_BOOKS, ".txt", _peshitta_book)}[source]
            for k, book in enumerate(books, 1):
                say(f"downloading the {SOURCES[source]}: {book} ({k} of {len(books)})")
                verses += parse(_get(source, book + ext))
        _write_cache(cache, pin, verses)
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
