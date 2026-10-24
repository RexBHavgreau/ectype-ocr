"""Erase text from a page image, leaving paper, shadows and figures.

The mask is exactly the ink that became glyph units (plus specks, optionally),
dilated by a pixel or two to take the antialiased halo. Masked pixels are filled
from a paper estimate: a grayscale closing that removes strokes narrower than the
kernel while keeping large-scale tone (page shading, gutter shadow). Ink that
belongs to no character (figures, rules, ornaments) is never touched.
"""

from __future__ import annotations

import cv2
import numpy as np

from .segment import Segmentation


def text_mask(seg: Segmentation, labels: np.ndarray, include_specks: bool = True) -> np.ndarray:
    ids = [c for u in seg.units for c in u.components]
    if include_specks:
        ids += [x["id"] for x in seg.unassigned if x["reason"] in ("speck", "speck in word")]
    lut = np.zeros(int(labels.max()) + 1, bool)
    lut[[i for i in ids if i < len(lut)]] = True
    return lut[labels]


def paper_estimate(gray: np.ndarray, stroke_px: int) -> np.ndarray:
    """What the page would look like without strokes up to stroke_px wide."""
    k = max(5, int(stroke_px) | 1)
    closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    return cv2.GaussianBlur(closed, (0, 0), 1.5)


def erase_text(gray: np.ndarray, seg: Segmentation, labels: np.ndarray, x_size: float,
               dilate_px: int = 2, include_specks: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Return the background with text erased, and the mask that was filled."""
    mask = text_mask(seg, labels, include_specks)
    if dilate_px:
        mask = cv2.dilate(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate_px + 1,) * 2)).astype(bool)
    paper = paper_estimate(gray, round(0.6 * x_size))
    out = gray.copy()
    out[mask] = np.maximum(gray[mask], paper[mask])  # never darker than the scan
    return out, mask


def smooth_paper(background: np.ndarray, size: int = 5, keep_px: int = 3) -> np.ndarray:
    """Median-filter the paper grain (it costs JPEG bytes and carries nothing), leaving
    alone any ink still in the background (image-fallback words, figures) and a few
    pixels around it, so no stroke is eroded."""
    from .preprocess import binarize

    smoothed = cv2.medianBlur(background, size)
    ink = binarize(background) if background.min() < 128 else np.zeros(background.shape, bool)
    keep = cv2.dilate(ink.astype(np.uint8), np.ones((2 * keep_px + 1,) * 2, np.uint8)).astype(bool)
    return np.where(keep, background, smoothed)
