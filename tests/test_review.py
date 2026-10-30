from __future__ import annotations

import json
import shutil
import threading
import urllib.request

import pytest

from ectype.ocr import ocr_pages
from ectype.review import apply_correction, serve
from ectype.ocr import OcrPage
from ectype.synth import Degrade, make_page


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    root = tmp_path_factory.mktemp("review")
    make_page(root / "truth" / "s01", degrade=Degrade(skew_degrees=0.2, blur_sigma=0.5, noise_sigma=4, speckles=5, jpeg_quality=90))
    (root / "pages").mkdir()
    shutil.copy(root / "truth" / "s01" / "scan.png", root / "pages" / "s01.png")
    ocr_pages(root / "pages", root / "ocr")
    return root


def test_apply_correction_keeps_or_spreads_boxes(workspace):
    ocr = OcrPage.from_json(json.loads((workspace / "ocr" / "s01.ocr.json").read_text()))
    w = ocr.lines[0].words[0]
    boxes = [c.box for c in w.chars]
    apply_correction(ocr, 0, 0, w.text.upper())
    assert [c.box for c in w.chars] == boxes and w.conf == 100
    apply_correction(ocr, 0, 0, "abcdefgh")
    assert len(w.chars) == 8 and w.chars[0].box[0] == w.box[0] and w.chars[-1].box[2] == w.box[2]


def test_punctuation_corrected_apart_from_the_letters():
    """A mark at a word's start or end is a piece of its own; correcting one piece keeps the rest."""
    from ectype.ocr import Char, Line, Word
    from ectype.review import parts

    def word(text, x=100):
        return Word(text, [x, 10, x + 20 * len(text), 40], 70.0,
                    [Char(c, [x + 20 * k, 10, x + 20 * (k + 1), 40], 70.0) for k, c in enumerate(text)])

    w = word("(ḥesed),")
    assert parts(w) == [(0, 1), (1, 6), (6, 7), (7, 8)]
    assert parts(word("plain")) == [(0, 5)] and parts(word("—")) == [(0, 1)]
    page = OcrPage("tesseract", "eng", [1000, 100], 300, [Line([0, 0, 900, 50], [0, 0], None, None, None, [w])])
    apply_correction(page, 0, 0, "hesd", span=(1, 6))  # shorter: spread over the letters' place only
    assert w.text == "(hesd)," and w.chars[0].box == [100, 10, 120, 40] and w.chars[-1].box == [240, 10, 260, 40]
    assert w.chars[1].box[0] == 120 and w.chars[4].box[2] == 220
    apply_correction(page, 0, 0, "]", span=(5, 6))  # the pieces after a correction: parts() again
    assert w.text == "(hesd]," and parts(w)[-2:] == [(5, 6), (6, 7)] and w.conf == 100
    # Hebrew with a mark after it (in reading order, so on its left): the letters placed from the right.
    h = word("אמר:")
    for c, b in zip(h.chars, reversed([c.box for c in h.chars])):
        c.box = b
    page.lines[0].words.append(h)
    assert parts(h) == [(0, 3), (3, 4)]
    apply_correction(page, 0, 1, "אמרו", span=(0, 3))
    assert h.text == "אמרו:" and h.chars[0].box[2] == 180 and h.chars[3].box[0] == 120 and h.chars[4].box == [100, 10, 120, 40]


def test_box_stretched_over_a_missed_letter():
    """"ZDMG." read as "DMG.": the box stretched over the Z, the Z typed; D M G . keep their places."""
    from ectype.ocr import Char, Line, Word
    from ectype.review import insert_word, place_chars

    w = Word("DMG.", [120, 10, 200, 40], 80.0, [Char(t, [120 + 20 * k, 12, 140 + 20 * k, 38], 80.0) for k, t in enumerate("DMG.")])
    page = OcrPage("tesseract", "eng", [1000, 100], 300, [Line([120, 10, 200, 40], [0, 0], None, None, None, [w])])
    kept = [c.box for c in w.chars]
    apply_correction(page, 0, 0, "ZDMG", span=(0, 3), box=[96, 10, 200, 40])
    assert w.text == "ZDMG." and w.box == [96, 10, 200, 40] and page.lines[0].box[0] == 96
    assert w.chars[0].box[:3:2] == [96, 120] and [c.box for c in w.chars[1:]] == kept
    # Little of the old text left: spread evenly.
    assert [c.box[0] for c in place_chars(w.chars, "xy", [0, 0, 100, 10])] == [0, 50]
    # A word drawn where the OCR found none: in its line, in reading order.
    assert insert_word(page, [20, 12, 90, 40], "see") == (0, 0) and page.lines[0].words[1] is w
    assert insert_word(page, [300, 12, 360, 40], "p.") == (0, 2)
    assert insert_word(page, [20, 60, 80, 90], "Alone") == (1, 0) and len(page.lines) == 2


def test_review_server_saves_corrections_and_training_pairs(workspace):
    server = serve(workspace, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        index = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert "/page/s01" in index
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/page/s01").read().decode()
        assert 'data-l="0" data-w="0"' in page
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/correct", method="POST",
                                     data=json.dumps({"page": "s01", "line": 1, "word": 0, "text": "First"}).encode(),
                                     headers={"Content-Type": "application/json"})
        result = json.loads(urllib.request.urlopen(req).read())
        assert result["new"] == "First"
        bad = urllib.request.Request(f"http://127.0.0.1:{port}/api/correct", method="POST",
                                     data=json.dumps({"page": "../etc", "line": 0, "word": 0, "text": "x"}).encode())
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad)
    finally:
        server.shutdown()
    ocr = json.loads((workspace / "ocr" / "s01.ocr.json").read_text())
    assert ocr["lines"][1]["words"][0]["text"] == "First"
    assert (workspace / "ocr" / "s01.ocr.original.json").is_file()
    log = json.loads((workspace / "corrections" / "s01.json").read_text())
    assert log[-1]["new"] == "First"
    gt = (workspace / "training" / "s01-l001.gt.txt").read_text()
    assert gt.startswith("First ")
    assert (workspace / "training" / "s01-l001.png").is_file()


def test_stretched_and_added_words_kept_when_read_again(tmp_path):
    """A stretched box and a word added by hand survive a new reading of the page."""
    import copy

    from PIL import Image

    from ectype import report
    from ectype.ocr import Char, Line, Word
    from ectype.review import add_word, carry_corrections, correct

    (tmp_path / "pages").mkdir()
    (tmp_path / "ocr").mkdir()
    Image.new("L", (600, 120), 255).save(tmp_path / "pages" / "p.png")

    def word(text, x):
        return Word(text, [x, 20, x + 20 * len(text), 50], 80.0,
                    [Char(t, [x + 20 * k, 20, x + 20 * (k + 1), 50], 80.0) for k, t in enumerate(text)])

    reading = OcrPage("tesseract", "eng", [600, 120], 300,
                      [Line([100, 20, 400, 50], [0, 0], None, None, None, [word("in", 100), word("DMG.", 200), word("vol", 340)])])
    report.write_json(tmp_path / "ocr" / "p.ocr.json", reading.to_json())
    correct(tmp_path, "p", 0, 2, "vol.")  # a later word on the line, corrected first
    correct(tmp_path, "p", 0, 1, "ZDMG", span=(0, 3), box=[176, 20, 280, 50])
    added = add_word(tmp_path, "p", [150, 22, 170, 50], "a")
    assert (added["line"], added["word"]) == (0, 1)
    log = json.loads((tmp_path / "corrections" / "p.json").read_text())
    assert [(e["line"], e["word"]) for e in log] == [(0, 3), (0, 2), (0, 1)], "the later words moved along"

    again = copy.deepcopy(reading)  # read again: the same misreading, nothing added
    assert carry_corrections(tmp_path, "p", again) == 3
    words = again.lines[0].words
    assert [w.text for w in words] == ["in", "a", "ZDMG.", "vol."] and words[2].box[0] == 176
