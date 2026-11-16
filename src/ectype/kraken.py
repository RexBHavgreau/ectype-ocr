"""Kraken: the second OCR engine, for Greek, Hebrew and other scripts beyond English.

Tesseract reads the page; words in a script a chosen Kraken model covers are then read again
by Kraken, a run of neighbouring words at a time (`read_runs`), since Kraken reads lines.
Kraken runs in a process of its own (`kraken_worker.py`), because it brings PyTorch:
- from source, when Kraken is installed beside Ectype (`pip install "ectype-ocr[kraken]"`), or
  through ECTYPE_KRAKEN, a command line that runs the worker in another environment;
- in the Windows app, as the Kraken engine, an optional download from the app's own release
  (`Ectype-OCR-Kraken-<version>-win64.zip`), unpacked into the user's folder.

Models come from Kraken's repository on Zenodo (`list_models`, `install_model`), chosen by the
user, and are saved in the user's folder. Each installed model is an OCR language of its own,
with a code like "kraken_7051645" ("Hebrew (Kraken): <the model's title>"), ticked in the
Languages menu beside Tesseract's.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import unicodedata
import zipfile
from pathlib import Path
from typing import Callable

from . import __version__, stopping
from .kraken_worker import PROTOCOL  # the protocol this app speaks: always the worker's own
from .resources import subprocess_flags, user_data_dir

PREFIX = "kraken_"
ENGINE_ASSET = "Ectype-OCR-Kraken-{version}-win64.zip"
ENGINE_EXE = "ectype-kraken.exe" if sys.platform == "win32" else "ectype-kraken"
# ISO 15924 codes (the repository's) to the script names used elsewhere (scripts.py).
ISO15924 = {"Latn": "Latin", "Grek": "Greek", "Hebr": "Hebrew", "Arab": "Arabic", "Syrc": "Syriac",
            "Syre": "Syriac", "Syrj": "Syriac", "Syrn": "Syriac", "Cyrl": "Cyrillic", "Armn": "Armenian",
            "Geor": "Georgian", "Ethi": "Ethiopic", "Deva": "Devanagari", "Copt": "Coptic", "Latf": "Latin", "Mlym": "Malayalam"}
LISTING_MAX_AGE = 7 * 24 * 3600


class KrakenError(RuntimeError):
    """A plain message for the user."""


# -- the engine ---------------------------------------------------------------------


def engine_dir() -> Path:
    return user_data_dir() / "kraken-engine"


def worker_command() -> list[str] | None:
    """How to start the worker, or None when Kraken isn't installed."""
    if os.environ.get("ECTYPE_KRAKEN"):
        return shlex.split(os.environ["ECTYPE_KRAKEN"], posix=sys.platform != "win32")
    exe = next(engine_dir().rglob(ENGINE_EXE), None) if engine_dir().is_dir() else None
    if exe is not None:
        return [str(exe)]
    if not getattr(sys, "frozen", False) and importlib.util.find_spec("kraken") is not None:
        return [sys.executable, "-m", "ectype.kraken_worker"]
    return None


def _env() -> dict:
    """The worker's environment: Python in UTF-8 mode (Kraken's repository client reads its data
    files in the default encoding, which on Windows isn't UTF-8), and UTF-8 on its pipes."""
    from .resources import frozen_child_env

    return {**frozen_child_env(), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _run(args: list[str], timeout: float = 600, progress: Callable[[int, int], None] | None = None) -> object:
    """Run one worker command; its JSON answer."""
    cmd = worker_command()
    if cmd is None:
        raise KrakenError("The Kraken engine isn't installed.")
    from .resources import clean_dll_search

    with clean_dll_search():
        proc = subprocess.Popen(cmd + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", env=_env(), **subprocess_flags())
    errors: list[str] = []

    def watch():
        for line in proc.stderr:
            m = re.match(r"progress (\d+) (\d+)", line)
            if m and progress:
                progress(int(m.group(1)), int(m.group(2)))
            elif not m:
                errors.append(line.rstrip())

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    killed = []
    timer = threading.Timer(timeout, lambda: (killed.append(True), proc.kill()))
    timer.start()
    stop_watch = stopping.watch(proc)  # Stop kills it
    try:
        out = proc.stdout.read()  # stdout here, stderr in the watcher: neither pipe fills up
        proc.wait()
    finally:
        timer.cancel()
        stop_watch.set()
    watcher.join(10)
    stopping.check()
    if killed:
        raise KrakenError(f"Kraken took longer than {timeout:.0f} seconds ({args[0]}).")
    if proc.returncode != 0:
        message = next((e for e in reversed(errors) if e.strip()), "no message")
        raise KrakenError(f"Kraken failed ({args[0]}): {message.removeprefix('error: ')}")
    lines = [line for line in out.splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else None


def engine_status() -> dict:
    """{"installed": bool, "kraken": version, "torch": version, "problem": message}"""
    if worker_command() is None:
        return {"installed": False, "problem": "The Kraken engine isn't installed.", "downloaded": False}
    try:
        # The first start can take minutes: thousands of files, each checked by the virus
        # scanner the first time (Windows Defender).
        info = _run(["version"], timeout=900)
    except (KrakenError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"installed": False, "problem": str(exc) or type(exc).__name__, "downloaded": True}
    if info.get("protocol") != PROTOCOL:
        return {"installed": False, "downloaded": True,
                "problem": "The Kraken engine downloaded is for another version of Ectype OCR: download it again."}
    return {"installed": True, **info}


def engine_asset(version: str = __version__, opener=None) -> dict | None:
    """The Kraken engine download in this version's release, or None when there is none."""
    import urllib.request

    from .updates import REPO, _agent

    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/tags/v{version}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": _agent()})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    name = ENGINE_ASSET.format(version=version)
    for a in data.get("assets") or []:
        if a.get("name") == name and a.get("browser_download_url"):
            return {"name": name, "url": a["browser_download_url"], "size": a.get("size") or 0,
                    "digest": a.get("digest") or ""}
    return None


def install_engine(asset: dict, progress: Callable[[float], None] | None = None) -> Path:
    """Download the Kraken engine (checked against the release's digest) and unpack it."""
    import shutil
    import tempfile

    from .updates import UpdateError, download

    with tempfile.TemporaryDirectory(prefix="ectype-kraken-") as tmp:
        try:
            archive = download(asset, Path(tmp), progress=progress, timeout=60)
        except UpdateError as exc:
            raise KrakenError(str(exc).replace("installer", "Kraken engine")) from exc
        target = engine_dir()
        partial = target.with_name(target.name + ".part")
        shutil.rmtree(partial, ignore_errors=True)
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():  # nothing may land outside the folder
                if member.startswith(("/", "\\")) or ".." in Path(member).parts:
                    raise KrakenError("The Kraken engine download is damaged.")
            z.extractall(partial)
        shutil.rmtree(target, ignore_errors=True)
        partial.replace(target)
    return target


# -- models ---------------------------------------------------------------------------


def models_dir() -> Path:
    return user_data_dir() / "kraken"


def _registry_path() -> Path:
    return models_dir() / "models.json"


def installed_models() -> dict[str, dict]:
    """code -> {"title", "doi", "file", "scripts", "languages", "base"?, "book"?}"""
    try:
        data = json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {code: m for code, m in data.items() if Path(m.get("file", "")).is_file()}


def register(code: str, model: dict) -> None:
    path = _registry_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[code] = model
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def unregister(code: str) -> None:
    path = _registry_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if data.pop(code, None) is not None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def is_model(code: str) -> bool:
    return code.startswith(PREFIX)


def scripts_of(record: dict) -> list[str]:
    """A model's scripts, as named in scripts.py (Greek, Hebrew...)."""
    out = []
    for s in record.get("scripts") or record.get("script") or []:
        name = ISO15924.get(s, s)
        if name not in out:
            out.append(name)
    return out


def main_script(code: str) -> str | None:
    """The script a model is chosen for: its first other than Latin (Greek models also read
    Latin numbers and punctuation), else Latin."""
    model = installed_models().get(code)
    if model is None:
        return None
    scripts = scripts_of(model)
    return next((s for s in scripts if s != "Latin"), scripts[0] if scripts else None)


def reads(code: str) -> list[str]:
    """Every script a model is used for: all of its scripts other than Latin, else Latin. A
    model of many scripts reads each of them (Beaux, 0.7.3: a multilingual model was shown,
    and used, for Arabic alone, the first in its list)."""
    model = installed_models().get(code)
    if model is None:
        return []
    scripts = scripts_of(model)
    return [s for s in scripts if s != "Latin"] or scripts[:1]


def name(code: str) -> str:
    model = installed_models().get(code)
    if model is None:
        return code
    many = reads(code)
    script = ", ".join(many) if len(many) > 1 else main_script(code) or "Kraken"
    title = model.get("title") or code
    if model.get("book"):
        return f"{script} (Kraken), trained on {model['book']}"
    return f"{script} (Kraken): {title}"


def code_for(doi: str) -> str:
    return PREFIX + re.sub(r"\D", "", doi.rsplit("zenodo.", 1)[-1])


def list_models(refresh: bool = False, progress: Callable[[int, int], None] | None = None) -> list[dict]:
    """The repository's recognition models (cached for a week in the user's folder)."""
    cache = models_dir() / "listing.json"
    if not refresh and cache.is_file() and time.time() - cache.stat().st_mtime < LISTING_MAX_AGE:
        try:
            kept = json.loads(cache.read_text(encoding="utf-8"))
            if kept:  # an empty list (Zenodo didn't answer) is asked for again, not kept for a week
                return [r for r in kept if not page_model(r)]
        except ValueError:
            pass
    records = _run(["list"], timeout=900, progress=progress)
    if records:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return [r for r in records if not page_model(r)]


def page_model(record: dict) -> bool:
    """A model that reads whole pages (Party), which Kraken's line reader can't load: the
    repository lists it among the recognition models (Beaux, 0.7.3: "No loader found")."""
    words = " ".join([record.get("summary", ""), *record.get("keywords", [])]).lower()
    return bool(re.search(r"\bparty\b", words)) or "page-wise" in record.get("description", "").lower()


def printed(record: dict) -> bool | None:
    """True for a model of printed text, False for handwriting, None when it doesn't say."""
    words = " ".join([record.get("summary", ""), *record.get("keywords", [])]).lower()
    if re.search(r"\bprint|typeset|\bprinted\b|incunab|early modern", words):
        return True
    if re.search(r"manuscript|handwrit|\bhtr\b|medieval", words):
        return False
    return None


def install_model(record: dict, progress: Callable[[int, int], None] | None = None) -> str:
    """Download a repository model and make it an OCR language. Returns its code."""
    import shutil

    code = code_for(record["doi"])
    folder = models_dir() / code
    try:
        got = _run(["get", record["doi"], str(folder)], timeout=1800, progress=progress)
        if not got or not got.get("model"):
            raise KrakenError(f"{record['doi']} has no model file Kraken can read.")
    except KrakenError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        if "known fingerprint" in str(exc):  # the worker checks the files against Zenodo's MD5s
            raise KrakenError(str(exc).split("): ", 1)[-1]) from exc
        raise
    register(code, {"title": record.get("summary") or record["doi"], "doi": record["doi"], "file": got["model"],
                    "scripts": record.get("script") or [], "languages": record.get("language") or [],
                    "license": record.get("license", ""), "cer": record.get("cer")})
    return code


# -- reading -------------------------------------------------------------------------


class Session:
    """One worker process for a whole OCR run, shared by the threads reading pages."""

    def __init__(self, threads: int | None = None):
        cmd = worker_command()
        if cmd is None:
            raise KrakenError("The Kraken engine isn't installed.")
        env = _env()
        if threads:  # several sessions side by side share the cores (manuscripts)
            env.update(ECTYPE_THREADS=str(threads), OMP_NUM_THREADS=str(threads))
        from .resources import clean_dll_search

        with clean_dll_search():
            self.proc = self._start(cmd, env)
        self.lock = threading.Lock()

    @staticmethod
    def _start(cmd, env):
        return subprocess.Popen(cmd + ["serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env, **subprocess_flags())

    def read(self, model_file: str, image: Path, boxes: list[list[int]], pack: str | None = None) -> list[dict]:
        return self._ask({"model": model_file, "pack": pack, "image": str(image), "lines": boxes})["lines"]

    def segment(self, image: Path, direction: str = "horizontal-lr", model: str | None = None) -> dict:
        """Kraken's line-finding on a page: {"lines": [{"baseline", "boundary", "region"}], "regions"}."""
        return self._ask({"op": "segment", "image": str(image), "direction": direction, "model": model}, "lines")

    def read_baselines(self, model_file: str, image: Path, direction: str, lines: list[dict]) -> list[dict]:
        """Lines found by `segment` read with a model: [{"text", "chars", "conf"}], in reading order."""
        if not lines:
            return []
        return self._ask({"model": model_file, "image": str(image), "direction": direction,
                          "baselines": [{"baseline": l["baseline"], "boundary": l["boundary"]} for l in lines]})["lines"]

    def _ask(self, request: dict, expect: str = "lines") -> dict:
        request = json.dumps(request, ensure_ascii=False)
        with self.lock:
            if self.proc.poll() is not None:
                raise KrakenError("The Kraken engine stopped.")
            self.proc.stdin.write(request + "\n")
            self.proc.stdin.flush()
            answer = json.loads(self.proc.stdout.readline() or "{}")
        if "error" in answer or expect not in answer:
            raise KrakenError(f"Kraken couldn't read the page: {answer.get('error', 'no answer')}")
        return answer

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)
        except Exception:
            self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_runs(page, png: Path, models: dict[str, tuple[str, str | None]], session: Session) -> int:
    """Read again with Kraken each run of neighbouring words in a script a chosen model covers
    (models: script -> (model file, family pack or None)). A word belongs to a script when most of its letters are in
    it, or when the script detector put it there (word.script, set by scripts.check_words).
    Returns how many words were read."""
    from .ocr import Char, Word
    from .scripts import LATIN_WORD, in_script

    def script_of_word(w):
        found = getattr(w, "script", None)
        if found in models:
            return found
        return next((s for s in models if in_script(w.text, s)), None)

    jobs = []  # (line, first, last, script)
    for line in page.lines:
        found = [script_of_word(w) for w in line.words]
        # A short word between two of a script's words, too small for the detector, is taken
        # with them, unless it reads as a plain word of the main text.
        for k in range(1, len(found) - 1):
            if found[k] is None and found[k - 1] and found[k - 1] == found[k + 1] and not LATIN_WORD.match(line.words[k].text):
                found[k] = found[k - 1]
        i = 0
        while i < len(line.words):
            script = found[i]
            if script is None:
                i += 1
                continue
            j = i
            while j + 1 < len(line.words) and found[j + 1] == script:
                j += 1
            jobs.append((line, i, j, script))
            i = j + 1
    # Every run is read before any is put in: a model of several scripts reads a line's Hebrew and
    # its Arabic as two runs, and putting the first in changes how many words the line has, so the
    # second's place, taken before, would point at the wrong words (Beaux, 0.10.0: "min() iterable
    # argument is empty" on a Hebrew grammar read with a multilingual model).
    readings = []  # (line, first, last, new words)
    gray = None
    for script, (model_file, pack) in models.items():
        mine = [job for job in jobs if job[3] == script]
        if not mine:
            continue
        boxes = []
        for line, i, j, _ in mine:
            words = line.words[i:j + 1]
            x0, x1 = min(w.box[0] for w in words), max(w.box[2] for w in words)
            y0 = min(line.box[1], *(w.box[1] for w in words))
            y1 = max(line.box[3], *(w.box[3] for w in words))
            pad = max(4, (y1 - y0) // 6)
            boxes.append([max(0, x0 - pad), max(0, y0 - pad), min(page.size_px[0], x1 + pad), min(page.size_px[1], y1 + pad)])
        results = session.read(model_file, png, boxes, pack)
        if gray is None:
            from .imagefiles import imread

            gray = imread(str(png), 0)  # the page, to fit each letter's box to its ink
        for (line, i, j, _), box, result in zip(mine, boxes, results):
            new = _words(result, box, Char, Word, gray)
            if new:
                readings.append((line, i, j, new))
    # Put in back to front within each line (runs don't overlap), so earlier places stay right.
    done = 0
    for line, i, j, new in sorted(readings, key=lambda t: (id(t[0]), -t[1])):
        line.words[i:j + 1] = new
        done += len(new)
    return done


def letter_spans(points: list[float], box: list[int], gray=None) -> list[tuple[int, int]]:
    """Each letter's left and right edge on the page, from Kraken's points.

    Kraken doesn't give a letter's width: each letter comes with one narrow column, at or just
    before its left edge, in both directions (measured: Hebrew א at 1295 with ink 1290-1332;
    Greek ε at 30 with ink 40-65), placed in steps of about 10 pixels. Taken as the letter itself,
    every word box ended at its first letter's left edge, which in Hebrew left that letter out of
    the box and so out of the traced type (Beaux, 0.11.0: every word's first letter missed).
    So a letter runs from its own point to the next letter's point to the right, the cut moved to
    the emptiest column near there (letters touch, or the point falls a little early), the outer
    letters reach a little past the end points, and every span is trimmed to the ink in it.
    `points` in any order; the spans come back in the same order. `gray`: the page (0-255), or
    None to use the points as they are."""
    import numpy as np

    n = len(points)
    if not n:
        return []
    x0, x1 = int(box[0]), int(box[2])
    order = sorted(range(n), key=lambda i: points[i])
    pts = [float(points[i]) for i in order]
    gaps = [b - a for a, b in zip(pts, pts[1:]) if b - a > 0]
    spacing = float(np.median(gaps)) if gaps else float(max(1, x1 - x0))
    reach = max(3.0, 0.35 * spacing)
    profile = None
    if gray is not None:
        rows = gray[max(0, int(box[1])):max(0, int(box[3])), max(0, x0):max(0, x1)]
        if rows.size:
            profile = (rows < 128).sum(axis=0)

    def ink(col: int) -> int:
        k = col - x0
        return int(profile[k]) if profile is not None and 0 <= k < len(profile) else 0

    cuts = [max(x0, int(round(pts[0] - reach)))]
    for a, b in zip(pts, pts[1:]):
        lo, hi = max(int(a) + 1, int(round(b - reach))), int(round(b + reach / 2))
        if profile is None or hi <= lo:
            cuts.append(int(round(b)))
        else:  # the emptiest column, the nearest to the point among equals
            cuts.append(min(range(lo, hi + 1), key=lambda c: (ink(c), abs(c - b))))
    cuts.append(min(x1, int(round(pts[-1] + 1.5 * spacing))))
    spans = []
    for k in range(n):
        a, b = cuts[k], max(cuts[k] + 1, cuts[k + 1])
        if profile is not None:
            cols = [c for c in range(a, b) if ink(c)]
            if cols:
                a, b = cols[0], cols[-1] + 1
        spans.append((a, b))
    out = [None] * n
    for k, i in enumerate(order):
        out[i] = spans[k]
    return out


def _words(result: dict, box: list[int], Char, Word, gray=None) -> list:
    """Kraken's reading of a run as words: split at spaces, accents and points joined to their
    letter (Kraken gives them apart), each letter's box from `letter_spans` (to the run's
    height), each word's box around its letters."""
    letters = []  # [text, point, conf] in reading order; None between words
    for text, (cx0, _cy0, cx1, _cy1), conf in result.get("chars", []):
        if text.isspace():
            letters.append(None)
        elif letters and letters[-1] is not None and unicodedata.combining(text):
            last = letters[-1]
            last[0] = unicodedata.normalize("NFC", last[0] + text)
            last[2] = min(last[2], float(conf))
        else:
            letters.append([text, (float(cx0) + float(cx1)) / 2, float(conf)])
    real = [l for l in letters if l is not None]
    spans = letter_spans([l[1] for l in real], box, gray)
    span_of = {id(l): s for l, s in zip(real, spans)}
    words, chars = [], []

    def flush():
        if chars:
            text = "".join(c.text for c in chars)
            wb = [min(c.box[0] for c in chars), box[1], max(c.box[2] for c in chars), box[3]]
            words.append(Word(text=text, box=wb, conf=round(sum(c.conf for c in chars) / len(chars), 1), chars=list(chars)))
            chars.clear()

    for l in letters:
        if l is None:
            flush()
            continue
        a, b = span_of[id(l)]
        chars.append(Char(text=l[0], box=[a, box[1], b, box[3]], conf=l[2]))
    flush()
    return words