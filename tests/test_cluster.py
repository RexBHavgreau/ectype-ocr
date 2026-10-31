from __future__ import annotations

import math
import shutil

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from ectype.cluster import UPSAMPLE, cluster_workspace, running_lines, word_slant
from ectype.ocr import Line, OcrPage, Word, ocr_pages
from ectype.segment import segment_workspace
from ectype.synth import Degrade, make_page


def _word_mask(shear: float) -> np.ndarray:
    from ectype.synth import find_font

    font = ImageFont.truetype(str(find_font()), 60)
    im = Image.new("L", (520, 100), 0)
    ImageDraw.Draw(im).text((10, 80), "questions", font=font, fill=255, anchor="ls")
    a = (np.asarray(im) > 128).astype(np.uint8)
    return cv2.warpAffine(a, np.float32([[1, -shear, shear * 80], [0, 1, 0]]), (520, 100)) > 0


def test_word_slant():
    assert word_slant(_word_mask(0.0), 80) == 0
    assert abs(word_slant(_word_mask(math.tan(math.radians(12))), 80) - 12) <= 2


@pytest.fixture(scope="module")
def clustered(tmp_path_factory):
    root = tmp_path_factory.mktemp("cluster")
    truths = []
    (root / "pages").mkdir()
    for i, seed in enumerate((1, 2)):
        name = f"s{i + 1:02d}"
        truths.append(make_page(root / "truth" / name, seed=seed,
                                degrade=Degrade(skew_degrees=0.3 * (i + 1), blur_sigma=0.6, noise_sigma=6, speckles=60, jpeg_quality=85)))
        shutil.copy(root / "truth" / name / "scan.png", root / "pages" / f"{name}.png")
    ocr_pages(root / "pages", root / "ocr")
    segment_workspace(root)
    return root, truths, cluster_workspace(root)


def test_one_glyph_per_common_character(clustered):
    _, _, s = clustered
    main_style = s["styles"][0]
    assert main_style["instances"] > 0.95 * s["instances"]
    counts = {}
    for g in s["glyphs"]:
        if g["style"] == 0 and g["count"] >= 10:
            counts[g["text"]] = counts.get(g["text"], 0) + 1
    assert len(counts) > 20
    assert sum(n == 1 for n in counts.values()) / len(counts) >= 0.9, counts


def test_composite_matches_the_font(clustered):
    """The averaged 'e' should be the font's 'e': specks gone, edges where they belong."""
    root, truths, s = clustered
    g = max((g for g in s["glyphs"] if g["text"] == "e"), key=lambda g: g["count"])
    composite = 1 - np.asarray(Image.open(root / "cluster" / "composites" / g["composite"]), np.float32) / 255
    ours = composite >= 0.5
    t = truths[0]
    font = ImageFont.truetype(t["font"]["path"], size=round(t["point_size"] * t["dpi"] / 72) * UPSAMPLE)
    ref = Image.new("L", (ours.shape[1] * 2, ours.shape[0] * 2), 0)
    ImageDraw.Draw(ref).text((ours.shape[1], ours.shape[0]), "e", font=font, fill=255, anchor="ls")
    ref = np.asarray(ref) > 128
    # Align by ink bounding boxes, then compare.
    def crop(m):
        ys, xs = np.nonzero(m)
        return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    a, b = crop(ours), crop(ref)
    assert abs(a.shape[0] - b.shape[0]) <= 1.5 * UPSAMPLE and abs(a.shape[1] - b.shape[1]) <= 1.5 * UPSAMPLE
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    a, b = a[:h, :w], b[:h, :w]
    iou = (a & b).sum() / (a | b).sum()
    # At 4x, half a scan pixel of edge disagreement already costs about 0.2 of IoU;
    # thin curves averaged from blurred instances also come out slightly short.
    assert iou > 0.75, iou
    assert abs(ours.sum() / ref.sum() - 1) < 0.08, "same amount of ink as the font's glyph"
    assert g["count"] > 100


def _page(*lines: tuple[int, str]) -> OcrPage:
    """An OCR page from (top y, text) lines, each 40 px tall."""
    out = []
    for y, text in lines:
        words, x = [], 150
        for t in text.split():
            words.append(Word(text=t, box=[x, y, x + 20 * len(t), y + 40], conf=95.0))
            x += 20 * len(t) + 15
        out.append(Line(box=[150, y, x, y + 40], baseline=[0.0, -9.0], x_size=21.0, ascenders=None, descenders=None, words=words))
    return OcrPage(engine="test", lang="eng", size_px=[1600, 2550], dpi=300.0, lines=out)


def test_running_lines():
    body = "told, because the Bible does not claim such a place for itself"
    text = [(350 + 53 * k, body) for k in range(6)]
    pages = [_page((300, "“Some Reflections"), (500, "upon Inspiration”"), (2400, "thinking. They may agree"))]
    for folio in range(170, 176):
        head = f"{folio} Inspiration" if folio % 2 == 0 else f"Edward J. Young, “Some Reflections upon Inspiration” {folio}"
        if folio == 173:
            head = head.replace("Young", "Yonng")  # an OCR slip still matches
        pages.append(_page((230, head), *text, (2400, body), (2453, "Ibid., 31.")))
    pages.append(_page(*text, (2400, "the end of a chapter"), (2500, "176")))
    zones = running_lines(pages)
    assert zones[0] == {}  # a chapter title is not a running head
    for z in zones[1:7]:
        assert z == {0: "head"}  # a footnote on many pages is not a running foot
    assert zones[7] == {7: "foot"}  # a lone folio


def test_kept_results_make_the_same_glyphs(clustered, monkeypatch):
    """Run again with nothing changed: the pages' instances, groupings and composites are taken
    from the cache (none averaged again), and the result is the same."""
    import json

    from ectype import cluster

    root, _, first = clustered
    calls = []
    real = cluster.average
    monkeypatch.setattr(cluster, "average", lambda members, **kw: calls.append(1) or real(members, **kw))
    monkeypatch.setattr(cluster, "_page_instances", lambda *a: (_ for _ in ()).throw(AssertionError("measured again")))
    again = cluster_workspace(root)
    assert calls == [] and json.dumps(again, sort_keys=True) == json.dumps(first, sort_keys=True)


def test_italic_limit_follows_the_book():
    """A book whose italic leans steeply (Driver's: 16-28 degrees) has its limit raised; one
    whose italic leans 8-12 (Young's) keeps it near 18-20, so an upright "very" stays upright."""
    from ectype.cluster import italic_limit

    assert italic_limit([0.0] * 500 + [9, 10, 11, 12, 10, 9, 11, 10, 12, 8, 23, 24]) == 20.0
    assert italic_limit([0.0] * 500 + [18, 20, 22, 26, 24, 21, 19, 20, 22, 27, 25, 23]) == 31.5
    assert italic_limit([0.0] * 500 + [10, 11]) == 18.0, "too few leaning words to judge: the usual limit"
