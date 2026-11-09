"""Corrections to the OCR; every correction is kept as training data.

The desktop app's Review tab shows each page with every word boxed: low-confidence words
in red, words holding a glyph that looks like another character (from the cluster report)
in orange. A correction updates <workspace>/ocr, is logged
in <workspace>/corrections/<page>.json, and the corrected line is written as a
training pair, <workspace>/training/<page>-l<line>.png + .gt.txt (the line image
and its text, as Kraken's ketos and most OCR trainers take them). Then
`ectype rebuild <workspace>` reruns segmentation through the output PDF.
"""

from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path

from PIL import Image

from . import report
from .ocr import Char, Line, OcrPage, Word


def _punctuation(text: str) -> bool:
    return bool(text) and all(unicodedata.category(c).startswith("P") for c in text)


def parts(word) -> list[tuple[int, int]]:
    """The pieces of a word to correct one at a time, as spans of its characters: each
    punctuation mark at its start or end on its own, the letters between together ("(ḥesed),"
    is "(", "ḥesed", ")", ","). Typed together, a mark beside Hebrew or Greek letters jumps
    about in the text box, as right-to-left and left-to-right text are laid out. The whole
    word is one piece when its characters don't spell it or it is all punctuation."""
    chars = word.chars
    n = len(chars)
    if not n or "".join(c.text for c in chars) != word.text or all(_punctuation(c.text) for c in chars):
        return [(0, n)]
    a, b = 0, n
    while _punctuation(chars[a].text):
        a += 1
    while _punctuation(chars[b - 1].text):
        b -= 1
    return [(i, i + 1) for i in range(a)] + [(a, b)] + [(i, i + 1) for i in range(b, n)]


def part_box(word, span: tuple[int, int]) -> list[int]:
    """The box around a piece of a word (the word's box for the whole word)."""
    chars = word.chars[span[0]:span[1]]
    if not chars or span == (0, len(word.chars)):
        return list(word.box)
    return [min(c.box[0] for c in chars), min(c.box[1] for c in chars),
            max(c.box[2] for c in chars), max(c.box[3] for c in chars)]


def place_chars(old: list[Char], text: str, box: list[int]) -> list[Char]:
    """Characters for a word's new text in its box (left to right): the old characters that
    stay keep their boxes, and new ones share the room between them, or the room a stretched
    box added ("DMG." corrected to "ZDMG." with the box stretched over the Z: the Z gets the
    new room on the left, D M G . keep theirs). Spread evenly over the box when too little
    of the old text stays or a new character would have too little room."""
    import difflib

    x0, y0, x1, y1 = box
    even = [Char(t, [round(x0 + k * (x1 - x0) / len(text)), y0, round(x0 + (k + 1) * (x1 - x0) / len(text)), y1], 100.0)
            for k, t in enumerate(text)]
    if not old:
        return even
    match = difflib.SequenceMatcher(None, [c.text for c in old], list(text), autojunk=False)
    if match.ratio() < 0.5:
        return even
    out: list[Char] = []
    for tag, i0, i1, j0, j1 in match.get_opcodes():
        if tag == "equal":
            out += [Char(c.text, list(c.box), 100.0) for c in old[i0:i1]]
        elif j1 > j0:
            left = out[-1].box[2] if out else x0
            right = old[i1].box[0] if i1 < len(old) else x1
            if (right - left) < 2 * (j1 - j0):
                return even
            step = (right - left) / (j1 - j0)
            out += [Char(t, [round(left + k * step), y0, round(left + (k + 1) * step), y1], 100.0)
                    for k, t in enumerate(text[j0:j1])]
    return out


def apply_correction(ocr: OcrPage, line: int, word: int, text: str, span: tuple[int, int] | None = None,
                     box: list[int] | None = None) -> str:
    """Set a word's text, or with span (from `parts`) the text of that piece of it; with box,
    the word's box too, stretched over ink the OCR left out (its line's box grows with it).
    Character boxes are kept when the length is unchanged; else the characters that stay
    keep theirs and new ones get the room between them (`place_chars`). Right-to-left text
    is placed in visual order. A corrected word counts as fully confident. Returns the
    word's old text."""
    from .scripts import is_rtl

    w = ocr.lines[line].words[word]
    old = w.text
    text = text.strip()
    if not text:
        raise ValueError("empty correction; delete words isn't supported")
    if box is not None:
        box = [int(v) for v in box]
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("the box is empty")
        w.box = box
        lb = ocr.lines[line].box
        ocr.lines[line].box = [min(lb[0], box[0]), min(lb[1], box[1]), max(lb[2], box[2]), max(lb[3], box[3])]
    start, end = span or (0, len(w.chars))
    piece = w.chars[start:end]
    if len(text) == len(piece):
        for c, t in zip(piece, text):
            c.text, c.conf = t, 100.0
    else:
        if not piece or span is None:
            room = list(w.box)
        else:
            room = part_box(w, (start, end))
            # The room around the piece up to its neighbours (or the word's edges): a stretched box's new part.
            others = w.chars[:start] + w.chars[end:]
            mid = (room[0] + room[2]) / 2
            lo = [c.box[2] for c in others if (c.box[0] + c.box[2]) / 2 < mid]
            hi = [c.box[0] for c in others if (c.box[0] + c.box[2]) / 2 >= mid]
            room = [max(lo) if lo else w.box[0], room[1], min(hi) if hi else w.box[2], room[3]]
        rtl = is_rtl(text)
        visual = list(reversed(piece)) if rtl else piece
        placed = place_chars(visual, text[::-1] if rtl else text, room)
        piece = list(reversed(placed)) if rtl else placed
    for c in w.chars:
        c.conf = 100.0
    w.chars = w.chars[:start] + piece + w.chars[end:]
    w.text = "".join(c.text for c in w.chars) if span else text
    w.conf = 100.0
    return old


def insert_word(ocr: OcrPage, box: list[int], text: str) -> tuple[int, int]:
    """A word the OCR missed, drawn on the scan: put in the line it stands on (the one its box
    overlaps most in height, by half at least), in its place in the line's reading order; on a
    line of its own when it stands on none. Returns (line, word)."""
    from .scripts import is_rtl

    box = [int(v) for v in box]
    text = text.strip()
    if not text or box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("draw a box around the word and type its text")
    h = box[3] - box[1]
    best, best_overlap = None, 0.5 * h
    for li, line in enumerate(ocr.lines):
        overlap = min(box[3], line.box[3]) - max(box[1], line.box[1])
        if overlap > best_overlap:
            best, best_overlap = li, overlap
    word = Word(text, box, 100.0, [])
    rtl = is_rtl(text)
    visual = place_chars([], text[::-1] if rtl else text, box)
    word.chars = list(reversed(visual)) if rtl else visual
    if best is None:
        ocr.lines.append(Line(list(box), [0.0, 0.0], None, None, None, [word]))
        return len(ocr.lines) - 1, 0
    line = ocr.lines[best]
    words = line.words
    backwards = len(words) > 1 and words[0].box[0] > words[-1].box[0]  # a right-to-left line
    mid = (box[0] + box[2]) / 2
    at = next((k for k, w in enumerate(words) if ((w.box[0] + w.box[2]) / 2 < mid if backwards
                                                  else (w.box[0] + w.box[2]) / 2 > mid)), len(words))
    words.insert(at, word)
    lb = line.box
    line.box = [min(lb[0], box[0]), min(lb[1], box[1]), max(lb[2], box[2]), max(lb[3], box[3])]
    return best, at


def _shift(log: list[dict], line: int, at: int, skip: dict | None = None) -> None:
    """A word inserted at (line, at): the log's later words on that line move along one."""
    for e in log:
        if e is not skip and e["line"] == line and e["word"] >= at:
            e["word"] += 1


def save_training_line(workspace: Path, page: str, ocr: OcrPage, line: int) -> Path:
    """Crop a line from the page image and write it with its text as a training pair."""
    ln = ocr.lines[line]
    x0, y0, x1, y1 = ln.box
    pad_y = max(4, (y1 - y0) // 4)
    image = Image.open(Path(workspace) / "pages" / f"{page}.png")
    crop = image.crop((max(0, x0 - 10), max(0, y0 - pad_y), min(image.width, x1 + 10), min(image.height, y1 + pad_y)))
    out = Path(workspace) / "training"
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"{page}-l{line:03d}"
    crop.save(stem.with_suffix(".png"))
    stem.with_suffix(".gt.txt").write_text(" ".join(w.text for w in ln.words) + "\n", encoding="utf-8", newline="\n")
    return stem.with_suffix(".png")


def save_training_word(workspace: Path, page: str, ocr: OcrPage, line: int, word: int) -> Path | None:
    """A word in another script than the main text's (Hebrew, Greek...), cropped with its text
    as a training pair in training/words/: those scripts' models are trained on words, since
    their lines mix scripts."""
    from .scripts import UNICODE_NAME, in_script

    w = ocr.lines[line].words[word]
    if not any(in_script(w.text, script) for script in UNICODE_NAME if script != "Latin"):
        return None
    x0, y0, x1, y1 = w.box
    pad = max(6, (y1 - y0) // 3)
    image = Image.open(Path(workspace) / "pages" / f"{page}.png")
    crop = image.crop((max(0, x0 - pad), max(0, y0 - pad), min(image.width, x1 + pad), min(image.height, y1 + pad)))
    out = Path(workspace) / "training" / "words"
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"{page}-l{line:03d}-w{word:03d}"
    crop.save(stem.with_suffix(".png"))
    stem.with_suffix(".gt.txt").write_text(w.text + "\n", encoding="utf-8", newline="\n")
    return stem.with_suffix(".png")


def _open(workspace: Path, page: str) -> OcrPage:
    """A page's reading, its first reading kept aside (the original) before it is changed."""
    path = workspace / "ocr" / f"{page}.ocr.json"
    original = workspace / "ocr" / f"{page}.ocr.original.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not original.is_file():
        original.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="\n")
    return OcrPage.from_json(data)


def _log(workspace: Path, page: str) -> tuple[Path, list[dict]]:
    path = workspace / "corrections" / f"{page}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path, json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


STYLES = ("italic", "bold", "smallcaps")


def correct(workspace: Path, page: str, line: int, word: int, text: str, span: tuple[int, int] | None = None,
            box: list[int] | None = None, style: dict | None = None) -> dict:
    """Apply one correction to a workspace (a word, or with span a piece of it; with box, the
    word's box stretched or shrunk; with style, its italic, bold and small capitals as set in
    Review, over what was detected): OCR file, correction log (the whole word's text, its box
    and any style), training pair."""
    workspace = Path(workspace)
    path = workspace / "ocr" / f"{page}.ocr.json"
    ocr = _open(workspace, page)
    if span is not None:
        w = ocr.lines[line].words[word]
        if not 0 <= span[0] < span[1] <= len(w.chars):
            raise ValueError("that part of the word has changed; click it again")
        old_part = "".join(c.text for c in w.chars[span[0]:span[1]])
    old = apply_correction(ocr, line, word, text, span, box)
    new = ocr.lines[line].words[word].text
    report.write_json(path, ocr.to_json())
    log_path, log = _log(workspace, page)
    entry = {"line": line, "word": word, "old": old, "new": new, "box": ocr.lines[line].words[word].box,
             "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if box is not None:
        entry["resized"] = True
    if style:
        entry["style"] = {k: bool(v) for k, v in style.items() if k in STYLES}
    if span is not None:
        entry["part"] = [old_part, text.strip()]
    log.append(entry)
    report.write_json(log_path, log)
    pair = save_training_line(workspace, page, ocr, line)
    save_training_word(workspace, page, ocr, line, word)
    return {**entry, "training": str(pair.relative_to(workspace))}


def word_styles(workspace: Path, page: str) -> dict[tuple[int, int], dict]:
    """The styles set in Review on a page's words: (line, word) -> {"italic", "bold",
    "smallcaps"}, the last set of each winning."""
    path = Path(workspace) / "corrections" / f"{page}.json"
    out: dict[tuple[int, int], dict] = {}
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for e in log:
        if e.get("style") and not e.get("lost"):
            out.setdefault((e["line"], e["word"]), {}).update(e["style"])
    return out


def _make_room(workspace: Path, page: str, original: OcrPage, log: list[dict], line: int, at: int) -> None:
    """A word inserted at (line, at): the later words' entries in the log and their training
    images move along one. Entries without a box (from before boxes were logged) get theirs
    from the first reading first, so they can still be carried over."""
    for e in log:
        if "box" not in e:
            try:
                e["box"] = original.lines[e["line"]].words[e["word"]].box
            except IndexError:
                pass
    _shift(log, line, at)
    folder = workspace / "training" / "words"
    later = []
    for png in folder.glob(f"{page}-l{line:03d}-w*.png"):
        try:
            k = int(png.stem.rsplit("-w", 1)[1])
        except ValueError:
            continue
        if k >= at:
            later.append(k)
    for k in sorted(later, reverse=True):
        for suffix in (".png", ".gt.txt"):
            src = folder / f"{page}-l{line:03d}-w{k:03d}{suffix}"
            if src.is_file():
                src.replace(folder / f"{page}-l{line:03d}-w{k + 1:03d}{suffix}")


def add_word(workspace: Path, page: str, box: list[int], text: str) -> dict:
    """A word the OCR missed, drawn on the scan and typed (`insert_word`): OCR file, correction
    log (marked as added, so a new reading gets it again), training pair."""
    workspace = Path(workspace)
    path = workspace / "ocr" / f"{page}.ocr.json"
    ocr = _open(workspace, page)
    line, word = insert_word(ocr, box, text)
    original = OcrPage.from_json(json.loads((workspace / "ocr" / f"{page}.ocr.original.json").read_text(encoding="utf-8")))
    log_path, log = _log(workspace, page)
    _make_room(workspace, page, original, log, line, word)
    report.write_json(path, ocr.to_json())
    w = ocr.lines[line].words[word]
    entry = {"line": line, "word": word, "old": "", "new": w.text, "box": w.box, "added": True,
             "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    log.append(entry)
    report.write_json(log_path, log)
    pair = save_training_line(workspace, page, ocr, line)
    save_training_word(workspace, page, ocr, line, word)
    return {**entry, "training": str(pair.relative_to(workspace))}


def remove_word(ocr: OcrPage, line: int, word: int) -> Word:
    """Take a word out of the reading (a box round several words run together, part of a word
    split in two, a speck read as a word); the line's box shrinks to its other words."""
    ln = ocr.lines[line]
    w = ln.words.pop(word)
    if ln.words:
        ln.box = [min(x.box[0] for x in ln.words), min(x.box[1] for x in ln.words),
                  max(x.box[2] for x in ln.words), max(x.box[3] for x in ln.words)]
    return w


def _close_gap(workspace: Path, page: str, log: list[dict], line: int, at: int, original: OcrPage) -> None:
    """A word removed at (line, at): the log's entries for it are dropped (it is gone), its
    training image too, and the line's later words' entries and images move back one."""
    for e in log:
        if "box" not in e:
            try:
                e["box"] = original.lines[e["line"]].words[e["word"]].box
            except IndexError:
                pass
    log[:] = [e for e in log if e.get("deleted") or not (e["line"] == line and e["word"] == at)]
    for e in log:
        if not e.get("deleted") and e["line"] == line and e["word"] > at:
            e["word"] -= 1
    folder = workspace / "training" / "words"
    for suffix in (".png", ".gt.txt"):
        (folder / f"{page}-l{line:03d}-w{at:03d}{suffix}").unlink(missing_ok=True)
    later = []
    for png in folder.glob(f"{page}-l{line:03d}-w*.png"):
        try:
            k = int(png.stem.rsplit("-w", 1)[1])
        except ValueError:
            continue
        if k > at:
            later.append(k)
    for k in sorted(later):
        for suffix in (".png", ".gt.txt"):
            src = folder / f"{page}-l{line:03d}-w{k:03d}{suffix}"
            if src.is_file():
                src.replace(folder / f"{page}-l{line:03d}-w{k - 1:03d}{suffix}")


def delete_word(workspace: Path, page: str, line: int, word: int) -> dict:
    """A word's box deleted in Review (to draw the right ones with Add a word): OCR file,
    correction log (marked as deleted, by its box, so a new reading loses it again), and the
    line's training pair."""
    workspace = Path(workspace)
    path = workspace / "ocr" / f"{page}.ocr.json"
    ocr = _open(workspace, page)
    w = remove_word(ocr, line, word)
    original = OcrPage.from_json(json.loads((workspace / "ocr" / f"{page}.ocr.original.json").read_text(encoding="utf-8")))
    log_path, log = _log(workspace, page)
    _close_gap(workspace, page, log, line, word, original)
    report.write_json(path, ocr.to_json())
    entry = {"line": line, "word": word, "old": w.text, "new": "", "box": w.box, "deleted": True,
             "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    log.append(entry)
    report.write_json(log_path, log)
    if ocr.lines[line].words:
        save_training_line(workspace, page, ocr, line)
    else:
        for suffix in (".png", ".gt.txt"):
            (workspace / "training" / f"{page}-l{line:03d}{suffix}").unlink(missing_ok=True)
    return entry


def carry_corrections(workspace: Path, page_name: str, page: OcrPage) -> int:
    """Carry the page's corrections over to a new reading of it (other languages, a trained
    model): each corrected word's text goes to the new word that covers the same place on the
    page, with its box when it was stretched; a word added by hand that the new reading still
    misses is added again. The new reading becomes the page's original, and the log points at
    the new words. Returns how many corrections were carried over."""
    import copy

    workspace = Path(workspace)
    log_path = workspace / "corrections" / f"{page_name}.json"
    original = workspace / "ocr" / f"{page_name}.ocr.original.json"
    if not log_path.is_file() or not original.is_file():
        return 0
    old = OcrPage.from_json(json.loads(original.read_text(encoding="utf-8")))
    raw = copy.deepcopy(page)
    log = json.loads(log_path.read_text(encoding="utf-8"))

    def overlap(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        inter = ix * iy
        union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return inter / union if union > 0 else 0.0

    carried = 0
    # Boxes deleted in Review first: the new reading's word in the same place goes (words
    # drawn there instead are then added again, not laid over it).
    for e in log:
        if not e.get("deleted"):
            continue
        now = [(li, wi, w.box) for li, line in enumerate(page.lines) for wi, w in enumerate(line.words)]
        best = max(now, key=lambda t: overlap(e["box"], t[2]), default=None)
        if best is None or overlap(e["box"], best[2]) < 0.5:
            continue  # nothing in its place now
        remove_word(page, best[0], best[1])
        e["line"], e["word"] = best[0], best[1]
        carried += 1
    words = [(li, wi, w.box) for li, line in enumerate(page.lines) for wi, w in enumerate(line.words)]
    missing = []
    for e in log:
        if e.get("deleted"):
            continue
        try:
            box = e.get("box") or old.lines[e["line"]].words[e["word"]].box
        except IndexError:
            e["lost"] = True
            continue
        best = max(words, key=lambda t: overlap(box, t[2]), default=None)
        if best is None or overlap(box, best[2]) < 0.3:
            if e.get("added"):
                missing.append(e)
            else:
                e["lost"] = True
            continue
        apply_correction(page, best[0], best[1], e["new"], box=e["box"] if e.get("resized") or e.get("added") else None)
        e["line"], e["word"] = best[0], best[1]
        e.pop("lost", None)
        carried += 1
    for e in missing:  # added after the rest, as adding moves the later words of a line along
        e["line"], e["word"] = insert_word(page, e["box"], e["new"])
        _shift(log, e["line"], e["word"], skip=e)
        e.pop("lost", None)
        carried += 1
    original.write_text(json.dumps(raw.to_json(), ensure_ascii=False), encoding="utf-8", newline="\n")
    report.write_json(log_path, log)
    return carried


def _flags(workspace: Path) -> dict[str, set[tuple[int, int]]]:
    """Words holding a probable misreading, per page, from the cluster report."""
    path = workspace / "cluster" / "clusters.json"
    out: dict[str, set[tuple[int, int]]] = {}
    if not path.is_file():
        return out
    suspects = json.loads(path.read_text(encoding="utf-8")).get("suspects", [])
    units: dict[str, set[int]] = {}
    for s in suspects:
        for page, unit in s["instances"]:
            units.setdefault(page, set()).add(unit)
    for page, ids in units.items():
        seg_path = workspace / "segment" / f"{page}.seg.json"
        if seg_path.is_file():
            seg = json.loads(seg_path.read_text(encoding="utf-8"))
            out[page] = {(u["line"], u["word"]) for u in seg["units"] if u["id"] in ids}
    return out
