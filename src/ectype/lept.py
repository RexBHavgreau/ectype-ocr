"""Minimal ctypes bindings to Leptonica's jbclass symbol classifier.

Only what clustering needs: build a correlation classifier, add 1-bpp pages,
and average every instance of each class into a grayscale composite template
(`jbAccumulateComposites` + `jbTemplatesFromComposites`, as jbig2enc does).
Images cross the boundary as PNG files written by Leptonica itself, which keeps
the binding free of Pix memory-layout assumptions. Requires the system
Leptonica library (BSD-2-Clause), e.g. `apt install libleptonica-dev`.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

JB_CONN_COMPS = 0
JB_CHARACTERS = 1
L_CLONE = 2
IFF_PNG = 3

c_pix_p = ctypes.c_void_p


class JbClasser(ctypes.Structure):
    """Mirror of `struct JbClasser` from leptonica/jbclass.h (stable since 1.6x)."""

    _fields_ = [
        ("safiles", ctypes.c_void_p),
        ("method", ctypes.c_int32),
        ("components", ctypes.c_int32),
        ("maxwidth", ctypes.c_int32),
        ("maxheight", ctypes.c_int32),
        ("npages", ctypes.c_int32),
        ("baseindex", ctypes.c_int32),
        ("nacomps", ctypes.c_void_p),
        ("sizehaus", ctypes.c_int32),
        ("rankhaus", ctypes.c_float),
        ("thresh", ctypes.c_float),
        ("weightfactor", ctypes.c_float),
        ("naarea", ctypes.c_void_p),
        ("w", ctypes.c_int32),
        ("h", ctypes.c_int32),
        ("nclass", ctypes.c_int32),
        ("keep_pixaa", ctypes.c_int32),
        ("pixaa", ctypes.c_void_p),
        ("pixat", ctypes.c_void_p),
        ("pixatd", ctypes.c_void_p),
        ("dahash", ctypes.c_void_p),
        ("nafgt", ctypes.c_void_p),
        ("ptac", ctypes.c_void_p),
        ("ptact", ctypes.c_void_p),
        ("naclass", ctypes.c_void_p),
        ("napage", ctypes.c_void_p),
        ("ptaul", ctypes.c_void_p),
        ("ptall", ctypes.c_void_p),
    ]


_lib = None


def lib() -> ctypes.CDLL:
    """Load Leptonica once and declare the functions we call."""
    global _lib
    if _lib is not None:
        return _lib
    name = ctypes.util.find_library("leptonica") or ctypes.util.find_library("lept")
    if not name:
        raise OSError("Leptonica not found; install it (e.g. apt install libleptonica-dev)")
    L = ctypes.CDLL(name)
    vp, i32, f32, cp = ctypes.c_void_p, ctypes.c_int32, ctypes.c_float, ctypes.c_char_p
    sigs = {
        "getLeptonicaVersion": (ctypes.c_void_p, []),
        "pixRead": (vp, [cp]),
        "pixWrite": (i32, [cp, vp, i32]),
        "pixDestroy": (None, [ctypes.POINTER(vp)]),
        "pixGetDepth": (i32, [vp]),
        "pixaGetCount": (i32, [vp]),
        "pixaGetPix": (vp, [vp, i32, i32]),
        "pixaDestroy": (None, [ctypes.POINTER(vp)]),
        "numaGetCount": (i32, [vp]),
        "numaGetIValue": (i32, [vp, i32, ctypes.POINTER(i32)]),
        "numaDestroy": (None, [ctypes.POINTER(vp)]),
        "ptaDestroy": (None, [ctypes.POINTER(vp)]),
        "jbCorrelationInit": (ctypes.POINTER(JbClasser), [i32, i32, i32, f32, f32]),
        "jbAddPage": (i32, [ctypes.POINTER(JbClasser), vp]),
        "jbAccumulateComposites": (vp, [vp, ctypes.POINTER(vp), ctypes.POINTER(vp)]),
        "jbTemplatesFromComposites": (vp, [vp, vp]),
        "jbClasserDestroy": (None, [ctypes.POINTER(ctypes.POINTER(JbClasser))]),
        "lept_free": (None, [vp]),
    }
    for fn, (restype, argtypes) in sigs.items():
        f = getattr(L, fn)
        f.restype, f.argtypes = restype, argtypes
    _lib = L
    return L


def version() -> str:
    L = lib()
    ptr = L.getLeptonicaVersion()
    text = ctypes.cast(ptr, ctypes.c_char_p).value.decode()
    L.lept_free(ptr)
    return text


@dataclass
class Classification:
    """Result of classifying connected components across pages."""

    templates: list[np.ndarray]  # one 8-bit grayscale average per class (0 = ink)
    instance_counts: list[int]
    component_class: list[int]  # class id of each component, in page order
    components_per_page: list[int]


def classify(
    pages: list[np.ndarray],
    thresh: float = 0.85,
    weight: float = 0.5,
    max_size: int = 200,
) -> Classification:
    """Cluster connected components of binary pages (True/nonzero = ink) by correlation."""
    L = lib()
    classer = L.jbCorrelationInit(JB_CONN_COMPS, max_size, max_size, thresh, weight)
    if not classer:
        raise RuntimeError("jbCorrelationInit failed")
    with tempfile.TemporaryDirectory(prefix="ectype-lept-") as tmp:
        tmpdir = Path(tmp)
        comps_per_page = []
        for i, page in enumerate(pages):
            path = tmpdir / f"page{i}.png"
            # 1-bit PNG, black ink; Leptonica reads it as 1 bpp with 1 = ink.
            Image.fromarray(np.where(page.astype(bool), 0, 255).astype(np.uint8)).convert("1").save(path)
            pix = c_pix_p(L.pixRead(str(path).encode()))
            if not pix or L.pixGetDepth(pix) != 1:
                raise RuntimeError(f"page {i}: Leptonica did not read a 1-bpp image")
            before = classer.contents.baseindex
            if L.jbAddPage(classer, pix) != 0:
                raise RuntimeError(f"jbAddPage failed on page {i}")
            comps_per_page.append(classer.contents.baseindex - before)
            L.pixDestroy(ctypes.byref(pix))

        na = c_pix_p()
        pta = c_pix_p()
        composites = c_pix_p(L.jbAccumulateComposites(classer.contents.pixaa, ctypes.byref(na), ctypes.byref(pta)))
        templates_pixa = c_pix_p(L.jbTemplatesFromComposites(composites, na))
        templates = []
        for k in range(L.pixaGetCount(templates_pixa)):
            pix = c_pix_p(L.pixaGetPix(templates_pixa, k, L_CLONE))
            path = tmpdir / f"t{k}.png"
            L.pixWrite(str(path).encode(), pix, IFF_PNG)
            L.pixDestroy(ctypes.byref(pix))
            # Leptonica's templates hold ink coverage (255 = always ink); flip to 0 = ink.
            templates.append(255 - np.asarray(Image.open(path).convert("L")))

        counts = []
        value = ctypes.c_int32()
        for k in range(L.numaGetCount(na)):
            L.numaGetIValue(na, k, ctypes.byref(value))
            counts.append(value.value)
        classes = []
        naclass = classer.contents.naclass
        for k in range(L.numaGetCount(naclass)):
            L.numaGetIValue(naclass, k, ctypes.byref(value))
            classes.append(value.value)

        L.pixaDestroy(ctypes.byref(templates_pixa))
        L.pixaDestroy(ctypes.byref(composites))
        L.numaDestroy(ctypes.byref(na))
        L.ptaDestroy(ctypes.byref(pta))
        L.jbClasserDestroy(ctypes.byref(classer))
    return Classification(templates, counts, classes, comps_per_page)
