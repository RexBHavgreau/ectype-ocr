"""OCR languages: which are installed, their names, and downloading more.

Tesseract reads every language from one folder. The app ships English and the
orientation data; languages added from the app are downloaded from the Tesseract
project's `tessdata` repository into a folder of the user's own
(`resources.user_tessdata_dir`), and the shipped or system languages are copied
beside them so that Tesseract still finds everything in one place.
"""

from __future__ import annotations

import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

from .resources import base_tessdata_dir, tessdata_dir, user_tessdata_dir

DOWNLOAD_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/{code}.traineddata"

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
             url: str = DOWNLOAD_URL) -> list[str]:
    """Download language data into the user's folder. `progress(code, bytes_done, bytes_total)`
    is called as data arrives. Returns the codes now installed. A failed download leaves
    no partial file behind."""
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
        with urllib.request.urlopen(url.format(code=code), timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            with tempfile.NamedTemporaryFile(dir=user, suffix=".part", delete=False) as tmp:
                try:
                    got = 0
                    while chunk := response.read(1 << 16):
                        tmp.write(chunk)
                        got += len(chunk)
                        if progress:
                            progress(code, got, total)
                    tmp.close()
                    if total and got != total:
                        raise OSError(f"download of {code} stopped at {got} of {total} bytes")
                    Path(tmp.name).replace(target)
                except BaseException:
                    tmp.close()
                    Path(tmp.name).unlink(missing_ok=True)
                    raise
        done.append(code)
    return done
