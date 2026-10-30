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
    # Hebrew with a mark after it (in reading order): the letters spread from the right.
    h = word("אמר:")
    page.lines[0].words.append(h)
    assert parts(h) == [(0, 3), (3, 4)]
    apply_correction(page, 0, 1, "אמרו", span=(0, 3))
    assert h.text == "אמרו:" and h.chars[0].box[2] == 160 and h.chars[3].box[0] == 100


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
