"""Kraken as the second engine. Most tests use a stand-in for the worker; the last ones need
the Kraken engine (ECTYPE_KRAKEN, or Kraken installed) and a model (ECTYPE_KRAKEN_TEST_MODEL),
which CI downloads from Kraken's repository."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from ectype import kraken, scripts
from ectype.ocr import Char, Line, OcrPage, Word


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    model = tmp_path / "heb.safetensors"
    model.write_bytes(b"x")
    kraken.register("kraken_123", {"title": "Printed Hebrew", "doi": "10.5281/zenodo.123", "file": str(model),
                                   "scripts": ["Hebr", "Latn"]})
    return tmp_path


def _word(text, x0, conf=90.0, w=80):
    return Word(text, [x0, 100, x0 + w, 140], conf, [Char(c, [x0 + 10 * i, 100, x0 + 10 * i + 9, 140], conf)
                                                    for i, c in enumerate(text)])


def test_models_are_languages(data):
    from ectype import languages

    assert kraken.is_model("kraken_123") and not kraken.is_model("heb")
    assert kraken.main_script("kraken_123") == "Hebrew", "the script it is chosen for, not Latin"
    assert scripts.script_of("kraken_123") == "Hebrew"
    assert languages.name("kraken_123") == "Hebrew (Kraken): Printed Hebrew"
    assert "kraken_123" in languages.installed()
    assert kraken.code_for("10.5281/zenodo.7051645") == "kraken_7051645"
    assert kraken.printed({"summary": "Printed Greek, 19th c.", "keywords": []}) is True
    assert kraken.printed({"summary": "Medieval Hebrew manuscripts", "keywords": []}) is False


class FakeSession:
    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def read(self, model, image, boxes, pack=None):
        self.requests.append(boxes)
        return [self.answers.pop(0) for _ in boxes]


def test_runs_of_a_script_are_read_by_kraken(data):
    line = Line([0, 100, 900, 140], [0, 0], 30.0, None, None,
                [_word("in", 0), _word("דבר", 100, 60), _word("8", 200, 40, w=20), _word("אמר", 240, 55),
                 _word("and", 400), _word("א", 500, 30)])
    page = OcrPage("tesseract", "eng+kraken_123", [1000, 300], 300.0, [line])
    # Kraken's reading of the first run: three words in reading order, right to left on the page.
    first = {"text": "דָּבָר אֵת אָמַר", "chars": [[c, [320 - 12 * i, 100, 326 - 12 * i, 140], 90.0]
                                                    for i, c in enumerate("דָּבָר אֵת אָמַר")]}
    second = {"text": "א", "chars": [["א", [505, 100, 520, 140], 80.0]]}
    session = FakeSession([first, second])
    assert kraken.read_runs(page, Path("p.png"), {"Hebrew": ("heb.safetensors", None)}, session) == 4
    assert [w.text for w in line.words] == ["in", "דָּבָר", "אֵת", "אָמַר", "and", "א"], \
        "the figure between two Hebrew words went with them"
    assert len(session.requests[0]) == 2, "one request for the page's runs"
    x0, y0, x1, y1 = session.requests[0][0]
    assert x0 < 100 and x1 > 320 and y0 < 100 and y1 > 140, "the run's box, padded"
    assert line.words[1].box[1] == y0 and line.words[1].conf == 90.0


def test_detected_words_are_left_for_kraken(data):
    page = OcrPage("tesseract", "eng+kraken_123", [1000, 300], 300.0,
                   [Line([0, 100, 600, 140], [0, 0], 30.0, None, None, [_word("the", 0, 96), _word("13723", 100, 80)])])
    unread = scripts.check_words(np.full((300, 1000), 255, np.uint8), page, ["eng", "kraken_123"],
                                 lambda crop, lang: pytest.fail("Tesseract mustn't read it again"),
                                 detect_fn=lambda crop: ("Hebrew", 3.0), defer=frozenset({"Hebrew"}))
    assert not unread and page.lines[0].words[1].script == "Hebrew", "a confident figure is checked too"
    assert not hasattr(page.lines[0].words[0], "script"), "plain words aren't sent to the detector"


def test_kraken_models_train_on_their_scripts_pool(data, tmp_path):
    from ectype import pool
    from ectype.project import Project

    folder = tmp_path / "ws" / "training" / "words"
    folder.mkdir(parents=True)
    for i in range(21):
        Image.new("L", (40 + i, 20), 255).save(folder / f"w{i}.png")
        (folder / f"w{i}.gt.txt").write_text("שָׁלוֹם\n", encoding="utf-8")
    project = Project(tmp_path / "ws")
    project.set_languages(["eng", "kraken_123"])
    assert pool.key_for("kraken_123") == "heb", "a Kraken Hebrew model learns from the Hebrew pool"
    assert project.trainable() == [("kraken_123", 21)]
    assert pool.summary("heb")["ws"]["source"] == "correction"


def test_engine_download_is_found_in_the_release(monkeypatch):
    class Response:
        def __init__(self, body):
            self.body = body

        def read(self):
            return json.dumps(self.body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    name = kraken.ENGINE_ASSET.format(version="0.3.0")
    release = {"assets": [{"name": "Ectype-OCR-0.3.0-Setup.exe", "browser_download_url": "u1"},
                          {"name": name, "browser_download_url": "u2", "size": 5, "digest": "sha256:ab"}]}
    asset = kraken.engine_asset("0.3.0", opener=lambda req, timeout: Response(release))
    assert asset == {"name": name, "url": "u2", "size": 5, "digest": "sha256:ab"}
    assert kraken.engine_asset("0.3.0", opener=lambda req, timeout: Response({"assets": []})) is None


def test_engine_status_without_kraken(monkeypatch):
    monkeypatch.setattr(kraken, "worker_command", lambda: None)
    assert kraken.engine_status() == {"installed": False, "problem": "The Kraken engine isn't installed."}


def test_models_panel(data, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from ectype.gui import AddLanguagesDialog

    records = [{"doi": "10.5281/zenodo.9", "summary": "Printed polytonic Greek", "script": ["Grek"], "keywords": [],
                "language": ["grc"], "cer": 2.5, "date": "2024-01-01", "license": "CC-BY-4.0", "description": "Teubner"},
               {"doi": "10.5281/zenodo.8", "summary": "Hebrew manuscripts", "script": ["Hebr"], "keywords": [],
                "language": ["heb"], "cer": 6.0, "date": "2023-01-01", "license": "CC-BY-4.0", "description": ""}]
    monkeypatch.setattr(kraken, "engine_status", lambda: {"installed": True, "kraken": "7.1", "torch": "2.9"})
    monkeypatch.setattr(kraken, "list_models", lambda refresh=False, progress=None: records)
    installed = []
    monkeypatch.setattr(kraken, "install_model", lambda r, progress=None: installed.append(r["doi"]) or "kraken_9")
    app = qt.QApplication.instance() or qt.QApplication([])
    dialog = AddLanguagesDialog()
    panel = dialog.kraken

    def wait():
        while panel.task is not None and panel.task.isRunning():
            app.processEvents()
        app.processEvents()

    wait()
    assert panel.engine_ok and "7.1" in panel.engine_label.text()
    panel.load()
    wait()
    assert panel.script.currentText() == "Greek" and panel.table.rowCount() == 1
    panel.script.setCurrentIndex(panel.script.findText("Hebrew"))
    assert panel.table.rowCount() == 0, "manuscript models hidden while 'printed only' is ticked"
    panel.printed.setChecked(False)
    assert panel.table.rowCount() == 1
    panel.script.setCurrentIndex(panel.script.findText("Greek"))
    panel.table.selectRow(0)
    assert "Teubner" in panel.description.toPlainText()
    panel.download()
    wait()
    assert installed == ["10.5281/zenodo.9"] and dialog.downloaded == ["kraken_9"]


# -- with the Kraken engine and a model ------------------------------------------------

MODEL = os.environ.get("ECTYPE_KRAKEN_TEST_MODEL")
needs_kraken = pytest.mark.skipif(not MODEL or kraken.worker_command() is None,
                                  reason="needs the Kraken engine and a model (ECTYPE_KRAKEN_TEST_MODEL)")


@needs_kraken
def test_worker_reads_a_line():
    font = ImageFont.truetype(str(__import__("ectype.synth", fromlist=["find_font"]).find_font("FreeSerif")), 40)
    img = Image.new("L", (600, 80), 255)
    ImageDraw.Draw(img).text((20, 56), "λόγος", font=font, fill=0, anchor="ls")
    with kraken.Session() as session, __import__("tempfile").TemporaryDirectory() as tmp:
        png = Path(tmp) / "line.png"
        img.save(png)
        (result,) = session.read(MODEL, png, [[0, 0, 600, 80]])
    assert result["text"].strip() and all(len(c) == 3 for c in result["chars"])


def test_accents_join_their_letters():
    from ectype.ocr import Char, Word

    result = {"chars": [["η", [10, 0, 10, 40], 99.0], ["̓", [14, 0, 14, 40], 90.0], ["͂", [16, 0, 16, 40], 95.0],
                        ["ν", [20, 0, 26, 40], 99.0]]}
    (word,) = kraken._words(result, [0, 100, 40, 140], Char, Word)
    assert word.text == "ἦν" and [c.text for c in word.chars] == ["ἦ", "ν"] and word.chars[0].conf == 90.0


@needs_kraken
def test_a_family_pack_over_the_general_model(tmp_path):
    """A pack of no changes, laid over the model, reads exactly as the model does."""
    if not MODEL.endswith(".safetensors"):
        pytest.skip("packs are over .safetensors models")
    info = kraken._run(["pack", MODEL, MODEL, str(tmp_path / "f1.pack.safetensors")])
    assert info["bytes"] < info["full_bytes"] / 10, "unchanged layers aren't stored"
    font = ImageFont.truetype(str(__import__("ectype.synth", fromlist=["find_font"]).find_font("FreeSerif")), 40)
    img = Image.new("L", (600, 80), 255)
    ImageDraw.Draw(img).text((20, 56), "λόγος", font=font, fill=0, anchor="ls")
    img.save(tmp_path / "line.png")
    with kraken.Session() as session:
        plain = session.read(MODEL, tmp_path / "line.png", [[0, 0, 600, 80]])
        packed = session.read(MODEL, tmp_path / "line.png", [[0, 0, 600, 80]], str(tmp_path / "f1.pack.safetensors"))
    assert plain == packed
