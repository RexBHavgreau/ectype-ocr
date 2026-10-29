from __future__ import annotations

import json
import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont, features

from ectype import scripts
from ectype.ocr import Char, Line, OcrPage, Word, ocr_pages


def test_script_helpers():
    assert scripts.clean("‎דבר‏") == "דבר"
    assert scripts.is_rtl("דבר") and not scripts.is_rtl("λόγος") and not scripts.is_rtl("word")
    assert scripts.in_script("ἐποίησεν", "Greek") and not scripts.in_script("X0yog", "Greek")
    assert scripts.script_of("grc") == "Greek" and scripts.script_of("eng") == "Latin"
    assert scripts.suggest({"Hebrew": 6, "Greek": 4, "Arabic": 1}, ["eng"]) == ["heb", "grc"]
    assert scripts.suggest({"Cyrillic": 2}, ["eng"], [{"Cyrillic": 1}, {"Cyrillic": 1}]) == [], "scattered: a guess"
    assert scripts.suggest({"Hebrew": 6}, ["eng", "heb"]) == []


def _word(text, x0, conf=10.0):
    return Word(text, [x0, 100, x0 + 80, 140], conf, [Char(c, [x0 + 10 * i, 100, x0 + 10 * i + 9, 140], conf)
                                                  for i, c in enumerate(text)])


def test_check_words_rereads_and_counts():
    page = OcrPage("tesseract", "eng+heb", [1000, 300], 300.0,
                   [Line([0, 100, 600, 140], [0, 0], 30.0, None, None,
                         [_word("Hebrew", 0, 96), _word("727", 100), _word("X0yog", 200), _word("tauta", 300)])])
    seen = {"727": "Hebrew", "X0yog": "Greek", "tauta": "Greek"}
    crops = {}

    def detect(crop):
        word = next(w for w in page.words() if crops.get(id(w)) is None and w.conf < 60)
        crops[id(word)] = True
        return seen[word.text], 5.0

    def reread(crop, lang):
        assert lang == "heb"
        return [Word("דבר", [20, 20, 70, 60], 0.0, [Char("ד", [50, 20, 70, 60], 0), Char("ב", [35, 20, 49, 60], 0),
                                                       Char("ר", [20, 20, 34, 60], 0)])]

    gray = np.full((300, 1000), 255, np.uint8)
    unread = scripts.check_words(gray, page, ["eng", "heb"], reread, detect_fn=detect)
    words = [w.text for w in page.words()]
    assert words == ["Hebrew", "דבר", "X0yog", "tauta"], "the Hebrew word read again with Hebrew alone"
    assert unread == {"Greek": 2}, "Greek words counted: no chosen language covers them"
    assert page.lines[0].words[1].chars[0].box[0] > 100, "boxes moved back to page coordinates"


@pytest.mark.skipif(not features.check("raqm"), reason="right-to-left text needs Pillow's raqm layout")
def test_detects_greek_and_hebrew_on_an_english_page(tmp_path):
    from ectype.synth import find_font

    font = ImageFont.truetype(str(find_font("FreeSerif")), 46)
    lines = ["The Greek word λόγος (logos) in John 1:1 and the",
             "Hebrew word דבר (davar) both mean “word.” In Genesis",
             "1:1, בראשית ברא אלהים is rendered by the Septuagint",
             "as ἐν ἀρχῇ ἐποίησεν ὁ θεός. The same root דבר also",
             "means a thing or matter, as in the phrase τὰ ῥήματα",
             "ταῦτα, “these words,” which follows the Hebrew.",
             "Pointed: בְּרֵאשִׁית בָּרָא אֱלֹהִים, “in the beginning God created.”"]
    img = Image.new("L", (2000, 160 + 70 * len(lines)), 255)
    draw = ImageDraw.Draw(img)
    for i, t in enumerate(lines):
        draw.text((120, 120 + 70 * i), t, font=font, fill=0, anchor="ls", direction="ltr")
    (tmp_path / "pages").mkdir()
    img.save(tmp_path / "pages" / "p001.png", dpi=(300, 300))
    s = ocr_pages(tmp_path / "pages", tmp_path / "ocr", lang="eng")
    assert s["unread_scripts"].get("Hebrew", 0) >= 2, s["unread_scripts"]
    assert s["unread_scripts"].get("Greek", 0) >= 2, s["unread_scripts"]
    assert s["suggest_languages"][:2] in (["heb", "grc"], ["grc", "heb"])


def test_rtl_characters_align_in_visual_order():
    from ectype.segment import _visual

    chars = [Char("ד", [50, 0, 60, 10], 90), Char("ב", [30, 0, 40, 10], 90), Char("ר", [10, 0, 20, 10], 90)]
    assert [c.text for c in _visual(chars)] == ["ר", "ב", "ד"]
    latin = [Char("a", [10, 0, 20, 10], 90), Char("b", [30, 0, 40, 10], 90)]
    assert _visual(latin) == latin


def test_corrections_carry_over_to_a_new_reading(tmp_path):
    from ectype.review import carry_corrections

    (tmp_path / "ocr").mkdir()
    (tmp_path / "corrections").mkdir()
    old = OcrPage("tesseract", "eng", [1000, 300], 300.0,
                  [Line([0, 100, 600, 140], [0, 0], 30.0, None, None, [_word("Tbid.", 0, 50), _word("230", 100, 90)])])
    (tmp_path / "ocr" / "p001.ocr.original.json").write_text(json.dumps(old.to_json()))
    (tmp_path / "corrections" / "p001.json").write_text(json.dumps([{"line": 0, "word": 0, "old": "Tbid.", "new": "Ibid."}]))
    # The new reading has an extra word in front, so the corrected word moved.
    new = OcrPage("tesseract", "eng_book", [1000, 300], 300.0,
                  [Line([0, 100, 600, 140], [0, 0], 30.0, None, None,
                        [_word("2", -90, 80), _word("Tbid.", 2, 60), _word("230", 100, 95)])])
    assert carry_corrections(tmp_path, "p001", new) == 1
    assert [w.text for w in new.words()] == ["2", "Ibid.", "230"]
    log = json.loads((tmp_path / "corrections" / "p001.json").read_text())
    assert (log[0]["line"], log[0]["word"]) == (0, 1), "the log points at the new word"
    original = json.loads((tmp_path / "ocr" / "p001.ocr.original.json").read_text())
    assert original["lang"] == "eng_book", "the new reading is the page's original now"


def test_fitting_tesseract_on_lines(tmp_path, monkeypatch):
    """Continue English on a few lines (a short run): the fitted model reads them."""
    from ectype import train

    if train.available():
        pytest.skip(train.available())
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    try:
        best = train.best_model("eng")
    except train.TrainingError as exc:
        pytest.skip(str(exc))
    from ectype.synth import find_font

    font = ImageFont.truetype(str(find_font("FreeSerif")), 40)
    words = "inspiration scripture doctrine original copies letter president teacher errors careless".split()
    rng = np.random.default_rng(0)
    pairs = []
    for i in range(14):
        text = " ".join(rng.choice(words, 5))
        im = Image.new("L", (int(font.getlength(text)) + 40, 70), 255)
        ImageDraw.Draw(im).text((20, 50), text, font=font, fill=0, anchor="ls")
        im.save(tmp_path / f"l{i:03d}.png", dpi=(300, 300))
        pairs.append((tmp_path / f"l{i:03d}.png", text))
    fit = train.fit_tesseract(best, "eng", pairs, tmp_path / "work", iterations=40)
    assert fit.path.is_file() and fit.float_path.is_file() and fit.items == 14
    assert fit.path.stat().st_size < fit.float_path.stat().st_size, "the integer model, to read with, is smaller"


def test_hebrew_words_saved_for_training(tmp_path):
    from ectype.review import save_training_word

    (tmp_path / "pages").mkdir()
    Image.new("L", (1000, 300), 255).save(tmp_path / "pages" / "p001.png")
    page = OcrPage("tesseract", "eng+heb", [1000, 300], 300.0,
                   [Line([0, 100, 600, 140], [0, 0], 30.0, None, None, [_word("the", 0, 90), _word("x", 100)])])
    assert save_training_word(tmp_path, "p001", page, 0, 0) is None, "a Latin word trains the main model, in its line"
    page.lines[0].words[1].text = "וּרְאֵה"
    png = save_training_word(tmp_path, "p001", page, 0, 1)
    assert png.parent.name == "words" and png.with_suffix(".gt.txt").read_text(encoding="utf-8") == "וּרְאֵה\n"


@pytest.mark.skipif(not features.check("raqm"), reason="right-to-left text needs Pillow's raqm layout")
def test_fitting_hebrew_adds_the_points_it_lacks(tmp_path, monkeypatch):
    """Continue Hebrew on pointed words, adding the vowel points the stock model lacks."""
    from ectype import train

    if train.available():
        pytest.skip(train.available())
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    try:
        best = train.best_model("heb")
    except train.TrainingError as exc:
        pytest.skip(str(exc))
    from ectype.synth import find_font

    font = ImageFont.truetype(str(find_font("FreeSerif")), 44)
    words = "בְּרֵאשִׁית בָּרָא אֱלֹהִים אֵת הַשָּׁמַיִם וְאֵת הָאָרֶץ וּבָרְכוּ וּמַלְּטִי וּרְאֵה דָּבָר".split()
    rng = np.random.default_rng(1)
    pairs = []
    for i in range(60):
        text = " ".join(rng.choice(words, 1 + i % 2))
        im = Image.new("L", (int(font.getlength(text)) + 40, 80), 255)
        ImageDraw.Draw(im).text((20, 58), text, font=font, fill=0, anchor="ls", direction="rtl", language="he")
        im.save(tmp_path / f"w{i:03d}.png", dpi=(300, 300))
        pairs.append((tmp_path / f"w{i:03d}.png", text))
    fit = train.fit_tesseract(best, "heb", pairs[:48], tmp_path / "work", iterations=800)
    assert set("ֱֵֹׁ") <= set(fit.added), "the points the stock model lacks are added"

    class Item:
        def __init__(self, png, text):
            self.png, self.text, self.book = png, text, "test"

    (tmp_path / "eval").mkdir()
    shutil.copyfile(best, tmp_path / "eval" / "stock.traineddata")
    shutil.copyfile(fit.path, tmp_path / "eval" / "trained.traineddata")
    held = [Item(p, t) for p, t in pairs[48:]]
    before = train.errors_tesseract(tmp_path / "eval", "stock", held)["test"]
    after = train.errors_tesseract(tmp_path / "eval", "trained", held)["test"]
    assert after[0] < before[0], (before, after)
