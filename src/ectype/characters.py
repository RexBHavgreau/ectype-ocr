"""Special characters for typing corrections (Review's character pane): sets of the characters
scholarly books use and keyboards lack, a search by Unicode name, and the ones used last.

A combining mark (a Hebrew point, a macron to set over a letter) is inserted after the letter
before the cursor; it is shown on a dotted circle (◌) so it can be seen.
"""

from __future__ import annotations

import json
import unicodedata

from .resources import user_data_dir

RECENT_MAX = 24


def _chars(*parts: str) -> list[str]:
    """The characters of the strings, split into letters with their marks (p̄), in order, once each."""
    out: list[str] = []
    for part in parts:
        for c in part.split() if " " in part else _clusters(part):
            if c not in out:
                out.append(c)
    return out


def _clusters(text: str) -> list[str]:
    out: list[str] = []
    for c in text:
        if out and unicodedata.combining(c) and not unicodedata.combining(out[-1][0]) and out[-1] != " ":
            out[-1] += c
        else:
            out.append(c)
    return out


def _range(a: int, b: int) -> str:
    return "".join(chr(c) for c in range(a, b + 1) if unicodedata.name(chr(c), ""))


SETS: dict[str, list[str]] = {
    "Superscripts": _chars("¹ ² ³ ⁴ ⁵ ⁶ ⁷ ⁸ ⁹ ⁰ ⁺ ⁻ ⁼ ⁽ ⁾ ᵃ ᵇ ᶜ ᵈ ᵉ ᶠ ᵍ ʰ ⁱ ʲ ᵏ ˡ ᵐ ⁿ ᵒ ᵖ ʳ ˢ ᵗ ᵘ ᵛ ʷ ˣ ʸ ᶻ "
                           "ᴬ ᴮ ᴰ ᴱ ᴳ ᴴ ᴵ ᴶ ᴷ ᴸ ᴹ ᴺ ᴼ ᴾ ᴿ ᵀ ᵁ ᵂ ₀ ₁ ₂ ₃ ₄ ₅ ₆ ₇ ₈ ₉"),
    "Transliteration": _chars("ʾ ʿ ḥ Ḥ ṭ Ṭ ṣ Ṣ š Š ś Ś ḇ Ḇ ḡ Ḡ ḏ Ḏ ḵ Ḵ p̄ P̄ ṯ Ṯ ḳ Ḳ ḫ Ḫ ẓ Ẓ ḍ Ḍ ġ Ġ ǧ Ǧ ž ż "
                              "ā Ā ē Ē ī Ī ō Ō ū Ū â Â ê Ê î Î ô Ô û Û ă Ă ĕ Ĕ ĭ ŏ Ŏ ŭ ə Ə ᵉ ᵃ ᵒ ʼ ʻ ʽ"),
    "Accented Latin": _chars("á à â ä ã å ā ă ą ç ć č ď é è ê ë ē ĕ ė ę ě ğ ģ í ì î ï ī ĭ į ı ķ ĺ ļ ľ ł ñ ń ņ ň "
                             "ó ò ô ö õ ø ō ŏ ő ŕ ř ś ş š ș ť ţ ț ú ù û ü ũ ū ŭ ů ű ų ý ÿ ź ż ž ß æ œ ð þ ŋ "
                             "Á À Â Ä Ã Å Ā Ç Č É È Ê Ë Ē Í Ì Î Ï Ī Ñ Ó Ò Ô Ö Õ Ø Ō Ř Š Ú Ù Û Ü Ū Ý Ž Æ Œ Ð Þ"),
    "Combining marks": _chars(" ".join("◌" + chr(c) for c in (0x0300, 0x0301, 0x0302, 0x0303, 0x0304, 0x0306, 0x0307,
                                                               0x0308, 0x030A, 0x030C, 0x0323, 0x0324, 0x0325, 0x0327,
                                                               0x0328, 0x032E, 0x0331, 0x0329, 0x032F, 0x0342, 0x0313, 0x0314))),
    "IPA": _chars(" ".join(_range(0x0250, 0x02AF)) + " θ β χ ː ˑ ˈ ˌ ʰ ʷ ʲ ˤ ˠ"),
    "Greek": _chars("α β γ δ ε ζ η θ ι κ λ μ ν ξ ο π ρ σ ς τ υ φ χ ψ ω Α Β Γ Δ Ε Ζ Η Θ Ι Κ Λ Μ Ν Ξ Ο Π Ρ Σ Τ Υ Φ Χ Ψ Ω "
                    "ἀ ἁ ἄ ἅ ἂ ἃ ἆ ἇ ά ὰ ᾶ ᾳ ᾴ ᾷ ἐ ἑ ἔ ἕ ἒ ἓ έ ὲ ἠ ἡ ἤ ἥ ἢ ἣ ἦ ἧ ή ὴ ῆ ῃ ῄ ῇ ᾐ ᾑ ᾖ ᾗ "
                    "ἰ ἱ ἴ ἵ ἲ ἳ ἶ ἷ ί ὶ ῖ ϊ ΐ ὀ ὁ ὄ ὅ ὂ ὃ ό ὸ ὐ ὑ ὔ ὕ ὒ ὓ ὖ ὗ ύ ὺ ῦ ϋ ΰ "
                    "ὠ ὡ ὤ ὥ ὢ ὣ ὦ ὧ ώ ὼ ῶ ῳ ῴ ῷ ᾠ ᾡ ᾦ ᾧ ῤ ῥ Ἀ Ἁ Ἐ Ἑ Ἠ Ἡ Ἰ Ἱ Ὀ Ὁ Ὑ Ὠ Ὡ Ῥ · ; ʹ ͵"),
    "Hebrew": _chars("א ב ג ד ה ו ז ח ט י כ ך ל מ ם נ ן ס ע פ ף צ ץ ק ר ש ת "
                     + " ".join("◌" + chr(c) for c in list(range(0x05B0, 0x05BE)) + [0x05BF, 0x05C1, 0x05C2, 0x05C4,
                                                                                    0x05C5, 0x05C7])
                     + " ־ ׀ ׃ ׆ ׳ ״"),
    "Hebrew accents": _chars(" ".join("◌" + chr(c) for c in range(0x0591, 0x05B0))),
    "Punctuation": _chars("— – ‐ ‘ ’ ‚ “ ” „ « » ‹ › † ‡ § ¶ · • … ′ ″ ‰ ° × ÷ ± ¬ ¦ ⸀ ⸁ ⸂ ⸃ ⸄ ⸅ ℵ ℶ"),
}


def as_typed(shown: str) -> str:
    """What a pane character inserts: a combining mark without the dotted circle it is shown on."""
    return shown.replace("◌", "")


def name(shown: str) -> str:
    """The character's Unicode name(s), for its tooltip."""
    return " + ".join(unicodedata.name(c, f"U+{ord(c):04X}").title() for c in as_typed(shown)) or shown


def search(query: str, limit: int = 300) -> list[str]:
    """Characters whose Unicode name has every word of the query ("dot below", "macron h",
    "superscript two"), from the sets first, then from the blocks scholarly books draw on."""
    words = query.upper().split()
    if not words:
        return []
    seen, out = set(), []
    pool = [c for chars in SETS.values() for c in chars]
    blocks = [(0x00A0, 0x024F), (0x0250, 0x02FF), (0x0300, 0x036F), (0x0370, 0x03FF), (0x0590, 0x05FF),
              (0x1D00, 0x1DBF), (0x1E00, 0x1EFF), (0x1F00, 0x1FFF), (0x2000, 0x209F), (0x2C60, 0x2C7F), (0x2E00, 0x2E4F)]
    for a, b in blocks:
        for code in range(a, b + 1):
            c = chr(code)
            if unicodedata.name(c, ""):
                pool.append("◌" + c if unicodedata.combining(c) else c)
    for shown in pool:
        if shown in seen:
            continue
        seen.add(shown)
        if all(w in name(shown).upper() for w in words):
            out.append(shown)
            if len(out) >= limit:
                break
    return out


def _path():
    return user_data_dir() / "characters.json"


def recent() -> list[str]:
    """The characters inserted last, most recent first."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return [c for c in data.get("recent", []) if isinstance(c, str)][:RECENT_MAX]
    except (OSError, ValueError, AttributeError):
        return []


def remember(shown: str) -> None:
    chars = [shown] + [c for c in recent() if c != shown]
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps({"recent": chars[:RECENT_MAX]}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
