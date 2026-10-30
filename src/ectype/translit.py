"""Transliteration text for training: English prose with transliterated words in it, so the
Latin-script model learns the letters scholarly books use beyond English's (`synthtrain.py`,
"transliteration").

Tesseract's English model knows one accented letter (é): it reads ḥ as h, ā as a, š as s,
drops ʾ and ʿ, and nothing shows it. Lines set from this text teach it:
- Hebrew in SBL academic style, made from the Hebrew Bible (`hebrew`): ʾ ʿ ḥ ṭ ṣ š ś q,
  the spirants ḇ ḡ ḏ ḵ p̄ ṯ, ā ē ō, ê î ô û â, ă ĕ ŏ, ə; some in the older style of
  Gesenius' grammar (ḳ, qᵉṭal, ᵃ ᵒ);
- Arabic, Akkadian, Ugaritic, Aramaic and Syriac words (ḫ ẓ ḍ ġ ǧ ṯ ḏ, â î û);
- IPA transcriptions (ə ʃ ʒ ŋ ɛ ɔ ʔ ʕ ħ θ ð β χ ɣ ː ˈ ...);
- European words with accents (ü ö ä ß é è ê ç ñ å ø č ř ő ā ō ...);
- superscripts: notes' numbers after words (word¹, word¹²) and verses' parts in references
  (Isa. 1, 3ᵃ), which the stock model reads as "!", "?" or "%".

The text is generated (the same each time): a passage is a sentence of common English words
of scholarly prose with these words among them, sometimes in brackets, with a reference or
a page number now and then.
"""

from __future__ import annotations

import random
import re
import unicodedata

# -- Hebrew, SBL academic style -------------------------------------------------

SHEVA, HATAF_SEGOL, HATAF_PATAH, HATAF_QAMATS = "ְ", "ֱ", "ֲ", "ֳ"
HIRIQ, TSERE, SEGOL, PATAH, QAMATS = "ִ", "ֵ", "ֶ", "ַ", "ָ"
HOLAM, HOLAM_VAV, QUBUTS, DAGESH = "ֹ", "ֺ", "ֻ", "ּ"
SHIN_DOT, SIN_DOT, QAMATS_QATAN, MAQAF = "ׁ", "ׂ", "ׇ", "־"
MACRON = "̄"  # combining: p̄ has no letter of its own

CONSONANTS = {"א": "ʾ", "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "w", "ז": "z", "ח": "ḥ", "ט": "ṭ", "י": "y",
              "כ": "k", "ך": "k", "ל": "l", "מ": "m", "ם": "m", "נ": "n", "ן": "n", "ס": "s", "ע": "ʿ", "פ": "p",
              "ף": "p", "צ": "ṣ", "ץ": "ṣ", "ק": "q", "ר": "r", "ש": "š", "ת": "t"}
SPIRANTS = {"b": "ḇ", "g": "ḡ", "d": "ḏ", "k": "ḵ", "p": "p" + MACRON, "t": "ṯ"}
VOWELS = {SHEVA: "ə", HATAF_SEGOL: "ĕ", HATAF_PATAH: "ă", HATAF_QAMATS: "ŏ", HIRIQ: "i", TSERE: "ē", SEGOL: "e",
          PATAH: "a", QAMATS: "ā", HOLAM: "ō", HOLAM_VAV: "ō", QUBUTS: "u", QAMATS_QATAN: "o"}
LONG = set("āēōîêôûâ")
# Gesenius' grammar (Kautzsch-Cowley) and books of its time: ḳ for qoph, raised vowels for
# vocal shewa and the hatephs.
OLDER = {"q": "ḳ", "ə": "ᵉ", "ă": "ᵃ", "ĕ": "ᵉ", "ŏ": "ᵒ"}


def _units(word: str) -> list[tuple[str, set[str]]]:
    """A Hebrew word as its letters, each with its marks."""
    out: list[tuple[str, set[str]]] = []
    for c in unicodedata.normalize("NFD", word):
        if c in CONSONANTS:
            out.append((c, set()))
        elif out and unicodedata.combining(c):
            out[-1][1].add(c)
    return out


def _vowel(marks: set[str]) -> str | None:
    return next((m for m in marks if m in VOWELS), None)


def hebrew_word(word: str) -> str:
    """One pointed Hebrew word in SBL academic transliteration (bərēʾšît, ʾĕlōhîm, rûaḥ).
    Rules of thumb, not a grammar: vocal shewa at the start of a word, after another shewa,
    under a doubled letter or after a long vowel; dagesh after a vowel doubles the letter."""
    units = _units(word)
    if [c for c, _ in units] == list("יהוה"):
        return "YHWH"
    out: list[str] = []  # the transliteration, a letter or vowel an item
    prev_vowel: str | None = None  # what the last letter was pointed with ("" for none)
    last = len(units) - 1
    for i, (c, marks) in enumerate(units):
        v, dagesh = _vowel(marks), DAGESH in marks
        if c == "ו" and i and prev_vowel == "" and (dagesh and v is None or v == HOLAM and marks <= {HOLAM}):
            out.append("û" if dagesh else "ô")  # shureq, holam male after an unpointed letter
            prev_vowel = "û"
            continue
        if c == "ו" and i == 0 and dagesh and v is None:
            out.append("û")  # the conjunction before a labial or shewa
            prev_vowel = "û"
            continue
        if c == "י" and not marks and out and out[-1] in ("i", "ē", "e") and prev_vowel in (HIRIQ, TSERE, SEGOL):
            out[-1] = "î" if out[-1] == "i" else "ê"  # yod as a vowel letter
            prev_vowel = "î"
            continue
        if c == "ה" and i == last and not marks and out and out[-1] in ("ā", "e", "ē", "ō"):
            out[-1] = {"ā": "â", "e": "ê", "ē": "ê", "ō": "ô"}[out[-1]]  # final he as a vowel letter
            break
        cons = CONSONANTS[c]
        if c == "ש":
            cons = "ś" if SIN_DOT in marks else "š"
        doubled = dagesh and bool(prev_vowel) and prev_vowel not in (SHEVA, "") and c not in "הא"
        if cons in SPIRANTS and not dagesh:
            cons = SPIRANTS[cons]
        sound = VOWELS.get(v, "") if v else ""
        if v == SHEVA:
            vocal = i < last and (i == 0 or prev_vowel == SHEVA or doubled or out and out[-1] in LONG)
            sound = "ə" if vocal else ""
        if i == last and v == PATAH and c in "חע" + ("ה" if dagesh else "") and prev_vowel:
            out += ["a", cons]  # furtive patah: rûaḥ
            break
        out += [cons * 2 if doubled else cons, sound] if sound else [cons * 2 if doubled else cons]
        prev_vowel = v or ""
    return "".join(out)


def hebrew(text: str, older: bool = False) -> str:
    """Pointed Hebrew words (a verse, or part of one) transliterated; maqaf as a hyphen."""
    words = []
    for word in text.replace("׃", "").split():
        parts = [hebrew_word(p) for p in word.split(MAQAF) if p]
        words.append("-".join(p for p in parts if p))
    out = " ".join(w for w in words if w)
    if older:
        out = "".join(OLDER.get(c, c) for c in out)
    return unicodedata.normalize("NFC", out)


# -- other languages ----------------------------------------------------------

ARABIC = """ʿilm ḥadīṯ qurʾān šarīʿa tafsīr iǧtihād fiqh ṣalāh zakāh ḥaǧǧ ṣawm ʿaqīda kalām ʿālim ʿulamāʾ
imām ḫalīfa ḫilāfa sulṭān wazīr qāḍī muftī fatwā maḏhab sunna ḍamīr ẓāhir bāṭin taʾwīl ʾisnād matn ḥikma
falsafa ʿaql nafs rūḥ qalb ḏikr ṣūfī ṭarīqa šayḫ murīd ġazal qaṣīda ʿarūḍ naḥw ṣarf balāġa luġa ʿarabiyya
ʾAllāh raḥmān raḥīm basmala šahāda ʾumma ǧihād hiǧra masǧid ǧāmiʿ minbar miḥrāb ʾaḏān muʾaḏḏin ḥarām ḥalāl
kitāb al-kitāb al-qurʾān bayt al-ḥikma Ibn Rušd al-Ġazālī al-Ṭabarī al-Buḫārī Ibn Ḫaldūn ʿAbbāsī Umayyad
maktūb muḥammad ʾaḥmad ḥāfiẓ ẓulm ḍaʿīf ṣaḥīḥ ḥasan mursal ʾisrāʾīliyyāt taḥrīf naṣṣ ʾasbāb al-nuzūl
khalīfa dhikr thawra ghazal shaykh ijtihād jāmiʿ""".split()
AKKADIAN = """šarrum šarru bītum bītu ilum ilu awīlum awīlu ṣābum ṭuppum ṭuppu ḫurāṣum mārum mārtum šamšum šamaš
Enūma eliš Gilgameš Ḫammurāpi Aššur Ištar Marduk Tiāmat Apsû Ea Enlil Nabû Sîn Adad šulmum ṣalmum ṣēnum
qaqqadum šumum ḫīṭum ḫaṭṭum qātum šēpum īnum libbum napištum ṣeḫrum rabûm dannum ālum mātum ṣērum ḫarrānum
iqbi išpur iddin ušapriš lišānum ṭēmum awātum šīmtum ilū ilānū šarrū bītū mâtum kīma ana ina ištu ul lā""".split()
UGARITIC = """bʿl ʾil ʾilm ʿnt ʾaṯrt ṯr ḏ ġzr ẓl ḫtk ṯpṭ yṯb ḏbḥ ʿbd mlk ḫrš ṣpn ʾarṣ šmm ym mt rpʾum
Kirta ʾAqhat Baʿlu ʿAnatu ʾIlu ʾAṯiratu Yammu Môtu Šapšu Ṯôru""".split()
ARAMAIC = """ʾĕlāh malkāʾ ḥezwāʾ ʿiddān ṭəʿēm šəlāṭ ṣəlēm Bēlšaʾṣṣar mərēʾ ʾabbāʾ ṭalyəṯāʾ qûmî ʾeppaṯaḥ
ʾĕlōhî šəḇaqtanî mārānāʾ ṯāʾ rabbûlî Pəšîṭtāʾ mēmrāʾ šəḵîntāʾ ʿālmāʾ ḥayyāʾ qūdšāʾ ʾAp̄rēm Ṭaṭyānōs""".split()
EUROPEAN = """über Übersetzung Erklärung Einführung Grundzüge Wörterbuch Wörter Bücher Mönch schön Größe groß Straße
Ähnlichkeit Ärzte Öffentlichkeit Schöpfung Geschichte Königs Psalmenstudien Jüdische Göttingen Tübingen München
Kritik Altertümer Untersuchungen Völker geläufig Gemeinde müssen können zurück Gefäß Fuß
être fenêtre où à déjà théologie études critique élève naïve Noël garçon français côté première société
Genèse Exode Lévitique Nombres Deutéronome Ésaïe Jérémie Ézéchiel Élie Moïse Hébreu hébraïque littérature
réflexions recherches méthode deuxième troisième siècle Ève coïncidence aïeul canoë
rōmānus lūx mēnsa fīlius ēgregius Rōma cōnsul sānctus prīmus quī quō hīc nōn dēus
señor años niño corazón María Sevilla canción España mañana pequeño Teología
città perché più Libreria Università così
København Århus søster Ørsted Æneas Skrifter Gamla testamentet Österreich Göteborg Tromsø
Dvořák Čapek přeložil česky čtení Brno žena Kraków Łódź źródło Pöschl Gödöllő Erdős tűz
Türkçe İstanbul ğ şehir Ağrı İzmir dağ çocuk ışık
São Paulo João Coimbra lições ação românica Brâncuși știință Țara Timișoara
Þórr Íslendinga saga goði Hrafnkell Snorri Sturluson Völuspá Óðinn
Noël Kuenen België Leiden Groningen""".split()
EUROPEAN = [w for w in EUROPEAN if len(w) > 1]

IPA_CONS = list("pbtdkqʔmnŋɲɳɴrɾɽʀɸβfvθðszʃʒʂʐçʝxɣχʁħʕhɦɬɮʋɹɻjɰlɭʎʟwʍɥʡʜʢɕʑ")
IPA_VOWELS = list("iyɨʉɯuɪʏʊeøɘɵɤoəɛœɜɞʌɔæɐaɶɑɒ")
IPA_CONS_MODS = ["ʰ", "ʷ", "ʲ", "ˤ", "ˠ", "ⁿ", "ˡ"]
# Letters with a diacritic, only as transcriptions have them (each is a character of its own
# to the model, so not every pairing): syllabic, voiceless and dental consonants; nasal and
# non-syllabic vowels.
IPA_MARKED_CONS = [c + "\u0329" for c in "nmlr"] + [c + "\u0325" for c in "nmlr"] + [c + "\u032a" for c in "tdn"]
IPA_MARKED_VOWELS = [v + "\u0303" for v in "aeiouɛɔəœɑ"] + [v + "\u032f" for v in "iueo"]


def ipa_word(rng: random.Random) -> str:
    """A made-up word in IPA: syllables of IPA consonants and vowels, with stress, length and
    some diacritics."""
    out = []
    for k in range(rng.randint(1, 3)):
        if k and rng.random() < 0.3:
            out.append(rng.choice("ˈˌ"))
        elif k == 0 and rng.random() < 0.4:
            out.append("ˈ")
        c = rng.choice(IPA_MARKED_CONS) if rng.random() < 0.06 else rng.choice(IPA_CONS)
        if rng.random() < 0.12:
            c += rng.choice(IPA_CONS_MODS)
        v = rng.choice(IPA_MARKED_VOWELS) if rng.random() < 0.1 else rng.choice(IPA_VOWELS)
        if rng.random() < 0.2:
            v += rng.choice("ːˑ")
        out.append(c + v)
        if rng.random() < 0.35:
            out.append(rng.choice(IPA_CONS))
    return unicodedata.normalize("NFC", "".join(out))


def ipa(rng: random.Random) -> str:
    words = " ".join(ipa_word(rng) for _ in range(rng.randint(1, 3)))
    return "/" + words + "/" if rng.random() < 0.6 else "[" + words + "]"


# -- the English around them ---------------------------------------------------

ENGLISH = """the of and to in a is that for it as was with be by on not he this are or his from at which but
have an they were their one all we can her has there been if more when will would who so no its also than
other into only these may such some them our then had first could any most two new between should both those
however where after each must under over many even because same through while much before well very about
text word words form forms verb noun root meaning reading readings translation translated sense passage
passages verse verses chapter book books scholars scholar author authors commentary commentaries grammar
grammatical usage term terms phrase phrases name names letter letters sound sounds vowel vowels consonant
language languages Hebrew Greek Arabic Akkadian Ugaritic Aramaic Syriac Semitic Latin English German French
derived compare cognate related usually often always never found occurs occur occurring attested common rare
later earlier ancient older original early late period evidence context sentence clause subject object
see note notes above below following preceding present perfect imperfect participle plural singular
masculine feminine construct absolute stem pronounced spelled written means denotes here there thus
probably perhaps clearly certainly indeed sometimes elsewhere already still yet again once similar same""".split()
REFERENCES = ["Gen", "Exod", "Lev", "Num", "Deut", "Josh", "Judg", "1 Sam", "2 Kgs", "Isa", "Jer", "Ezek", "Hos",
              "Ps", "Prov", "Job", "Dan", "Neh", "Matt", "Rom"]
STYLES = (("hebrew", 0.35), ("semitic", 0.2), ("ipa", 0.15), ("european", 0.3))


def _capital(word: str) -> str:
    """The word with its first cased letter a capital (ʿĒlî, Ḥanukkah)."""
    for i, c in enumerate(word):
        if c.isalpha() and c.upper() != c:
            return word[:i] + c.upper() + word[i + 1:]
        if c.isalpha() and c.isupper():
            return word
    return word


def _foreign(style: str, rng: random.Random, verses: list[str]) -> str:
    if style == "hebrew" and verses:
        verse = verses[rng.randrange(len(verses))].split()
        n = rng.randint(1, 3)
        start = rng.randrange(max(1, len(verse) - n + 1))
        text = hebrew(" ".join(verse[start:start + n]), older=rng.random() < 0.15)
        return _capital(text) if rng.random() < 0.1 else text
    if style in ("semitic", "hebrew"):
        group = rng.choices((ARABIC, AKKADIAN, UGARITIC, ARAMAIC), (0.45, 0.3, 0.1, 0.15))[0]
        return " ".join(rng.choice(group) for _ in range(rng.randint(1, 2)))
    if style == "ipa":
        return ipa(rng)
    word = " ".join(rng.choice(EUROPEAN) for _ in range(rng.randint(1, 2)))
    return word.upper() if rng.random() < 0.04 else _capital(word) if rng.random() < 0.15 else word


SUPER_FIGURES = "⁰¹²³⁴⁵⁶⁷⁸⁹"
VERSE_PARTS = "ᵃᵇᶜᵈ"  # Isa. 1, 3ᵃ: the first part of the verse


def _note(n: int) -> str:
    """A footnote number as superscript figures: 12 -> ¹²."""
    return "".join(SUPER_FIGURES[int(d)] for d in str(n))


def _reference(rng: random.Random) -> str:
    """A biblical reference, in the styles books use (Gen 12:3, Isa. 1, 3ᵃ, Ps. 7, 16), now
    and then with the verse's part as a raised letter."""
    book, chapter, verse = rng.choice(REFERENCES), rng.randint(1, 40), rng.randint(1, 30)
    part = rng.choice(VERSE_PARTS[:2]) if rng.random() < 0.3 else ""
    if rng.random() < 0.5:
        return f"({book} {chapter}:{verse}{part})"
    return f"{book}. {chapter}, {verse}{part}"


def _english(rng: random.Random) -> str:
    words = [rng.choice(ENGLISH) for _ in range(rng.randint(1, 4))]
    if rng.random() < 0.12:
        words[-1] += _note(rng.choice((1, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15)))  # a note's number
    if rng.random() < 0.15:
        words.append(_reference(rng))
    elif rng.random() < 0.05:
        words.append(rng.choice(("cf.", "e.g.", "i.e.", f"p. {rng.randint(2, 480)}", f"n. {rng.randint(1, 90)}")))
    return " ".join(words)


def passages(verses: list[str], count: int = 6000, seed: int = 0) -> list[str]:
    """Sentences of English with transliterated words among them (see the module docstring);
    `verses` are pointed Hebrew verses (the Hebrew Bible) to transliterate."""
    rng = random.Random(seed)
    names, weights = zip(*STYLES)
    out = []
    for _ in range(count):
        parts: list[str] = []
        while sum(len(p.split()) for p in parts) < rng.randint(14, 22):
            parts.append(_english(rng))
            foreign = _foreign(rng.choices(names, weights)[0], rng, verses)
            r = rng.random()
            parts.append(f"({foreign})" if r < 0.15 else f"“{foreign}”" if r < 0.22 else foreign + ("," if r > 0.9 else ""))
        text = re.sub(r"\s+", " ", " ".join(parts)).strip().rstrip(",")
        out.append(_capital(text[0]) + text[1:] + ".")
    return out
