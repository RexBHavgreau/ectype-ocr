"""Training lines set from known text in the user's fonts: no correcting by hand.

    ectype train-synthetic --fonts <folder> --script hebrew greek

For each font in the folder (and its subfolders) that has the script's letters:
1. Passages of the known text (`texts.py`: the Hebrew Bible, the Greek New Testament) are cut
   into lines of varied length and set in the font at several sizes, right to left for
   Hebrew (Pillow's raqm layout, which places the vowel points).
2. Each line is made to look scanned (`degrade`): a slight blur, ink lighter or heavier,
   paper noise, specks, a small tilt.
3. The image and its exact text go into the language's training pool (`pool.py`) as the
   font's "book", with the font's typeface fingerprint (`families.py`), so fonts and real
   books fall into typeface families together.

A fifth of the fonts (at least one, given two or more) is kept aside entirely: their lines
are only used to test, so the check of a new model is on type it never saw. Running again
replaces each font's lines; fonts that haven't changed are skipped.

Fonts are read where they are and never copied; only the generated images go into the pool.
"""

from __future__ import annotations

import hashlib
import io
import random
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from . import pool, texts

SCRIPTS = {"hebrew": ("heb", "hebrew-bible", "Hebrew"), "greek": ("grc", "greek-nt", "Greek")}
VERSION = 1  # of the generation: lines made by an older one are made again
FONT_SUFFIXES = (".ttf", ".otf", ".ttc")


@dataclass
class FontInfo:
    path: Path
    name: str
    index: int = 0  # face in a collection


def fonts_in(folder: Path) -> list[FontInfo]:
    from fontTools.ttLib import TTCollection, TTFont

    out = []
    for path in sorted(Path(folder).rglob("*")):
        if path.suffix.lower() not in FONT_SUFFIXES:
            continue
        try:
            faces = TTCollection(str(path)).fonts if path.suffix.lower() == ".ttc" else [TTFont(str(path), lazy=True)]
        except Exception:
            continue
        for i, face in enumerate(faces):
            try:
                names = face["name"]
                family = names.getDebugName(1) or path.stem
                style = names.getDebugName(2) or ""
            except Exception:
                family, style = path.stem, ""
            out.append(FontInfo(path, f"{family} {style}".strip(), i))
    return out


def covers(font: FontInfo, chars: set[str]) -> set[str]:
    """The characters of `chars` the font has glyphs for."""
    from fontTools.ttLib import TTFont

    try:
        cmap = TTFont(str(font.path), fontNumber=font.index, lazy=True).getBestCmap() or {}
    except Exception:
        return set()
    return {c for c in chars if ord(c) in cmap}


def lines_from(passages: list[str], count: int, rng: random.Random, words=(3, 9)) -> list[str]:
    """Lines of `count` passages' words, from 3 to 9 words long, from places spread through the text."""
    out = []
    while len(out) < count:
        verse = passages[rng.randrange(len(passages))].split()
        if not verse:
            continue
        n = rng.randint(*words)
        start = rng.randrange(max(1, len(verse) - n + 1))
        out.append(" ".join(verse[start:start + n]))
    return out


def degrade(img: Image.Image, rng: random.Random) -> Image.Image:
    """Make a clean line look scanned: tilt, blur, lighter or heavier ink, paper, specks."""
    if rng.random() < 0.5:
        img = img.rotate(rng.uniform(-0.6, 0.6), resample=Image.BICUBIC, expand=False, fillcolor=255)
    ink = rng.choice(("light", "normal", "normal", "heavy"))
    if ink == "heavy":
        img = img.filter(ImageFilter.MinFilter(3)) if rng.random() < 0.3 else img
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.1)))
    a = np.asarray(img).astype(float)
    if ink == "light":
        a = 255 - (255 - a) * rng.uniform(0.55, 0.8)
    elif ink == "heavy":
        a = np.clip((a - 128) * 1.4 + 110, 0, 255)
    paper = rng.uniform(225, 255)
    noise = np.random.default_rng(rng.randrange(2**32)).normal(0, rng.uniform(2, 9), a.shape)
    a = a * (paper / 255) + noise
    out = np.clip(a, 0, 255).astype(np.uint8)
    for _ in range(rng.randint(0, 6)):  # specks
        y, x = rng.randrange(out.shape[0]), rng.randrange(out.shape[1])
        out[max(0, y - 1):y + 1, max(0, x - 1):x + 1] = rng.randint(0, 120)
    return Image.fromarray(out)


def render(text: str, font: FontInfo, size: int, rtl: bool) -> Image.Image | None:
    face = ImageFont.truetype(str(font.path), size, index=font.index)
    direction = "rtl" if rtl else "ltr"
    try:
        width = int(face.getlength(text, direction=direction))
    except (KeyError, OSError, ValueError):
        return None
    pad = int(size * 0.6)
    img = Image.new("L", (width + 2 * pad, int(size * 2.0)), 255)
    ImageDraw.Draw(img).text((pad, int(size * 1.35)), text, font=face, fill=0, anchor="ls", direction=direction,
                             language="he" if rtl else None)
    box = Image.eval(img, lambda v: 255 - v).getbbox()
    if box is None:
        return None
    x0, y0, x1, y1 = box
    m = max(4, size // 5)
    return img.crop((max(0, x0 - m), max(0, y0 - m), min(img.width, x1 + m), min(img.height, y1 + m)))


def _stamp(font: FontInfo, lines: int) -> str:
    st = font.path.stat()
    return hashlib.sha1(f"{font.path.name}:{font.index}:{st.st_size}:{st.st_mtime_ns}:{lines}:{VERSION}".encode()).hexdigest()[:12]


def held_out(fonts: list[FontInfo]) -> set[str]:
    """The fonts kept aside for testing: every fifth by a fixed order, at least one of two or more."""
    if len(fonts) < 2:
        return set()
    order = sorted(fonts, key=lambda f: hashlib.sha1(f.name.encode()).hexdigest())
    return {f.name for f in order[: max(1, len(order) // 5)]}


def generate(fonts_folder: Path, scripts: list[str], lines_per_font: int = 400, seed: int = 0,
             progress: Callable[[str], None] | None = None) -> dict:
    """Generate lines in every font of the folder for each script; add them to the pools.
    Returns {"fonts": {script: [names]}, "skipped": {name: reason}, "added": {lang: n}}."""
    from .families import measure

    say = progress or (lambda msg: None)
    if not features.check("raqm") and "hebrew" in scripts:
        raise RuntimeError("Setting Hebrew needs Pillow's raqm layout (for right-to-left text and vowel points), "
                           "which this installation lacks.")
    fonts = fonts_in(fonts_folder)
    if not fonts:
        raise FileNotFoundError(f"No fonts (.ttf, .otf, .ttc) in {fonts_folder}.")
    report = {"fonts": {}, "skipped": {}, "added": {}}
    for script in scripts:
        lang, source, name = SCRIPTS[script]
        rtl = script == "hebrew"
        passages = texts.load(source, say)
        needed = {c for p in passages[:3000] for c in unicodedata.normalize("NFC", p) if not c.isspace()}
        letters = {c for c in needed if c.isalpha()}
        usable = []
        for font in fonts:
            have = covers(font, needed)
            if len(letters & have) < 0.9 * len(letters):
                continue  # not a font for this script
            usable.append((font, have))
        report["fonts"][script] = [f.name for f, _ in usable]
        test_fonts = held_out([f for f, _ in usable])
        books = pool.books(lang)
        with pool.Writer(lang) as w:
            for k, (font, have) in enumerate(usable, 1):
                book = f"font: {font.name}"
                stamp = _stamp(font, lines_per_font)
                if books.get(book, {}).get("stamp") == stamp:
                    say(f"{name}: {font.name} already done")
                    continue
                say(f"{name}: setting {lines_per_font} lines in {font.name} ({k} of {len(usable)})")
                w.remove_book(book, source="synthetic")
                rng = random.Random(f"{seed}:{font.name}")
                split = "test" if font.name in test_fonts else "train"
                made, samples = 0, []
                candidates = lines_from(passages, lines_per_font * 2, rng)
                for text in candidates:
                    if made >= lines_per_font:
                        break
                    if any(not c.isspace() and c not in have for c in text):
                        continue  # the font lacks a character of this line
                    size = rng.choice((30, 36, 42, 46, 50, 56))
                    img = render(text, font, size, rtl)
                    if img is None:
                        continue
                    if len(samples) < 60:
                        samples.append(np.asarray(img))
                    img = degrade(img, rng)
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    w.add(buf.getvalue(), text, book, "line", "synthetic", origin=f"synthetic::{book}::{made}", split=split)
                    made += 1
                report["added"][lang] = report["added"].get(lang, 0) + made
                pool.set_book(lang, book, source="synthetic", stamp=stamp, split=split, fingerprint=measure(samples))
                if made < lines_per_font // 2:
                    report["skipped"][font.name] = f"only {made} lines could be set"
    return report
