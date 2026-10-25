"""Spike: classify one scanned page with Leptonica's jbclass through ctypes and save the composites.

    python spikes/leptonica_jbclass.py "corpus/E J. Young Inspiration-1.pdf" --page 6 --half top --rotate 90

Writes reports/spike-jbclass/: the binarized page, a sheet of every class template
(averaged composite) sorted by instance count, and stats.json.
"""

from __future__ import annotations

import argparse
import math
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ectype import lept, report
from ectype.compare import Source


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, default=6, help="1-based page")
    ap.add_argument("--half", choices=["top", "bottom", "left", "right", "full"], default="top",
                    help="part of the displayed page to keep (the corpus spreads display as two stacked pages)")
    ap.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=90,
                    help="counter-clockwise rotation that makes the text upright")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--thresh", type=float, default=0.85)
    ap.add_argument("--weight", type=float, default=0.5)
    ap.add_argument("-o", "--output", default="reports/spike-jbclass")
    args = ap.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    gray = Source(Path(args.pdf), "scan").render(args.page - 1, args.dpi)
    h, w = gray.shape
    gray = {
        "top": gray[: h // 2], "bottom": gray[h // 2 :],
        "left": gray[:, : w // 2], "right": gray[:, w // 2 :], "full": gray,
    }[args.half]
    gray = np.ascontiguousarray(np.rot90(gray, args.rotate // 90))
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = binary == 0
    Image.fromarray(binary).save(out / "binarized.png")

    t0 = time.perf_counter()
    result = lept.classify([ink], thresh=args.thresh, weight=args.weight)
    seconds = time.perf_counter() - t0

    order = sorted(range(len(result.templates)), key=lambda k: -result.instance_counts[k])
    cell, cols = 64, 20
    rows = math.ceil(len(order) / cols)
    sheet = Image.new("L", (cols * cell, rows * cell), 255)
    draw = ImageDraw.Draw(sheet)
    for i, k in enumerate(order):
        t = result.templates[k]
        scale = min(1.0, (cell - 16) / max(t.shape))
        tile = Image.fromarray(t).resize((max(1, round(t.shape[1] * scale)), max(1, round(t.shape[0] * scale))))
        x, y = (i % cols) * cell, (i // cols) * cell
        sheet.paste(tile, (x + (cell - tile.width) // 2, y + 2))
        draw.text((x + 2, y + cell - 12), str(result.instance_counts[k]), fill=110)
    sheet.save(out / "templates.png")

    stats = {
        "leptonica": lept.version(),
        "page": args.page,
        "half": args.half,
        "rotate": args.rotate,
        "thresh": args.thresh,
        "weight": args.weight,
        "components": len(result.component_class),
        "classes": len(result.templates),
        "singletons": sum(1 for c in result.instance_counts if c == 1),
        "largest_classes": [result.instance_counts[k] for k in order[:20]],
        "template_dtype_levels": int(len(np.unique(np.concatenate([t.ravel() for t in result.templates])))),
        "seconds": round(seconds, 2),
    }
    report.write_json(out / "stats.json", stats)
    print(stats)


if __name__ == "__main__":
    main()
