"""One run of all the training, from a settings file: what the batch file
("Train Hebrew and Greek.bat") does.

    ectype train-all --config "%LOCALAPPDATA%\\Ectype OCR\\training.ini"

1. Lines set in the fonts of the fonts folder (`synthtrain.py`), for each script asked for.
2. Each edition listed: its scan read and its lines matched to the known text (`align.py`);
   an edition already done, and not changed since, is skipped.
3. Each language of those scripts trained from its pool (`training.py`): Tesseract's, and
   every Kraken model installed for the script, if the Kraken engine is installed.
4. A report, printed and saved beside the settings file (training-report.txt).

Steps already done are skipped when it is run again, so a run that was stopped can be started
again. The settings file:

    [training]
    fonts = C:\\Fonts\\Training
    scripts = hebrew greek
    lines per font = 400

    [edition: Letteris 1866]
    scan = C:\\Scans\\Letteris Genesis.pdf
    text = hebrew-bible

    [edition: Westcott-Hort 1881]
    scan = C:\\Scans\\WH John.pdf
    text = C:\\Texts\\westcott-hort.txt
    language = grc
"""

from __future__ import annotations

import configparser
import hashlib
import json
import time
from pathlib import Path
from typing import Callable

TEMPLATE = """# Ectype OCR: training settings, read by "Train Hebrew and Greek.bat"
# (ectype train-all). Lines starting with # are notes. Save this file and close Notepad
# to start; run the batch file again whenever you add fonts or scans.

[training]
# The folder with your Hebrew and Greek fonts (.ttf, .otf). Subfolders are included.
# The fonts stay where they are and are only read.
fonts =

# Which scripts to train: hebrew, greek, or both.
scripts = hebrew greek

# Lines set in each font. More is better but slower; 400 is a good start.
lines per font = 400

# Scanned editions whose text is known: one section each, named [edition: <a name>].
# scan = the PDF (or image); text = hebrew-bible, greek-nt, or a UTF-8 text file of the
# edition (one passage a line), with language = heb or grc for a file.
#
# [edition: Letteris 1866]
# scan = C:\\Scans\\Letteris Genesis.pdf
# text = hebrew-bible
#
# [edition: Westcott-Hort 1881]
# scan = C:\\Scans\\Westcott-Hort John.pdf
# text = C:\\Texts\\westcott-hort.txt
# language = grc
"""

SCRIPT_LANG = {"hebrew": "heb", "greek": "grc"}


def write_template(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(TEMPLATE, encoding="utf-8")
    return path


def read(path: Path) -> dict:
    cp = configparser.ConfigParser(interpolation=None, comment_prefixes=("#", ";"), inline_comment_prefixes=None)
    cp.read(path, encoding="utf-8")
    t = cp["training"] if cp.has_section("training") else {}
    scripts = [s.strip().lower() for s in (t.get("scripts") or "hebrew greek").replace(",", " ").split()]
    unknown = [s for s in scripts if s not in SCRIPT_LANG]
    if unknown:
        raise ValueError(f"Unknown script(s) {', '.join(unknown)} in {path}: use hebrew, greek.")
    editions = []
    for section in cp.sections():
        if section.lower().startswith("edition:"):
            e = cp[section]
            editions.append({"name": section.split(":", 1)[1].strip(), "scan": (e.get("scan") or "").strip(),
                             "text": (e.get("text") or "").strip(), "language": (e.get("language") or "").strip()})
    return {"fonts": (t.get("fonts") or "").strip(), "scripts": scripts,
            "lines": int((t.get("lines per font") or "400").strip() or 400), "editions": editions}


def _stamp(scan: Path, text: str) -> str:
    st = scan.stat()
    return hashlib.sha1(f"{scan.resolve()}:{st.st_size}:{st.st_mtime_ns}:{text}".encode()).hexdigest()[:12]


def codes_for(scripts: list[str]) -> list[str]:
    """The languages to train for these scripts: Tesseract's, and the Kraken models installed
    for them (when the Kraken engine is there)."""
    from . import kraken

    codes = [SCRIPT_LANG[s] for s in scripts]
    if kraken.worker_command() is not None:
        names = {SCRIPT_LANG[s]: s.capitalize() for s in scripts}
        for code in kraken.installed_models():
            if not kraken.installed_models()[code].get("book") and kraken.main_script(code) in names.values():
                codes.append(code)
    return codes


def train_scripts(scripts: list[str], progress: Callable[[str], None] | None = None, iterations: int | None = None,
                  epochs: int | None = None) -> list[str]:
    """Train each language of the scripts on its pool (Tesseract's, and Kraken's models for
    them). Returns the report's lines."""
    from .train import TrainingError
    from .training import MIN_ITEMS, train_language, trainable

    say = progress or (lambda msg: None)
    report = []
    for code in codes_for(scripts):
        n = trainable(code)
        if n < MIN_ITEMS:
            report.append(f"{code}: only {n} images in its pool; not trained.")
            continue
        say(f"Training {code} on its pool ({n} images)")
        try:
            report += train_language(code, say, iterations=iterations, epochs=epochs)["lines"]
        except TrainingError as exc:
            report.append(f"{code}: {exc}")
    return report


def run(config: Path, progress: Callable[[str], None] | None = None, iterations: int | None = None,
        epochs: int | None = None) -> list[str]:
    """Do the training the settings ask for (see the module docstring). Returns the report."""
    from . import align, pool, synthtrain

    say = progress or (lambda msg: None)
    settings = read(config)
    report = [f"Ectype OCR training, {time.strftime('%Y-%m-%d %H:%M')}", ""]
    if settings["fonts"]:
        folder = Path(settings["fonts"])
        if not folder.is_dir():
            raise FileNotFoundError(f"The fonts folder {folder} doesn't exist: check 'fonts =' in {config}.")
        say(f"Setting lines in the fonts of {folder}")
        out = synthtrain.generate(folder, settings["scripts"], settings["lines"], progress=say)
        for script, names in out["fonts"].items():
            report.append(f"{script.capitalize()}: {len(names)} fonts ({', '.join(names) or 'none with its letters'})")
        for name, why in out["skipped"].items():
            report.append(f"  {name}: {why}")
    else:
        report.append("No fonts folder given: no lines set in fonts.")
    done_path = Path(config).with_name("training-editions.json")
    try:
        done = json.loads(done_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        done = {}
    for e in settings["editions"]:
        scan = Path(e["scan"])
        if not scan.is_file():
            report.append(f"Edition {e['name']}: scan not found ({scan}); skipped.")
            continue
        stamp = _stamp(scan, e["text"])
        if done.get(e["name"], {}).get("stamp") == stamp:
            report.append(f"Edition {e['name']}: already matched ({done[e['name']]['kept']} lines).")
            continue
        say(f"Edition {e['name']}: reading the scan and matching it to the text")
        r = align.read_and_align(scan, e["text"], e["name"], e["language"] or None, progress=say)
        done[e["name"]] = {"stamp": stamp, "kept": r["kept"], "skipped": r["skipped"]}
        done_path.write_text(json.dumps(done, indent=1), encoding="utf-8")
        report.append(f"Edition {e['name']}: {r['kept']} lines matched to the text, {r['skipped']} skipped.")
    report.append("")
    report += train_scripts(settings["scripts"], say, iterations, epochs)
    report += ["", "Pools:"]
    for lang in pool.languages():
        books = pool.summary(lang)
        report.append(f"  {lang}: {sum(b['train'] + b['test'] for b in books.values())} images from {len(books)} sources")
    Path(config).with_name("training-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    return report
