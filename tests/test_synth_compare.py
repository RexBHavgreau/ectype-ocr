from __future__ import annotations

import numpy as np
from PIL import Image

from ectype.compare import align, cer, compare, normalize_text, parse_pages
from ectype.synth import Degrade, make_page


def test_synth_truth_matches_ink(tmp_path):
    truth = make_page(tmp_path, degrade=Degrade.none())
    clean = np.asarray(Image.open(tmp_path / "clean.png"))
    assert clean.shape[::-1] == tuple(truth["size_px"])
    assert truth["text"] == (tmp_path / "truth.txt").read_text()
    chars = [c for line in truth["lines"] for w in line["words"] for c in w["chars"]]
    assert "".join(c["text"] for c in chars) == "".join(truth["text"].split())
    # Every non-space character's box contains ink, and the text outside all boxes is blank.
    mask = np.zeros(clean.shape, bool)
    for c in chars:
        x0, y0, x1, y1 = (int(round(v)) for v in c["box"])
        if x1 > x0 and y1 > y0:
            assert (clean[y0:y1, x0:x1] < 128).any(), c
            mask[max(y0 - 1, 0):y1 + 1, max(x0 - 1, 0):x1 + 1] = True
    assert (clean[~mask] < 128).sum() == 0


def test_synth_is_deterministic(tmp_path):
    make_page(tmp_path / "a", seed=3)
    make_page(tmp_path / "b", seed=3)
    a = np.asarray(Image.open(tmp_path / "a" / "scan.png"))
    b = np.asarray(Image.open(tmp_path / "b" / "scan.png"))
    assert np.array_equal(a, b)


def test_cer_and_normalization():
    assert normalize_text(" a\n\nb\tc ") == "a b c"
    assert cer("abc", "abc") == 0
    assert cer("abd", "abc") == 1 / 3
    assert cer("x", "") is None


def test_parse_pages():
    assert parse_pages(None, 3) == [0, 1, 2]
    assert parse_pages("1-2,5,9", 5) == [0, 1, 4]


def test_compare_identical_and_degraded(tmp_path):
    make_page(tmp_path / "clean", degrade=Degrade.none())
    make_page(tmp_path / "scan")
    same = compare(tmp_path / "clean" / "scan.pdf", tmp_path / "clean" / "scan.pdf", tmp_path / "r1", dpi=100)
    assert same["mean"]["ssim"] == 1.0
    assert same["mean"]["ink_disagreement"] == 0.0

    diff = compare(
        tmp_path / "clean" / "scan.pdf", tmp_path / "scan" / "scan.pdf", tmp_path / "r2", dpi=100,
        truth=tmp_path / "clean" / "truth.json",
    )
    assert diff["mean"]["ssim"] < 0.99
    assert diff["mean"]["ink_disagreement"] > 0.05
    # Image-only PDFs extract no text, so every character is an error.
    assert diff["mean"]["cer_a"] == 1.0
    assert (tmp_path / "r2" / "pages" / "p001-overlay.png").is_file()
    assert (tmp_path / "r2" / "index.html").is_file()


def test_compare_png_against_pdf(tmp_path):
    make_page(tmp_path, degrade=Degrade.none())
    s = compare(tmp_path / "clean.png", tmp_path / "scan.pdf", tmp_path / "r", dpi=300)
    assert s["pages"][0]["ssim"] > 0.999


def test_align_finds_quarter_turn_and_skew(tmp_path):
    import cv2

    make_page(tmp_path, degrade=Degrade.none())
    ref = cv2.resize(np.asarray(Image.open(tmp_path / "clean.png")), None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    h, w = ref.shape
    skew = cv2.getRotationMatrix2D((w / 2, h / 2), 0.8, 1.0)
    skew[:, 2] += (6, -4)
    moved = cv2.warpAffine(ref, skew, (w, h), borderValue=255)
    turned = np.ascontiguousarray(np.rot90(moved, -1))  # stored a quarter turn off, like the corpus scan

    out, info = align(ref, turned)
    assert info["aligned"] and info["turned_deg"] == 90
    assert abs(abs(info["rotation_deg"]) - 0.8) < 0.1
    assert np.abs(out.astype(int) - ref).mean() < np.abs(moved.astype(int) - ref).mean() / 3
