"""The training pool, typeface families, model choice, known texts, generated lines and
matched scans (pool.py, families.py, models.py, training.py, texts.py, synthtrain.py, align.py)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from ectype import families, models, pool, texts, training


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def _font(name="FreeSerif"):
    from ectype.synth import find_font

    return str(find_font(name))


def _line(text, font, size=40):
    f = ImageFont.truetype(font, size)
    im = Image.new("L", (int(f.getlength(text)) + 30, int(size * 1.8)), 255)
    ImageDraw.Draw(im).text((15, int(size * 1.3)), text, font=f, fill=0, anchor="ls")
    return im


def test_pool_gathers_every_projects_corrections(data, tmp_path):
    ws = tmp_path / "book.ectype"
    (ws / "training" / "words").mkdir(parents=True)
    for i in range(6):
        _line(f"line {i} of the book", _font()).save(ws / "training" / f"p001-l{i:03d}.png")
        (ws / "training" / f"p001-l{i:03d}.gt.txt").write_text(f"line {i} of the book\n", encoding="utf-8")
    Image.new("L", (60, 30), 255).save(ws / "training" / "words" / "p001-l000-w002.png")
    (ws / "training" / "words" / "p001-l000-w002.gt.txt").write_text("λόγος\n", encoding="utf-8")
    gained = pool.sync_workspace(ws, "Young", ["eng", "grc"])
    assert gained == {"eng": 6, "grc": 1}, "lines to the main language, the Greek word to the Greek pool"
    assert pool.sync_workspace(ws, "Young", ["eng", "grc"]) == {"eng": 0, "grc": 0}, "nothing twice"
    # Correcting a line again replaces its item.
    (ws / "training" / "p001-l000.gt.txt").write_text("line 0 of the Book\n", encoding="utf-8")
    pool.sync_workspace(ws, "Young", ["eng", "grc"])
    texts_now = sorted(i.text for i in pool.items("eng"))
    assert "line 0 of the Book" in texts_now and "line 0 of the book" not in texts_now and len(texts_now) == 6
    splits = {i.id: i.split for i in pool.items("eng")}
    assert set(splits.values()) <= {"train", "test"}
    assert {i.id: i.split for i in pool.items("eng")} == splits, "an item's split never changes"


def test_fingerprints_tell_typefaces_apart():
    words = "inspiration scripture doctrine original copies letter president teacher errors careless".split()
    lines = [" ".join(words[i:i + 4]) for i in range(0, 7)]
    serif = families.measure([np.asarray(_line(t, _font("FreeSerif"), 44)) for t in lines])
    serif2 = families.measure([np.asarray(_line(t, _font("FreeSerif"), 36)) for t in lines])
    sans = families.measure([np.asarray(_line(t, _font("DejaVu Sans"), 44)) for t in lines])
    assert families.distance(serif, serif2) < families.distance(serif, sans), "size matters less than typeface"
    groups = families.cluster({"a": serif, "b": serif2, "c": sans})
    assert groups["a"] == groups["b"] != groups["c"]
    assert families.nearest(serif2, {"f1": serif, "f2": sans}) == "f1"


def test_judging_a_new_model():
    ok, why = training.judge({"A": (10, 1000), "B": (10, 1000)}, {"A": (5, 1000), "B": (9, 1000)})
    assert ok, why
    ok, why = training.judge({"A": (10, 1000), "B": (10, 1000)}, {"A": (1, 1000), "B": (18, 1000)})
    assert not ok and "B" in why, "fewer errors overall, but one book clearly worse"
    ok, _ = training.judge({"A": (10, 1000)}, {"A": (10, 1000)})
    assert not ok, "no better"


def test_sample_keeps_every_book(data):
    items = [pool.Item(f"{b}{i}", "eng", b, "line", "correction", "train", None, "x")
             for b, n in (("big", 900), ("small", 30)) for i in range(n)]
    items += [pool.Item(f"c{i}", "eng", "big", "line", "confident", "train", None, "x") for i in range(500)]
    s = training._sample(items, 300)
    books = [i.book for i in s if i.source != "confident"]
    assert books.count("small") == 30, "a small book is kept whole"
    assert len(s) <= 300 and sum(i.source == "confident" for i in s) <= 100


def test_resolving_the_model_for_a_book(data):
    from ectype.resources import user_tessdata_dir

    assert models.resolve("grc").which == "stock"
    user_tessdata_dir().mkdir(parents=True)
    for name in ("grc_ectype", "grc_ectype_f1"):
        (user_tessdata_dir() / f"{name}.traineddata").write_bytes(b"x")
    serif = [0.15, 1.8, 0.39, 0.84, 0.0]
    models.save_registry({"grc": {"engine": "tesseract", "generation": 2, "general": {"items": 10},
                                  "families": {"f1": {"centroid": serif, "books": ["Teubner"]}}}})
    assert models.resolve("grc").model == "grc_ectype", "trained: the general model"
    assert models.resolve("grc", {"Greek": serif}).model == "grc_ectype_f1", "the family the type is like"
    assert models.resolve("grc", {"Greek": [0.3, 1.0, 0.6, 1.2, 10]}).which == "general", "unlike every family"
    chosen = {"grc": {"which": "general", "generation": 2}}
    assert models.resolve("grc", {"Greek": serif}, chosen).which == "general", "best on its corrections wins"
    chosen = {"grc": {"which": "general", "generation": 1}}
    assert models.resolve("grc", {"Greek": serif}, chosen).which == "f1", "a choice from before training again lapses"
    from ectype import languages

    assert "grc_ectype" not in languages.installed() and "grc_ectype_f1" not in languages.installed()
    models.remove("grc")
    assert models.resolve("grc").which == "stock"


def test_known_texts_without_accents():
    xml = """<osis xmlns="http://www.bibletechnologies.net/2003/OSIS/namespace"><osisText><div>
      <verse osisID="Gen.1.1"><w>בְּ/רֵאשִׁ֖ית</w><w>בָּרָ֣א</w><w>אֱלֹהִ֑ים</w>
      <w>הָ/אָֽרֶץ</w><seg type="x-sof-pasuq">׃</seg></verse>
      <verse osisID="Gen.1.2"><w>עַל</w><seg type="x-maqqef">־</seg><w>פְּנֵ֥י</w><w>תְה֑וֹם</w></verse>
      </div></osisText></osis>"""
    verses = [texts.without_accents(v) for v in texts._hebrew_book(xml)]
    assert verses == ["בְּרֵאשִׁית בָּרָא אֱלֹהִים הָאָרֶץ׃", "עַל־פְּנֵי תְהוֹם"]
    assert texts._greek_book("John 1:1\tἘν ἀρχῇ ⸀ἦν ὁ λόγος,\n") == ["Ἐν ἀρχῇ ἦν ὁ λόγος,"]
    assert texts.base_letters("וּבָרְכוּ") == "וברכו" and texts.base_letters("Λόγος,") == "λογοσ"


def test_matching_ocr_lines_to_the_text():
    from ectype.align import Index, match_line

    index = Index(["Ἐν ἀρχῇ ἦν ὁ λόγος, καὶ ὁ λόγος ἦν πρὸς τὸν θεόν, καὶ θεὸς ἦν ὁ λόγος.",
                   "οὗτος ἦν ἐν ἀρχῇ πρὸς τὸν θεόν."])
    # An OCR line with a verse number in front and a misread letter.
    found = match_line(index, ["2", "οὗτος", "ἦν", "ἐν", "ἀρχῆ", "πρὸς", "τὸν", "θεόν."])
    first, last, true, similarity = found
    assert (first, last) == (1, 7) and true[0] == "οὗτος" and true[-1] == "θεόν." and similarity > 0.9
    assert match_line(index, ["quite", "other", "words", "here"]) is None


def test_lines_set_in_fonts(data, tmp_path, monkeypatch):
    from ectype import synthtrain
    from ectype.synth import find_font

    fonts = tmp_path / "fonts"
    fonts.mkdir()
    import shutil

    for name in ("FreeSerif", "DejaVu Sans", "Liberation Serif"):
        shutil.copy(find_font(name), fonts)
    (fonts / "copy").mkdir()
    shutil.copy(find_font("FreeSerif"), fonts / "copy")  # the same font twice counts once
    monkeypatch.setattr(texts, "load", lambda source, progress=None, accents=False: [
        "Ἐν ἀρχῇ ἦν ὁ λόγος, καὶ ὁ λόγος ἦν πρὸς τὸν θεόν, καὶ θεὸς ἦν ὁ λόγος.",
        "οὗτος ἦν ἐν ἀρχῇ πρὸς τὸν θεόν. πάντα δι’ αὐτοῦ ἐγένετο, καὶ χωρὶς αὐτοῦ ἐγένετο οὐδὲ ἕν ὃ γέγονεν."])
    r = synthtrain.generate(fonts, ["greek"], lines_per_font=12)
    assert len(r["fonts"]["greek"]) == 3 and r["added"]["grc"] == 36
    summary = pool.summary("grc")
    assert sum(1 for b in summary.values() if b["test"]) == 1, "one font of three kept aside, for testing"
    assert all(info.get("fingerprint") for info in pool.books("grc").values())
    assert synthtrain.generate(fonts, ["greek"], lines_per_font=12)["added"].get("grc", 0) == 0, "unchanged fonts skipped"
    # Fonts chosen one by one (Training -> Train from fonts), not a folder: only those.
    chosen = [f for f in synthtrain.fonts_in(fonts) if f.name.startswith("FreeSerif")]
    assert synthtrain.generate(chosen, ["greek"], lines_per_font=12)["fonts"]["greek"] == ["FreeSerif Regular"]
    # A Hebrew font without the points: its lines set without them.
    _unpointed(Path(find_font("FreeSerif")), tmp_path / "Plain.ttf", "Plain Hebrew")
    monkeypatch.setattr(texts, "load", lambda source, progress=None, accents=False: ["בְּרֵאשִׁית בָּרָא אֱלֹהִים אֵת הַשָּׁמַיִם וְאֵת הָאָרֶץ"] * 5)
    plain = synthtrain.fonts_in(tmp_path / "Plain.ttf") or [synthtrain.FontInfo(tmp_path / "Plain.ttf", "Plain Hebrew Regular")]
    r = synthtrain.generate(plain, ["hebrew"], lines_per_font=12)
    assert r["fonts"]["hebrew"] == ["Plain Hebrew Regular (without marks)"] and r["added"]["heb"] == 12
    lines = [i.text for i in pool.items("heb")]
    assert lines and all(not any(0x05B0 <= ord(c) <= 0x05C7 for c in t) for t in lines), "set without points"
    monkeypatch.setattr(texts, "load", lambda source, progress=None, accents=False: [
        "Ἐν ἀρχῇ ἦν ὁ λόγος, καὶ ὁ λόγος ἦν πρὸς τὸν θεόν, καὶ θεὸς ἦν ὁ λόγος.",
        "οὗτος ἦν ἐν ἀρχῇ πρὸς τὸν θεόν. πάντα δι’ αὐτοῦ ἐγένετο, καὶ χωρὶς αὐτοῦ ἐγένετο οὐδὲ ἕν ὃ γέγονεν."])
    # A font whose lines were lost is set again, though nothing about it changed.
    book = next(iter(pool.books("grc")))
    for f in (pool.root() / "grc" / pool.slug(book)).iterdir():
        f.unlink()
    assert synthtrain.generate(fonts, ["greek"], lines_per_font=12)["added"]["grc"] == 12
    # A font file touched (or the same font found in another copy) is set again: the same
    # lines, the same images, which must be kept, not lost.
    import os

    for f in fonts.glob("*.*"):
        os.utime(f, (1_000_000_000, 1_000_000_000))
    synthtrain.generate(fonts, ["greek"], lines_per_font=12)
    with pool.Writer("grc") as w:
        assert all(w.count(b) == 12 for b in pool.books("grc"))


def test_transliteration_text():
    """Hebrew in SBL academic style, and English lines with transliterated words of each kind."""
    from ectype import translit

    assert translit.hebrew("בְּרֵאשִׁית בָּרָא אֱלֹהִים אֵת הַשָּׁמַיִם וְאֵת הָאָרֶץ") == \
        "bərēʾšîṯ bārāʾ ʾĕlōhîm ʾēṯ haššāmayim wəʾēṯ hāʾāreṣ"
    assert translit.hebrew("וְרוּחַ יְהוָה עַל־פְּנֵי תּוֹרָה מֶלֶךְ יִשְׂרָאֵל") == "wərûaḥ YHWH ʿal-pənê tôrâ meleḵ yiśrāʾēl"
    assert translit.hebrew("קְטַל", older=True) == "ḳᵉṭal"
    assert translit.hebrew("וּמִשְׁפָּטִים") == "ûmišpāṭîm"
    lines = translit.passages(["בְּרֵאשִׁית בָּרָא אֱלֹהִים"], count=400)
    assert lines == translit.passages(["בְּרֵאשִׁית בָּרָא אֱלֹהִים"], count=400), "the same each time"
    chars = set("".join(lines))
    for c in "ʾʿḥšēāəḫẓʃŋɛːüçøčō":
        assert c in chars, c
    assert all(any(w in translit.ENGLISH for w in line.lower().split()) for line in lines), "English around them"


def test_transliteration_lines_set_in_fonts(data, tmp_path, monkeypatch):
    """Lines set for English in fonts with the letters; a font with only some sets the lines it can."""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    from ectype import synthtrain, translit
    from ectype.train import _foreign

    lines = translit.passages(["בְּרֵאשִׁית בָּרָא אֱלֹהִים אֵת הַשָּׁמַיִם וְאֵת הָאָרֶץ"], count=300)
    monkeypatch.setattr(texts, "load", lambda source, progress=None, accents=False: lines)
    full = synthtrain.FontInfo(Path(_font("DejaVu Serif")), "DejaVu Serif")
    # A font with a-z and the European accents, but no ḥ, ʾ, ə or IPA.
    font = TTFont(_font("DejaVu Serif"))
    keep = [u for u in font.getBestCmap() if u < 0x250]
    sub = subset.Subsetter(subset.Options())
    sub.populate(unicodes=keep)
    sub.subset(font)
    font.save(str(tmp_path / "Plain.ttf"))
    plain = synthtrain.FontInfo(tmp_path / "Plain.ttf", "Plain Latin")
    _, needed, _ = synthtrain.needs("transliteration")
    assert synthtrain.level(full, needed, script="transliteration") == synthtrain.FULL
    assert synthtrain.level(plain, needed, script="transliteration") is None, "too few of the letters"
    r = synthtrain.generate([full], ["transliteration"], lines_per_font=12)
    assert r["fonts"]["transliteration"] == ["DejaVu Serif"] and r["added"]["eng"] == 12
    got = [i.text for i in pool.items("eng")]
    assert any(c in "".join(got) for c in "ʾʿḥšəʃ")
    assert not any(_foreign(c, "Latin") for t in got for c in t), "nothing left out of English training"
    assert _foreign("α", "Latin") and _foreign("א", "Latin")


def test_hebrew_lines_with_accents_or_as_grammars_print_it():
    """The Codex's accents kept, or as grammars print Hebrew: ֫ over a stress before the last
    syllable (Driver's Treatise), none over a last-syllable stress."""
    import random

    from ectype import synthtrain

    verse = "וַיֹּ֥אמֶר אֱלֹהִ֖ים יְהִ֣י א֑וֹר וַֽיְהִי־אֽוֹר׃"
    assert texts.grammar_style(verse) == "וַיֹּ֫אמֶר אֱלֹהִים יְהִי אוֹר וַיְהִי־אוֹר׃"
    assert texts.grammar_style("הַשָּׁמַ֖יִם וְר֣וּחַ הָאָֽרֶץ׃") == "הַשָּׁמַ֫יִם וְרוּחַ הָאָ֫רֶץ׃"
    assert texts.grammar_style("תֹ֙הוּ֙") == "תֹ֫הוּ", "a pashta written twice: the first marks the stress"
    rng = random.Random(1)
    every = {chr(c) for c in range(0x0590, 0x05FF)}
    forms = {synthtrain.hebrew_form(verse, rng, every) for _ in range(60)}
    assert verse in forms and texts.without_accents(verse) in forms and texts.grammar_style(verse) in forms
    bare = every - synthtrain.ACCENT_MARKS - {texts.STRESS}  # a font without the accents: points only
    assert {synthtrain.hebrew_form(verse, rng, bare) for _ in range(20)} == {texts.without_accents(verse)}


def test_training_settings_file(tmp_path):
    from ectype import trainall

    path = trainall.write_template(tmp_path / "training.ini")
    text = path.read_text(encoding="utf-8").replace("fonts =\n", "fonts = C:\\Fonts\n")
    text += "\n[edition: Letteris 1866]\nscan = C:\\Scans\\a.pdf\ntext = hebrew-bible\n"
    path.write_text(text, encoding="utf-8")
    s = trainall.read(path)
    assert s["fonts"] == "C:\\Fonts" and s["scripts"] == ["hebrew", "greek"] and s["lines"] == 400
    assert s["editions"] == [{"name": "Letteris 1866", "scan": "C:\\Scans\\a.pdf", "text": "hebrew-bible", "language": ""}]


def test_training_a_language_from_its_pool(data, tmp_path):
    """A general model trained from the pool, checked, installed, and read with."""
    from ectype import train

    if train.available():
        pytest.skip(train.available())
    try:
        train.best_model("eng")
    except train.TrainingError as exc:
        pytest.skip(str(exc))
    words = "inspiration scripture doctrine original copies letter president teacher errors careless".split()
    rng = np.random.default_rng(0)
    with pool.Writer("eng") as w:
        for book, font in (("Young", "FreeSerif"), ("Warfield", "DejaVu Serif")):
            for i in range(30):
                text = " ".join(rng.choice(words, 4))
                im = _line(text, _font(font))
                path = tmp_path / f"{book}{i}.png"
                im.save(path)
                w.add(path, text, book, "line", "correction", origin=f"{book}{i}")
    r = training.train_language("eng", iterations=60)
    assert r["general"]["installed"] or "no fewer" in r["general"]["why"], r["lines"]
    if r["general"]["installed"]:
        assert models.resolve("eng").model == "eng_ectype"
        reg = models.registry()["eng"]
        assert set(reg["general"]["books"]) == {"Young", "Warfield"}
    assert json.dumps(r["lines"])


def test_quick_update_adds_new_corrections_to_the_model_in_use(data, tmp_path, monkeypatch):
    """A couple dozen corrections more: the model in use trained further on them with earlier
    images, checked with the old model's errors kept; nothing new: nothing done."""
    from ectype import train
    from ectype.train import Fit

    fits, reads = [], []

    def fit(start, base, pairs, work, say, iterations=None):
        work.mkdir(parents=True, exist_ok=True)
        for name in ("int", "float"):
            (work / name).write_bytes(f"{start}{len(pairs)}{name}".encode())
        fits.append((Path(start).name, len(pairs), iterations))
        return Fit(work / "int", work / "float", len(pairs), 0, "", iterations or 100)

    def errors(model, items, work, name, per_item=None):
        reads.append((name, len(items)))
        per = {i.id: (1 if name == "candidate" else 3, 10) for i in items}
        if per_item is not None:
            per_item.update(per)
        return training._by_book(items, per)

    monkeypatch.setattr(train, "fit_tesseract", fit)
    monkeypatch.setattr(train, "available", lambda: None)
    (tmp_path / "best.traineddata").write_bytes(b"stock")
    monkeypatch.setattr(train, "best_model", lambda code, say=None: tmp_path / "best.traineddata")
    monkeypatch.setattr(training, "_tess_errors", errors)
    monkeypatch.setattr(training, "_tess_current", lambda code: (
        models.tesseract_dir(code).parent.parent.parent / "tessdata" / f"{code}_ectype.traineddata"
        if "general" in models.choices(code) else tmp_path / "best.traineddata"))

    def add(n, book, start=0):
        with pool.Writer("eng") as w:
            for i in range(start, start + n):
                w.add(f"image {book} {i}".encode(), f"line {i} of {book}", book, "line", "correction", origin=f"{book}{i}")

    add(60, "Young")
    assert training.plan("eng")["mode"] == "full"
    r = training.train_language("eng")
    assert r["mode"] == "full" and r["general"]["installed"] and fits[-1][0] == "best.traineddata"
    assert training.plan("eng")["mode"] == "none" and training.train_language("eng")["mode"] == "none"

    add(24, "Driver")
    p = training.plan("eng")
    assert p["mode"] == "quick" and p["new"] == len([i for i in training._items_for("eng", "train") if i.book == "Driver"])
    reads.clear()
    q = training.train_language("eng")
    assert q["mode"] == "quick" and q["general"]["installed"], q["lines"]
    start, n, iterations = fits[-1]
    assert start == "general.traineddata", "trained further from the model in use"
    assert n == len(training._items_for("eng", "train")) and iterations == training.QUICK_ITERATIONS[0]
    in_use = [k for name, k in reads if name.startswith("in_use")]
    assert in_use == [len([i for i in training._items_for("eng", "test") if i.book == "Driver"])], \
        "the model in use reads only the test images it hadn't read"
    assert models.registry()["eng"]["quick_updates"] == 1
    # A model trained by an earlier version, with no list of what it knew: the pool's images
    # older than its training count as known.
    import os

    training._known_path("eng").unlink()
    data = models.registry()
    data["eng"]["general"]["trained"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + 120))
    models.save_registry(data)
    assert training.plan("eng")["mode"] == "none"
    for item in training._items_for("eng", "train")[:5]:
        os.utime(item.png, (time.time() + 3600, time.time() + 3600))  # added after it was trained
    assert training.plan("eng") == {"mode": "quick", "new": 5, "why": "5 new images"}
    assert training.train_language("eng", mode="full")["mode"] == "full" and fits[-1][0] == "best.traineddata"


def test_trained_models_window(data):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ectype.gui import ModelsDialog
    from ectype.resources import user_tessdata_dir

    user_tessdata_dir().mkdir(parents=True)
    (user_tessdata_dir() / "grc_ectype.traineddata").write_bytes(b"x" * 1000)
    models.save_registry({"grc": {"engine": "tesseract", "general": {"items": 120, "trained": "2026-10-29 10:00",
                                                                      "books": ["Young", "font: Porson"]}}})
    with pool.Writer("grc") as w:
        w.add(b"png", "λόγος", "Young", "word", "correction")
    app = qt.QApplication.instance() or qt.QApplication([])
    dialog = ModelsDialog()
    text = dialog.text.toPlainText()
    assert "120 images from 2 sources" in text and "Young (correction): 1" in text
    monkey = dialog._confirm
    dialog._confirm = lambda text: True
    dialog.remove_models()
    assert models.resolve("grc").which == "stock" and "No trained models yet." in dialog.text.toPlainText()
    dialog._confirm = monkey
    # Freed here, on the interface thread: collected later from a worker thread, Qt would crash.
    import gc

    dialog.close()
    dialog.deleteLater()
    del dialog
    app.processEvents()
    gc.collect()


def _unpointed(source: Path, dest: Path, family: str) -> None:
    """A copy of the font without Hebrew's points, under another name."""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    font = TTFont(str(source))
    keep = [u for u in font.getBestCmap() if not 0x05B0 <= u <= 0x05C7]
    sub = subset.Subsetter(subset.Options())
    sub.populate(unicodes=keep)
    sub.subset(font)
    for rec in font["name"].names:
        if rec.nameID in (1, 4, 16):
            rec.string = family
    font.save(str(dest))


def test_train_from_fonts_window(data, monkeypatch):
    """The fonts of the computer and added folders, each with the chosen languages it supports."""
    import os
    import shutil

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ectype import gui, synthtrain
    from ectype.synth import find_font

    system, mine = data / "system", data / "mine"
    for folder, names in ((system, ("FreeSerif", "DejaVu Serif")), (mine, ("Liberation Serif",))):
        folder.mkdir()
        for name in names:
            shutil.copy(find_font(name), folder)
    _unpointed(Path(find_font("FreeSerif")), system / "Plain.ttf", "Plain Hebrew")
    monkeypatch.setattr(synthtrain, "system_font_folders", lambda: [system])
    monkeypatch.setattr(texts, "load", lambda source, progress=None, accents=False: (
        ["בְּרֵאשִׁית בָּרָא אֱלֹהִים אֵת הַשָּׁמַיִם וְאֵת הָאָרֶץ"] if source == "hebrew-bible"
        else ["The word ḥesed in Hebrew, ʾĕlōhîm and /ʃəˈlɔm/ über ç"] if source == "transliteration"
        else ["Ἐν ἀρχῇ ἦν ὁ λόγος, καὶ ὁ λόγος ἦν πρὸς τὸν θεόν"]))
    app = qt.QApplication.instance() or qt.QApplication([])

    def opened():
        d = gui.FontTrainingDialog()
        d.scanner.wait()
        app.processEvents()
        return d

    d = opened()
    assert not d.lang_boxes["transliteration"].isChecked(), "Hebrew and Greek chosen at first"
    rows = {d.table.item(r, 0).text(): [d.table.item(r, c).text() for c in (1, 2)] for r in range(d.table.rowCount())}
    assert rows == {"FreeSerif Regular": ["Full", "Full"], "Plain Hebrew Regular": ["Partial", "Full"]}, \
        "only fonts with both languages (Hebrew without points too), and how far each supports them"
    d.only_all.setChecked(False)
    rows = {d.table.item(r, 0).text(): [d.table.item(r, c).text() for c in (1, 2)] for r in range(d.table.rowCount())}
    assert rows["DejaVu Serif Book"] == ["—", "Full"] and rows["FreeSerif Regular"] == ["Full", "Full"]
    d.lang_boxes["hebrew"].setChecked(False)
    assert d.table.columnCount() == 3 and d.table.horizontalHeaderItem(1).text() == "Greek"
    d.lang_boxes["transliteration"].setChecked(True)
    col = {d.table.item(r, 0).text(): d.table.item(r, 2).text() for r in range(d.table.rowCount())}
    assert d.table.horizontalHeaderItem(2).text() == "Transliteration" and col["DejaVu Serif Book"] == "Full"
    d.lang_boxes["transliteration"].setChecked(False)
    d.settings["folders"] = [str(mine)]
    d._scan()
    d.scanner.wait()
    app.processEvents()
    assert "Liberation Serif Regular" in d._shown()
    d.table.item(d._shown().index("DejaVu Serif Book"), 0).setCheckState(gui.Qt.Unchecked)
    assert "DejaVu Serif Book" not in [f.name for f in d.chosen_fonts()] and d.train_button.isEnabled()
    d._accept()
    again = opened()
    assert again.scripts() == ["greek"] and not again.only_all.isChecked() and "Liberation Serif Regular" in again._shown()
    assert "DejaVu Serif Book" not in again.chosen_names() and "FreeSerif Regular" in again.chosen_names()
    import gc

    for dialog in (d, again):
        dialog.close()
        dialog.deleteLater()
    del d, again, dialog
    app.processEvents()
    gc.collect()
