"""Fetch EB Garamond (SIL OFL 1.1) for the packaged app: the default substitute font.

    python packaging/fetch_fonts.py <out_dir>

Downloads the variable fonts from the Google Fonts repository and saves static
Regular and Italic instances (weight 400), plus the licence. If instancing fails,
the variable file itself is kept; its default instance is the Regular.
"""

from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/ebgaramond/"
FILES = {"EBGaramond[wght].ttf": "EBGaramond-Regular.ttf", "EBGaramond-Italic[wght].ttf": "EBGaramond-Italic.ttf"}


def fetch(name: str, dest: Path) -> None:
    url = BASE + urllib.request.quote(name)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                dest.write_bytes(r.read())
            return
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))


def main(out: Path) -> None:
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    out.mkdir(parents=True, exist_ok=True)
    fetch("OFL.txt", out / "EBGaramond-OFL.txt")
    for remote, static in FILES.items():
        raw = out / remote
        fetch(remote, raw)
        try:
            font = instantiateVariableFont(TTFont(str(raw)), {"wght": 400}, updateFontNames=True)
            font.save(str(out / static))
            raw.unlink()
            print(f"{static}: static instance at weight 400")
        except Exception as exc:  # keep the variable font; its default is the Regular
            raw.rename(out / static)
            print(f"{static}: kept the variable font ({exc})")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "packaging/build/fonts"))
