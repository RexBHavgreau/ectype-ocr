"""The training pool: everything the OCR models learn from, kept per language in the user's folder.

One pool for each language (`eng`, `heb`, `grc`...), shared by every project and by both
engines, so that what is learned from one book carries over to the next:
- corrections made in Review, from every project ("correction"): lines of the main language,
  and words of other scripts (a Hebrew word in an English line goes to the Hebrew pool);
- lines set from known text in the user's fonts ("synthetic", `synthtrain.py`);
- lines of scanned editions matched to their known text ("aligned", `align.py`);
- lines the OCR read with every word at confidence 90 or more ("confident"), a share of each
  project's, so that training on corrections doesn't unlearn what the model already reads well.
  They are only ever trained on, never used to test.

    <user folder>/pool/<language>/index.json        id -> {book, unit, source, origin, split}
    <user folder>/pool/<language>/books.json        book -> {fingerprint, source, name}
    <user folder>/pool/<language>/<book>/<id>.png   the image, with <id>.gt.txt beside it

Each item is either for training or kept aside for testing ("split"): a fifth of each book's
items, chosen by their id so it never changes; for generated lines, the fonts held out.
Models are only ever replaced by ones that do better on the items kept aside (`train.py`).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .resources import user_data_dir

SOURCES = ("correction", "synthetic", "aligned", "confident")
CONFIDENT_MAX = 300  # confidently read lines kept from each project


@dataclass
class Item:
    id: str
    lang: str
    book: str
    unit: str  # "line" or "word"
    source: str
    split: str  # "train" or "test"
    png: Path
    text: str


def root() -> Path:
    return user_data_dir() / "pool"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "book"


def key_for(code: str) -> str:
    """The pool a chosen OCR language learns from: a Tesseract language's own (its stock code,
    for a model trained on it), a Kraken model's by its script (Hebrew -> heb)."""
    from .kraken import installed_models, is_model, main_script
    from .scripts import SUGGEST

    if is_model(code):
        model = installed_models().get(code, {})
        base = model.get("base") or code
        script = main_script(base) or main_script(code) or "Latin"
        langs = [lang for lang in model.get("languages") or [] if len(lang) == 3]
        return SUGGEST.get(script) or (langs[0] if langs else "eng")
    from .train import trained_models

    return trained_models().get(code, {}).get("base", code)


def _dir(lang: str) -> Path:
    return root() / lang


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def index(lang: str) -> dict:
    return _load(_dir(lang) / "index.json")


def books(lang: str) -> dict:
    return _load(_dir(lang) / "books.json")


def set_book(lang: str, book: str, **info) -> None:
    path = _dir(lang) / "books.json"
    data = _load(path)
    data[book] = {**data.get(book, {}), **info}
    _save(path, data)


def item_id(image: bytes, text: str) -> str:
    return hashlib.sha1(image + b"\0" + text.encode("utf-8")).hexdigest()[:20]


def _split(item: str) -> str:
    return "test" if int(item[:8], 16) % 5 == 0 else "train"


class Writer:
    """Adds items to one language's pool, saving the index once at the end."""

    def __init__(self, lang: str):
        self.lang = lang
        self.path = _dir(lang) / "index.json"
        self.data = _load(self.path)
        self.by_origin = {m["origin"]: i for i, m in self.data.items() if m.get("origin")}
        self.added = 0

    def add(self, png: Path | bytes, text: str, book: str, unit: str, source: str, origin: str = "",
            split: str | None = None) -> str | None:
        """Add an image and its text. An item from the same origin (the same line of the same
        project, corrected again) replaces the older one. Returns the id, or None if empty."""
        text = text.strip()
        if not text:
            return None
        stamp = None
        if not isinstance(png, bytes):
            st = Path(png).stat()
            stamp = f"{st.st_size}:{st.st_mtime_ns}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]}"
            known = self.by_origin.get(origin) if origin else None
            if known and self.data.get(known, {}).get("stamp") == stamp:
                return known  # unchanged since it was added
        image = png if isinstance(png, bytes) else Path(png).read_bytes()
        new = item_id(image, text)
        old = self.by_origin.get(origin) if origin else None
        if old == new or new in self.data:
            return new
        if old:
            self.remove(old)
        folder = _dir(self.lang) / slug(book)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{new}.png").write_bytes(image)
        (folder / f"{new}.gt.txt").write_text(text + "\n", encoding="utf-8")
        self.data[new] = {"book": book, "unit": unit, "source": source, "origin": origin,
                          "split": "train" if source == "confident" else split or _split(new),
                          **({"stamp": stamp} if stamp else {})}
        if origin:
            self.by_origin[origin] = new
        self.added += 1
        return new

    def remove(self, item: str) -> None:
        meta = self.data.pop(item, None)
        if meta:
            folder = _dir(self.lang) / slug(meta["book"])
            for suffix in (".png", ".gt.txt"):
                (folder / f"{item}{suffix}").unlink(missing_ok=True)

    def remove_book(self, book: str, source: str | None = None) -> int:
        gone = [i for i, m in self.data.items() if m["book"] == book and (source is None or m["source"] == source)]
        for i in gone:
            self.remove(i)
        return len(gone)

    def close(self) -> None:
        _save(self.path, self.data)
        self.by_origin = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def items(lang: str, split: str | None = None, sources: tuple[str, ...] = SOURCES) -> list[Item]:
    out = []
    for i, m in index(lang).items():
        if (split is None or m["split"] == split) and m["source"] in sources:
            png = _dir(lang) / slug(m["book"]) / f"{i}.png"
            gt = png.with_suffix("").with_suffix(".gt.txt")
            if png.is_file() and gt.is_file():
                out.append(Item(i, lang, m["book"], m["unit"], m["source"], m["split"], png,
                                gt.read_text(encoding="utf-8").strip()))
    return out


def summary(lang: str) -> dict:
    """Counts by book and source, for reports."""
    out: dict = {}
    for m in index(lang).values():
        book = out.setdefault(m["book"], {"source": m["source"], "train": 0, "test": 0})
        book[m["split"]] += 1
    return out


def languages() -> list[str]:
    return sorted(p.name for p in root().glob("*") if (p / "index.json").is_file()) if root().is_dir() else []


def remove_language(lang: str) -> None:
    shutil.rmtree(_dir(lang), ignore_errors=True)


def sync_workspace(workspace: Path, book: str, codes: list[str]) -> dict[str, int]:
    """Copy a project's corrections into the pools: its corrected lines to the main language's
    pool (codes[0]), its corrected words in other scripts to the pool of the chosen language
    covering their script, and some of its confidently read lines to the main pool. The book's
    typeface fingerprints go with them (families.py). Returns how many items each pool gained."""
    from .scripts import in_script, script_of

    workspace = Path(workspace)
    folder = workspace / "training"
    gained: dict[str, int] = {}
    if not codes:
        return gained
    main = key_for(codes[0])
    lines = sorted(folder.glob("*.gt.txt")) if folder.is_dir() else []
    with Writer(main) as w:
        for gt in lines:
            png = gt.with_name(gt.name[: -len(".gt.txt")] + ".png")
            if png.is_file():
                w.add(png, gt.read_text(encoding="utf-8"), book, "line", "correction",
                      origin=f"{workspace.resolve()}::{gt.name}")
        for png_bytes, text, origin in _confident_lines(workspace):
            w.add(png_bytes, text, book, "line", "confident", origin=origin)
        gained[main] = w.added
    others = {}
    for code in codes[1:]:
        script = script_of(code)
        if script != "Latin":
            others.setdefault(script, key_for(code))
    words = sorted((folder / "words").glob("*.gt.txt")) if (folder / "words").is_dir() else []
    writers = {}
    try:
        for gt in words:
            text = gt.read_text(encoding="utf-8")
            png = gt.with_name(gt.name[: -len(".gt.txt")] + ".png")
            script = next((s for s in others if in_script(text, s)), None)
            if script is None or not png.is_file():
                continue
            lang = others[script]
            w = writers.setdefault(lang, Writer(lang))
            w.add(png, text, book, "word", "correction", origin=f"{workspace.resolve()}::words/{gt.name}")
    finally:
        for lang, w in writers.items():
            w.close()
            gained[lang] = gained.get(lang, 0) + w.added
    _fingerprint(workspace, book, codes, {main, *others.values()})
    return gained


def _fingerprint(workspace: Path, book: str, codes: list[str], langs: set[str]) -> None:
    from .families import book_fingerprints
    from .scripts import script_of

    try:
        prints = book_fingerprints(workspace, codes)
    except Exception:  # a fingerprint helps choose models; reading and training do without
        return
    for lang in langs:
        fp = prints.get(script_of(lang))
        if fp:
            set_book(lang, book, fingerprint=fp, source="correction")


def _confident_lines(workspace: Path, limit: int = CONFIDENT_MAX):
    """Lines of the project the OCR read with every word at 90 or more: (png bytes, text, origin)."""
    import io
    import random

    from PIL import Image

    from .ocr import OcrPage

    found = []
    for path in sorted((workspace / "ocr").glob("*.ocr.json")):
        page = path.name[: -len(".ocr.json")]
        try:
            ocr = OcrPage.from_json(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        for li, line in enumerate(ocr.lines):
            if len(line.words) >= 3 and all(w.conf >= 90 for w in line.words):
                found.append((page, li, line))
    random.Random(0).shuffle(found)
    for page, li, line in found[:limit]:
        png = workspace / "pages" / f"{page}.png"
        if not png.is_file():
            continue
        with Image.open(png) as image:
            x0, y0, x1, y1 = line.box
            pad = max(4, (y1 - y0) // 4)
            crop = image.crop((max(0, x0 - 10), max(0, y0 - pad), min(image.width, x1 + 10), min(image.height, y1 + pad)))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
        yield buf.getvalue(), " ".join(w.text for w in line.words), f"{workspace.resolve()}::confident/{page}-{li}"
