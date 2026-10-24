from __future__ import annotations

import json

import cv2
import numpy as np
import pikepdf
import pytest
from PIL import Image

from ectype import preprocess as pre
from ectype.synth import Degrade, make_page

LEFT_SKEW, RIGHT_SKEW = 0.6, -0.4
GUTTER_OFFSET = 70  # fold sits this far right of centre


@pytest.fixture(scope="module")
def spread(tmp_path_factory):
    """Two synthetic pages as an off-centre spread with a fold shadow, each half skewed,
    stored upside down in a PDF whose /Rotate is wrong (like the corpus scan)."""
    root = tmp_path_factory.mktemp("spread")
    truths = []
    halves = []
    for side, skew in (("left", LEFT_SKEW), ("right", RIGHT_SKEW)):
        truths.append(make_page(root / side, degrade=Degrade.none(), point_size=10, page_size_pt=(396, 612)))
        page = np.asarray(Image.open(root / side / "clean.png"))
        h, w = page.shape
        m = cv2.getRotationMatrix2D((w / 2, h / 2), skew, 1.0)
        halves.append(cv2.warpAffine(page, m, (w, h), borderValue=255))
    h, w = halves[0].shape
    extra = 2 * GUTTER_OFFSET
    image = np.full((h, 2 * w + extra), 255, np.uint8)
    image[:, : w + extra] = np.minimum(image[:, : w + extra], np.pad(halves[0], ((0, 0), (0, extra)), constant_values=255))
    image[:, w + extra:] = halves[1]
    fold = w + GUTTER_OFFSET + 40
    xs = np.arange(image.shape[1])
    shadow = (120 * np.exp(-((xs - fold) / 18.0) ** 2)).astype(np.int16)
    image = np.clip(image.astype(np.int16) - shadow[None, :], 0, 255).astype(np.uint8)
    stored = np.ascontiguousarray(np.rot90(image, 2))

    pdf = pikepdf.new()
    xobj = pikepdf.Stream(pdf, stored.tobytes())
    xobj.Type, xobj.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    xobj.Width, xobj.Height, xobj.BitsPerComponent = stored.shape[1], stored.shape[0], 8
    xobj.ColorSpace = pikepdf.Name.DeviceGray
    wp, hp = stored.shape[1] * 72 / 300, stored.shape[0] * 72 / 300
    page = pdf.add_blank_page(page_size=(wp, hp))
    page.Resources.XObject = pikepdf.Dictionary(Im0=xobj)
    page.Contents = pikepdf.Stream(pdf, f"q {wp} 0 0 {hp} 0 0 cm /Im0 Do Q".encode())
    page.Rotate = 270
    path = root / "spread.pdf"
    pdf.save(path)
    return {"path": path, "image": image, "fold": fold, "truths": truths, "page_w": w, "extra": extra}


def test_turn_matrix_matches_rot90():
    img = np.zeros((5, 8), np.uint8)
    img[1, 6] = 255
    for cw in (0, 90, 180, 270):
        turned = pre.turn(img, cw)
        x, y, _ = pre.turn_matrix(cw, 8, 5) @ [6, 1, 1]
        assert turned[int(y), int(x)] == 255, cw


def test_orientation_and_gutter(spread):
    upside_down = np.ascontiguousarray(np.rot90(spread["image"], 2))
    rotate, conf = pre.detect_orientation(upside_down)
    assert rotate == 180 and conf > 2
    gutter = pre.find_gutter(spread["image"])
    assert abs(gutter.x - spread["fold"]) <= 8
    assert gutter.method == "fold shadow" and gutter.confidence > 0.5


def test_estimate_skew(spread):
    w = spread["page_w"]
    angle, gain = pre.estimate_skew(spread["image"][:, :w])
    assert abs(angle + LEFT_SKEW) <= 0.06, "the angle that levels the page undoes its skew"
    assert gain > 1


def test_preprocess_spread(spread, tmp_path):
    s = pre.preprocess(spread["path"], tmp_path)
    src = s["sources"][0]
    assert src["origin"] == "embedded image"
    assert src["rotate"] == 180, "orientation comes from the image, not the wrong /Rotate"
    assert abs(src["split_x"] - spread["fold"]) <= 8
    names = [p["name"] for p in s["pages"]]
    assert names == ["p001L", "p001R"]
    left, right = s["pages"]
    assert abs(left["skew_degrees"] + LEFT_SKEW) <= 0.06
    assert abs(right["skew_degrees"] + RIGHT_SKEW) <= 0.06

    # The stored transform carries a known glyph from the stored image to the output page.
    out = np.asarray(Image.open(tmp_path / right["file"]))
    char = spread["truths"][1]["lines"][3]["words"][1]["chars"][0]
    cx, cy = (char["box"][0] + char["box"][2]) / 2, (char["box"][1] + char["box"][3]) / 2
    w = spread["page_w"]
    # truth (clean right page) -> skewed half -> spread image -> stored (upside down) image
    m = cv2.getRotationMatrix2D((w / 2, spread["image"].shape[0] / 2), RIGHT_SKEW, 1.0)
    sx, sy = m @ [cx, cy, 1]
    sx += w + spread["extra"]
    H, W = spread["image"].shape
    stored_x, stored_y = W - 1 - sx, H - 1 - sy
    ox, oy = np.array(right["transform"]) @ [stored_x, stored_y, 1]
    # Output pixel should be where the clean page had that glyph (ink nearby).
    assert out[int(oy) - 3:int(oy) + 4, int(ox) - 3:int(ox) + 4].min() < 128
    assert abs(ox - cx - (right["size_px"][0] - w)) < 15

    sidecar = json.loads((tmp_path / "pages" / "p001R.json").read_text())
    assert sidecar["transform"] == right["transform"]
    assert (tmp_path / "preprocess" / "index.html").is_file()


def test_overrides_and_keep_spreads(spread, tmp_path):
    s = pre.preprocess(spread["path"], tmp_path / "a", overrides={"1": {"rotate": 180, "split": 1500, "skew": 0}})
    src = s["sources"][0]
    assert src["split_x"] == 1500 and set(src["overridden"]) == {"rotate", "split"}
    assert [p["skew_degrees"] for p in s["pages"]] == [0.0, 0.0]

    s = pre.preprocess(spread["path"], tmp_path / "b", keep_spreads=True)
    assert [p["name"] for p in s["pages"]] == ["p001"]
    s = pre.preprocess(spread["path"], tmp_path / "c", overrides={1: {"split": False}})
    assert [p["name"] for p in s["pages"]] == ["p001"]
