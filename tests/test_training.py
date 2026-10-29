"""The training pool, typeface families, model choice, known texts, generated lines and
matched scans (pool.py, families.py, models.py, training.py, texts.py, synthtrain.py, align.py)."""

from __future__ import annotations

import json

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
    monkeypatch.setattr(texts, "load", lambda source, progress=None: [
        "Ἐν ἀρχῇ ἦν ὁ λόγος, καὶ ὁ λόγος ἦν πρὸς τὸν θεόν, καὶ θεὸς ἦν ὁ λόγος.",
        "οὗτος ἦν ἐν ἀρχῇ πρὸς τὸν θεόν. πάντα δι’ αὐτοῦ ἐγένετο, καὶ χωρὶς αὐτοῦ ἐγένετο οὐδὲ ἕν ὃ γέγονεν."])
    r = synthtrain.generate(fonts, ["greek"], lines_per_font=12)
    assert len(r["fonts"]["greek"]) == 3 and r["added"]["grc"] == 36
    summary = pool.summary("grc")
    assert sum(1 for b in summary.values() if b["test"]) == 1, "one font of three kept aside, for testing"
    assert all(info.get("fingerprint") for info in pool.books("grc").values())
    assert synthtrain.generate(fonts, ["greek"], lines_per_font=12)["added"].get("grc", 0) == 0, "unchanged fonts skipped"


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
    app.processEvents()
