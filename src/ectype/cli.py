"""Command-line interface: `ectype analyze | compare | synth`, plus `--credits`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__


def _default_out(kind: str, path: Path) -> Path:
    return Path("reports") / f"{kind}-{path.stem.replace(' ', '_')}"


def cmd_analyze(args: argparse.Namespace) -> int:
    from .analyze import analyze_pdf

    out = Path(args.output) if args.output else _default_out("analyze", Path(args.pdf))
    summary = analyze_pdf(Path(args.pdf), out, images=not args.no_images, glyph_sheets=not args.no_glyphs)
    print(f"{summary['page_count']} pages, {summary['font_count']} fonts "
          f"({summary['synthesized_font_count']} synthesized), {summary['file_bytes']:,} bytes")
    print(f"Report: {out / 'index.html'}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from .compare import compare

    labels = tuple(args.labels.split(",", 1)) if args.labels else ("A", "B")
    out = Path(args.output) if args.output else _default_out("compare", Path(args.b))
    s = compare(Path(args.a), Path(args.b), out, dpi=args.dpi, pages=args.pages,
                truth=Path(args.truth) if args.truth else None, labels=labels,
                register=args.align)
    m = s["mean"]
    print(f"{s['pages_compared']} pages · mean SSIM {m['ssim']} · ink disagreement {m['ink_disagreement']} ({m['ink_disagreement_1px']} allowing 1 px)")
    if s["truth"]:
        print(f"CER vs truth: {labels[0]} {m['cer_a']} · {labels[1]} {m['cer_b']}")
    print(f"Report: {out / 'index.html'}")
    return 0


def cmd_preprocess(args: argparse.Namespace) -> int:
    from .compare import parse_pages
    from .preprocess import load_overrides, preprocess

    out = Path(args.output) if args.output else _default_out("work", Path(args.input))
    pages = [i + 1 for i in parse_pages(args.pages, 100000)] if args.pages else None
    s = preprocess(Path(args.input), out, keep_spreads=args.keep_spreads,
                   overrides=load_overrides(Path(args.overrides) if args.overrides else None),
                   pages=pages, deskew=not args.no_deskew, dpi=args.dpi)
    flagged = [str(src["page"]) for src in s["sources"] if src["flags"]]
    print(f"{len(s['sources'])} source pages -> {len(s['pages'])} pages"
          + (f"; check source pages {', '.join(flagged)}" if flagged else ""))
    print(f"Pages: {out / 'pages'} · report: {out / 'preprocess' / 'index.html'}")
    return 0


def cmd_ocr(args: argparse.Namespace) -> int:
    from .ocr import ocr_pages

    ws = Path(args.workspace)
    lang = args.lang
    if lang is None:  # the project's languages (the app's choice, or a trained model), else English
        from .project import Project

        lang = Project(ws).settings.lang if (ws / "project.json").is_file() else "eng"
    s = ocr_pages(ws / "pages", ws / "ocr", engine=args.engine, lang=lang)
    words = sum(r["words"] for r in s["pages"])
    print(f"{len(s['pages'])} pages, {words} words ({lang}). Report: {ws / 'ocr' / 'index.html'}")
    if s.get("unread_scripts"):
        from .languages import name

        found = ", ".join(f"{script} ({n} words)" for script, n in s["unread_scripts"].items())
        print(f"Words in scripts the OCR languages don't cover: {found}.")
        if s.get("suggest_languages"):
            codes = s["suggest_languages"]
            print(f"Add {', '.join(name(c) for c in codes)}: ectype languages --add {' '.join(codes)}, "
                  f"then ectype ocr {ws} --lang {lang}+{'+'.join(codes)}")
    return 0


def cmd_segment(args: argparse.Namespace) -> int:
    from .segment import segment_workspace

    ws = Path(args.workspace)
    truth = Path(args.truth) if args.truth else (ws / "truth" if (ws / "truth").is_dir() else None)
    s = segment_workspace(ws, truth_root=truth)
    units = sum(r["units"] for r in s["pages"])
    multi = sum(len(r["multi"]) for r in s["pages"])
    print(f"{len(s['pages'])} pages, {units} glyph units ({multi} multi-character)")
    if "eval" in s:
        e = s["eval"]
        print(f"Synthetic truth: {e['owned']} of {e['truth_chars']} characters own the right ink ({100 * e['owned_share']:.2f}%)")
    print(f"Report: {ws / 'segment' / 'index.html'}")
    return 0


def cmd_substitute(args: argparse.Namespace) -> int:
    from .substitute import substitute_workspace
    from .synth import find_font

    ws = Path(args.workspace)
    s = substitute_workspace(ws, find_font(args.font), out_name=args.name, background_dpi=args.background_dpi,
                              image=getattr(args, "page_image", "gray"))
    w = s["within_1px"] or {}
    print(f"{ws / 'substitute' / s['pdf']}: {s['bytes']:,} bytes; word ends within 1 px of the scan: "
          f"left {100 * w.get('left', 0):.1f}%, right {100 * w.get('right', 0):.1f}%")
    print(f"Report: {ws / 'substitute' / 'index.html'}")
    return 0


def cmd_cluster(args: argparse.Namespace) -> int:
    from .cluster import cluster_workspace

    ws = Path(args.workspace)
    progress = _ConsoleProgress()
    s = cluster_workspace(ws, threshold=args.threshold, progress=progress.phase)
    progress.end_line()
    print(f"{s['instances']} glyph instances -> {len(s['glyphs'])} averaged glyphs in {len(s['styles'])} styles; "
          f"{len(s['suspects'])} probable OCR misreadings listed")
    print(f"Report: {ws / 'cluster' / 'index.html'}")
    return 0


def cmd_fonts(args: argparse.Namespace) -> int:
    from .fontbuild import build_workspace_fonts

    ws = Path(args.workspace)
    progress = _ConsoleProgress()
    s = build_workspace_fonts(ws, family=args.family, threshold=args.threshold, progress=progress.phase)
    progress.end_line()
    bad = [st["style"] for st in s["styles"]
           if st["validation"]["ots"] not in ("pass", "not installed") or st["validation"]["fonttools"] != "pass"]
    checks = "OTS and fontTools" if all(st["validation"]["ots"] == "pass" for st in s["styles"]) else "fontTools"
    print(f"{len(s['styles'])} fonts, {sum(st['glyphs'] for st in s['styles'])} glyphs; "
          + (f"all pass {checks}" if not bad else f"validation problems in styles {bad}"))
    print(f"Report: {ws / 'fonts' / 'index.html'}")
    return 0 if not bad else 1


def cmd_traced(args: argparse.Namespace) -> int:
    from .traced import traced_workspace

    ws = Path(args.workspace)
    s = traced_workspace(ws, out_name=args.name, background_dpi=args.background_dpi,
                              image=getattr(args, "page_image", "gray"))
    w = s["within_1px"] or {}
    print(f"{ws / 'traced' / s['pdf']}: {s['bytes']:,} bytes ({s['bytes_per_page']:,} per page); "
          f"{s['words']} words as text, {s['fallback_words']} left to the image")
    print(f"Word ends within 1 px of the scan: left {100 * w.get('left', 0):.1f}%, right {100 * w.get('right', 0):.1f}%")
    print(f"Report: {ws / 'traced' / 'index.html'}")
    return 0


def cmd_textlayer(args: argparse.Namespace) -> int:
    from .textlayer import textlayer_workspace

    ws = Path(args.workspace)
    s = textlayer_workspace(ws, out_name=args.name, image=args.page_image)
    print(f"{ws / 'textlayer' / s['pdf']}: {s['bytes']:,} bytes; {s['words']} words as an invisible text layer")
    return 0


def cmd_languages(args: argparse.Namespace) -> int:
    from . import languages
    from .resources import tessdata_dir

    if args.add:
        def show(code, got, total):
            print(f"\r{languages.name(code)}: {got / 1e6:.1f} MB" + (f" of {total / 1e6:.1f} MB" if total else ""), end="", flush=True)

        done = languages.download(args.add, show)
        print(f"\nInstalled: {', '.join(languages.name(c) for c in done)} (in {tessdata_dir()})")
        return 0
    installed = languages.installed()
    print(f"Installed ({tessdata_dir()}):")
    for code in languages.tesseract_codes(installed):
        print(f"  {code:14} {languages.name(code)}")
    _print_kraken_models(installed)
    if args.all:
        print("Available to add (ectype languages --add CODE ...):")
        for code in languages.available():
            if code not in installed:
                print(f"  {code:14} {languages.name(code)}")
    return 0


def _print_kraken_models(installed: list[str]) -> None:
    """The Kraken models installed, by the script they read (a many-script model under each)."""
    from . import kraken, languages

    models = languages.kraken_codes(installed)
    if not models:
        return
    print("Kraken models (choose with --kraken LANG=MODEL, e.g. --kraken heb=%s):"
          % models[0].removeprefix(kraken.PREFIX))
    scripts = sorted({s for k in models for s in kraken.reads(k)})
    for script in scripts:
        print(f"  {script}:")
        for k in languages.models_for_script(script):
            print(f"    {k.removeprefix(kraken.PREFIX):12} {languages.reader_title(k)}")


def _engine_problem() -> str | None:
    from . import kraken

    if kraken.worker_command() is None:
        return ("The Kraken engine isn't installed. In the Ectype OCR app, open Languages > Add languages… > "
                "Kraken models and press \"Download the Kraken engine\" (it is saved in your user folder and "
                "the command line uses it too).")
    return None


def _kraken_code(text: str) -> str:
    text = text.strip()
    if text.startswith("kraken_"):
        return text
    from . import kraken

    if "zenodo" in text.lower() or "/" in text:
        return kraken.code_for(text)
    return kraken.PREFIX + text.lstrip("#")


def cmd_kraken(args: argparse.Namespace) -> int:
    from . import kraken, languages

    if args.action == "list":
        models = kraken.installed_models()
        if not models:
            print("No Kraken models are installed. See what can be added: ectype kraken available "
                  "(then ectype kraken add <number>).")
            return 0
        print("Installed Kraken models (use one with --kraken LANG=MODEL, e.g. --kraken heb=%s):"
              % next(iter(models)).removeprefix(kraken.PREFIX))
        for code in sorted(models, key=lambda c: languages.name(c).lower()):
            m = models[code]
            try:
                size = f"{Path(m['file']).stat().st_size / 1e6:.1f} MB"
            except (OSError, KeyError):
                size = "?"
            trained = f"  (trained on {m['book']})" if m.get("book") else ""
            print(f"  {code.removeprefix(kraken.PREFIX):12} {', '.join(kraken.reads(code)) or '?':14} "
                  f"{m.get('title') or code}{trained}")
            print(f"  {'':12} {code}  {m.get('doi') or ''}  {size}")
        return 0
    if args.action == "available":
        problem = _engine_problem()
        if problem:
            print(problem)
            return 2
        records = kraken.list_models(progress=lambda done, total: None)
        script = (args.script or "").lower()
        have = {m.get("doi") for m in kraken.installed_models().values()}
        shown = 0
        for r in records:
            if script and script not in [s.lower() for s in kraken.scripts_of(r)]:
                continue
            if args.printed and kraken.printed(r) is False:
                continue
            note = "  (installed)" if r["doi"] in have else ""
            cer = f", error {r['cer']:.1f}%" if isinstance(r.get("cer"), (int, float)) else ""
            kind = {True: "printed", False: "handwritten", None: ""}[kraken.printed(r)]
            print(f"  {kraken.code_for(r['doi']).removeprefix(kraken.PREFIX):10} {', '.join(kraken.scripts_of(r)):12} "
                  f"{r.get('summary') or r['doi']} ({kind}{cer}){note}")
            shown += 1
        print(f"{shown} models. Add one: ectype kraken add <number>. (Models that read whole pages, such as Party, "
              "are left out: Ectype's Kraken reads lines.)")
        return 0
    if args.action == "add":
        problem = _engine_problem()
        if problem:
            print(problem)
            return 2
        code = _kraken_code(args.model)
        if code in kraken.installed_models():
            print(f"Already installed: {languages.reader_title(code)}")
            return 0
        number = code.removeprefix(kraken.PREFIX)
        record = next((r for r in kraken.list_models(progress=lambda done, total: None)
                       if kraken.code_for(r["doi"]) == code), None)
        if record is None:
            print(f"Model {number} isn't among Kraken's line-reading models"
                  + (" (it reads whole pages, which Ectype can't use)" if _page_model_named(number) else "")
                  + ". See: ectype kraken available")
            return 2
        progress = _ConsoleProgress()

        def show(done: int, total: int) -> None:
            if total:
                progress("fraction", str(done / total))

        print(f"Downloading {record.get('summary') or record['doi']} …", flush=True)
        try:
            got = kraken.install_model(record, show)
        except kraken.KrakenError as exc:
            progress.end_line()
            print(f"Not installed: {exc}")
            return 1
        progress.end_line()
        print(f"Installed: {languages.reader_title(got)}. Use it with --kraken <language>={got.removeprefix(kraken.PREFIX)}")
        return 0
    if args.action == "remove":
        code = _kraken_code(args.model)
        models = kraken.installed_models()
        if code not in models:
            print(f"No installed Kraken model {args.model}. See: ectype kraken list")
            return 2
        import shutil

        title = languages.reader_title(code)
        folder = Path(models[code]["file"]).parent
        kraken.unregister(code)
        if folder.parent == kraken.models_dir():
            shutil.rmtree(folder, ignore_errors=True)
        print(f"Removed: {title}")
        return 0
    return 2


def _page_model_named(number: str) -> bool:
    """Whether a number is a page model (Party), judged from the listing kept on disk."""
    from . import kraken

    try:
        data = json.loads((kraken.models_dir() / "listing.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(kraken.code_for(r["doi"]) == kraken.PREFIX + number and kraken.page_model(r) for r in data)


def apply_kraken_options(args: argparse.Namespace) -> str | None:
    """--kraken LANG=MODEL (repeatable) put into the language list; a refusal in plain words."""
    from . import languages

    choices = getattr(args, "kraken", None)
    if not choices:
        return None
    lang = args.lang
    if lang is None:  # ocr: the project's own languages
        from .project import Project

        ws = Path(args.workspace)
        lang = Project(ws).settings.lang if (ws / "project.json").is_file() else "eng"
    try:
        args.lang = languages.join(languages.apply_kraken_choices(languages.split(lang), choices))
    except ValueError as exc:
        return str(exc)
    return None


def cmd_idml(args: argparse.Namespace) -> int:
    from .converter import ConverterMissing
    from .idml import IdmlOptions
    from .project import Project

    ws = Path(args.workspace)
    project = Project(ws)
    dest = Path(args.output) if args.output else ws / f"{project.settings.name or ws.name}-{args.mode}.idml"
    options = IdmlOptions(mode=args.mode, include_scan=not args.no_scan, page_markers=args.page_markers,
                          check_below=0.0 if args.no_check_ocr else 70.0, profile=args.profile)
    try:
        progress = _ConsoleProgress()
        out = project.export_idml(dest, options, progress=progress)
        progress.end_line()
    except ConverterMissing as exc:
        print(exc)
        return 1
    for alert in out.get("alerts", []):
        print(f"Note: {alert}")
    if args.mode == "facsimile":
        for name, face in out["styles"].items():
            print(f"  {name}: {face}")
    return 0


def cmd_strip_text(args: argparse.Namespace) -> int:
    from . import striptext

    if args.output and len(args.pdfs) > 1:
        print("-o names one result, so give it with one PDF only.")
        return 2
    status = 0
    for name in args.pdfs:
        src = Path(name)
        if not src.is_file():
            print(f"No such file: {src}")
            status = 1
            continue
        try:
            out = striptext.strip_text(src, Path(args.output) if args.output else None, everything=args.all)
        except (FileExistsError, ValueError) as exc:
            print(f"{src.name}: {exc}")
            status = 1
            continue
        what = "text" if args.all else "hidden text"
        if out["output"] is None:
            print(f"{src.name}: no hidden text layer found; nothing written."
                  if not args.all else f"{src.name}: no text found; nothing written.")
        else:
            print(f"{src.name}: {what} removed from {out['pages_changed']} of {out['pages']} pages.\n  Saved: {out['output']}")
        if out["visible_left"]:
            print("  It also has visible text (type drawn on the page, as Acrobat's ClearScan and Ectype's traced PDFs "
                  "have); that was left.\n  --all removes it too, which leaves gaps where the type was.")
        if out["mixed"]:
            print(f"  {out['mixed']} piece(s) of text holding both hidden and visible words were left as they were.")
    return status


def cmd_data_folder(args: argparse.Namespace) -> int:
    from . import datafolder
    from .resources import user_data_dir

    if args.move:
        progress = _ConsoleProgress()
        try:
            result = datafolder.move_data(Path(args.move), progress)
        except ValueError as exc:
            progress.end_line()
            print(exc)
            return 2
        except Exception as exc:  # copying or checking failed: nothing was changed
            progress.end_line()
            print(f"Nothing was moved: {exc}")
            return 1
        progress.end_line()
        print(f"Ectype's data is now in {result['dest']} ({result['files']:,} files, {result['bytes'] / 1e9:.1f} GB).")
        if result["left"]:
            print("These old files could not be deleted (in use); delete them yourself later:")
            for name in result["left"]:
                print(f"  {name}")
        return 0
    folder = user_data_dir()
    files, size = datafolder.size_of(folder)
    print(f"Ectype's data is in {folder}")
    print(f"  {files:,} files, {size / 1e9:.1f} GB")
    return 0


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def cmd_free_space(args: argparse.Namespace) -> int:
    from . import freespace

    asked = args.cache or args.font_lines is not None
    if not asked:
        r = freespace.report()
        print(f"Ectype's data is in {r['folder']} ({_gb(r['total']['bytes'])} in all)")
        print(f"  Prepared copies of training lines: {_gb(r['cache']['bytes'])} (made again when needed)   [--cache]")
        print(f"  Leftovers of stopped runs: {_gb(r['leftovers']['bytes'])}   [--cache]")
        print(f"  Working files of book projects: {_gb(r['project_work']['bytes'])} (made again when needed)   [--cache]")
        print(f"  Book projects: {_gb(r['projects']['bytes'])} in {r['projects']['folders']} books "
              "(their readings and corrections: not removed here)")
        for lang, by in r["pool"].items():
            synth, corr = by["synthetic"], by["correction"]
            print(f"  {lang}: lines set from fonts {synth['lines']:,} ({_gb(synth['bytes'])})   [--font-lines {lang}];"
                  f" your corrections {corr['lines']:,} ({_gb(corr['bytes'])}, never removed)")
        print("Lines set from fonts are made again by Training -> Train from fonts (this can take hours).")
        print("To free space:  ectype free-space --cache      ectype free-space --font-lines [LANG ...]")
        return 0
    progress = _ConsoleProgress()
    freed = 0
    if args.cache:
        n = freespace.remove_cache(progress) + freespace.remove_leftovers(progress) + freespace.remove_project_work(progress)
        progress.end_line()
        print(f"Removed prepared copies, leftovers and the book projects' working files: {_gb(n)} freed.")
        freed += n
    if args.font_lines is not None:
        langs = args.font_lines or None
        result = freespace.remove_font_lines(langs, progress)
        progress.end_line()
        for lang in (langs or []):
            if lang not in result:
                print(f"No training pool for {lang}.")
        for lang, r in result.items():
            print(f"Removed {r['lines']:,} lines set from fonts in {lang}: {_gb(r['bytes'])} freed.")
            freed += r["bytes"]
    print(f"Freed {_gb(freed)} in all. Your corrections were not touched.")
    return 0


class _ConsoleProgress:
    """Progress for a console: stage messages as lines; ("fraction", "0.42") as one percentage
    kept up to date on a single line (only in a terminal; left out when output goes to a file)."""

    def __init__(self) -> None:
        self.open = False

    def __call__(self, stage: str, msg: str) -> None:
        if stage == "fraction":
            if sys.stdout.isatty():
                try:
                    print(f"\r  {float(msg) * 100:5.1f}% done", end="", flush=True)
                    self.open = True
                except ValueError:
                    pass
            return
        self.end_line()
        print(f"{stage}: {msg}", flush=True)

    def phase(self, done: int, total: int, what: str) -> None:
        """Progress inside a long step (`progress(done, total, what)`): the phase and its count on one line."""
        if sys.stdout.isatty():
            print(f"\r  {what}: {done:,} of {total:,}\x1b[K", end="", flush=True)
            self.open = True
            if done >= total:
                self.end_line()

    def end_line(self) -> None:
        if self.open:
            print(flush=True)
            self.open = False


def _language_note(lang: str) -> None:
    from . import languages

    names = ", ".join(languages.name(c) for c in languages.split(lang))
    print(f"Reading in: {names} ({lang}). For a book in other languages give --lang, e.g. --lang spa "
          "(Spanish) or --lang eng+grc+heb; `ectype languages --all` lists them.", flush=True)


def cmd_text(args: argparse.Namespace) -> int:
    from .project import Project

    ws = Path(args.workspace)
    if ws.is_file():  # a scan, not a project folder: its own project if it has one, else read it now
        found = Project.find_for_scan(ws)  # looking never makes a project folder
        if found is None or not any((found / "ocr").glob("*.ocr.json")):
            args.inputs, args.recursive, args.redo, args.keep = [str(ws)], False, True, False
            return cmd_text_files(args)
        ws = found
    project = Project(ws)
    dest = Path(args.output) if args.output else None
    out = project.write_text(dest, form="lines" if args.lines else "paragraphs", page_markers=not args.no_page_markers,
                             keep_heads=args.keep_heads, keep_marks=args.keep_marks)
    print(f"{out['text']}: {out['pages']} pages, {out['paragraphs']} paragraphs, {out['notes']} notes, "
          f"{out['chars']:,} characters")
    return 0


def cmd_text_files(args: argparse.Namespace) -> int:
    from .textfiles import make_text_files, summary_text

    _language_note(args.lang)
    progress = _ConsoleProgress()
    try:
        result = make_text_files([Path(i) for i in args.inputs], lang=args.lang, recursive=args.recursive,
                                 redo=args.redo, keep=args.keep, keep_marks=args.keep_marks, progress=progress)
    except ValueError as exc:  # a language not installed
        progress.end_line()
        print(exc)
        return 2
    progress.end_line()
    print(summary_text(result))
    return 1 if result["failed"] and not result["done"] else 0


def cmd_train(args: argparse.Namespace) -> int:
    """Train the OCR languages' models from their pools, with this project's corrections added
    (or, with --lang and no workspace, just those languages)."""
    from .train import TrainingError

    say = lambda msg: print(f"train: {msg}", flush=True)  # noqa: E731
    try:
        if args.workspace:
            from .project import Project

            reports = Project(Path(args.workspace)).train_model(lambda stage, msg: stage == "fraction" or say(msg),
                                                                args.mode)
        else:
            from .training import train_languages

            if not args.lang:
                print("Give a project folder, or the languages to train (--lang heb grc).")
                return 2
            done = train_languages(args.lang, say, mode=args.mode)  # side by side
            for code, r in done.items():
                if isinstance(r, Exception):
                    print(f"{code}: {r}")
            reports = [r for r in done.values() if not isinstance(r, Exception)]
            if not reports:
                return 1
    except TrainingError as exc:
        print(exc)
        return 1
    for r in reports:
        for line in r["lines"]:
            print(line)
        if r.get("chosen"):
            print(f"  For this book: {r['chosen']}.")
    return 0


def cmd_train_synthetic(args: argparse.Namespace) -> int:
    """Lines set from known text in a folder of fonts, into the training pools."""
    from .synthtrain import generate

    r = generate(Path(args.fonts), args.script, args.lines, progress=lambda m: print(m, flush=True))
    for script, names in r["fonts"].items():
        print(f"{script}: {len(names)} fonts: {', '.join(names)}")
    for name, why in r["skipped"].items():
        print(f"  {name}: {why}")
    print("Added:", ", ".join(f"{lang} {n} lines" for lang, n in r["added"].items()) or "nothing new")
    print("Train with: ectype train --lang " + " ".join(r["added"]))
    return 0


def _pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def cmd_train_scans(args: argparse.Namespace) -> int:
    """A scanned edition read and matched to its known text, into the training pools."""
    from .align import read_and_align

    r = read_and_align(Path(args.scan), args.text, args.edition, args.lang, _pages(args.pages),
                       progress=lambda m: print(m, flush=True))
    print(f"{r['edition']}: {r['kept']} lines matched to the text and added to the {r['lang']} pool; {r['skipped']} skipped.")
    return 0


def cmd_train_all(args: argparse.Namespace) -> int:
    from .resources import user_data_dir
    from .train import TrainingError
    from .trainall import run, write_template

    if args.write_config:
        print(write_template(Path(args.write_config)))
        return 0
    config = Path(args.config) if args.config else user_data_dir() / "training.ini"
    if not config.is_file():
        print(f"No settings file at {config}. Make one with: ectype train-all --write-config \"{config}\"")
        return 2
    try:
        report = run(config, progress=lambda m: print(m, flush=True), iterations=args.iterations, epochs=args.epochs)
    except (TrainingError, FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Stopped: {exc}")
        return 1
    print()
    print("\n".join(report))
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    from . import models, pool
    from .languages import name

    if args.remove:
        models.remove(args.remove)
        print(f"Removed the trained models of {name(args.remove)}.")
    if args.remove_pool:
        pool.remove_language(args.remove_pool)
        print(f"Emptied the training pool of {args.remove_pool}.")
    if args.remove_legacy:
        for code in models.legacy():
            models.remove_legacy(code)
            print(f"Removed {code}.")
    reg = models.registry()
    for code, e in sorted(reg.items()):
        have = models.choices(code)
        if not have:
            continue
        g = e.get("general", {})
        print(f"{name(code)} [{code}]: general model trained {g.get('trained', '?')} on {g.get('items', '?')} images "
              f"({len(g.get('books', []))} sources), {models.size_of(code) / 1e6:.1f} MB in all")
        for fid, f in (e.get("families") or {}).items():
            print(f"  family {fid[1:]}: {', '.join(f.get('books', []))}")
    for lang in pool.languages():
        books = pool.summary(lang)
        print(f"Pool {lang}: {sum(b['train'] + b['test'] for b in books.values())} images from {len(books)} sources")
        for book, b in sorted(books.items()):
            print(f"  {book} ({b['source']}): {b['train']} to train, {b['test']} to test")
    old = models.legacy()
    if old:
        print("Per-book models from earlier versions (remove with --remove-legacy): " + ", ".join(old))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Check for a newer release; with --install (the installed Windows app), install it."""
    from . import __version__, updates

    r = updates.check(timeout=15)
    if r["status"] == "error":
        print(f"Couldn't check for updates: {r['error']}")
        return 1
    if r["status"] != "newer":
        print(f"Ectype OCR {__version__} is the latest version.")
        return 0
    print(f"Ectype OCR {r['latest']} is available (you have {__version__}): {r['url']}")
    if not args.install:
        return 0
    if not updates.can_install() or not r.get("asset"):
        print("Install it from the releases page (this copy isn't the installed Windows app).")
        return 1
    setup = updates.download(r["asset"], progress=lambda f: print(f"\rDownloading {100 * f:.0f}%", end="", flush=True))
    print(f"\nStarting {setup.name}; Ectype opens again when it's done.")
    updates.run_installer(setup, updates.installed_app())
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Every stage, scan to PDF, and the PDF copied beside the scan ("<name> - OCR.pdf"). Traced
    and substitute runs keep the book's project folder (in Ectype's projects folder, as the app
    has it, unless -o) for correcting in Review; text and plaintext runs work in a temporary
    folder and delete it, unless --keep or -o."""
    import shutil
    import uuid

    from .project import Project
    from .textfiles import work_root

    scan = Path(args.input)
    temporary = args.mode in ("text", "plaintext") and not args.output and not args.keep
    if temporary:
        ws = work_root() / f"{scan.stem[:40]}-{uuid.uuid4().hex[:8]}.ectype"
    elif args.output:
        ws = Path(args.output)
    else:
        ws = Project.for_scan(scan).workspace
    try:
        return _run_in(args, scan, ws)
    finally:
        if temporary:
            shutil.rmtree(ws, ignore_errors=True)
            try:
                ws.parent.rmdir()
            except OSError:
                pass


def _run_in(args: argparse.Namespace, scan: Path, ws: Path) -> int:
    from .project import Project, beside_scan

    args.name = args.name or scan.stem
    project = Project(ws, scan)  # remembers the scan and the choices, so the app can open it
    project.settings.mode = args.mode
    project.settings.name = args.name
    project.settings.page_image = args.page_image
    project.set_languages(args.lang.split("+"))
    project.save_settings()
    steps = [
        ("preprocess", cmd_preprocess, argparse.Namespace(input=args.input, output=str(ws), keep_spreads=args.keep_spreads,
                                                          overrides=args.overrides, pages=args.pages, no_deskew=False, dpi=300)),
        ("ocr", cmd_ocr, argparse.Namespace(workspace=str(ws), engine="tesseract", lang=args.lang)),
    ]
    if args.mode not in ("text", "plaintext"):
        steps.append(("segment", cmd_segment, argparse.Namespace(workspace=str(ws), truth=None)))
    if args.mode == "plaintext":
        steps.append(("plaintext", cmd_text, argparse.Namespace(workspace=str(ws), output=None, lines=False,
                                                                no_page_markers=False, keep_heads=False,
                                                                keep_marks=False, lang=args.lang)))
    elif args.mode == "text":
        steps.append(("textlayer", cmd_textlayer, argparse.Namespace(workspace=str(ws), name=args.name, page_image=args.page_image)))
    elif args.mode == "substitute":
        steps.append(("substitute", cmd_substitute, argparse.Namespace(workspace=str(ws), font=args.font, name=args.name,
                                                                       background_dpi=150, page_image=args.page_image)))
    else:
        steps += [
            ("cluster", cmd_cluster, argparse.Namespace(workspace=str(ws), threshold=0.25)),
            ("fonts", cmd_fonts, argparse.Namespace(workspace=str(ws), family=None, threshold=0.45)),
            ("traced", cmd_traced, argparse.Namespace(workspace=str(ws), name=args.name, background_dpi=150,
                                                           page_image=args.page_image)),
        ]
    missing = None
    for name, fn, ns in steps:
        print(f"== {name}")
        fn(ns)
        if name == "ocr":
            missing = Project(ws).missing_languages()
            if missing and args.mode in ("traced", "substitute") and not args.anyway:
                print(_missing_message(args, scan, *missing, stopped=True))
                return 3
    pdf = Project(ws).output_pdf()
    if pdf is not None:
        print(f"PDF: {beside_scan(pdf, scan)}")
    if missing:  # text and plaintext runs finish (cheap), then say what to do
        print(_missing_message(args, scan, *missing, stopped=False))
    return 0


def _missing_message(args: argparse.Namespace, scan: Path, codes: list[str], found: dict[str, int],
                     stopped: bool) -> str:
    """Thomson's Athanasius read as English only went on for hours on misread Greek."""
    from . import languages
    from .project import found_words

    lang = "+".join(dict.fromkeys(args.lang.split("+") + codes))
    mode = f" --mode {args.mode}" if args.mode != "traced" else ""
    lines = [("Stopped after reading the pages: " if stopped else "Note: ")
             + f"this book has {found_words(found)} that {args.lang} doesn't read."]
    absent = [c for c in codes if c not in languages.installed()]
    if absent:
        lines.append(f"  ectype languages --add {' '.join(absent)}")
    lines.append(f'  ectype run "{scan}" --lang {lang}{mode}')
    if stopped:
        lines.append("Or, to go on without it anyway, run the same command with --anyway.")
    return "\n".join(lines)


def cmd_rebuild(args: argparse.Namespace) -> int:
    """After corrections: segmentation through the output PDF again."""
    ws = str(args.workspace)
    if args.mode == "plaintext":
        return cmd_text(argparse.Namespace(workspace=ws, output=None, lines=False, no_page_markers=False, keep_heads=False))
    if args.mode == "text":
        return cmd_textlayer(argparse.Namespace(workspace=ws, name=args.name, page_image=args.page_image))
    cmd_segment(argparse.Namespace(workspace=ws, truth=None))
    if args.mode == "substitute":
        return cmd_substitute(argparse.Namespace(workspace=ws, font=args.font, name=args.name, background_dpi=150,
                                                 page_image=args.page_image))
    cmd_cluster(argparse.Namespace(workspace=ws, threshold=0.25))
    cmd_fonts(argparse.Namespace(workspace=ws, family=None, threshold=0.45))
    return cmd_traced(argparse.Namespace(workspace=ws, name=args.name, background_dpi=150, page_image=args.page_image))


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .gui import main as gui_main
    except ImportError as exc:
        print(f"The desktop app needs PySide6: pip install 'ectype-ocr[gui]' ({exc})")
        return 1
    return gui_main([args.target] if args.target else [])


def cmd_synth(args: argparse.Namespace) -> int:
    from .synth import DEFAULT_TEXT, Degrade, find_font, make_page, make_set

    if args.set:
        out = Path(args.output) if args.output else Path("reports") / "work-synth"
        made = make_set(out, count=args.set, seed=args.seed)
        print(f"{len(made)} synthetic pages -> {out / 'pages'} (truth in {out / 'truth'})")
        return 0

    degrade = Degrade.none() if args.clean else Degrade(
        skew_degrees=args.skew, ink_spread_px=args.ink_spread, blur_sigma=args.blur,
        noise_sigma=args.noise, speckles=args.speckles, jpeg_quality=args.jpeg or None,
    )
    text = Path(args.text).read_text(encoding="utf-8") if args.text else DEFAULT_TEXT
    out = Path(args.output) if args.output else Path("reports") / "synth"
    truth = make_page(out, text=text, font_path=find_font(args.font), dpi=args.dpi,
                      point_size=args.size, degrade=degrade, seed=args.seed)
    print(f"{len(truth['lines'])} lines in {truth['font']['family']} {truth['font']['style']} "
          f"at {args.size:g} pt, {args.dpi} dpi -> {out}")
    return 0


def cmd_credits() -> int:
    from .resources import bundle_dir

    places = [Path(__file__).resolve().parents[2] / "CREDITS.md", Path("CREDITS.md")]
    if bundle_dir():
        places.insert(0, bundle_dir() / "CREDITS.md")
    for candidate in places:
        if candidate.is_file():
            print(candidate.read_text(encoding="utf-8"))
            return 0
    print("CREDITS.md not found; see https://github.com/RexBHavgreau/ectype-ocr/blob/main/CREDITS.md")
    return 1


GUIDE = """\
Each command has its own options (--lang, --page-markers, ...). To see them, put --help after
the command:   ectype text --help      ectype text-files --help      ectype run --help

Options go after the command and what it works on. Put a path with spaces in quotes.

Examples:
  Text files beside every scan in a folder (and its subfolders), read in Spanish:
    ectype text-files "D:\\Library" --recursive --lang spa
  A text file for one scan, read in English, Greek and Hebrew:
    ectype text "D:\\Library\\Treatise.pdf" --lang eng+grc+heb
  Keep Hebrew points and accents and Greek accents in the text:
    ectype text-files "D:\\Library" --keep-marks
  Make the text files again (only those Ectype made are replaced):
    ectype text-files "D:\\Library" --redo --lang spa
  A whole book, scan to PDF:
    ectype run "D:\\Library\\Treatise.pdf" --lang eng+grc+heb
  Where Ectype keeps its data (models, training), and moving it to another drive:
    ectype strip-text "D:\\Books\\Old scan.pdf"      (a copy without its bad hidden text layer)
    ectype data-folder      ectype data-folder --move "D:\\Ectype OCR"
  See what takes space in Ectype's data folder, and free some (your corrections are never removed):
    ectype free-space      ectype free-space --cache      ectype free-space --font-lines heb grc
  Languages: what is installed, what can be added, and adding some:
    ectype languages      ectype languages --all      ectype languages --add spa lat

preprocess, ocr, segment, cluster, fonts, traced, substitute and textlayer are single steps on a
book's project folder (the .ectype folder Ectype keeps in its data folder); for a scan use text,
text-files or run. `run --mode text` and `--mode plaintext` keep nothing but the result beside
the scan (add --keep to keep the project for Review); traced and substitute runs keep it.

  Read Hebrew with a particular Kraken model (it must be installed):
    ectype kraken list      ectype kraken available --script Hebrew      ectype kraken add 7051645
    ectype text "D:\\Library\\Grammar.pdf" --lang eng+heb --kraken heb=7051645

Language codes join with +: eng (English), spa (Spanish), lat (Latin), grc (ancient Greek),
heb (Hebrew), syr (Syriac), deu (German), fra (French), ita (Italian). Without --lang a scan is
read as English.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ectype", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Ectype OCR from the command line. Type a command, then what it works on, then its options.",
        epilog=GUIDE)
    parser.add_argument("--version", action="version", version=f"ectype {__version__}")
    parser.add_argument("--credits", action="store_true", help="show credits and exit")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("analyze", help="dump fonts, glyph sheets, page structure and images of a PDF")
    p.add_argument("pdf")
    p.add_argument("-o", "--output", help="report folder (default reports/analyze-<name>)")
    p.add_argument("--no-images", action="store_true", help="don't extract images")
    p.add_argument("--no-glyphs", action="store_true", help="don't extract fonts or draw glyph sheets")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("compare", help="compare two PDFs (or page images) page by page")
    p.add_argument("a", help="reference, e.g. the scan or Acrobat's output")
    p.add_argument("b", help="candidate, e.g. our output")
    p.add_argument("-o", "--output", help="report folder (default reports/compare-<b>)")
    p.add_argument("--dpi", type=float, default=150)
    p.add_argument("--pages", help="1-based pages, e.g. 1-3,5")
    p.add_argument("--truth", help="truth.json from synth, or .txt with pages separated by form feeds")
    p.add_argument("--labels", help="names for A and B, e.g. Acrobat,Ectype")
    p.add_argument("--align", action="store_true",
                   help="register B onto A: best quarter turn, then affine fit (for turned, deskewed or rescaled output such as Acrobat's)")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("preprocess", help="(a step, on a project folder) orient, split spreads and deskew a scan into one image per book page")
    p.add_argument("input", help="scanned PDF or page image")
    p.add_argument("-o", "--output", help="workspace folder (default reports/work-<name>); pages go to <workspace>/pages")
    p.add_argument("--keep-spreads", action="store_true", help="don't split two-page spreads")
    p.add_argument("--overrides", help="JSON of per-page corrections: rotate, split, skew")
    p.add_argument("--pages", help="1-based source pages, e.g. 1-3,5")
    p.add_argument("--no-deskew", action="store_true")
    p.add_argument("--dpi", type=float, default=300, help="render resolution for pages that aren't a single image")
    p.set_defaults(func=cmd_preprocess)

    p = sub.add_parser("ocr", help="(a step, on a project folder) OCR <workspace>/pages into <workspace>/ocr")
    p.add_argument("workspace")
    p.add_argument("--engine", default="tesseract", help="tesseract (default) or kraken")
    p.add_argument("--lang", help="engine language(s), e.g. eng or eng+grc (default: the project's, else eng)")
    p.add_argument("--kraken", action="append", metavar="LANG=MODEL",
                   help="read a language with a Kraken model, e.g. --kraken heb=7051645 (repeatable; see: ectype kraken list)")
    p.set_defaults(func=cmd_ocr)

    p = sub.add_parser("segment", help="(a step, on a project folder) tie OCR characters to their ink: <workspace>/segment")
    p.add_argument("workspace")
    p.add_argument("--truth", help="synthetic truth folder (default <workspace>/truth if present)")
    p.set_defaults(func=cmd_segment)

    p = sub.add_parser("substitute", help="(a step, on a project folder) build the substitute-mode PDF: <workspace>/substitute")
    p.add_argument("workspace")
    p.add_argument("--font", help="font file or family name (default: EB Garamond, else a system serif)")
    p.add_argument("--name", help="output file name without .pdf (default: the workspace name)")
    p.add_argument("--background-dpi", type=float, default=150, help="resolution of the erased background image")
    p.add_argument("--page-image", choices=["gray", "lossless"], default="gray", help="background as JPEG, or without loss (full resolution, larger)")
    p.set_defaults(func=cmd_substitute)

    p = sub.add_parser("cluster", help="(a step, on a project folder) group glyphs by style and average them: <workspace>/cluster")
    p.add_argument("workspace")
    p.add_argument("--threshold", type=float, default=0.25, help="shape distance (1 - correlation) that splits a group")
    p.set_defaults(func=cmd_cluster)

    p = sub.add_parser("fonts", help="(a step, on a project folder) trace composites and build one font per style: <workspace>/fonts")
    p.add_argument("workspace")
    p.add_argument("--family", help="font family name (default: Ectype <workspace>)")
    p.add_argument("--threshold", type=float, default=0.45, help="coverage at which a composite pixel counts as ink")
    p.set_defaults(func=cmd_fonts)

    p = sub.add_parser("traced", help="(a step, on a project folder) build the traced-mode PDF: <workspace>/traced")
    p.add_argument("workspace")
    p.add_argument("--name", help="output file name without .pdf (default: the workspace name)")
    p.add_argument("--background-dpi", type=float, default=150)
    p.add_argument("--page-image", choices=["gray", "lossless"], default="gray", help="background as JPEG, or without loss (full resolution, larger)")
    p.set_defaults(func=cmd_traced)

    p = sub.add_parser("run", help="every stage, scan to PDF")
    p.add_argument("input", help="scanned PDF or page image")
    p.add_argument("-o", "--output", help="project folder (default: Ectype's projects folder; text and plaintext keep nothing)")
    p.add_argument("--anyway", action="store_true",
                   help="go on to the slow steps even if the book has a script the languages don't read")
    p.add_argument("--keep", action="store_true",
                   help="keep the project for correcting in Review (text and plaintext modes delete it otherwise)")
    p.add_argument("--mode", choices=["traced", "substitute", "text", "plaintext"], default="traced",
                   help="text: just the scan with an invisible OCR text layer (no tracing); "
                        "plaintext: no PDF, the OCR text as a .txt file")
    p.add_argument("--font", help="substitute mode: font file or family name")
    p.add_argument("--name", help="output file name without .pdf")
    p.add_argument("--lang", default="eng", help="OCR languages, e.g. eng or eng+grc (see: ectype languages)")
    p.add_argument("--page-image", choices=["gray", "lossless", "bw"], default="gray",
                   help="grayscale JPEG, lossless grayscale (larger), or black and white (text mode only)")
    p.add_argument("--keep-spreads", action="store_true")
    p.add_argument("--overrides", help="JSON of per-page preprocessing corrections")
    p.add_argument("--pages", help="1-based source pages, e.g. 1-3,5")
    p.add_argument("--kraken", action="append", metavar="LANG=MODEL",
                   help="read a language with a Kraken model, e.g. --kraken heb=7051645 (repeatable; see: ectype kraken list)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("textlayer", help="(a step, on a project folder) build the text-layer PDF (the scan + invisible OCR text): <workspace>/textlayer")
    p.add_argument("workspace")
    p.add_argument("--name", help="output file name without .pdf (default: the workspace name)")
    p.add_argument("--page-image", choices=["gray", "lossless", "bw"], default="gray",
                   help="grayscale JPEG, lossless grayscale (larger), or black and white (CCITT G4)")
    p.set_defaults(func=cmd_textlayer)

    p = sub.add_parser("languages", help="list the OCR languages, or download more")
    p.add_argument("--add", nargs="+", metavar="CODE", help="download these languages, e.g. grc lat heb")
    p.add_argument("--all", action="store_true", help="also list the languages that can be added")
    p.set_defaults(func=cmd_languages)

    p = sub.add_parser("kraken", help="Kraken models: list the installed ones, find and add more, remove one")
    ksub = p.add_subparsers(dest="action", required=True)
    ksub.add_parser("list", help="the installed models: number, scripts, title, size")
    k = ksub.add_parser("available", help="models in Kraken's repository (needs the Kraken engine)")
    k.add_argument("--script", help="only models for this script, e.g. Hebrew, Greek, Syriac")
    k.add_argument("--printed", action="store_true", help="leave out models for handwriting")
    k = ksub.add_parser("add", help="download and install a model (needs the Kraken engine)")
    k.add_argument("model", help="its number (from `ectype kraken available`), its Zenodo DOI, or kraken_<number>")
    k = ksub.add_parser("remove", help="remove an installed model")
    k.add_argument("model", help="its number or code (from `ectype kraken list`)")
    p.set_defaults(func=cmd_kraken)

    p = sub.add_parser("idml", help="export the OCR text as an InDesign document (IDML), in a folder with its links")
    p.add_argument("workspace")
    p.add_argument("-o", "--output", help="the .idml path; the export folder takes its name (default <workspace>/<name>-<mode>.idml)")
    p.add_argument("--mode", choices=["reflow", "facsimile"], default="reflow",
                   help="reflow: laid out as a book by docx-to-idml; facsimile: line for line where the scan has it")
    p.add_argument("--profile", help="reflow: a docx-to-idml layout profile (name or .json)")
    p.add_argument("--page-markers", action="store_true", help="reflow: mark where each scanned page begins, {p. 172}")
    p.add_argument("--no-check-ocr", action="store_true", help="reflow: don't mark words the OCR was unsure of")
    p.add_argument("--no-scan", action="store_true", help="facsimile: don't place the scan on a hidden layer")
    p.set_defaults(func=cmd_idml)

    p = sub.add_parser("text", help="a text file of a book: from its project folder (corrections included), "
                                    "or from a scan, read now (as text-files does for one scan)")
    p.add_argument("workspace", help="a project folder, or a scan (PDF or page image)")
    p.add_argument("--lang", default="eng", help="for a scan read now: OCR languages, e.g. spa or eng+grc+heb")
    p.add_argument("--page-markers", action="store_true", help="mark where each page begins (the default)")
    p.add_argument("-o", "--output", help="the .txt path (default: beside the scan, with its name)")
    p.add_argument("--lines", action="store_true", help="every line as printed, page by page (default: paragraphs, lines joined)")
    p.add_argument("--no-page-markers", action="store_true", help="don't mark where each page begins ({p. 172 · PDF 90})")
    p.add_argument("--keep-marks", action="store_true",
                   help="keep vowel points, accents and breathings as read (default: removed, for searching)")
    p.add_argument("--keep-heads", action="store_true", help="keep running heads and page numbers")
    p.add_argument("--kraken", action="append", metavar="LANG=MODEL",
                   help="read a language with a Kraken model, e.g. --kraken heb=7051645 (repeatable; see: ectype kraken list)")
    p.set_defaults(func=cmd_text)

    p = sub.add_parser("text-files", help="make a text file beside each scan (a PDF or image) in folders, with its name")
    p.add_argument("inputs", nargs="+", help="folders, or scans")
    p.add_argument("--lang", default="eng", help="OCR languages, e.g. eng+grc+heb (see: ectype languages)")
    p.add_argument("--recursive", action="store_true", help="also the folders inside the folders")
    p.add_argument("--redo", action="store_true", help="make the text again for scans that have one Ectype wrote")
    p.add_argument("--keep-marks", action="store_true",
                   help="keep vowel points, accents and breathings as read (default: removed, for searching)")
    p.add_argument("--keep", action="store_true", help="keep each book's work folder (page images, OCR) instead of deleting it")
    p.add_argument("--kraken", action="append", metavar="LANG=MODEL",
                   help="read a language with a Kraken model, e.g. --kraken heb=7051645 (repeatable; see: ectype kraken list)")
    p.set_defaults(func=cmd_text_files)

    p = sub.add_parser("strip-text", help="a copy of a PDF without its hidden (OCR) text layer, beside it as \"<name> - no text.pdf\"")
    p.add_argument("pdfs", nargs="+", metavar="PDF", help="the PDF, or several; none of them is changed")
    p.add_argument("-o", "--output", metavar="FILE", help="where to save the copy (with one PDF only; never over a file that exists)")
    p.add_argument("--all", action="store_true",
                   help="remove visible text too. Careful: in Acrobat ClearScan PDFs and Ectype's own traced PDFs the "
                        "visible type is how the page looks, and removing it leaves gaps")
    p.set_defaults(func=cmd_strip_text)

    p = sub.add_parser("data-folder", help="show where Ectype keeps its data and how big it is; --move puts it elsewhere")
    p.add_argument("--move", metavar="FOLDER", help="move all of it to this (empty or new) folder, e.g. \"D:\\Ectype OCR\"")
    p.set_defaults(func=cmd_data_folder)

    p = sub.add_parser("free-space", help="show what takes space in Ectype's data folder; --cache and --font-lines free some")
    p.add_argument("--cache", action="store_true", help="remove prepared copies of training lines, leftovers of stopped runs and book projects' working files")
    p.add_argument("--font-lines", nargs="*", metavar="LANG",
                   help="remove lines set from fonts (all languages, or those named); Train from fonts makes them again")
    p.set_defaults(func=cmd_free_space)

    p = sub.add_parser("rebuild", help="after corrections: segment, cluster, fonts and the PDF again")
    p.add_argument("workspace")
    p.add_argument("--mode", choices=["traced", "substitute", "text", "plaintext"], default="traced")
    p.add_argument("--page-image", choices=["gray", "lossless", "bw"], default="gray")
    p.add_argument("--font", help="substitute mode: font file or family name")
    p.add_argument("--name", help="output file name without .pdf")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("train", help="train the OCR languages' models from their pools (with a project's corrections added)")
    p.add_argument("workspace", nargs="?", help="a project: its corrections are added to the pools first")
    p.add_argument("--lang", nargs="+", help="languages to train, without a project (e.g. heb grc kraken_7051645)")
    p.add_argument("--iterations", type=int, help="Tesseract: training iterations (default: by the pool's size)")
    p.add_argument("--epochs", type=int, help="Kraken: most epochs (default 15)")
    p.add_argument("--full", dest="mode", action="store_const", const="full", default="auto",
                   help="train afresh from the stock models (default: a quick update when little is new)")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("train-synthetic", help="add lines set in your fonts from known text to the training pools")
    p.add_argument("--fonts", required=True, help="folder of fonts (.ttf, .otf; subfolders included)")
    p.add_argument("--script", nargs="+", default=["hebrew", "greek"], choices=["hebrew", "greek", "latin", "syriac", "arabic", "transliteration"])
    p.add_argument("--lines", type=int, default=400, help="lines a font (default 400)")
    p.set_defaults(func=cmd_train_synthetic)

    p = sub.add_parser("train-scans", help="add lines of a scanned edition, matched to its known text, to the pools")
    p.add_argument("scan", help="the scan (PDF or image)")
    p.add_argument("--text", required=True, help="hebrew-bible, greek-nt, or a UTF-8 text file of the edition")
    p.add_argument("--edition", required=True, help="the edition's name, e.g. 'Letteris 1866'")
    p.add_argument("--lang", help="heb or grc (needed with a text file)")
    p.add_argument("--pages", help="pages to use, e.g. 1-40")
    p.set_defaults(func=cmd_train_scans)

    p = sub.add_parser("train-all", help="all the training a settings file asks for (what the batch file runs)")
    p.add_argument("--config", help="the settings file (default: training.ini in the user folder)")
    p.add_argument("--write-config", metavar="PATH", help="write a settings file to fill in, and stop")
    p.add_argument("--iterations", type=int)
    p.add_argument("--epochs", type=int)
    p.set_defaults(func=cmd_train_all)

    p = sub.add_parser("models", help="the trained models and training pools: list, or remove")
    p.add_argument("--remove", metavar="LANG", help="remove a language's trained models (back to the stock model)")
    p.add_argument("--remove-pool", metavar="LANG", help="empty a language's training pool")
    p.add_argument("--remove-legacy", action="store_true", help="remove per-book models made by earlier versions")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("update", help="check for a newer version (and install it, with --install)")
    p.add_argument("--install", action="store_true", help="download and run the new installer (installed Windows app)")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("gui", help="open the desktop app")
    p.add_argument("target", nargs="?", help="a scan to open, or a project folder")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("synth", help="make a synthetic test page with known text and font")
    p.add_argument("-o", "--output", help="output folder (default reports/synth)")
    p.add_argument("--font", help="font file or family name (default: EB Garamond, else a system serif)")
    p.add_argument("--text", help="UTF-8 text file; paragraphs separated by blank lines")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--size", type=float, default=11.0, help="point size")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--clean", action="store_true", help="no degradation")
    p.add_argument("--skew", type=float, default=0.4, help="rotation in degrees")
    p.add_argument("--ink-spread", type=int, default=0, help="thicken ink by this many pixels")
    p.add_argument("--blur", type=float, default=0.6, help="Gaussian blur sigma in pixels")
    p.add_argument("--noise", type=float, default=6.0, help="Gaussian noise sigma in gray levels")
    p.add_argument("--speckles", type=int, default=40)
    p.add_argument("--jpeg", type=int, default=85, help="JPEG quality; 0 for lossless")
    p.add_argument("--set", type=int, metavar="N",
                   help="instead, make a workspace of N varied pages (fonts, sizes, damage) with truth")
    p.set_defaults(func=cmd_synth)
    return parser


def main(argv: list[str] | None = None) -> int:
    import multiprocessing

    multiprocessing.freeze_support()  # the packaged app's worker processes start through here
    from .resources import quiet_windows_errors

    quiet_windows_errors()
    for stream in (sys.stdout, sys.stderr):  # a Windows console may not have every character we print
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    argv = _as_run(list(sys.argv[1:] if argv is None else argv))
    args = parser.parse_args(argv)
    if args.credits:
        return cmd_credits()
    if not args.command:
        parser.print_help()
        return 2
    refusal = _wrong_target(args) or apply_kraken_options(args)
    if refusal:
        print(refusal)
        return 2
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except Exception as exc:  # said in a line, with the details kept in a file, not a Python traceback
        if os.environ.get("ECTYPE_DEBUG"):
            raise
        return _report_failure(args, exc)


# The step that makes each kind of PDF, given a scan instead of a project folder, is taken as
# `run` in that mode (Beaux, 0.11: `ectype textlayer "<book>.pdf" --lang …` was refused).
STEP_MODES = {"textlayer": "text", "traced": "traced", "substitute": "substitute"}


def _as_run(argv: list[str]) -> list[str]:
    if len(argv) >= 2 and argv[0] in STEP_MODES and Path(argv[1]).is_file() and "--mode" not in argv:
        mode = STEP_MODES[argv[0]]
        print(f"(A scan, not a project folder: doing it as `ectype run … --mode {mode}`.)")
        return ["run", argv[1], "--mode", mode, *argv[2:]]
    return argv


def _wrong_target(args: argparse.Namespace) -> str | None:
    """A step command given a scan instead of a book's project folder (Beaux, 0.9.0: `ectype ocr
    "<book>.pdf"` ended in a Python traceback), or a project folder that isn't there."""
    ws = getattr(args, "workspace", None)
    if not ws or args.command == "text":
        return None
    path = Path(ws)
    if path.is_file():
        return (f"`ectype {args.command}` works on a book's project folder (the folder Ectype makes for it), "
                f"not on a scan.\nFor a scan, use:\n"
                f'  ectype text "{path}" --lang spa        (a text file beside it)\n'
                f'  ectype run "{path}" --lang spa         (a PDF beside it; the project folder is kept in '
                f"Ectype's data folder)\nPut the book's languages after --lang (eng, spa, lat, grc, heb, … joined with +).")
    if not path.exists():
        return f"No such folder: {path}"
    return None


def _report_failure(args: argparse.Namespace, exc: BaseException) -> int:
    import traceback

    from .resources import user_data_dir

    message = str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__
    print(f"Ectype stopped: {message}")
    try:
        log = user_data_dir() / "logs" / "last-command-error.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"ectype {' '.join(sys.argv[1:])}\n\n" + "".join(traceback.format_exception(exc)),
                       encoding="utf-8", newline="\n")
        print(f"The details are in {log} (send that file if you need help).")
    except OSError:
        pass
    return 1


if __name__ == "__main__":
    sys.exit(main())
