"""Manuscript Mode: handwritten books read with Kraken, by hands the user names and trains.

A book in Manuscript Mode (`Settings.script == "manuscript"`) isn't read by Tesseract and has
no traced type. Its pages go through Kraken's own line-finding (its baseline segmenter, the
model that comes with Kraken, or one trained on the book's corrected lines), and each line is
read with the hands chosen for the book's languages:

- A **hand** is a Kraken recognition model with a name the user gives it ("Sephardi square",
  "Masorah hand"), for one language, kept in the user's folder (`hands/`). A new hand starts
  as a copy of an installed handwriting model or of another hand, and is trained on the
  lines corrected in its reading.
- With several hands chosen, each line is read either by every hand ("every") or by the
  first, and by the others only when the first is unsure of it ("unsure": whole lines, since
  Kraken reads by lines). The most confident reading is kept; where hands read a line
  differently, it is flagged for Review.
- No known text (the Leningrad Codex, the SBL Greek NT) is ever used: it would bias the
  reading towards the familiar text and hide real variants.

Files in the workspace (`ms/`):

    <page>.lines.json      the lines found: baseline, outline, region; "edited" when the user
                           changed them in Review (they then train the line-finding)
    <page>.read.json       each line's readings by hand, the one kept, and its flags
    corrections/<page>.json   line key -> corrected text (the key follows the line's outline)
    segmenter.safetensors  the line-finding trained on this book, when there is one

`ocr/<page>.ocr.json` is written from the readings and corrections, so the searchable PDF
(textlayer) is made as for a printed book.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable

from .report import write_json
from .resources import user_data_dir

# The languages Manuscript Mode offers (its list doesn't overlap print's: names stay short).
LANGUAGES = {"heb": "Hebrew", "grc": "Greek", "syr": "Syriac", "ara": "Arabic", "lat": "Latin"}
RTL = {"heb", "syr", "ara"}
EVERY, UNSURE = "every", "unsure"
LINE_SURE = 85.0  # a line read with less confidence (mean of its characters) is unsure
CHAR_SURE = 50.0  # ... or with a character under this
SAMPLE_SKIP = 0.1  # "Train for this book" picks pages away from the first and last tenth


# -- hands --------------------------------------------------------------------------


def hands_dir() -> Path:
    return user_data_dir() / "hands"


def _hands_path() -> Path:
    return hands_dir() / "hands.json"


_HANDS_LOCK = threading.RLock()


def hands(lang: str | None = None) -> dict[str, dict]:
    """id -> {"name", "lang", "file", "started_from", "created", "lines", "trained", "errors"}"""
    try:
        data = json.loads(_hands_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {k: h for k, h in data.items() if (lang is None or h.get("lang") == lang) and Path(h.get("file", "")).is_file()}


def _save_hand(hand_id: str, info: dict) -> None:
    with _HANDS_LOCK:
        try:
            data = json.loads(_hands_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[hand_id] = info
        _hands_path().parent.mkdir(parents=True, exist_ok=True)
        _hands_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def hand_name(hand_id: str) -> str:
    return hands().get(hand_id, {}).get("name", hand_id)


def starting_points(lang: str) -> list[tuple[str, str]]:
    """What a new hand can start from: (label, model file) of the installed Kraken models for
    the language's script that aren't of print, then the language's hands."""
    from . import kraken
    from .scripts import script_of

    script = script_of(lang)
    out = []
    for code, m in kraken.installed_models().items():
        if m.get("book") or script not in kraken.scripts_of(m):
            continue
        if kraken.printed({"summary": m.get("title", ""), "keywords": []}) is True:
            continue
        out.append((m.get("title") or code, m["file"]))
    out += [(f"{h['name']} (a hand)", h["file"]) for h in hands(lang).values()]
    return out


def new_hand(name: str, lang: str, start: str) -> str:
    """A hand named by the user, starting as a copy of a model file. Returns its id."""
    name = name.strip()
    if not name:
        raise ValueError("A hand needs a name.")
    if any(h["name"].casefold() == name.casefold() for h in hands(lang).values()):
        raise ValueError(f"There is already a hand called {name!r} for {LANGUAGES.get(lang, lang)}.")
    hand_id = f"{lang}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:40] or 'hand'}-{int(time.time())}"
    folder = hands_dir() / hand_id
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / ("model" + Path(start).suffix)
    shutil.copyfile(start, file)
    started = next((label for label, f in starting_points(lang) if f == start), Path(start).name)
    _save_hand(hand_id, {"name": name, "lang": lang, "file": str(file), "started_from": started,
                         "created": time.strftime("%Y-%m-%d %H:%M"), "lines": 0, "trained": None, "errors": None})
    return hand_id


def remove_hand(hand_id: str) -> None:
    with _HANDS_LOCK:
        try:
            data = json.loads(_hands_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.pop(hand_id, None) is not None:
            _hands_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(hands_dir() / hand_id, ignore_errors=True)


def hand_lines_dir(hand_id: str) -> Path:
    """The corrected lines a hand trains on (every book's)."""
    return hands_dir() / hand_id / "lines"


# -- the book's choices -------------------------------------------------------------


def chosen_hands(settings) -> list[str]:
    """The book's hands in reading order: its languages' in turn, each language's as chosen."""
    have = hands()
    return [h for lang in settings.ms_languages for h in settings.hands.get(lang, []) if h in have]


def direction(settings) -> str:
    first = settings.ms_languages[0] if settings.ms_languages else "heb"
    return "horizontal-rl" if first in RTL else "horizontal-lr"


# -- geometry ------------------------------------------------------------------------


def line_key(line: dict) -> str:
    """A line's identity: its outline and baseline (a correction belongs to the line it was made on)."""
    raw = json.dumps([line["baseline"], line["boundary"]])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def bbox(points) -> list[int]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1]


def _rect(box) -> list[list[int]]:
    x0, y0, x1, y1 = (int(v) for v in box)
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def _clip(poly: list, x: float, keep_left: bool) -> list:
    """A polygon cut by the vertical line at x, the left or right part kept (Sutherland-Hodgman)."""
    inside = (lambda p: p[0] <= x) if keep_left else (lambda p: p[0] >= x)
    out = []
    pts = poly[:-1] if len(poly) > 1 and poly[0] == poly[-1] else poly
    for i, cur in enumerate(pts):
        prev = pts[i - 1]
        if inside(cur):
            if not inside(prev):
                out.append(_cross(prev, cur, x))
            out.append(list(cur))
        elif inside(prev):
            out.append(_cross(prev, cur, x))
    if out:
        out.append(list(out[0]))
    return out


def _cross(a, b, x) -> list[int]:
    t = (x - a[0]) / (b[0] - a[0]) if b[0] != a[0] else 0.0
    return [int(round(x)), int(round(a[1] + t * (b[1] - a[1])))]


def _baseline_part(baseline: list, x: float, keep_left: bool) -> list:
    pts = sorted(baseline, key=lambda p: p[0])
    out = [list(p) for p in pts if (p[0] <= x if keep_left else p[0] >= x)]
    for a, b in zip(pts, pts[1:]):
        if (a[0] - x) * (b[0] - x) < 0:
            out.append(_cross(a, b, x))
    out.sort(key=lambda p: p[0])
    return out if len(out) >= 2 else ([[int(x) - 1, pts[0][1]], [int(x), pts[0][1]]] if keep_left else
                                      [[int(x), pts[-1][1]], [int(x) + 1, pts[-1][1]]])


def _fit_baseline(baseline: list, box: list[int]) -> list[float]:
    """Ectype's baseline (slope, offset from the box's bottom at its left edge) from Kraken's polyline."""
    import numpy as np

    xs = np.array([p[0] for p in baseline], float)
    ys = np.array([p[1] for p in baseline], float)
    slope = float(np.polyfit(xs, ys, 1)[0]) if len(xs) >= 2 and xs.max() > xs.min() else 0.0
    y_at_x0 = float(ys.mean() + slope * (box[0] - xs.mean()))
    return [round(slope, 5), round(y_at_x0 - box[3], 2)]


# -- workspace files -------------------------------------------------------------------


def _dir(ws: Path) -> Path:
    return Path(ws) / "ms"


def _stamp(path: Path) -> str:
    st = Path(path).stat()
    return f"{st.st_size}-{int(st.st_mtime)}"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def lines_of(ws: Path, page: str) -> dict:
    return _load(_dir(ws) / f"{page}.lines.json", {"lines": [], "regions": []})


def readings_of(ws: Path, page: str) -> dict:
    return _load(_dir(ws) / f"{page}.read.json", {"lines": []})


def corrections_of(ws: Path, page: str) -> dict[str, str]:
    return _load(_dir(ws) / "corrections" / f"{page}.json", {})


def _save_lines(ws: Path, page: str, data: dict) -> None:
    _dir(ws).mkdir(parents=True, exist_ok=True)
    write_json(_dir(ws) / f"{page}.lines.json", data)


def segmenter_file(ws: Path) -> Path | None:
    f = _dir(ws) / "segmenter.safetensors"
    return f if f.is_file() else None


# -- reading ---------------------------------------------------------------------------


def cores_plan(all_cores: bool, pages: int) -> tuple[int, int]:
    """(Kraken processes, threads each). All cores: a process for every four cores, each
    with its share; otherwise one process with half of them, leaving the machine free."""
    cores = os.cpu_count() or 2
    if not all_cores:
        return 1, max(1, cores // 2)
    procs = max(1, min(pages, cores // 4))
    return procs, max(1, cores // procs)


def _unsure(reading: dict) -> bool:
    return reading.get("conf", 0) < LINE_SURE or any(c[2] < CHAR_SURE for c in reading.get("chars", []))


def read_page(ws: Path, page: str, image: Path, hand_ids: list[str], how: str, direction: str, session,
              resegment: bool = False) -> dict:
    """Find a page's lines (unless found already and the page image is unchanged, or the
    user edited them) and read each with the hands. Readings already made of the same line
    by the same hand model are kept. Returns the page's read.json."""
    ws = Path(ws)
    have = hands()
    found = lines_of(ws, page)
    model = segmenter_file(ws)
    finder = _stamp(model) if model else "kraken"
    # Lines are found again when the page image changed, or when the book's line-finding was
    # trained since; not on a page whose lines were changed or corrected (that work is kept).
    untouched = not found.get("edited") and not corrections_of(ws, page)
    if resegment or not found["lines"] or (untouched and (found.get("image") != _stamp(image)
                                                          or found.get("segmenter") != finder)):
        seg = session.segment(image, direction, str(model) if model else None)
        found = {"image": _stamp(image), "lines": seg["lines"], "regions": seg.get("regions", []),
                 "segmenter": finder, "edited": False}
        _save_lines(ws, page, found)
    old = {l["key"]: l for l in readings_of(ws, page)["lines"]}
    stamps = {h: _stamp(Path(have[h]["file"])) for h in hand_ids if h in have}
    lines = []
    for line in found["lines"]:
        key = line_key(line)
        prev = old.get(key, {})
        readings = {h: r for h, r in prev.get("readings", {}).items() if r.get("model") == stamps.get(h)}
        lines.append({"key": key, "readings": readings})

    def read_with(hand: str, todo: list[int]) -> None:
        todo = [i for i in todo if hand not in lines[i]["readings"]]
        if not todo:
            return
        got = session.read_baselines(have[hand]["file"], image, direction, [found["lines"][i] for i in todo])
        for i, r in zip(todo, got):
            lines[i]["readings"][hand] = {**r, "model": stamps[hand]}

    order = [h for h in hand_ids if h in have]
    if order:
        read_with(order[0], list(range(len(lines))))
        for hand in order[1:]:
            if how == EVERY:
                read_with(hand, list(range(len(lines))))
            else:
                read_with(hand, [i for i, l in enumerate(lines) if order[0] not in l["readings"]
                                 or _unsure(l["readings"][order[0]])])
    for l in lines:
        rs = {h: r for h, r in l["readings"].items() if h in order}
        if rs:
            best = max(rs, key=lambda h: rs[h].get("conf", 0))
            l.update(hand=best, text=rs[best]["text"], conf=rs[best].get("conf", 0),
                     disagree=len({_norm(r["text"]) for r in rs.values()}) > 1)
        else:
            l.update(hand=None, text="", conf=0.0, disagree=False)
    data = {"lines": lines, "hands": order, "how": how}
    write_json(_dir(ws) / f"{page}.read.json", data)
    return data


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", " ".join(text.split()))


def read_workspace(ws: Path, settings, on_page: Callable[[int, int], None] | None = None,
                   all_cores: bool = True, sessions: Callable[[int], object] | None = None,
                   pages: list[str] | None = None) -> dict:
    """Read every page (or the pages given) with the book's hands, side by side in several
    Kraken processes when all cores are used; then write each page's OCR file. sessions(threads)
    makes a Kraken session (kraken.Session by default)."""
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue

    ws = Path(ws)
    hand_ids = chosen_hands(settings)
    if not hand_ids:
        raise RuntimeError("No hand is chosen for this manuscript. In the Languages menu, choose a hand under a "
                           "language (or make one with New hand…).")
    all_pages = sorted(p.stem for p in (ws / "pages").glob("p*.png"))
    todo = pages if pages is not None else all_pages
    procs, threads = cores_plan(all_cores, len(todo))
    if sessions is None:
        from .kraken import Session

        sessions = (lambda n: Session(threads=n))
    pool: Queue = Queue()
    made = [sessions(threads) for _ in range(max(1, procs))]
    for s in made:
        pool.put(s)
    done, lock = [0], threading.Lock()
    flagged = {"unsure": 0, "disagree": 0, "lines": 0}

    def one(page: str) -> None:
        s = pool.get()
        try:
            data = read_page(ws, page, ws / "pages" / f"{page}.png", hand_ids, settings.hand_reading,
                             direction(settings), s)
        finally:
            pool.put(s)
        write_ocr(ws, page, settings)
        with lock:
            done[0] += 1
            flagged["lines"] += len(data["lines"])
            flagged["unsure"] += sum(1 for l in data["lines"] if l["hand"] and _unsure(l["readings"][l["hand"]]))
            flagged["disagree"] += sum(1 for l in data["lines"] if l["disagree"])
            if on_page:
                on_page(done[0], len(todo))

    try:
        if len(made) == 1:
            for page in todo:
                one(page)
        else:
            with ThreadPoolExecutor(max_workers=len(made)) as ex:
                list(ex.map(one, todo))
    finally:
        for s in made:
            close = getattr(s, "close", None)
            if close:
                close()
    return {"pages": len(todo), **flagged, "processes": len(made), "threads": threads}


def write_ocr(ws: Path, page: str, settings) -> Path:
    """The page's OCR file from its readings and corrections: each line's words with their
    letters' boxes (a corrected line's letters spread over the line), for the text layer."""
    from PIL import Image

    from .ocr import Char, Line, OcrPage
    from .report import image_dpi
    from .review import place_chars

    ws = Path(ws)
    found = lines_of(ws, page)
    read = {l["key"]: l for l in readings_of(ws, page)["lines"]}
    fixed = corrections_of(ws, page)
    rtl = direction(settings) == "horizontal-rl"
    im = Image.open(ws / "pages" / f"{page}.png")
    out = OcrPage(engine="kraken", lang="+".join(settings.ms_languages), size_px=list(im.size), dpi=image_dpi(im),
                  models=[f"hand={hand_name(h)}" for h in chosen_hands(settings)])
    for line in found["lines"]:
        key = line_key(line)
        r = read.get(key, {})
        box = bbox(line["boundary"])
        chars = [Char(c, list(b), float(conf)) for c, b, conf in (r.get("readings", {}).get(r.get("hand"), {}).get("chars") or [])]
        for c in chars:  # Kraken's cuts are narrow columns: the line's height
            c.box = [c.box[0], box[1], max(c.box[2], c.box[0] + 1), box[3]]
        text = r.get("text", "")
        if key in fixed:
            text = fixed[key]
            visual = list(reversed(chars)) if rtl else chars
            placed = place_chars(visual, text[::-1] if rtl else text, box)
            chars = list(reversed(placed)) if rtl else placed
        words = _words(chars, box)
        out.lines.append(Line(box=box, baseline=_fit_baseline(line["baseline"], box), x_size=None, ascenders=None,
                              descenders=None, words=words))
    (ws / "ocr").mkdir(parents=True, exist_ok=True)
    path = ws / "ocr" / f"{page}.ocr.json"
    write_json(path, out.to_json())
    return path


def _words(chars, box) -> list:
    from .ocr import Word

    words, cur = [], []

    def flush():
        if cur:
            words.append(Word(text="".join(c.text for c in cur), box=[min(c.box[0] for c in cur), box[1],
                                                                    max(c.box[2] for c in cur), box[3]],
                              conf=round(sum(c.conf for c in cur) / len(cur), 1), chars=list(cur)))
            cur.clear()

    for c in chars:
        if c.text.isspace():
            flush()
        else:
            cur.append(c)
    flush()
    return words


# -- Review: lines, their flags, corrections ---------------------------------------------


def page_lines(ws: Path, page: str) -> list[dict]:
    """Each line of a page for Review: {"index", "key", "boundary", "baseline", "box", "text",
    "hand", "conf", "unsure", "disagree", "readings": {hand name: text}, "corrected"}."""
    found = lines_of(ws, page)
    read = {l["key"]: l for l in readings_of(ws, page)["lines"]}
    fixed = corrections_of(ws, page)
    out = []
    for i, line in enumerate(found["lines"]):
        key = line_key(line)
        r = read.get(key, {})
        chosen = r.get("readings", {}).get(r.get("hand"), {})
        out.append({"index": i, "key": key, "boundary": line["boundary"], "baseline": line["baseline"],
                    "box": bbox(line["boundary"]), "text": fixed.get(key, r.get("text", "")),
                    "read": r.get("text", ""), "hand": r.get("hand"), "conf": r.get("conf", 0.0),
                    "unsure": bool(chosen) and _unsure(chosen) and key not in fixed,
                    "disagree": bool(r.get("disagree")) and key not in fixed,
                    "readings": {hand_name(h): x.get("text", "") for h, x in r.get("readings", {}).items()},
                    "corrected": key in fixed, "unread": not r})
    return out


def least_sure(ws: Path, pages: list[str], limit: int = 500) -> list[tuple[str, int, float]]:
    """(page, line, confidence) of the lines read least surely, across the pages, least sure
    first; lines hands disagree on count as least sure. Corrected lines are left out."""
    out = []
    for page in pages:
        for l in page_lines(ws, page):
            if l["corrected"] or l["unread"]:
                continue
            out.append((page, l["index"], -1.0 if l["disagree"] else l["conf"]))
    out.sort(key=lambda t: t[2])
    return out[:limit]


def correct_line(ws: Path, page: str, index: int, text: str, settings, book: str) -> dict:
    """A line's text corrected (or confirmed, unchanged): kept for the output and, as a
    picture of the line with its text, for training the hand that read it."""
    ws = Path(ws)
    lines = page_lines(ws, page)
    line = lines[index]
    path = _dir(ws) / "corrections" / f"{page}.json"
    data = corrections_of(ws, page)
    text = unicodedata.normalize("NFC", text.strip())
    data[line["key"]] = text
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)
    hand = line["hand"] or next(iter(chosen_hands(settings)), None)
    saved = save_training_line(ws, page, lines_of(ws, page)["lines"][index], text, hand, book) if hand else None
    write_ocr(ws, page, settings)
    return {"old": line["read"], "new": text, "hand": hand, "training": str(saved) if saved else None}


def save_training_line(ws: Path, page: str, line: dict, text: str, hand: str, book: str) -> Path:
    """The line's picture (inside its outline, the rest white) with its text, among the
    hand's training lines; the same line saved again replaces its earlier picture."""
    from PIL import Image, ImageDraw

    im = Image.open(Path(ws) / "pages" / f"{page}.png").convert("L")
    x0, y0, x1, y1 = bbox(line["boundary"])
    pad = 4
    x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)
    crop = im.crop((x0, y0, x1, y1))
    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).polygon([(x - x0, y - y0) for x, y in line["boundary"]], fill=255)
    white = Image.new("L", crop.size, 255)
    out = Image.composite(crop, white, mask)
    folder = hand_lines_dir(hand)
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", book).strip("-")[:40] or "book"
    stem = folder / f"{slug}-{page}-{line_key(line)}"
    out.save(stem.with_suffix(".png"))
    stem.with_suffix(".gt.txt").write_text(text + "\n", encoding="utf-8", newline="\n")
    stem.with_suffix(".json").write_text(json.dumps({"book": book, "page": page}), encoding="utf-8")
    return stem.with_suffix(".png")


# -- the Kraken view: changing the lines found -------------------------------------------


def _edit(ws: Path, page: str, change: Callable[[list], None]) -> dict:
    """Change a page's lines, keeping corrections with lines that keep their shape."""
    data = lines_of(ws, page)
    if "found" not in data:
        data["found"] = json.loads(json.dumps(data["lines"]))  # as Kraken found them
    change(data["lines"])
    data["edited"] = True
    _save_lines(ws, page, data)
    return data


def set_line_box(ws: Path, page: str, index: int, box: list[int]) -> None:
    """A line stretched or shrunk to a box: its outline and baseline scaled into it."""
    def change(lines):
        line = lines[index]
        ox0, oy0, ox1, oy1 = bbox(line["boundary"])
        nx0, ny0, nx1, ny1 = box
        ox1, oy1, nx1, ny1 = ox1 - 1, oy1 - 1, nx1 - 1, ny1 - 1  # the last pixels inside (boxes end after them)
        sx = (nx1 - nx0) / max(1, ox1 - ox0)
        sy = (ny1 - ny0) / max(1, oy1 - oy0)
        f = (lambda p: [int(round(nx0 + (p[0] - ox0) * sx)), int(round(ny0 + (p[1] - oy0) * sy))])
        line["boundary"] = [f(p) for p in line["boundary"]]
        line["baseline"] = [f(p) for p in line["baseline"]]
    _edit(ws, page, change)


def join_lines(ws: Path, page: str, index: int) -> None:
    """A line and the next one made one (a line Kraken split in two)."""
    def change(lines):
        a, b = lines[index], lines[index + 1]
        x0, y0, x1, y1 = bbox(a["boundary"] + b["boundary"])
        base = sorted(a["baseline"] + b["baseline"], key=lambda p: p[0])
        lines[index] = {"baseline": [base[0], base[-1]] if len(base) > 1 else base, "boundary": _rect([x0, y0, x1, y1]),
                        "region": a.get("region")}
        del lines[index + 1]
    _edit(ws, page, change)


def split_line(ws: Path, page: str, index: int, x: float) -> None:
    """A line cut in two at x (two lines Kraken took as one); the reading order follows the
    script's direction."""
    def change(lines):
        line = lines[index]
        left = {"baseline": _baseline_part(line["baseline"], x, True), "boundary": _clip(line["boundary"], x, True),
                "region": line.get("region")}
        right = {"baseline": _baseline_part(line["baseline"], x, False), "boundary": _clip(line["boundary"], x, False),
                 "region": line.get("region")}
        parts = [p for p in (right, left) if len(p["boundary"]) >= 4]
        lines[index:index + 1] = parts
    _edit(ws, page, change)


def delete_line(ws: Path, page: str, index: int) -> None:
    _edit(ws, page, lambda lines: lines.pop(index))


def add_line(ws: Path, page: str, box: list[int]) -> int:
    """A line Kraken missed, drawn as a box; placed in reading order by its top. Returns its index."""
    x0, y0, x1, y1 = (int(v) for v in box)
    new = {"baseline": [[x0, int(y0 + 0.8 * (y1 - y0))], [x1, int(y0 + 0.8 * (y1 - y0))]], "boundary": _rect(box),
           "region": None}
    at = [0]

    def change(lines):
        i = 0
        while i < len(lines) and bbox(lines[i]["boundary"])[1] <= y0:
            i += 1
        lines.insert(i, new)
        at[0] = i
    _edit(ws, page, change)
    return at[0]


def edited_pages(ws: Path) -> list[str]:
    return sorted(p.name.split(".")[0] for p in _dir(ws).glob("*.lines.json") if _load(p, {}).get("edited"))


# -- Train for this book -------------------------------------------------------------------


def sample_pages(pages: list[str], n: int, seed: int | None = None) -> list[str]:
    """n pages at random from the middle of the book: away from the first and last tenth,
    where front and back matter are."""
    if not pages:
        return []
    skip = int(len(pages) * SAMPLE_SKIP)
    middle = pages[skip:len(pages) - skip] or pages
    rng = random.Random(seed)
    return sorted(rng.sample(middle, min(n, len(middle))))


# -- training ----------------------------------------------------------------------------

HAND_MIN = 10  # corrected lines a hand needs before it is trained
SEG_EPOCHS = 10  # rounds over the changed pages: fixed (Kraken's early stopping has no maximum)


def hand_items(hand_id: str) -> list:
    """A hand's corrected lines: items with id, png, text and book, as `train.errors_kraken` takes them."""
    from types import SimpleNamespace

    out = []
    for png in sorted(hand_lines_dir(hand_id).glob("*.png")):
        gt = png.with_suffix(".gt.txt")
        if not gt.is_file():
            continue
        meta = _load(png.with_suffix(".json"), {})
        out.append(SimpleNamespace(id=png.stem, png=png, text=gt.read_text(encoding="utf-8").strip(),
                                   book=meta.get("book", "")))
    return out


def _held(items: list) -> tuple[list, list]:
    """A tenth of the lines (at least two) kept aside to check the training, always the same ones."""
    def h(i):
        return hashlib.sha1(i.id.encode()).hexdigest()
    ranked = sorted(items, key=h)
    n = max(2, len(items) // 10)
    held = {i.id for i in ranked[:n]}
    return [i for i in items if i.id not in held], [i for i in items if i.id in held]


def train_hand(hand_id: str, progress=None, epochs: int | None = None) -> dict:
    """Train a hand further on its corrected lines (every book's), with Kraken's augmented
    copies; the new model replaces the hand's only if it reads the lines kept aside better."""
    import tempfile

    from .train import TrainingError, errors_kraken, fit_kraken
    from .training import as_progress

    say = as_progress(progress)
    info = hands().get(hand_id)
    if info is None:
        raise TrainingError(f"There is no hand {hand_id}.")
    items = hand_items(hand_id)
    if len(items) < HAND_MIN:
        return {"hand": hand_id, "trained": False,
                "why": f"{len(items)} corrected lines; at least {HAND_MIN} are needed"}
    train, held = _held(items)
    say(f"{info['name']}: checking it on {len(held)} lines kept aside")
    before = errors_kraken(info["file"], held)
    say.fraction(0.1)
    say(f"{info['name']}: training on {len(train)} corrected lines")
    with tempfile.TemporaryDirectory(prefix="ectype-hand-") as tmp:
        fit = fit_kraken(info["file"], [(i.png, i.text) for i in train], Path(tmp), [(i.png, i.text) for i in held],
                         say, epochs=epochs or 15)
        say.fraction(0.85)
        after = errors_kraken(str(fit.path), held)
        e0, n0 = (sum(v[0] for v in before.values()), sum(v[1] for v in before.values()))
        e1, n1 = (sum(v[0] for v in after.values()), sum(v[1] for v in after.values()))
        better = e1 < e0
        if better:
            target = Path(info["file"]).with_suffix(fit.path.suffix)
            shutil.copyfile(fit.path, target)
            if target != Path(info["file"]):
                Path(info["file"]).unlink(missing_ok=True)
            info = {**info, "file": str(target), "trained": time.strftime("%Y-%m-%d %H:%M"),
                    "errors": round(e1 / max(1, n1), 4)}
        info = {**info, "lines": len(items)}
        _save_hand(hand_id, info)
    say.fraction(1.0)
    rate = (lambda e, n: f"{100 * e / max(1, n):.1f}%")
    return {"hand": hand_id, "trained": better, "before": rate(e0, n0), "after": rate(e1, n1),
            "why": (f"{rate(e0, n0)} -> {rate(e1, n1)} character errors on the lines kept aside"
                    + ("" if better else "; the hand in use reads them as well, so it stays"))}


def page_xml(ws: Path, page: str) -> Path:
    """A page's lines as PageXML (baselines, outlines, regions), for training the line-finding."""
    from xml.sax.saxutils import quoteattr

    from PIL import Image

    ws = Path(ws)
    data = lines_of(ws, page)
    # The whole path: Kraken takes a relative one as beside the XML file (the release check
    # failed so: "All 1 samples in dataset invalid").
    image = (ws / "pages" / f"{page}.png").resolve()
    w, h = Image.open(image).size
    pts = (lambda ps: " ".join(f"{int(x)},{int(y)}" for x, y in ps))
    regions = data.get("regions") or []
    body = []
    groups: dict = {}
    for i, line in enumerate(data["lines"]):
        groups.setdefault(line.get("region"), []).append((i, line))
    for r, members in groups.items():
        outline = regions[r] if r is not None and r < len(regions) else _rect(bbox([p for _, l in members for p in l["boundary"]]))
        body.append(f'  <TextRegion id="r{len(body)}"><Coords points="{pts(outline)}"/>')
        for i, line in members:
            body.append(f'   <TextLine id="l{i}"><Coords points="{pts(line["boundary"])}"/>'
                        f'<Baseline points="{pts(line["baseline"])}"/></TextLine>')
        body.append("  </TextRegion>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15">\n'
           f' <Metadata><Creator>Ectype OCR</Creator><Created>{time.strftime("%Y-%m-%dT%H:%M:%S")}</Created>'
           f'<LastChange>{time.strftime("%Y-%m-%dT%H:%M:%S")}</LastChange></Metadata>\n'
           f' <Page imageFilename={quoteattr(str(image))} imageWidth="{w}" imageHeight="{h}">\n'
           + "\n".join(body) + "\n </Page>\n</PcGts>\n")
    out = _dir(ws) / "segtrain" / f"{page}.xml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml, encoding="utf-8", newline="\n")
    return out


def train_segmenter(ws: Path, progress=None, epochs: int = SEG_EPOCHS) -> dict:
    """Train the line-finding on the pages whose lines were changed in Review (Kraken's
    model, or the book's own, trained further); kept as the book's line-finding."""
    import tempfile

    from . import kraken
    from .train import KRAKEN_ONE
    from .training import as_progress

    say = as_progress(progress)
    pages = edited_pages(ws)
    if not pages:
        return {"trained": False, "why": "no page's lines were changed"}
    say(f"Line-finding: training on the lines of {len(pages)} pages changed in Review")
    files = [str(page_xml(ws, p)) for p in pages]
    start = segmenter_file(ws)
    with tempfile.TemporaryDirectory(prefix="ectype-seg-") as tmp:
        out = Path(tmp) / "model"
        # A page in ten checks the training; with only a few pages, the pages check themselves.
        check = files[::10] if len(files) >= 5 else files
        train_on = [f for f in files if f not in check] or files
        lists = {}
        for name, chosen in (("train", train_on), ("eval", check)):
            lists[name] = Path(tmp) / f"{name}.txt"
            lists[name].write_text("\n".join(chosen) + "\n", encoding="utf-8", newline="\n")
        with KRAKEN_ONE:
            kraken._run(["segtrain", "-i", str(start) if start else "default", "-o", str(out), "-f", "page",
                         "-t", str(lists["train"]), "-e", str(lists["eval"]),
                         "-N", str(epochs), "-q", "fixed", "--resize", "union"], timeout=12 * 3600)
        best = sorted(out.glob("best_*.safetensors")) or sorted(out.glob("*.safetensors"))
        if not best:
            return {"trained": False, "why": "Kraken's training didn't save a model"}
        shutil.copyfile(best[-1], _dir(ws) / "segmenter.safetensors")
    say.fraction(1.0)
    return {"trained": True, "pages": len(pages), "why": f"trained on {len(pages)} pages' lines"}


def train_book(ws: Path, settings, progress=None) -> list[str]:
    """"Train OCR model" in Manuscript Mode: the book's hands on their corrected lines, then the
    line-finding on the pages whose lines were changed. Returns the report's lines."""
    from .training import as_progress

    say = as_progress(progress)
    ids = chosen_hands(settings)
    report = []
    parts = len(ids) + (1 if edited_pages(ws) else 0)
    for k, hand in enumerate(ids):
        r = train_hand(hand, say.part(k / max(1, parts), (k + 1) / max(1, parts)))
        report.append(f"{hand_name(hand)}: " + ("trained, " if r["trained"] else "not trained: ") + r["why"])
    if edited_pages(ws):
        r = train_segmenter(ws, say.part(len(ids) / parts, 1.0))
        report.append("Line-finding: " + ("trained, " if r["trained"] else "not trained: ") + r["why"])
    say.fraction(1.0)
    return report
