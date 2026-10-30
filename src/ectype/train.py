"""Fitting OCR models: the tools `training.py` uses to train a language's models from its pool.

Tesseract (`fit_tesseract`):
1. The starting model is in Tesseract's float form: tessdata_best's for the stock language
   (the shipped models are integer models, which can't be trained further), downloaded once
   into the user's folder; or a general model's float form, for a typeface family's model.
2. Each image with its text becomes Tesseract's training file (.lstmf), from the image and a
   box file holding its text (right-to-left text in visual order). Images with letters of
   another script (a Hebrew word in an English line) are left out and counted. Characters of
   the model's own script that it lacks are added to it, as tesstrain does: the stock Hebrew
   model has no holam, tsere, hataf segol or shin dot, so it can't read fully pointed Hebrew
   until trained on some.
3. lstmtraining continues the model for a number of iterations that grows with the images,
   and saves it in float form (to train further) and integer form (to read with: smaller).

Kraken (`fit_kraken`): Kraken's own trainer, `ketos train`, run by the Kraken engine,
continuing the model and adding characters it lacks.

`errors_tesseract` and `errors_kraken` count a model's character errors on test images, by
book, which is how a new model is judged before it replaces the old one.

Needs Tesseract's training tools (lstmtraining, combine_tessdata, and for new characters
unicharset_extractor, merge_unicharsets, combine_lang_model, dawg2wordlist), which come with
Tesseract on Windows (UB Mannheim, bundled in the app) and in Linux packages.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .resources import subprocess_flags, tesseract_cmd, tesseract_env, user_data_dir

BEST_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/{code}.traineddata"
MIN_LINES = 10  # corrected lines needed to train at all
MIN_WORDS = 20  # corrected words needed to train another script's language


class TrainingError(RuntimeError):
    """A plain message for the user."""


def tool(name: str) -> str | None:
    """A Tesseract training tool: beside tesseract (the app's own, or the system's), or on PATH.
    The Windows app may carry some in a "training" folder of their own, with their libraries."""
    cmd = tesseract_cmd()
    if cmd:
        here = Path(cmd).parent
        for candidate in (here / "training" / (name + ".exe"), here / (name + ".exe"), here / name):
            if candidate.is_file():
                return str(candidate)
    return shutil.which(name)


def available() -> str | None:
    """None when training can run; else what's missing, for the user."""
    missing = [t for t in ("lstmtraining", "combine_tessdata") if tool(t) is None]
    if tesseract_cmd() is None:
        return "Tesseract isn't installed."
    if missing:
        return f"Tesseract's training tools ({', '.join(missing)}) aren't installed."
    return None


def training_pairs(workspace: Path, words: bool = False) -> list[tuple[Path, str]]:
    """The corrected lines (or, with words, the corrected words in other scripts) as (image, text)."""
    folder = Path(workspace) / "training" / ("words" if words else "")
    out = []
    for gt in sorted(folder.glob("*.gt.txt")):
        png = gt.with_name(gt.name[: -len(".gt.txt")] + ".png")
        text = gt.read_text(encoding="utf-8").strip()
        if png.is_file() and text:
            out.append((png, text))
    return out


def _run(args: list[str], cwd: Path | None = None, timeout: float = 3600) -> subprocess.CompletedProcess:
    # Tesseract writes UTF-8, whatever Windows' code page is.
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd,
                          timeout=timeout, env=tesseract_env(), **subprocess_flags())


def best_model(base: str, progress: Callable[[str], None] | None = None) -> Path:
    """The base language's float model (tessdata_best), downloaded once."""
    folder = user_data_dir() / "tessdata_best"
    path = folder / f"{base}.traineddata"
    if path.is_file():
        return path
    folder.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(f"downloading the trainable {base} model (tessdata_best)")
    part = path.with_suffix(".part")
    try:
        with urllib.request.urlopen(BEST_URL.format(code=base), timeout=120) as r, open(part, "wb") as out:
            shutil.copyfileobj(r, out)
        part.replace(path)
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise TrainingError(f"Couldn't download the trainable {base} model ({exc}). Check the connection and try again.") from exc
    return path


def _unicharset(best: Path, work: Path, base: str) -> set[str]:
    """The characters the base model knows."""
    out = _run([tool("combine_tessdata"), "-u", str(best), str(work / f"{base}.")])
    if out.returncode != 0:
        raise TrainingError(f"Couldn't unpack the {base} model: {out.stderr.strip()[-200:]}")
    chars = set()
    lines = (work / f"{base}.lstm-unicharset").read_text(encoding="utf-8").splitlines()[1:]
    for line in lines:
        token = line.split(" ", 1)[0]
        chars.add(" " if token == "NULL" else token)
    return chars


def _foreign(c: str, script: str) -> bool:
    """A letter of another script than the model's (which training leaves out)."""
    from .scripts import UNICODE_NAME

    prefix = UNICODE_NAME.get(script)
    return c.isalpha() and prefix is not None and not unicodedata.name(c, "").startswith(prefix)


def _extended(best: Path, work: Path, base: str, texts: list[str]) -> Path | None:
    """A starter model whose character set is the base model's plus the new characters in
    the texts, keeping its dictionary (as tesstrain does); None if it couldn't be made."""
    from .scripts import is_rtl, script_of

    needed = ("unicharset_extractor", "merge_unicharsets", "combine_lang_model")
    if any(tool(t) is None for t in needed) or script_of(base) in ("Han", "Japanese", "Hangul"):
        return None  # those scripts' character sets need Tesseract's radical table
    (work / "gt.txt").write_text("\n".join(texts) + "\n", encoding="utf-8", newline="\n")
    rtl = is_rtl("".join(texts))
    norm = "3" if rtl else "1" if script_of(base) == "Latin" else "2"  # as tesstrain sets them
    _run([tool("unicharset_extractor"), "--output_unicharset", str(work / "new.unicharset"), "--norm_mode", norm,
          str(work / "gt.txt")], cwd=work)
    _run([tool("merge_unicharsets"), str(work / f"{base}.lstm-unicharset"), str(work / "new.unicharset"),
          str(work / "unicharset")], cwd=work)
    script_dir = work / "langdata"
    script_dir.mkdir(exist_ok=True)
    (script_dir / "radical-stroke.txt").write_text("\n", encoding="utf-8", newline="\n")  # only Han uses it
    args = [tool("combine_lang_model"), "--input_unicharset", str(work / "unicharset"), "--script_dir", str(script_dir),
            "--output_dir", str(work / "starter"), "--lang", base]
    # Keep the base model's word lists, so the new model still has its dictionary.
    for part, flag in (("word", "--words"), ("punc", "--puncs"), ("number", "--numbers")):
        dawg = work / f"{base}.lstm-{part}-dawg"
        if dawg.is_file() and tool("dawg2wordlist"):
            listing = work / f"{part}.txt"
            _run([tool("dawg2wordlist"), str(work / f"{base}.lstm-unicharset"), str(dawg), str(listing)], cwd=work)
            if listing.is_file():
                args += [flag, str(listing)]
    if rtl:
        args += ["--pass_through_recoder", "--lang_is_rtl"]
    (work / "starter" / base).mkdir(parents=True, exist_ok=True)
    _run(args, cwd=work)
    starter = work / "starter" / base / f"{base}.traineddata"
    return starter if starter.is_file() else None


def _box(png: Path, text: str, dest: Path) -> None:
    """A box file for a whole line, as tesstrain writes them: one entry a character (a letter
    with its marks), each over the full image, and a space at the end. Right-to-left text goes
    in visual order, character by character reversed: Tesseract's right-to-left models read
    that way and turn their readings round (trained in reading order, they learn it backwards)."""
    from PIL import Image

    from .scripts import is_rtl

    with Image.open(png) as im:
        w, h = im.size
    text = unicodedata.normalize("NFC", text)
    if is_rtl(text):
        units = list(reversed(text))
    else:
        units = []
        for c in text:
            if units and unicodedata.combining(c):
                units[-1] += c
            else:
                units.append(c)
    dest.write_text("".join(f"{c} 0 0 {w} {h} 0\n" for c in units) + f"\t 0 0 {w} {h} 0\n", encoding="utf-8", newline="\n")


def _lstmf(png: Path, text: str, work: Path, n: int) -> Path | None:
    """Tesseract's training file for one line, or None if it couldn't make one."""
    stem = work / f"line{n:05d}"
    shutil.copyfile(png, stem.with_suffix(".png"))
    _box(png, text, stem.with_suffix(".box"))
    out = _run([tesseract_cmd(), str(stem.with_suffix(".png")), str(stem), "--psm", "13", "lstm.train"])
    lstmf = stem.with_suffix(".lstmf")
    return lstmf if out.returncode == 0 and lstmf.is_file() else None


@dataclass
class Fit:
    """A model trained: Tesseract's in float form (to train further) and integer form (to read
    with), or Kraken's weights."""
    path: Path  # the model to read with
    float_path: Path | None  # Tesseract: the float model, the base for family models
    items: int  # trained on
    skipped: int  # left out: letters of another script, or failed to prepare
    added: str  # characters the starting model lacked
    iterations: int


def _tess_files(start: Path, work: Path, base: str) -> set[str]:
    """Unpack a model's parts into work/<base>.*; the characters it knows."""
    return _unicharset(start, work, base)


def fit_tesseract(start: Path, base: str, pairs: list[tuple[Path, str]], work: Path,
                  progress: Callable[[str], None] | None = None, iterations: int | None = None) -> Fit:
    """Continue a Tesseract model (float form, e.g. tessdata_best's or a general model) on
    images with their text. Lines with letters of another script are left out; characters
    of the model's own script that it lacks are added (`_extended`). lstmtraining counts its
    iterations afresh from a saved model, and learns only from images it reads wrongly."""
    from .scripts import script_of

    progress = progress or (lambda msg: None)
    problem = available()
    if problem:
        raise TrainingError(problem + " Training needs them; reinstall Ectype OCR, or install Tesseract with its training tools.")
    work.mkdir(parents=True, exist_ok=True)
    known = _tess_files(start, work, base)
    script = script_of(base)
    kept = [(png, text) for png, text in pairs if not any(_foreign(c, script) for c in text)]
    skipped = len(pairs) - len(kept)
    added = "".join(sorted({c for _, text in kept for c in text if not c.isspace() and c not in known}))
    traineddata = start
    if added:
        progress(f"{base}: adding {len(added)} characters the model lacks ({' '.join(added)})")
        traineddata = _extended(start, work, base, [text for _, text in kept])
        if traineddata is None:  # train without them
            progress(f"{base}: couldn't add those characters (Tesseract's tools for it didn't run); "
                     "training without them")
            kept = [(png, text) for png, text in kept if all(c in known for c in text if not c.isspace())]
            skipped = len(pairs) - len(kept)
            added, traineddata = "", start
    files = []
    progress(f"{base}: preparing {len(kept)} images")
    for n, (png, text) in enumerate(kept):
        lstmf = _lstmf(png, text, work, n)
        if lstmf is not None:
            files.append(lstmf)
        else:
            skipped += 1
    if len(files) < MIN_LINES // 2:
        raise TrainingError(f"Too few images could be prepared for training ({len(files)}; "
                            f"{skipped} had letters of another script than the {base} model's, or failed).")
    random.Random(0).shuffle(files)
    (work / "list.txt").write_text("\n".join(str(f) for f in files) + "\n", encoding="utf-8", newline="\n")
    iterations = iterations or min(12000, max(600, 3 * len(files)))
    progress(f"{base}: training on {len(files)} images, {iterations} iterations")
    old = ["--old_traineddata", str(start)] if traineddata != start else []
    out = _run([tool("lstmtraining"), "--model_output", str(work / "model"), "--continue_from", str(work / f"{base}.lstm"),
                *old, "--traineddata", str(traineddata), "--train_listfile", str(work / "list.txt"),
                "--max_iterations", str(iterations), "--target_error_rate", "0.005"], cwd=work, timeout=12 * 3600)
    checkpoint = work / "model_checkpoint"
    if not checkpoint.is_file():
        raise TrainingError("Training didn't finish: " + (out.stderr or out.stdout).strip()[-300:])
    as_float, as_int = work / f"{base}.float.traineddata", work / f"{base}.int.traineddata"
    for dest, extra in ((as_float, []), (as_int, ["--convert_to_int"])):
        out = _run([tool("lstmtraining"), "--stop_training", *extra, "--continue_from", str(checkpoint),
                    "--traineddata", str(traineddata), "--model_output", str(dest)], cwd=work)
        if not dest.is_file():
            raise TrainingError("Couldn't save the trained model: " + (out.stderr or out.stdout).strip()[-300:])
    return Fit(as_int, as_float, len(files), skipped, added, iterations)


def errors_tesseract(tessdata: Path, name: str, items) -> dict[str, tuple[int, int]]:
    """Character errors of a Tesseract model on test images, by book: book -> (errors, characters)."""
    from rapidfuzz.distance import Levenshtein

    from .scripts import clean

    out: dict[str, list[int]] = {}
    for item in items:
        got = _run([tesseract_cmd(), str(item.png), "-", "--psm", "7", "--tessdata-dir", str(tessdata), "-l", name])
        text = unicodedata.normalize("NFC", clean(got.stdout).strip())
        e = out.setdefault(item.book, [0, 0])
        e[0] += Levenshtein.distance(text, unicodedata.normalize("NFC", item.text))
        e[1] += len(item.text)
    return {b: (e[0], e[1]) for b, e in out.items()}


def fit_kraken(start: str, pairs: list[tuple[Path, str]], work: Path, held: list[tuple[Path, str]],
               progress: Callable[[str], None] | None = None, epochs: int = 15) -> Fit:
    """Continue a Kraken model on images with their text, with Kraken's trainer (`ketos train`,
    run by the Kraken engine); characters it lacks are added (--resize union)."""
    from . import kraken

    progress = progress or (lambda msg: None)
    if kraken.worker_command() is None:
        raise TrainingError("The Kraken engine isn't installed. Add it in Languages > Add languages > Kraken models.")
    work.mkdir(parents=True, exist_ok=True)
    lists = {}
    for part, chosen in (("train", pairs), ("eval", held or pairs[:20])):
        folder = work / part
        folder.mkdir(exist_ok=True)
        for n, (png, text) in enumerate(chosen):  # ketos reads <image> with <image stem>.gt.txt beside it
            shutil.copyfile(png, folder / f"i{n:05d}.png")
            (folder / f"i{n:05d}.gt.txt").write_text(text, encoding="utf-8", newline="\n")
        lists[part] = work / f"{part}.txt"
        lists[part].write_text("\n".join(str(p) for p in sorted(folder.glob("*.png"))) + "\n", encoding="utf-8", newline="\n")
    progress(f"Kraken: training on {len(pairs)} images, up to {epochs} epochs")
    out = work / "model"
    kraken._run(["train", "-f", "path", "-i", start, "--resize", "union", "-o", str(out),
                 "-t", str(lists["train"]), "-e", str(lists["eval"]), "-N", str(epochs), "-q", "early",
                 "--lag", "4", "--min-epochs", str(min(3, epochs))], timeout=12 * 3600)
    best = sorted(out.glob("best_*.safetensors")) or sorted(out.glob("*.safetensors"))
    if not best:
        raise TrainingError("Kraken's training didn't save a model.")
    return Fit(best[-1], None, len(pairs), 0, "", epochs)


def errors_kraken(model_file: str, items, pack: str | None = None) -> dict[str, tuple[int, int]]:
    """Character errors of a Kraken model (with a family pack, if any) on test images, by book."""
    from PIL import Image
    from rapidfuzz.distance import Levenshtein

    from . import kraken

    out: dict[str, list[int]] = {}
    with kraken.Session() as session:
        for item in items:
            with Image.open(item.png) as im:
                box = [0, 0, im.width, im.height]
            got = session.read(model_file, item.png, [box], pack)[0]["text"]
            e = out.setdefault(item.book, [0, 0])
            e[0] += Levenshtein.distance(unicodedata.normalize("NFC", got.strip()), unicodedata.normalize("NFC", item.text))
            e[1] += len(item.text)
    return {b: (e[0], e[1]) for b, e in out.items()}


def forget_trained(name: str) -> None:
    """Drop a per-book model (made by an earlier version) from the record."""
    path = user_data_dir() / "models.json"
    data = trained_models()
    if data.pop(name, None) is not None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def trained_models() -> dict[str, dict]:
    try:
        return json.loads((user_data_dir() / "models.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
