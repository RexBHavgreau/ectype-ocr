from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from ectype import ocr as O
from ectype import segment as S
from ectype.synth import Degrade, make_page, scan_labels

HOCR = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<div class='ocr_page' title='bbox 0 0 600 400'>
 <span class='ocr_line' title="bbox 10 20 200 60; baseline 0.01 -8; x_size 30; x_descenders 8; x_ascenders 9">
  <span class='ocrx_word' title='bbox 10 20 60 52; x_wconf 91; x_fsize 10'>
   <span class='ocrx_cinfo' title='x_bboxes 10 30 30 52; x_conf 99.1'>o</span>
   <span class='ocrx_cinfo' title='x_bboxes 32 20 60 52; x_conf 97.5'>f</span>
  </span>
  <span class='ocrx_word' title='bbox 590 20 599 30; x_wconf 12'>
   <span class='ocrx_cinfo' title='x_bboxes 590 20 599 30; x_conf 12'>:</span>
  </span>
 </span>
</div></body></html>"""


def test_parse_hocr():
    page = O.parse_hocr(HOCR)
    assert page.size_px == [600, 400]
    line = page.lines[0]
    assert [w.text for w in line.words] == ["of", ":"]
    word = line.words[0]
    assert word.box == [10, 20, 60, 52] and word.conf == 91 and word.font_size_pt == 10
    assert word.chars[1].text == "f" and word.chars[1].box == [32, 20, 60, 52]
    assert line.baseline_y(10) == pytest.approx(52)  # bottom 60, offset -8
    assert line.baseline_y(110) == pytest.approx(53)
    again = O.OcrPage.from_json(json.loads(json.dumps(page.to_json())))
    assert again.text() == page.text()


def test_unknown_engine():
    with pytest.raises(O.EngineUnavailable):
        O.get_engine("nope")


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    root = tmp_path_factory.mktemp("seg")
    truth = make_page(root, degrade=Degrade(skew_degrees=0.3, blur_sigma=0.5, noise_sigma=5, speckles=20, jpeg_quality=90))
    gray = np.asarray(Image.open(root / "scan.png"))
    page = O.drop_margin_noise(O.get_engine().recognize(gray, dpi=300))
    return root, truth, gray, page


def test_tesseract_reads_synthetic_page(synthetic):
    from ectype.compare import cer

    _, truth, _, page = synthetic
    assert cer(page.text(), truth["text"]) < 0.02
    baselines = [line.baseline_y((line.box[0] + line.box[2]) / 2) for line in page.lines]
    truth_baselines = [line["baseline"] for line in truth["lines"]]
    # Skew moves baselines by up to a few pixels across the line; centres stay close.
    assert len(baselines) == len(truth_baselines)
    assert max(abs(a - b) for a, b in zip(baselines, truth_baselines)) < 6


def test_segmentation_owns_ink(synthetic):
    root, truth, gray, page = synthetic
    seg, labels = S.segment_page(gray, page)
    result = S.evaluate(seg, page, labels, gray, truth, scan_labels(root, truth))
    assert result["owned_share"] >= 0.98, result
    # i-dots ride with their stems: every "i" unit has two components.
    i_units = [u for u in seg.units if u.text == "i" and u.kind == "single"]
    assert i_units and sum(len(u.components) == 2 for u in i_units) / len(i_units) > 0.9
    again = S.Segmentation.from_json(json.loads(json.dumps(seg.to_json())))
    assert len(again.units) == len(seg.units)


def test_align_prefers_one_to_one_despite_drift():
    Char = O.Char
    # Boxes drifted a whole letter left (as Tesseract's do), five clusters for five chars.
    chars = [Char(t, [x, 0, x + 18, 20], 99) for t, x in zip("words", [1069, 1086, 1103, 1124, 1140])]
    clusters = [{"components": [i], "x0": x0, "x1": x1, "area": 200}
                for i, (x0, x1) in enumerate([(1070, 1100), (1102, 1122), (1124, 1138), (1139, 1159), (1161, 1176)])]
    groups, skipped = S._align(chars, clusters, 37, 11)
    assert groups == [([i], [i]) for i in range(5)] and skipped == []


def test_align_merges_touching_pair_by_width():
    Char = O.Char
    widths = {"r": 0.45, "n": 0.55, "o": 0.55}
    chars = [Char(t, [x, 0, x + 20, 20], 99) for t, x in zip("orno", [0, 22, 40, 62])]
    # "rn" printed touching: one wide cluster in the middle.
    clusters = [{"components": [0], "x0": 0, "x1": 20, "area": 200},
                {"components": [1], "x0": 22, "x1": 58, "area": 300},
                {"components": [2], "x0": 60, "x1": 80, "area": 200}]
    groups, _ = S._align(chars, clusters, 37, 11, [widths[c.text] for c in chars])
    assert groups == [([0], [0]), ([1, 2], [1]), ([3], [2])]


def test_marks_and_broken_pieces_join_their_letters():
    """An italic i's dot leaning over the next letter, and the broken-off arm of a v, join their
    letters (Driver's Treatise: "I gıve", "gize"); a fitted baseline doesn't follow old-style
    figures below the line."""
    import numpy as np

    from ectype.ocr import Char
    from ectype.segment import NO_DESCENDER, _join_marks

    # Components: 1 the i's stem, 2 its dot (over the gap before the v), 3 the v, 4 the v's arm.
    stats = np.array([[0, 0, 0, 0, 0],
                      [100, 20, 10, 30, 200], [111, 5, 8, 8, 50], [116, 20, 18, 30, 300], [135, 22, 5, 20, 60]])
    clusters = [{"components": [1], "area": 200, "x0": 100, "x1": 110},
                {"components": [3], "area": 300, "x0": 116, "x1": 134},
                {"components": [2], "area": 50, "x0": 111, "x1": 119},
                {"components": [4], "area": 60, "x0": 135, "x1": 140}]
    chars = [Char("i", [100, 5, 115, 50], 90), Char("v", [115, 5, 140, 50], 90)]
    groups, left = _join_marks([([0], [0]), ([1], [1])], [2, 3], chars, clusters, stats, x_size=30)
    assert groups == [([0], [0, 2]), ([1], [1, 3])] and left == []
    assert "7" not in NO_DESCENDER and "1" in NO_DESCENDER


def test_raised_note_numbers_read_again():
    """A figure set small above the line at a word's end is read again and stored as a
    superscript; a letter whose box holds only the top of its arm (a y) is not raised."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    from ectype.ocr import Char, Line, OcrPage, Word
    from ectype.raised import raised_runs, reread_raised
    from ectype.synth import find_font

    img = Image.new("L", (700, 120), 255)
    draw = ImageDraw.Draw(img)
    big, small = ImageFont.truetype(str(find_font("FreeSerif")), 60), ImageFont.truetype(str(find_font("FreeSerif")), 30)
    draw.text((20, 90), "present", font=big, fill=0, anchor="ls")
    x = 20 + big.getlength("present")
    draw.text((x + 2, 62), "1", font=small, fill=0, anchor="ls")  # raised: its foot well above the line
    draw.text((400, 90), "clearly", font=big, fill=0, anchor="ls")
    gray = np.asarray(img)
    y_end = 400 + big.getlength("clearly")

    def word(text, x0, boxes):
        return Word(text, [x0, 30, int(boxes[-1][1]), 110], 80.0, [Char(t, [a, 30, b, 110], 80.0) for t, (a, b) in zip(text, boxes)])

    w1 = word("present!", 20, [(20 + 25 * k, 45 + 25 * k) for k in range(7)] + [(int(x), int(x) + 18)])
    w2 = word("clearly", 400, [(400 + 25 * k, 425 + 25 * k) for k in range(6)] + [(int(y_end) - 6, int(y_end))])
    line = Line([20, 30, int(y_end), 110], [0.0, -20.0], 60.0, 18.0, 15.0, [w1, w2])  # baseline 90, x-height 27
    page = OcrPage("tesseract", "eng", [700, 120], 300, [line])
    assert raised_runs(gray, page) == [(0, 0, 7, 8)], "the figure, not the top of the y's arm"
    assert reread_raised(gray, page, lambda crop: "1") == 1 and w1.text == "present¹" and w2.text == "clearly"
