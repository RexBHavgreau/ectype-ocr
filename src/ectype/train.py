"""Train the OCR model on a book's corrections (Tesseract fine-tuning).

Every correction made in Review saves its line as a training pair in <workspace>/training/
(the line's image, <name>.png, and its corrected text, <name>.gt.txt). Training fine-tunes a
copy of the book's main OCR language on those lines, so the next reading makes fewer of the
same mistakes:

1. The base model in Tesseract's float form (tessdata_best: the shipped models are integer
   models, which can't be trained further) is downloaded once into the user's folder.
2. The corrected lines, plus as many lines the OCR read with high confidence (so the model
   keeps what it already does well), become Tesseract's training files (.lstmf), each from
   a line image and a box file holding its text. Lines with letters of another script (a
   Hebrew word in an English line) are left out and counted. Characters of the model's own
   script that it lacks are added to it: the stock Hebrew model has no holam, tsere, hataf
   segol or shin dot, so it can't read fully pointed Hebrew until trained on some.
3. A fifth of the corrected lines (at least two) is kept aside. lstmtraining continues the
   base model on the rest for a number of iterations that grows with the lines; the result
   is saved as a new language, `<base>_<book>` (e.g. eng_young), beside the downloaded ones,
   so it can be chosen in the Languages menu for this book or others.
4. The lines kept aside are read with the old model and the new one, and the character
   error rates compared, so the report says whether training helped.

The new model replaces the base language among the book's OCR languages; reading the pages
again uses it, and corrections already made are carried over to the new reading.

Other scripts in the book (Hebrew or Greek words in English text) are trained on words, not
lines, since their lines mix scripts: a word corrected into Hebrew is also saved by itself in
training/words/, and with enough of them the book's Hebrew language is fine-tuned on those
word images (plus Hebrew words read confidently) the same way, as e.g. heb_treatise.

Needs Tesseract's training tools (lstmtraining, combine_tessdata), which come with
Tesseract on Windows (UB Mannheim) and in Linux packages.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .resources import subprocess_flags, tesseract_cmd, tesseract_env, user_data_dir, user_tessdata_dir

BEST_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/{code}.traineddata"
MIN_LINES = 10  # corrected lines needed to train at all
MIN_WORDS = 20  # corrected words needed to train another script's language


class TrainingError(RuntimeError):
    """A plain message for the user."""


@dataclass
class TrainResult:
    model: str  # the new language code
    base: str
    lines: int  # corrected lines trained on
    confident: int  # confidently read lines added
    skipped: int  # lines with letters of another script
    iterations: int
    cer_before: float | None  # on the lines kept aside
    cer_after: float | None
    held_out: int
    unit: str = "lines"  # or "words", for another script's language
    added: str = ""  # characters the base model didn't have


def tool(name: str) -> str | None:
    """A Tesseract training tool: beside tesseract (the app's own, or the system's), or on PATH."""
    cmd = tesseract_cmd()
    if cmd:
        for candidate in (Path(cmd).with_name(name + ".exe"), Path(cmd).with_name(name)):
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


def model_name(base: str, book: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", book.lower())[:24] or "book"
    return f"{base}_{slug}"


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
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=timeout, env=tesseract_env(),
                          **subprocess_flags())


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
    (work / "gt.txt").write_text("\n".join(texts) + "\n", encoding="utf-8")
    rtl = is_rtl("".join(texts))
    norm = "3" if rtl else "1" if script_of(base) == "Latin" else "2"  # as tesstrain sets them
    _run([tool("unicharset_extractor"), "--output_unicharset", str(work / "new.unicharset"), "--norm_mode", norm,
          str(work / "gt.txt")], cwd=work)
    _run([tool("merge_unicharsets"), str(work / f"{base}.lstm-unicharset"), str(work / "new.unicharset"),
          str(work / "unicharset")], cwd=work)
    script_dir = work / "langdata"
    script_dir.mkdir(exist_ok=True)
    (script_dir / "radical-stroke.txt").write_text("\n", encoding="utf-8")  # only Han uses it
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
    dest.write_text("".join(f"{c} 0 0 {w} {h} 0\n" for c in units) + f"\t 0 0 {w} {h} 0\n", encoding="utf-8")


def _lstmf(png: Path, text: str, work: Path, n: int) -> Path | None:
    """Tesseract's training file for one line, or None if it couldn't make one."""
    stem = work / f"line{n:05d}"
    shutil.copyfile(png, stem.with_suffix(".png"))
    _box(png, text, stem.with_suffix(".box"))
    out = _run([tesseract_cmd(), str(stem.with_suffix(".png")), str(stem), "--psm", "13", "lstm.train"])
    lstmf = stem.with_suffix(".lstmf")
    return lstmf if out.returncode == 0 and lstmf.is_file() else None


def script_words(workspace: Path, base: str) -> list[tuple[Path, str]]:
    """Corrected words in the script of the language `base` (training/words/)."""
    from .scripts import in_script, script_of

    script = script_of(base)
    return [(png, text) for png, text in training_pairs(workspace, words=True) if in_script(text, script)]


def confident_words(workspace: Path, base: str, limit: int, min_conf: float = 90.0, seed: int = 0) -> list[tuple[Path, str]]:
    """Words of the base language's script the OCR read with min_conf or more, as training
    pairs, to keep what the model already reads well."""
    from PIL import Image

    from .ocr import OcrPage
    from .scripts import in_script, script_of

    workspace = Path(workspace)
    script = script_of(base)
    found = []
    for path in sorted((workspace / "ocr").glob("*.ocr.json")):
        page = path.name[: -len(".ocr.json")]
        ocr = OcrPage.from_json(json.loads(path.read_text(encoding="utf-8")))
        for li, line in enumerate(ocr.lines):
            for wi, w in enumerate(line.words):
                if w.conf >= min_conf and len(w.text) >= 2 and in_script(w.text, script):
                    found.append((page, li, wi, w))
    random.Random(seed).shuffle(found)
    out_dir = workspace / "training" / "confident-words"
    if out_dir.is_dir():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    out = []
    for page, li, wi, w in found[:limit]:
        with Image.open(workspace / "pages" / f"{page}.png") as image:
            x0, y0, x1, y1 = w.box
            pad = max(6, (y1 - y0) // 3)
            crop = image.crop((max(0, x0 - pad), max(0, y0 - pad), min(image.width, x1 + pad), min(image.height, y1 + pad)))
            png = out_dir / f"{page}-l{li:03d}-w{wi:03d}.png"
            crop.save(png)
        out.append((png, w.text))
    return out


def confident_lines(workspace: Path, limit: int, min_conf: float = 90.0, seed: int = 0) -> list[tuple[Path, str]]:
    """Lines the OCR read with every word at min_conf or more, as training pairs (cropped
    into a temporary folder in the workspace), to keep the model's other skills."""
    from PIL import Image

    from .ocr import OcrPage

    workspace = Path(workspace)
    found = []
    for path in sorted((workspace / "ocr").glob("*.ocr.json")):
        page = path.name[: -len(".ocr.json")]
        ocr = OcrPage.from_json(json.loads(path.read_text(encoding="utf-8")))
        for li, line in enumerate(ocr.lines):
            if len(line.words) >= 3 and all(w.conf >= min_conf for w in line.words):
                found.append((page, li, line))
    random.Random(seed).shuffle(found)
    out_dir = workspace / "training" / "confident"
    if out_dir.is_dir():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    out = []
    for page, li, line in found[:limit]:
        with Image.open(workspace / "pages" / f"{page}.png") as image:
            x0, y0, x1, y1 = line.box
            pad = max(4, (y1 - y0) // 4)
            crop = image.crop((max(0, x0 - 10), max(0, y0 - pad), min(image.width, x1 + 10), min(image.height, y1 + pad)))
            png = out_dir / f"{page}-l{li:03d}.png"
            crop.save(png)
        out.append((png, " ".join(w.text for w in line.words)))
    return out


def _cer(model_dir: Path, lang: str, pairs: list[tuple[Path, str]]) -> float | None:
    """Character error rate of a model on line (or word) images."""
    from rapidfuzz.distance import Levenshtein

    errors = total = 0
    for png, text in pairs:
        out = _run([tesseract_cmd(), str(png), "-", "--psm", "7", "--tessdata-dir", str(model_dir), "-l", lang])
        errors += Levenshtein.distance(out.stdout.strip(), text)
        total += len(text)
    return round(errors / total, 4) if total else None


def train(workspace: Path, base: str, book: str, progress: Callable[[str], None] | None = None,
          iterations: int | None = None, seed: int = 0, words: bool = False) -> TrainResult:
    """Fine-tune the base language on the workspace's corrections (see the module docstring):
    on corrected lines, or with words, on the corrected words in the base language's script."""
    progress = progress or (lambda msg: None)
    problem = available()
    if problem:
        raise TrainingError(problem + " Training needs them; reinstall Ectype OCR, or install Tesseract with its training tools.")
    unit = "words" if words else "lines"
    corrected = script_words(workspace, base) if words else training_pairs(workspace)
    least = MIN_WORDS if words else MIN_LINES
    if len(corrected) < least:
        raise TrainingError(f"Correct at least {least} {unit} in Review first ({len(corrected)} so far). "
                            f"Each corrected {unit[:-1]} becomes training data.")
    best = best_model(base, progress)
    name = model_name(base, book)
    with tempfile.TemporaryDirectory(prefix="ectype-train-") as tmp:
        work = Path(tmp)
        known = _unicharset(best, work, base)
        rng = random.Random(seed)
        rng.shuffle(corrected)
        held = corrected[: max(2, len(corrected) // 5)]
        train_pairs = corrected[len(held):]
        extra = (confident_words if words else confident_lines)(workspace, **({"base": base} if words else {}),
                                                               limit=len(train_pairs), seed=seed)
        from .scripts import script_of

        script = script_of(base)
        kept = [(png, text) for png, text in train_pairs + extra if not any(_foreign(c, script) for c in text)]
        skipped = len(train_pairs) + len(extra) - len(kept)
        added = "".join(sorted({c for _, text in kept for c in text if not c.isspace() and c not in known}))
        traineddata = best
        if added:
            progress(f"{base}: adding {len(added)} characters the model lacks ({' '.join(added)})")
            traineddata = _extended(best, work, base, [text for _, text in kept])
            if traineddata is None:  # train without them
                kept = [(png, text) for png, text in kept if all(c in known for c in text if not c.isspace())]
                skipped = len(train_pairs) + len(extra) - len(kept)
                added, traineddata = "", best
        files = []
        progress(f"{base}: preparing {len(train_pairs)} corrected and {len(extra)} confidently read {unit}")
        for n, (png, text) in enumerate(kept):
            lstmf = _lstmf(png, text, work, n)
            if lstmf is not None:
                files.append(lstmf)
        if len(files) < MIN_LINES // 2:
            raise TrainingError(f"Too few {unit} could be prepared for training "
                                f"({len(files)}; {skipped} had letters of another script than the {base} model's).")
        (work / "list.txt").write_text("\n".join(str(f) for f in files) + "\n", encoding="utf-8")
        iterations = iterations or min(3000, max(300, 30 * len(files)))
        progress(f"{base}: training on {len(files)} {unit}, {iterations} iterations (a few minutes)")
        old = ["--old_traineddata", str(best)] if traineddata != best else []
        out = _run([tool("lstmtraining"), "--model_output", str(work / "model"), "--continue_from", str(work / f"{base}.lstm"),
                    *old, "--traineddata", str(traineddata), "--train_listfile", str(work / "list.txt"),
                    "--max_iterations", str(iterations), "--target_error_rate", "0.01"], cwd=work)
        checkpoint = work / "model_checkpoint"
        if not checkpoint.is_file():
            raise TrainingError("Training didn't finish: " + (out.stderr or out.stdout).strip()[-300:])
        model_dir = work / "models"
        model_dir.mkdir()
        new = model_dir / f"{name}.traineddata"
        out = _run([tool("lstmtraining"), "--stop_training", "--continue_from", str(checkpoint),
                    "--traineddata", str(traineddata), "--model_output", str(new)], cwd=work)
        if not new.is_file():
            raise TrainingError("Couldn't save the trained model: " + (out.stderr or out.stdout).strip()[-300:])
        progress("comparing the old and new model on lines kept aside")
        shutil.copyfile(best, model_dir / f"{base}.traineddata")
        before = _cer(model_dir, base, held)
        after = _cer(model_dir, name, held)
        # Install beside the other languages (the user's folder, with the shipped ones copied in).
        from .languages import _seed

        target = user_tessdata_dir()
        _seed(target)
        shutil.copyfile(new, target / new.name)
    _record(name, base, book)
    return TrainResult(name, base, len(train_pairs), len(extra), skipped, iterations, before, after, len(held), unit, added)


def _record(name: str, base: str, book: str) -> None:
    """Remember what each trained model is, for its name in the Languages menu."""
    path = user_data_dir() / "models.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[name] = {"base": base, "book": book}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def trained_models() -> dict[str, dict]:
    try:
        return json.loads((user_data_dir() / "models.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}




def train_kraken(workspace: Path, code: str, book: str, progress: Callable[[str], None] | None = None,
                 epochs: int = 50, seed: int = 0) -> TrainResult:
    """Fine-tune an installed Kraken model (`code`) on the words corrected into its script,
    with Kraken's own trainer (`ketos train`, run by the Kraken engine). The result is installed
    as a Kraken model of its own, e.g. "Hebrew (Kraken), trained on Treatise"."""
    from . import kraken

    progress = progress or (lambda msg: None)
    model = kraken.installed_models().get(code)
    if model is None:
        raise TrainingError(f"The Kraken model {code} isn't installed.")
    if kraken.worker_command() is None:
        raise TrainingError("The Kraken engine isn't installed. Add it in Languages > Add languages > Kraken models.")
    corrected = script_words(workspace, code)
    if len(corrected) < MIN_WORDS:
        raise TrainingError(f"Correct at least {MIN_WORDS} words in Review first ({len(corrected)} so far).")
    random.Random(seed).shuffle(corrected)
    held = corrected[: max(2, len(corrected) // 5)]
    train_pairs = corrected[len(held):]
    new_code = f"{code}_{re.sub(r'[^a-z0-9]+', '', book.lower())[:24] or 'book'}"
    with tempfile.TemporaryDirectory(prefix="ectype-ketos-") as tmp:
        work = Path(tmp)
        lists = {}
        for part, pairs in (("train", train_pairs), ("eval", held)):
            folder = work / part
            folder.mkdir()
            for n, (png, text) in enumerate(pairs):  # ketos reads <image> with <image stem>.gt.txt beside it
                shutil.copyfile(png, folder / f"w{n:05d}.png")
                (folder / f"w{n:05d}.gt.txt").write_text(text, encoding="utf-8")
            lists[part] = work / f"{part}.txt"
            lists[part].write_text("\n".join(str(p) for p in sorted(folder.glob("*.png"))) + "\n", encoding="utf-8")
        progress(f"{kraken.name(code)}: training on {len(train_pairs)} corrected words, up to {epochs} epochs (a few minutes)")
        out = work / "model"
        kraken._run(["train", "-f", "path", "-i", model["file"], "--resize", "union", "-o", str(out),
                     "-t", str(lists["train"]), "-e", str(lists["eval"]), "-N", str(epochs), "-q", "early",
                     "--lag", "5", "--min-epochs", str(min(5, epochs))], timeout=6 * 3600,
                    progress=lambda done, total: progress(f"epoch {done} of {total}"))
        best = sorted(out.glob("best_*.safetensors")) or sorted(out.glob("*.safetensors"))
        if not best:
            raise TrainingError("Kraken's training didn't save a model.")
        target = kraken.models_dir() / new_code
        target.mkdir(parents=True, exist_ok=True)
        weights = target / "model.safetensors"
        shutil.copyfile(best[-1], weights)
        progress("comparing the old and new model on words kept aside")
        before = _kraken_cer(model["file"], held)
        after = _kraken_cer(str(weights), held)
    kraken.register(new_code, {**{k: v for k, v in model.items() if k not in ("book", "base")},
                               "file": str(weights), "base": code, "book": book})
    return TrainResult(new_code, code, len(train_pairs), 0, 0, epochs, before, after, len(held), "words")


def _kraken_cer(model_file: str, pairs: list[tuple[Path, str]]) -> float | None:
    """Character error rate of a Kraken model on word images."""
    from PIL import Image
    from rapidfuzz.distance import Levenshtein

    from . import kraken

    errors = total = 0
    with kraken.Session() as session:
        for png, text in pairs:
            with Image.open(png) as im:
                box = [0, 0, im.width, im.height]
            got = session.read(model_file, png, [box])[0]["text"]
            errors += Levenshtein.distance(unicodedata.normalize("NFC", got.strip()), unicodedata.normalize("NFC", text))
            total += len(text)
    return round(errors / total, 4) if total else None
