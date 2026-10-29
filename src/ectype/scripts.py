"""Writing systems in the scan: finding words in a script the OCR languages don't cover, and
reading again, in their own language, words of another script among the main text.

Tesseract reads a page with all the chosen languages at once ("eng+grc+heb"), which works for
most Greek and Hebrew words in an English book, but a short word or one with vowel points can
still come out as Latin look-alikes ("O°7>8" for אֱלֹהִים). And when Greek or Hebrew isn't among
the chosen languages at all, those words come out as garbage that nothing points out.

So after the page is read, each word Tesseract was unsure of is shown to its script detector
(the orientation and script data, osd, that the app ships), the word's image repeated a few
times so the detector has enough to go on. Then:
- a word in a script one of the chosen languages covers, but not read in that script, is read
  again by itself with that language alone;
- words in a script none of the chosen languages covers are counted, so the app can offer to
  add a language for it.

Right-to-left scripts (Hebrew, Arabic, Syriac...) come from Tesseract in logical order, with
character boxes from right to left; `is_rtl` tells the later stages to take them in visual order.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

# Script of each Tesseract language (Latin unless listed).
SCRIPT_OF = {
    "grc": "Greek", "ell": "Greek",
    "heb": "Hebrew", "yid": "Hebrew",
    "rus": "Cyrillic", "ukr": "Cyrillic", "bel": "Cyrillic", "bul": "Cyrillic", "mkd": "Cyrillic", "srp": "Cyrillic",
    "kaz": "Cyrillic", "kir": "Cyrillic", "mon": "Cyrillic", "tat": "Cyrillic", "tgk": "Cyrillic",
    "uzb_cyrl": "Cyrillic", "aze_cyrl": "Cyrillic",
    "ara": "Arabic", "fas": "Arabic", "urd": "Arabic", "pus": "Arabic", "snd": "Arabic", "uig": "Arabic",
    "syr": "Syriac", "hye": "Armenian", "kat": "Georgian", "kat_old": "Georgian", "amh": "Ethiopic", "tir": "Ethiopic",
    "hin": "Devanagari", "mar": "Devanagari", "nep": "Devanagari", "san": "Devanagari",
    "ben": "Bengali", "asm": "Bengali", "guj": "Gujarati", "pan": "Gurmukhi", "ori": "Oriya", "tam": "Tamil",
    "tel": "Telugu", "kan": "Kannada", "mal": "Malayalam", "sin": "Sinhala", "tha": "Thai", "lao": "Lao",
    "khm": "Khmer", "mya": "Myanmar", "bod": "Tibetan", "dzo": "Tibetan", "div": "Thaana", "chr": "Cherokee",
    "chi_sim": "Han", "chi_tra": "Han", "jpn": "Japanese", "kor": "Hangul",
}
# The language to suggest for a script the detector found (scholarly defaults: Ancient Greek,
# which also reads modern monotonic Greek, and Hebrew).
SUGGEST = {"Greek": "grc", "Hebrew": "heb", "Cyrillic": "rus", "Arabic": "ara", "Syriac": "syr",
           "Armenian": "hye", "Georgian": "kat", "Ethiopic": "amh", "Devanagari": "hin", "Bengali": "ben",
           "Gujarati": "guj", "Gurmukhi": "pan", "Oriya": "ori", "Tamil": "tam", "Telugu": "tel", "Kannada": "kan",
           "Malayalam": "mal", "Sinhala": "sin", "Thai": "tha", "Lao": "lao", "Khmer": "khm", "Myanmar": "mya",
           "Tibetan": "bod", "Thaana": "div", "Cherokee": "chr", "Han": "chi_sim", "HanS": "chi_sim",
           "HanT": "chi_tra", "Japanese": "jpn", "Hangul": "kor", "Korean": "kor"}
# Unicode character-name prefix of a script's letters, to check that a reading is in it.
UNICODE_NAME = {"Greek": "GREEK", "Hebrew": "HEBREW", "Cyrillic": "CYRILLIC", "Arabic": "ARABIC",
                "Syriac": "SYRIAC", "Armenian": "ARMENIAN", "Georgian": "GEORGIAN", "Ethiopic": "ETHIOPIC",
                "Devanagari": "DEVANAGARI", "Thai": "THAI", "Hangul": "HANGUL", "Latin": "LATIN"}

UNSURE = 60.0  # words below this confidence are checked
MAX_CHECKED = 40  # per page, the least confident first
# A plausible word in Latin script: letters (mostly lower case, with a vowel), maybe in
# brackets or quotes, maybe with an apostrophe or closing punctuation.
LATIN_WORD = re.compile(r"^[(\[“‘\"']?(?=[A-Za-z'’]*[aeiouyAEIOUY])[A-Z]?[a-z'’]{2,}[)\]”’\"'.,;:!?]*$")
BIDI_MARKS = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮⁦⁧⁨⁩"))


def script_of(lang: str) -> str:
    if lang.startswith("kraken_"):
        from .kraken import main_script

        return main_script(lang) or "Latin"
    if lang not in SCRIPT_OF:
        from .train import trained_models

        lang = trained_models().get(lang, {}).get("base", lang)  # a trained model reads its base's script
    return SCRIPT_OF.get(lang, "Latin")


def clean(text: str) -> str:
    """Text without the direction marks Tesseract puts around right-to-left words."""
    return text.translate(BIDI_MARKS)


def is_rtl(text: str) -> bool:
    """True for a word in a right-to-left script (Hebrew, Arabic, Syriac...)."""
    return any(unicodedata.bidirectional(c) in ("R", "AL") for c in text)


def in_script(text: str, script: str) -> bool:
    """True when most of the text's letters are in the script."""
    prefix = UNICODE_NAME.get(script)
    letters = [c for c in text if c.isalpha()]
    if not letters or prefix is None:
        return bool(letters)
    return sum(unicodedata.name(c, "").startswith(prefix) for c in letters) * 2 > len(letters)


def detect(cmd: str, env: dict | None, crop: np.ndarray, flags: dict) -> tuple[str | None, float]:
    """The script of one word's image, by Tesseract's script detector (psm 0), the word repeated
    so the detector has enough text: (script, confidence), or (None, 0) when it can't tell."""
    word = np.pad(crop, ((0, 0), (0, max(8, crop.shape[0] // 2))), constant_values=255)
    row = np.hstack([word] * max(3, 600 // max(1, word.shape[1]) + 1))
    tile = np.vstack([np.pad(row, ((10, 10), (0, 0)), constant_values=255)] * 3)
    with tempfile.TemporaryDirectory(prefix="ectype-osd-") as tmp:
        src = Path(tmp) / "word.png"
        Image.fromarray(tile).save(src, dpi=(300, 300))
        try:
            out = subprocess.run([cmd, str(src), "-", "--psm", "0"], capture_output=True, text=True,
                                 timeout=60, env=env, **flags).stdout
        except (OSError, subprocess.SubprocessError):
            return None, 0.0
    script = re.search(r"Script: (\w+)", out)
    conf = re.search(r"Script confidence: ([\d.]+)", out)
    return (script.group(1) if script else None), (float(conf.group(1)) if conf else 0.0)


def osd_available(tessdata: Path | None) -> bool:
    return tessdata is not None and (Path(tessdata) / "osd.traineddata").is_file()


def check_words(gray: np.ndarray, page, langs: list[str], reread, detect_fn=None,
                defer: frozenset = frozenset()) -> Counter:
    """Check the page's unsure words (see the module docstring). `reread(crop, lang)` reads one
    word image with one language and returns its words (an OcrPage's, in crop coordinates).
    Words are changed in place. Words found in a script in `defer` (one a Kraken model reads)
    are only marked, word.script, for Kraken to read with their neighbours. Returns how many
    words were found in each script that none of the chosen languages covers."""
    from .resources import subprocess_flags, tessdata_dir, tesseract_cmd, tesseract_env

    unread: Counter = Counter()
    if detect_fn is None:
        cmd = tesseract_cmd()
        if not cmd or not osd_available(tessdata_dir()):
            return unread
        env, flags = tesseract_env(), subprocess_flags()
        detect_fn = lambda crop: detect(cmd, env, crop, flags)  # noqa: E731
    covered: dict[str, str] = {}
    for lang in langs:
        covered.setdefault(script_of(lang), lang)
    h, w = gray.shape
    # With a Kraken model chosen for a script, Tesseract (without that script's language) can
    # read its words as confident figures ("13723" for וּבָרְכוּ): check what isn't a plain word too.
    unsure = sorted((wd for wd in page.words() if wd.conf < UNSURE or defer and not LATIN_WORD.match(wd.text)
                     and not any(in_script(wd.text, s) for s in defer)), key=lambda wd: wd.conf)[:MAX_CHECKED]
    for word in unsure:
        x0, y0, x1, y1 = word.box
        if not any(c.isalnum() for c in word.text) or (x1 - x0) < (y1 - y0):
            continue  # punctuation, or too short to judge (a lone figure): the detector guesses
        pad = max(6, (y1 - y0) // 4)
        ox, oy = max(0, x0 - pad), max(0, y0 - pad)
        crop = gray[oy:min(h, y1 + pad), ox:min(w, x1 + pad)]
        if crop.size == 0 or crop.shape[1] < 8:
            continue
        script, conf = detect_fn(crop)
        if not script or conf <= 0 or script == "Latin" and "Latin" in covered:
            continue  # no answer, a guess with no confidence, or the main script
        if script in defer:
            if not in_script(word.text, script) and not (LATIN_WORD.match(word.text) and conf < 5):
                word.script = script
            continue
        if script not in covered:
            if script in SUGGEST or script == "Latin":
                unread[script] += 1
            continue
        if any(in_script(word.text, other) for other in covered if other != "Latin"):
            continue  # already read in a script of the chosen languages
        if LATIN_WORD.match(word.text) and conf < 5:
            continue  # looks like a word of the main text ("(mil'el)"): only on a clear detection
        words = reread(np.pad(crop, 20, constant_values=255), covered[script])
        text = "".join(wd.text for wd in words)
        if not words or not in_script(text, script):
            continue
        # The new reading, moved back to page coordinates (the crop was padded by 20).
        dx, dy = ox - 20, oy - 20
        new = words[0] if len(words) == 1 else _join_words(words)
        for c in new.chars:
            c.box = [c.box[0] + dx, c.box[1] + dy, c.box[2] + dx, c.box[3] + dy]
        word.text, word.chars, word.conf = new.text, new.chars, new.conf
    return unread


def _join_words(words):
    """Several words read from one word's image (a split): one word, their text joined."""
    first = words[0]
    first.text = "".join(w.text for w in words)
    first.chars = [c for w in words for c in w.chars]
    first.conf = float(min(w.conf for w in words))
    return first


def suggest(unread: dict[str, int], chosen: list[str], pages: list[dict[str, int]] | None = None,
            min_words: int = 3, min_on_a_page: int = 3) -> list[str]:
    """Languages to add for scripts none of the chosen covers, found in at least min_words words,
    with min_on_a_page of them on one page (real text in another script comes in clusters; the
    detector's occasional wrong guess on a smudged word doesn't)."""
    covered = {script_of(lang) for lang in chosen}
    out = []
    for script, n in sorted(unread.items(), key=lambda kv: -kv[1]):
        lang = SUGGEST.get(script) or ("eng" if script == "Latin" else None)
        clustered = pages is None or any(p.get(script, 0) >= min_on_a_page for p in pages)
        if lang and n >= min_words and clustered and script not in covered and lang not in out:
            out.append(lang)
    return out
