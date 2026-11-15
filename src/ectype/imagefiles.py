"""Reading and writing images with OpenCV whatever letters the path holds.

On Windows cv2.imread and cv2.imwrite take the path in the system's old code page, so a book
named with letters outside it ("Tratado de la Oración") failed: imread returned None (Beaux,
0.9.0: traced mode stopped at the glyph grouping). Python opens the file instead and OpenCV only
decodes or encodes the bytes.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def imread(path, flags: int = cv2.IMREAD_UNCHANGED) -> np.ndarray | None:
    """As cv2.imread: the image, or None when the file is missing or can't be decoded."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def imwrite(path, image: np.ndarray) -> bool:
    """As cv2.imwrite (the format from the extension), through a temporary file."""
    path = Path(path)
    ok, buf = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        return False
    part = path.with_name(path.name + ".part")
    buf.tofile(str(part))
    part.replace(path)
    return True
