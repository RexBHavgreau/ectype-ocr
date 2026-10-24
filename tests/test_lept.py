from __future__ import annotations

import numpy as np
import pytest

lept = pytest.importorskip("ectype.lept")

try:
    lept.lib()
except OSError:
    pytest.skip("Leptonica not installed", allow_module_level=True)


def _page() -> np.ndarray:
    """Ten copies of one shape and three of another, with one-pixel jitter."""
    page = np.zeros((200, 400), bool)
    rng = np.random.default_rng(0)
    for i in range(10):
        x, y = 10 + 35 * i, 20 + int(rng.integers(0, 2))
        page[y:y + 20, x:x + 6] = True  # a stem
        page[y:y + 4, x:x + 16] = True  # with a bar: a "T"-like shape
    for i in range(3):
        x = 20 + 60 * i
        page[120:150, x:x + 30] = True
        page[128:142, x + 8:x + 22] = False  # a square ring
    return page


def test_classify_groups_and_averages():
    result = lept.classify([_page()], thresh=0.8)
    assert result.components_per_page == [13]
    assert sorted(result.instance_counts) == [3, 10]
    assert len(result.component_class) == 13
    big = result.templates[result.instance_counts.index(10)]
    assert big.dtype == np.uint8
    assert big.min() == 0, "0 means ink in every instance"
    assert big.max() == 255


def test_version():
    assert lept.version().startswith("leptonica-")
