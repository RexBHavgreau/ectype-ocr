"""OCR languages: which are installed, their names, and downloading more.

Tesseract reads every language from one folder. The app ships English and the
orientation data; languages added from the app are downloaded from the Tesseract
project's `tessdata` repository into a folder of the user's own
(`resources.user_tessdata_dir`), and the shipped or system languages are copied
beside them so that Tesseract still finds everything in one place.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from . import verify
from .resources import base_tessdata_dir, tessdata_dir, user_tessdata_dir

# Pinned to one commit and checked against its known fingerprints (verify.py).
DOWNLOAD_URL = verify.RAW.format(repo=verify.PINS["tessdata"][0], commit=verify.TESSDATA_COMMIT,
                                 path="{code}.traineddata")

# Tesseract's language data (tessdata repository), by code. "osd" (orientation) and
# "equ" (equations) are not languages to choose and are left out.
LANGUAGES = {
    "afr": "Afrikaans", "amh": "Amharic", "ara": "Arabic", "asm": "Assamese", "aze": "Azerbaijani",
    "aze_cyrl": "Azerbaijani (Cyrillic)", "bel": "Belarusian", "ben": "Bengali", "bod": "Tibetan",
    "bos": "Bosnian", "bre": "Breton", "bul": "Bulgarian", "cat": "Catalan", "ceb": "Cebuano",
    "ces": "Czech", "chi_sim": "Chinese (Simplified)", "chi_sim_vert": "Chinese (Simplified, vertical)",
    "chi_tra": "Chinese (Traditional)", "chi_tra_vert": "Chinese (Traditional, vertical)", "chr": "Cherokee",
    "cos": "Corsican", "cym": "Welsh", "dan": "Danish", "deu": "German", "div": "Dhivehi", "dzo": "Dzongkha",
    "ell": "Greek (Modern)", "eng": "English", "enm": "English (Middle, 1100–1500)", "epo": "Esperanto",
    "est": "Estonian", "eus": "Basque", "fao": "Faroese", "fas": "Persian", "fil": "Filipino",
    "fin": "Finnish", "fra": "French", "frm": "French (Middle, ca. 1400–1600)", "fry": "Frisian (Western)",
    "gla": "Scottish Gaelic", "gle": "Irish", "glg": "Galician", "grc": "Greek (Ancient)", "guj": "Gujarati",
    "hat": "Haitian Creole", "heb": "Hebrew", "hin": "Hindi", "hrv": "Croatian", "hun": "Hungarian",
    "hye": "Armenian", "iku": "Inuktitut", "ind": "Indonesian", "isl": "Icelandic", "ita": "Italian",
    "ita_old": "Italian (Old)", "jav": "Javanese", "jpn": "Japanese", "jpn_vert": "Japanese (vertical)",
    "kan": "Kannada", "kat": "Georgian", "kat_old": "Georgian (Old)", "kaz": "Kazakh", "khm": "Khmer",
    "kir": "Kyrgyz", "kmr": "Kurdish (Kurmanji)", "kor": "Korean", "kor_vert": "Korean (vertical)",
    "lao": "Lao", "lat": "Latin", "lav": "Latvian", "lit": "Lithuanian", "ltz": "Luxembourgish",
    "mal": "Malayalam", "mar": "Marathi", "mkd": "Macedonian", "mlt": "Maltese", "mon": "Mongolian",
    "mri": "Maori", "msa": "Malay", "mya": "Burmese", "nep": "Nepali", "nld": "Dutch", "nor": "Norwegian",
    "oci": "Occitan", "ori": "Odia", "pan": "Punjabi", "pol": "Polish", "por": "Portuguese", "pus": "Pashto",
    "que": "Quechua", "ron": "Romanian", "rus": "Russian", "san": "Sanskrit", "sin": "Sinhala",
    "slk": "Slovak", "slv": "Slovenian", "snd": "Sindhi", "spa": "Spanish", "spa_old": "Spanish (Old)",
    "sqi": "Albanian", "srp": "Serbian", "srp_latn": "Serbian (Latin)", "sun": "Sundanese", "swa": "Swahili",
    "swe": "Swedish", "syr": "Syriac", "tam": "Tamil", "tat": "Tatar", "tel": "Telugu", "tgk": "Tajik",
    "tha": "Thai", "tir": "Tigrinya", "ton": "Tongan", "tur": "Turkish", "uig": "Uyghur", "ukr": "Ukrainian",
    "urd": "Urdu", "uzb": "Uzbek", "uzb_cyrl": "Uzbek (Cyrillic)", "vie": "Vietnamese", "yid": "Yiddish",
    "yor": "Yoruba",
}
NOT_LANGUAGES = {"osd", "equ"}


def name(code: str) -> str:
    if code in LANGUAGES:
        return LANGUAGES[code]
    if code.startswith("kraken_"):
        from .kraken import name as kraken_name

        return kraken_name(code)
    from .train import trained_models

    model = trained_models().get(code)
    if model:
        return f"{LANGUAGES.get(model['base'], model['base'])}, trained on {model['book']}"
    return code


def split(lang: str) -> list[str]:
    """"eng+grc" -> ["eng", "grc"], dropping empties and repeats."""
    out: list[str] = []
    for code in lang.split("+"):
        code = code.strip()
        if code and code not in out:
            out.append(code)
    return out


def join(codes: list[str]) -> str:
    return "+".join(codes) or "eng"


def installed() -> list[str]:
    """Codes of the languages that can be used now, sorted by name: Tesseract's, and the
    Kraken models installed (kraken.py). The models Ectype trains are not among them: they
    stand behind the language they were trained for (models.py)."""
    from .kraken import installed_models
    from .models import is_internal

    folder = tessdata_dir()
    codes = set(installed_models())
    if folder is not None:
        codes |= {p.name[:-len(".traineddata")] for p in folder.glob("*.traineddata")} - NOT_LANGUAGES
    return sorted((c for c in codes if not is_internal(c)), key=lambda c: name(c).lower())


def available() -> list[str]:
    """Codes that can be downloaded, sorted by name."""
    return sorted(LANGUAGES, key=lambda c: LANGUAGES[c].lower())


def _seed(user: Path) -> None:
    """Copy the shipped (or system) language data and Tesseract's output configs into
    the user's folder, so that one folder holds everything. Files already there stay."""
    base = base_tessdata_dir()
    user.mkdir(parents=True, exist_ok=True)
    if base is None or base.resolve() == user.resolve():
        return
    for item in base.iterdir():
        target = user / item.name
        if item.is_file() and item.suffix == ".traineddata" and not target.exists():
            shutil.copy2(item, target)
        elif item.is_dir() and item.name in ("configs", "tessconfigs") and not target.exists():
            shutil.copytree(item, target)


def download(codes: list[str], progress: Callable[[str, int, int], None] | None = None,
             url: str = DOWNLOAD_URL, fingerprints: dict[str, dict] | None = None) -> list[str]:
    """Download language data into the user's folder. `progress(code, bytes_done, bytes_total)`
    is called as data arrives. Returns the codes now installed. Each file is checked against its
    known fingerprint (`fingerprints`, code -> fingerprint, in place of the shipped list) and
    only then put in place; one that doesn't match, or isn't known, raises
    `verify.DownloadMismatch` and leaves no partial file behind."""
    user = user_tessdata_dir()
    _seed(user)
    done = []
    for code in codes:
        if code not in LANGUAGES:
            raise ValueError(f"unknown language code {code!r}")
        target = user / f"{code}.traineddata"
        if target.is_file():
            done.append(code)
            continue
        want = fingerprints.get(code) if fingerprints is not None else verify.known("tessdata", target.name)
        verify.fetch(url.format(code=code), target, want or {}, name=f"The {LANGUAGES[code]} language data",
                     progress=(lambda got, total, code=code: progress(code, got, total)) if progress else None)
        done.append(code)
    return done


# -- Choosing a Kraken model for a language (Beaux, 0.11.0: "they're all lumped together") ----
# A project still keeps its Kraken models as codes ("kraken_7051645") inside its language list;
# these functions only separate the two kinds, so the menu and the command line can offer
# "read Hebrew with: Tesseract alone / this model / that model".


def tesseract_codes(lang_list: list[str]) -> list[str]:
    """The Tesseract languages (and Ectype's trained ones) in a list, in order."""
    return [c for c in lang_list if not c.startswith("kraken_")]


def kraken_codes(lang_list: list[str]) -> list[str]:
    """The Kraken models in a list, in order."""
    return [c for c in lang_list if c.startswith("kraken_")]


def kraken_for(lang_code: str) -> list[str]:
    """The installed Kraken models that read this language's script, by name. A model of
    several scripts is offered for each of them."""
    from .kraken import installed_models, reads
    from .scripts import script_of

    return models_for_script(script_of(lang_code), installed_models(), reads)


def models_for_script(script: str, installed=None, reads_fn=None) -> list[str]:
    from .kraken import installed_models, reads

    installed = installed_models() if installed is None else installed
    reads_fn = reads if reads_fn is None else reads_fn
    return sorted((k for k in installed if script in reads_fn(k)), key=lambda k: name(k).lower())


def reader_of(lang_list: list[str], lang_code: str) -> str | None:
    """The Kraken model chosen for this language (the first in the list that reads its
    script), or None: Tesseract alone."""
    from .scripts import script_of

    return reader_for_script(lang_list, script_of(lang_code))


def reader_for_script(lang_list: list[str], script: str) -> str | None:
    """The model that reads this script, as reading picks it (ocr.py): of the models in the list
    that read it, the one of fewest scripts, the first listed among equals."""
    from .kraken import reads

    found = [k for k in kraken_codes(lang_list) if script in reads(k)]
    return min(found, key=lambda k: len(reads(k))) if found else None


def choose_reader_for_script(lang_list: list[str], script: str, kraken_code: str | None) -> list[str]:
    """The list with the Kraken models that read this script taken out, and the chosen one
    (or none: Tesseract alone) put in the place of the first removed, else at the end. A model
    that also reads another ticked language's script stays for that one: reading gives each
    script the chosen model of fewest scripts (`reader_for_script`), so a model of one script
    chosen here takes this script from it. (Choosing Tesseract alone can't take a script from
    such a model; the menu then still shows the model as this script's reader.)"""
    from .kraken import reads
    from .scripts import script_of

    others = {script_of(c) for c in tesseract_codes(lang_list)} - {script}
    out: list[str] = []
    placed = kraken_code is None
    for code in lang_list:
        if code.startswith("kraken_") and script in reads(code) and not others & set(reads(code)):
            if not placed:
                out.append(kraken_code)
                placed = True
            continue
        if code != kraken_code:
            out.append(code)
    if not placed:
        out.append(kraken_code)
    return out


def choose_reader(lang_list: list[str], lang_code: str, kraken_code: str | None) -> list[str]:
    """The language list with this language read by the chosen Kraken model (None: by
    Tesseract alone). The language itself is ticked too. Other languages and models stay."""
    from .scripts import script_of

    out = choose_reader_for_script(list(lang_list), script_of(lang_code), kraken_code)
    if lang_code not in out:
        out.insert(out.index(kraken_code) if kraken_code else 0, lang_code)
    return out


def orphan_readers(lang_list: list[str]) -> list[str]:
    """Kraken models in a list that no ticked language of their script goes with (older
    projects held a model alone). They are still shown, as that script's reader."""
    from .kraken import reads
    from .scripts import script_of

    scripts = {script_of(c) for c in tesseract_codes(lang_list)}
    return [k for k in kraken_codes(lang_list) if reads(k) and not scripts & set(reads(k))]


def reader_rows(lang_list: list[str]) -> list[tuple[str, str, list[str]]]:
    """What the menu shows to choose a reader: (script, what it's for, models available), one
    row per script that has a ticked language and a model installed or chosen, or a model
    chosen alone. 'what it's for' is the language name(s), e.g. "Hebrew"."""
    from .kraken import reads
    from .scripts import script_of

    rows: dict[str, list[str]] = {}
    for code in tesseract_codes(lang_list):
        rows.setdefault(script_of(code), []).append(code)
    for k in orphan_readers(lang_list):
        for s in reads(k)[:1]:
            rows.setdefault(s, [])
    out = []
    for script, codes in rows.items():
        have = models_for_script(script)
        chosen = reader_for_script(lang_list, script)
        if chosen and chosen not in have:
            have.append(chosen)
        if have:
            label = " and ".join(name(c) for c in codes) if codes else script
            out.append((script, label, have))
    return out


def reader_title(code: str) -> str:
    """"Kraken: <title>" or "Kraken: trained on <book>" - a model as the menus name it."""
    from .kraken import installed_models

    m = installed_models().get(code)
    if m is None:
        return f"Kraken: {code} (not installed)"
    if m.get("book"):
        return f"Kraken: trained on {m['book']}"
    return f"Kraken: {m.get('title') or code}"


def apply_kraken_choices(lang_list: list[str], choices: list[str]) -> list[str]:
    """Command line: --kraken LANG=MODEL (MODEL a code, or just its number). Raises ValueError
    in plain words if the model isn't installed or doesn't read the language's script."""
    from .kraken import installed_models, reads
    from .scripts import script_of

    installed = installed_models()
    out = list(lang_list)
    for item in choices:
        lang, sep, model = item.partition("=")
        lang, model = lang.strip(), model.strip()
        if not sep or not lang or not model:
            raise ValueError(f"--kraken {item!r}: write it as LANG=MODEL, for example --kraken heb=7051645 "
                             "(see: ectype kraken list)")
        if lang.startswith("kraken_"):
            raise ValueError(f"--kraken {item!r}: the first part is the language (heb, grc, eng…), the second the model.")
        code = model if model.startswith("kraken_") else "kraken_" + model.lstrip("#")
        if code not in installed:
            raise ValueError(f"The Kraken model {model} isn't installed. See what is: ectype kraken list. "
                             f"To add one: ectype kraken available, then ectype kraken add <number>.")
        script = script_of(lang)
        if script not in reads(code):
            raise ValueError(f"The Kraken model {model} reads {', '.join(reads(code))}, not {name(lang)} ({script}). "
                             f"Models for {script}: " + (", ".join(k.removeprefix('kraken_') for k in models_for_script(script))
                                                         or "none installed yet (ectype kraken available --script "
                                                         f"{script})"))
        out = choose_reader(out, lang, code)
    return out


class ReaderWithoutLanguage(ValueError):
    """A Kraken model of several scripts chosen with none of its languages ticked: which script
    the book is in can't be known, so the reading stops with this message in plain words."""


def reading_codes(codes: list[str]) -> list[str]:
    """The languages a page is read with. Tesseract reads every page first (it finds the lines and
    words Kraken rereads), so a Kraken model chosen without a ticked language of its script brings
    that script's Tesseract language along: before, Tesseract quietly read such pages as English,
    left to right, and Kraken's Hebrew came out in pieces back to front (Beaux, the Hebrew grammar
    read with the PP-OCRv6 model alone). A model of several scripts with none ticked can't say
    which script the book is in: ReaderWithoutLanguage."""
    from .kraken import reads
    from .scripts import SUGGEST, script_of

    out = list(codes)
    ticked = {script_of(c) for c in tesseract_codes(codes)}
    for k in kraken_codes(codes):
        scripts = reads(k)
        if not scripts or ticked & set(scripts):
            continue
        if len(scripts) > 1:
            raise ReaderWithoutLanguage(
                f"The Kraken model “{reader_title(k).removeprefix('Kraken: ')}” reads several scripts "
                f"({', '.join(scripts)}), and none of their languages is ticked, "
                "so Ectype can't tell which the book is in. Tick the book's language (for a Hebrew book: Hebrew), "
                "then choose the model under “Read Hebrew with” (command line: --lang heb --kraken heb="
                f"{k.removeprefix('kraken_')}).")
        partner = SUGGEST.get(scripts[0]) or ("eng" if scripts[0] == "Latin" else None)
        if partner:
            out.insert(len(tesseract_codes(out)), partner)
            ticked.add(scripts[0])
    return out
