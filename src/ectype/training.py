"""Training a language's models from its pool: the general model, then typeface families.

For each language chosen (`train_language`):
1. **General model.** Trained afresh from the stock model on a balanced sample of the whole
   pool (`pool.py`): every book's corrections, the lines set in the user's fonts, scanned
   editions matched to their text, and some confidently read lines. Starting from the stock
   model each time means nothing drifts or is forgotten as books are added; the sample keeps
   the time in bounds as the pool grows (at most `GENERAL_CAP` images, every book represented).
2. **The check.** The new model and the one in use read every book's test images. The new
   one is installed only if it makes fewer errors overall, and no book does clearly worse
   (`judge`). Otherwise the old one stays, and the report says which book got worse.
3. **Families.** Books (and fonts) are grouped by their typeface fingerprints
   (`families.py`). A family with at least `FAMILY_MIN` training images gets a model of its
   own, trained further from the general model on the family's images. It is kept only if
   it reads the family's test images better than the general model does. For Tesseract it
   is a model of its own (a few megabytes); for Kraken, a pack of its changes from the
   general model (`kraken_worker.make_pack`, about a quarter of a full copy).
4. **This book.** After training, the models are tried on the current book's own
   corrections and the best is remembered for it (`choose_for_workspace`); other books are
   matched by fingerprint (`models.resolve`).

**Quick update** (`mode="quick"`, chosen by itself when little is new): a couple dozen
corrections shouldn't cost a whole training. The general model in use is trained further on
the images added since it was trained (`general.items.json` lists those it knew), with three
times as many earlier ones (200 at least) so it doesn't forget them, for far fewer
iterations; it is checked as above and installed only if better. Only the families with a
book among the new images are trained again (from their own model); the others stay. For
Kraken, their packs stay laid over the general model they were made over (kept as
base-g<n>.safetensors). Training afresh now and then (`mode="full"`) starts again from the
stock model on the whole pool, as above.

Checks reuse what is known: a model in use has its errors on each test image kept
(`<which>.errors.json`, for as long as the model file is unchanged), so only the new model
and new test images are read.
"""

from __future__ import annotations

import json
import random
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable

from . import models, pool
from .train import TrainingError

GENERAL_CAP = 4000  # images in a general model's training sample
FAMILY_CAP = 1500
FAMILY_MIN = 200  # training images a family needs for a model of its own
MIN_ITEMS = 20  # corrected (or generated, or matched) images a language needs to train at all
TEST_PER_BOOK = 80  # test images read per book in a check
WORSE = 0.10  # a book may get this much worse (relative)...
WORSE_ABS = 0.005  # ...or this much (absolute), and the new model still be taken
FAMILY_GAIN = 0.03  # a family model must make this much fewer errors than the general one
QUICK_SHARE = 0.25  # new images up to this share of those the general model knew (or REPLAY_MIN): a quick update
QUICK_REPLAY = 3  # earlier images trained on again for each new one in a quick update...
REPLAY_MIN = 200  # ...and at least this many
QUICK_ITERATIONS = (400, 3000)  # Tesseract, quick update: 4 a training image, within these
QUICK_EPOCHS = 5  # Kraken, quick update

Say = Callable[[str], None]


def cer(errors: dict[str, tuple[int, int]], books: set[str] | None = None) -> float | None:
    e = sum(v[0] for b, v in errors.items() if books is None or b in books)
    n = sum(v[1] for b, v in errors.items() if books is None or b in books)
    return round(e / n, 4) if n else None


def judge(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> tuple[bool, str]:
    """Take the new model? Fewer errors overall, and no book (with enough test text) clearly worse."""
    b, a = cer(before), cer(after)
    if a is None or b is None:
        return True, "no test images to check against"
    if a >= b:
        return False, f"it made no fewer errors ({100 * a:.2f}% against {100 * b:.2f}%)"
    for book, (e, n) in after.items():
        if n < 30 or book not in before:
            continue
        old, new = before[book][0] / max(1, before[book][1]), e / n
        if new > old + max(WORSE_ABS, WORSE * old):
            return False, f"it read {book} worse ({100 * new:.1f}% against {100 * old:.1f}%)"
    return True, f"{100 * b:.2f}% -> {100 * a:.2f}% character errors"


def _sample(items: list[pool.Item], cap: int, seed: int = 0) -> list[pool.Item]:
    """At most `cap` items with every book represented: an equal share each, unused shares
    passed on; confidently read lines no more than the rest together."""
    rng = random.Random(seed)
    main = [i for i in items if i.source != "confident"]
    confident = [i for i in items if i.source == "confident"]
    rng.shuffle(confident)
    confident = confident[: min(len(main), cap // 3)]
    by_book: dict[str, list[pool.Item]] = {}
    for i in main:
        by_book.setdefault(i.book, []).append(i)
    room = cap - len(confident)
    chosen: list[pool.Item] = []
    books = sorted(by_book, key=lambda b: len(by_book[b]))
    for k, book in enumerate(books):
        share = (room - len(chosen)) // (len(books) - k)
        group = by_book[book]
        rng.shuffle(group)
        chosen += group[:share]
    return chosen + confident


def _tests(items: list[pool.Item], per_book: int = TEST_PER_BOOK, books: set[str] | None = None) -> list[pool.Item]:
    rng = random.Random(1)
    out, by_book = [], {}
    for i in items:
        if books is None or i.book in books:
            by_book.setdefault(i.book, []).append(i)
    for group in by_book.values():
        group = sorted(group, key=lambda i: i.id)
        rng.shuffle(group)
        out += group[:per_book]
    return out


def _pairs(items: list[pool.Item]) -> list[tuple[Path, str]]:
    return [(i.png, i.text) for i in items]


def _items_for(code: str, split: str) -> list[pool.Item]:
    """The pool's items a language's model can learn from: for a script other than Latin, only
    text in that script (a Hebrew model doesn't train on English lines)."""
    from .scripts import in_script, script_of

    script = script_of(code)
    items = pool.items(pool.key_for(code), split)
    if script == "Latin":
        return items
    return [i for i in items if in_script(i.text, script)]


def trainable(code: str) -> int:
    """How many images a language's pool has (not counting confidently read lines), those kept
    aside for testing included."""
    code = models.stock_code(code)
    return sum(1 for split in ("train", "test") for i in _items_for(code, split) if i.source != "confident")


# -- what is known from the last training ---------------------------------------------


def _model_dir(code: str) -> Path:
    return models.tesseract_dir(code) if models.engine_of(code) == "tesseract" else models.kraken_dir(code)


def _known_path(code: str) -> Path:
    return _model_dir(code) / "general.items.json"


def known_items(code: str) -> set[str] | None:
    """The training images the general model in use was trained with (its pool when trained), or
    None when that isn't known (trained by an earlier version)."""
    try:
        return set(json.loads(_known_path(code).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _save_known(code: str, ids) -> None:
    path = _known_path(code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(ids)), encoding="utf-8", newline="\n")


def _stamp(path) -> str:
    st = Path(path).stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _checked(code: str, which: str, model_file, items: list[pool.Item], read) -> dict[str, tuple[int, int]]:
    """A model's errors on the test images, by book. Its errors on each image are kept while the
    model file is unchanged, so only images it hasn't read are read (`read(items, per_item)`)."""
    path = _model_dir(code) / f"{which}.errors.json"
    stamp = _stamp(model_file)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if data.get("stamp") != stamp:
        data = {"stamp": stamp, "items": {}}
    missing = [i for i in items if i.id not in data["items"]]
    if missing:
        per: dict = {}
        read(missing, per)
        data["items"].update({k: list(v) for k, v in per.items()})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    return _by_book(items, data["items"])


def _by_book(items: list[pool.Item], per_item: dict) -> dict[str, tuple[int, int]]:
    out: dict[str, list[int]] = {}
    for i in items:
        if i.id in per_item:
            e = out.setdefault(i.book, [0, 0])
            e[0] += per_item[i.id][0]
            e[1] += per_item[i.id][1]
    return {b: (e[0], e[1]) for b, e in out.items()}


def _keep_checked(code: str, which: str, model_file, per_item: dict) -> None:
    """A model just installed: its errors (read when it was checked) kept for the next check."""
    path = _model_dir(code) / f"{which}.errors.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stamp": _stamp(model_file), "items": {k: list(v) for k, v in per_item.items()}}),
                    encoding="utf-8", newline="\n")


def plan(code: str) -> dict:
    """What training would do now: {"mode": "quick", "full" or "none", "new": images added since
    the general model was trained, "why"}."""
    code = models.stock_code(code)
    known = known_items(code)
    has_general = "general" in models.choices(code)
    if models.engine_of(code) == "tesseract":
        has_general = has_general and (models.tesseract_dir(code) / "general.traineddata").is_file()
    if not has_general or known is None:
        return {"mode": "full", "new": None, "why": "no trained model to add to yet"}
    new = [i for i in _items_for(code, "train") if i.id not in known and i.source != "confident"]
    if not new:
        return {"mode": "none", "new": 0, "why": "nothing new since the last training"}
    if len(new) > max(REPLAY_MIN, QUICK_SHARE * len(known)):
        return {"mode": "full", "new": len(new), "why": f"{len(new)} new images: a lot to add"}
    return {"mode": "quick", "new": len(new), "why": f"{len(new)} new images"}


# -- Tesseract ------------------------------------------------------------------------


def _tess_current(code: str) -> Path | None:
    from .resources import tessdata_dir, user_tessdata_dir

    general = user_tessdata_dir() / f"{models.tesseract_name(code, 'general')}.traineddata"
    if "general" in models.choices(code) and general.is_file():
        return general
    for folder in (user_tessdata_dir(), tessdata_dir()):
        if folder and (Path(folder) / f"{code}.traineddata").is_file():
            return Path(folder) / f"{code}.traineddata"
    return None


def _tess_errors(model: Path, items: list[pool.Item], work: Path, name: str,
                 per_item: dict | None = None) -> dict[str, tuple[int, int]]:
    from .train import errors_tesseract

    folder = work / "eval"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(model, folder / f"{name}.traineddata")
    return errors_tesseract(folder, name, items, per_item)


def _tess_checked(code: str, which: str, model: Path, items, work: Path) -> dict[str, tuple[int, int]]:
    """A Tesseract model in use, checked: its kept errors, and the images it hasn't read."""
    return _checked(code, which, model, items, lambda missing, per: _tess_errors(model, missing, work, f"in_use_{which}", per))


def _install_tess(model: Path, name: str) -> None:
    from .languages import _seed
    from .resources import user_tessdata_dir

    target = user_tessdata_dir()
    _seed(target)
    shutil.copyfile(model, target / f"{name}.traineddata")


def _general_tesseract(code: str, train_items, tests, work: Path, say: Say, iterations, quick: list | None = None) -> dict:
    """The general model trained afresh from the stock one on a sample of the pool, or with quick
    (the new images and the earlier ones to train again with them) trained further."""
    from .resources import user_tessdata_dir
    from .train import best_model, fit_tesseract

    if quick is None:
        sample, start = _sample(train_items, GENERAL_CAP), best_model(code, say)
        say(f"{code}: training the general model on {len(sample)} images from {len({i.book for i in sample})} sources")
    else:
        sample, start = quick, models.tesseract_dir(code) / "general.traineddata"
        lo, hi = QUICK_ITERATIONS
        iterations = iterations or min(hi, max(lo, 4 * len(sample)))
    fit = fit_tesseract(start, code, _pairs(sample), work / "general", say, iterations)
    current = _tess_current(code)
    say(f"{code}: checking it against the model in use, on {len(tests)} test images")
    which = "general" if "general" in models.choices(code) else "stock"
    before = _tess_checked(code, which, current, tests, work) if current else {}
    per: dict = {}
    after = _tess_errors(fit.path, tests, work, "candidate", per)
    ok, why = judge(before, after) if before else (True, "no model in use to compare with")
    if ok:
        name = models.tesseract_name(code, "general")
        _install_tess(fit.path, name)
        models.tesseract_dir(code).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fit.float_path, models.tesseract_dir(code) / "general.traineddata")
        _keep_checked(code, "general", user_tessdata_dir() / f"{name}.traineddata", per)
    return {"installed": ok, "why": why, "before": before, "after": after, "items": fit.items, "added": fit.added,
            "skipped": fit.skipped, "iterations": fit.iterations}


def _family_tesseract(code: str, fid: str, items, tests, work: Path, say: Say, iterations,
                      quick: list | None = None) -> dict:
    """A family's model trained from the general one on the family's images, or with quick (its
    new images and earlier ones) trained further from its own model, when there is one."""
    from .resources import user_tessdata_dir
    from .train import fit_tesseract

    own = models.tesseract_dir(code) / f"{fid}.traineddata"
    if quick is not None and own.is_file():
        start, sample = own, quick
        lo, hi = QUICK_ITERATIONS
        iterations = iterations or min(hi, max(lo, 4 * len(sample)))
    else:
        start, sample = models.tesseract_dir(code) / "general.traineddata", _sample(items, FAMILY_CAP)
    fit = fit_tesseract(start, code, _pairs(sample), work / fid, say, iterations)
    general = user_tessdata_dir() / f"{models.tesseract_name(code, 'general')}.traineddata"
    before = _tess_checked(code, "general", general, tests, work)
    after = _tess_errors(fit.path, tests, work, f"family{fid}")
    better = _better(before, after)
    if better:
        _install_tess(fit.path, models.tesseract_name(code, fid))
        shutil.copyfile(fit.float_path, own)  # to train further in a quick update
    return {"kept": better, "before": before, "after": after, "items": fit.items}


# -- Kraken ---------------------------------------------------------------------------


def _general_kraken(code: str, train_items, tests, work: Path, say: Say, epochs, quick: list | None = None) -> dict:
    """As `_general_tesseract`, with Kraken's trainer. When a quick update replaces the general
    model, families not trained again keep the one their packs were made over."""
    from .kraken import installed_models
    from .train import errors_kraken, fit_kraken

    stock = installed_models()[code]["file"]
    entry = models.entry(code)
    current = entry["general"]["file"] if "general" in models.choices(code) else stock
    sample = _sample(train_items, GENERAL_CAP) if quick is None else quick
    rng = random.Random(2)
    held = rng.sample(sample, min(len(sample) // 10 or 1, 200))
    rest = [i for i in sample if i not in held]
    say(f"{code}: training the general model on {len(rest)} images from {len({i.book for i in rest})} sources")
    fit = fit_kraken(stock if quick is None else current, _pairs(rest), work / "general", _pairs(held), say,
                     epochs or (15 if quick is None else QUICK_EPOCHS))
    say(f"{code}: checking it against the model in use, on {len(tests)} test images")
    which = "general" if "general" in models.choices(code) else "stock"
    before = _checked(code, which, current, tests, lambda missing, per: errors_kraken(current, missing, per_item=per))
    per: dict = {}
    after = errors_kraken(str(fit.path), tests, per_item=per)
    ok, why = judge(before, after)
    if ok:
        folder = models.kraken_dir(code)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "general.safetensors"
        if quick is not None and target.is_file():
            base = folder / f"base-g{entry.get('generation', 0)}.safetensors"
            shutil.copyfile(target, base)
            data = models.registry()
            for info in data.get(code, {}).get("families", {}).values():
                info.setdefault("base", str(base))  # its pack was made over the model being replaced
            models.save_registry(data)
        shutil.copyfile(fit.path, target)
        _keep_checked(code, "general", target, per)
    return {"installed": ok, "why": why, "before": before, "after": after, "items": fit.items}


def _family_kraken(code: str, fid: str, items, tests, work: Path, say: Say, epochs) -> dict:
    from . import kraken
    from .train import errors_kraken, fit_kraken

    general = str(models.kraken_dir(code) / "general.safetensors")
    sample = _sample(items, FAMILY_CAP)  # a pack is made over the general model, so always from it
    held = sample[: max(1, len(sample) // 10)]
    fit = fit_kraken(general, _pairs(sample[len(held):]), work / fid, _pairs(held), say, epochs or 8)
    pack = models.kraken_dir(code) / f"{fid}.pack.safetensors"
    info = kraken._run(["pack", general, str(fit.path), str(pack)], timeout=600)
    before = _checked(code, "general", general, tests, lambda missing, per: errors_kraken(general, missing, per_item=per))
    after = errors_kraken(general, tests, str(pack))  # read as it will be: the pack over the general model
    better = _better(before, after)
    if not better:
        pack.unlink(missing_ok=True)
    return {"kept": better, "before": before, "after": after, "items": fit.items, "pack": str(pack),
            "pack_bytes": (info or {}).get("bytes"), "full_bytes": (info or {}).get("full_bytes")}


def _better(before, after) -> bool:
    b, a = cer(before), cer(after)
    return a is not None and b is not None and a < b * (1 - FAMILY_GAIN)


# -- a language --------------------------------------------------------------------------


def train_language(code: str, progress: Say | None = None, iterations: int | None = None,
                   epochs: int | None = None, family_min: int = FAMILY_MIN, mode: str = "auto") -> dict:
    """Train a language's general model and its family models from its pool (see the module
    docstring). mode: "full" (afresh), "quick" (the new images added to the models in use, when
    there are models to add to), or "auto" (`plan`'s choice). Returns a report:
    {"code", "mode", "general": {...}, "families": {fid: {...}}, "lines": [...]}."""
    from .families import centroid, cluster
    from .scripts import script_of

    say = progress or (lambda msg: None)
    code = models.stock_code(code)
    engine = models.engine_of(code)
    lang = pool.key_for(code)
    train_items = _items_for(code, "train")
    if trainable(code) < MIN_ITEMS:
        raise TrainingError(f"{_name(code)}: {trainable(code)} images to train on; at least {MIN_ITEMS} are needed. "
                            "Correct more in Review, or add generated lines (train-synthetic).")
    tests = _tests(_items_for(code, "test"))
    if mode != "full":
        chosen = plan(code)
        if mode == "auto" or chosen["new"] is None or chosen["mode"] == "none":
            mode = chosen["mode"]  # asked for quick: unless there's nothing to add to, or nothing new
    report: dict = {"code": code, "engine": engine, "mode": mode, "families": {}, "lines": []}
    if mode == "none":
        report["general"] = {"installed": False, "why": "nothing new since the last training", "before": {}, "after": {}}
        report["lines"].append(f"{_name(code)}: nothing new since the last training; the models in use stay.")
        return report
    with tempfile.TemporaryDirectory(prefix="ectype-training-") as tmp:
        work = Path(tmp)
        if mode == "quick":
            _quick(code, train_items, tests, work, say, iterations, epochs, report)
            return report
        if engine == "tesseract":
            general = _general_tesseract(code, train_items, tests, work, say, iterations)
        else:
            general = _general_kraken(code, train_items, tests, work, say, epochs)
        report["general"] = general
        report["lines"].append(f"{_name(code)}: general model {'updated' if general['installed'] else 'kept'} "
                               f"({general['why']}).")
        data = models.registry()
        entry = data.setdefault(code, {"engine": engine})
        if general["installed"]:
            file = str(models.kraken_dir(code) / "general.safetensors") if engine == "kraken" else None
            entry["general"] = {"trained": time.strftime("%Y-%m-%d %H:%M"), "items": general["items"],
                                "iterations": general.get("iterations", 0),
                                "errors": cer(general["after"]), "books": sorted({i.book for i in train_items}),
                                **({"file": file} if file else {})}
            _drop_families(code, entry.get("families") or {}, {})
            entry["families"] = {}  # trained against the old general model: made again below
            entry["quick_updates"] = 0
            _save_known(code, (i.id for i in train_items))
        models.save_registry(data)

        # Families: only over an installed general model.
        if "general" in models.choices(code):
            prints = {b: info["fingerprint"] for b, info in pool.books(lang).items() if info.get("fingerprint")}
            prints = {b: fp for b, fp in prints.items() if any(i.book == b for i in train_items)}
            groups: dict[int, list[str]] = {}
            for book, fam in cluster(prints).items():
                groups.setdefault(fam, []).append(book)
            kept = {}
            for fam, books in sorted(groups.items()):
                fid = f"f{fam}"
                items = [i for i in train_items if i.book in books]
                if sum(1 for i in items if i.source != "confident") < family_min:
                    continue
                ftests = _tests(_items_for(code, "test"), books=set(books))
                if not ftests:
                    # No test images of its own (fonts not kept aside): keep a tenth of its training images.
                    own = sorted((i for i in items if i.source != "confident"), key=lambda i: i.id)
                    ftests = own[::10][:TEST_PER_BOOK * 2]
                    items = [i for i in items if i not in ftests]
                say(f"{code}: training a model for typeface family {fam} ({', '.join(books[:3])}"
                    f"{'…' if len(books) > 3 else ''})")
                fit = (_family_tesseract if engine == "tesseract" else _family_kraken)(
                    code, fid, items, ftests, work, say, iterations if engine == "tesseract" else epochs)
                report["families"][fid] = {**fit, "books": books}
                verdict = (f"{100 * cer(fit['before']):.2f}% -> {100 * cer(fit['after']):.2f}%"
                           if cer(fit["before"]) is not None and cer(fit["after"]) is not None else "")
                report["lines"].append(f"  family {fam} ({', '.join(books)}): "
                                       f"{'its own model' if fit['kept'] else 'the general model reads it as well'} {verdict}")
                if fit["kept"]:
                    kept[fid] = {"centroid": centroid([prints[b] for b in books]), "books": books,
                                 "script": script_of(code), "errors": cer(fit["after"]),
                                 **({"pack": fit["pack"]} if engine == "kraken" else {})}
            data = models.registry()
            entry = data.setdefault(code, {"engine": engine})
            _drop_families(code, entry.get("families", {}), kept)
            entry["families"] = kept
            entry["generation"] = entry.get("generation", 0) + 1
            entry["pool_items"] = len(pool.index(lang))
            models.save_registry(data)
    _prune_bases(code)
    return report


def _quick(code: str, train_items, tests, work: Path, say: Say, iterations, epochs, report: dict) -> None:
    """A quick update (see the module docstring): the general model trained further on the new
    images with earlier ones, then the families with a book among the new images."""
    engine = models.engine_of(code)
    known = known_items(code) or set()
    mine = [i for i in train_items if i.source != "confident"]
    new = [i for i in mine if i.id not in known]
    old = [i for i in mine if i.id in known]
    replay = _sample(old, min(len(old), max(REPLAY_MIN, QUICK_REPLAY * len(new))), seed=len(known))
    say(f"{code}: quick update: {len(new)} new images, trained with {len(replay)} earlier ones")
    if engine == "tesseract":
        general = _general_tesseract(code, train_items, tests, work, say, iterations, quick=new + replay)
    else:
        general = _general_kraken(code, train_items, tests, work, say, epochs, quick=new + replay)
    report["general"] = general
    report["lines"].append(f"{_name(code)}: quick update on {len(new)} new images: general model "
                           f"{'updated' if general['installed'] else 'kept'} ({general['why']}).")
    data = models.registry()
    entry = data.setdefault(code, {"engine": engine})
    changed = general["installed"]
    if general["installed"]:
        g = entry.setdefault("general", {})
        g.update(trained=time.strftime("%Y-%m-%d %H:%M"), items=general["items"],
                 iterations=general.get("iterations", 0), errors=cer(general["after"]),
                 books=sorted({i.book for i in train_items}))
        entry["quick_updates"] = entry.get("quick_updates", 0) + 1
        _save_known(code, (i.id for i in train_items))
    models.save_registry(data)

    families = dict(entry.get("families") or {})
    touched = {i.book for i in new}
    for fid, info in families.items():
        books = info.get("books") or []
        if not touched & set(books):
            continue
        items = [i for i in train_items if i.book in books]
        ftests = _tests(_items_for(code, "test"), books=set(books))
        if not ftests:
            own = sorted((i for i in items if i.source != "confident"), key=lambda i: i.id)
            ftests = own[::10][:TEST_PER_BOOK * 2]
            items = [i for i in items if i not in ftests]
        fnew = [i for i in items if i.id not in known and i.source != "confident"]
        fold = [i for i in items if i.id in known and i.source != "confident"]
        fquick = fnew + _sample(fold, min(len(fold), max(REPLAY_MIN, QUICK_REPLAY * len(fnew))), seed=len(fnew))
        say(f"{code}: training typeface family {fid[1:]} further ({len(fnew)} new images)")
        if engine == "tesseract":
            fit = _family_tesseract(code, fid, items, ftests, work, say, iterations, quick=fquick)
        else:
            fit = _family_kraken(code, fid, items, ftests, work, say, epochs or QUICK_EPOCHS)
        report["families"][fid] = {**fit, "books": books}
        report["lines"].append(f"  family {fid[1:]} ({', '.join(books)}): "
                               f"{'its own model, trained further' if fit['kept'] else 'the general model reads it as well; dropped'}")
        changed = True
        if fit["kept"]:
            families[fid] = {**info, "errors": cer(fit["after"]), **({"pack": fit["pack"]} if engine == "kraken" else {})}
            families[fid].pop("base", None)  # made over the general model in use
        else:
            _drop_families(code, {fid: info}, {})
            families.pop(fid)
    if changed:
        data = models.registry()
        entry = data.setdefault(code, {"engine": engine})
        entry["families"] = families
        entry["generation"] = entry.get("generation", 0) + 1
        entry["pool_items"] = len(pool.index(pool.key_for(code)))
        models.save_registry(data)
    _prune_bases(code)


def _prune_bases(code: str) -> None:
    """Kraken: earlier general models no family pack is laid over any more."""
    if models.engine_of(code) != "kraken":
        return
    used = {info.get("base") for info in (models.entry(code).get("families") or {}).values()}
    for base in models.kraken_dir(code).glob("base-g*.safetensors"):
        if str(base) not in used:
            base.unlink(missing_ok=True)


def _drop_families(code: str, old: dict, new: dict) -> None:
    """Files of family models no longer kept."""
    from .resources import user_tessdata_dir

    for fid, info in old.items():
        if fid in new:
            continue
        if models.engine_of(code) == "tesseract":
            (user_tessdata_dir() / f"{models.tesseract_name(code, fid)}.traineddata").unlink(missing_ok=True)
            (models.tesseract_dir(code) / f"{fid}.traineddata").unlink(missing_ok=True)
        elif info.get("pack"):
            Path(info["pack"]).unlink(missing_ok=True)


def _name(code: str) -> str:
    from .languages import name

    return name(code)


# -- a book --------------------------------------------------------------------------------


def choose_for_workspace(workspace: Path, book: str, codes: list[str], progress: Say | None = None) -> dict[str, str]:
    """Try each language's models on this book's own corrections and remember the best for it.
    Returns code -> description of the model chosen."""
    from .train import errors_kraken

    say = progress or (lambda msg: None)
    state = models.workspace_state(workspace)
    chosen = dict(state.get("chosen") or {})
    out = {}
    for code in codes:
        code = models.stock_code(code)
        have = models.choices(code)
        if len(have) < 2:
            continue
        items = [i for i in _items_for(code, "train") + _items_for(code, "test")
                 if i.book == book and i.source == "correction"][:TEST_PER_BOOK]
        if len(items) < 5:
            continue
        say(f"{code}: trying its models on this book's {len(items)} corrections")
        scores = {}
        with tempfile.TemporaryDirectory(prefix="ectype-choose-") as tmp:
            for which in have:
                r = models._resolved(code, which)
                if r.engine == "tesseract":
                    from .resources import user_tessdata_dir

                    scores[which] = cer(_tess_errors(user_tessdata_dir() / f"{r.model}.traineddata", items, Path(tmp), r.model))
                else:
                    scores[which] = cer(errors_kraken(r.model, items, r.pack))
        best = min((w for w in scores if scores[w] is not None), key=lambda w: scores[w], default=None)
        if best:
            chosen[code] = {"which": best, "generation": models.entry(code).get("generation", 0)}
            out[code] = f"{models.describe(models._resolved(code, best))}: {100 * scores[best]:.1f}% on its corrections"
    models.save_workspace_state(workspace, chosen=chosen)
    return out
