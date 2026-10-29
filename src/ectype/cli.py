"""Command-line interface: `ectype analyze | compare | synth`, plus `--credits`."""

from __future__ import annotations

import argparse
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
    s = substitute_workspace(ws, find_font(args.font), out_name=args.name, background_dpi=args.background_dpi)
    w = s["within_1px"] or {}
    print(f"{ws / 'substitute' / s['pdf']}: {s['bytes']:,} bytes; word ends within 1 px of the scan: "
          f"left {100 * w.get('left', 0):.1f}%, right {100 * w.get('right', 0):.1f}%")
    print(f"Report: {ws / 'substitute' / 'index.html'}")
    return 0


def cmd_cluster(args: argparse.Namespace) -> int:
    from .cluster import cluster_workspace

    ws = Path(args.workspace)
    s = cluster_workspace(ws, threshold=args.threshold)
    print(f"{s['instances']} glyph instances -> {len(s['glyphs'])} averaged glyphs in {len(s['styles'])} styles; "
          f"{len(s['suspects'])} probable OCR misreadings listed")
    print(f"Report: {ws / 'cluster' / 'index.html'}")
    return 0


def cmd_fonts(args: argparse.Namespace) -> int:
    from .fontbuild import build_workspace_fonts

    ws = Path(args.workspace)
    s = build_workspace_fonts(ws, family=args.family, threshold=args.threshold)
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
    s = traced_workspace(ws, out_name=args.name, background_dpi=args.background_dpi, min_word_conf=args.min_conf)
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
    for code in installed:
        print(f"  {code:14} {languages.name(code)}")
    if args.all:
        print("Available to add (ectype languages --add CODE ...):")
        for code in languages.available():
            if code not in installed:
                print(f"  {code:14} {languages.name(code)}")
    return 0


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
        out = project.export_idml(dest, options, progress=lambda stage, msg: print(f"{stage}: {msg}"))
    except ConverterMissing as exc:
        print(exc)
        return 1
    for alert in out.get("alerts", []):
        print(f"Note: {alert}")
    if args.mode == "facsimile":
        for name, face in out["styles"].items():
            print(f"  {name}: {face}")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    """Train the OCR languages' models from their pools, with this project's corrections added
    (or, with --lang and no workspace, just those languages)."""
    from .train import TrainingError

    say = lambda msg: print(f"train: {msg}", flush=True)  # noqa: E731
    try:
        if args.workspace:
            from .project import Project

            reports = Project(Path(args.workspace)).train_model(lambda stage, msg: say(msg))
        else:
            from .training import train_language

            if not args.lang:
                print("Give a project folder, or the languages to train (--lang heb grc).")
                return 2
            reports = [train_language(code, say) for code in args.lang]
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
    """Every stage, scan to PDF, in one workspace."""
    ws = Path(args.output) if args.output else _default_out("work", Path(args.input))
    steps = [
        ("preprocess", cmd_preprocess, argparse.Namespace(input=args.input, output=str(ws), keep_spreads=args.keep_spreads,
                                                          overrides=args.overrides, pages=args.pages, no_deskew=False, dpi=300)),
        ("ocr", cmd_ocr, argparse.Namespace(workspace=str(ws), engine="tesseract", lang=args.lang)),
    ]
    if args.mode != "text":
        steps.append(("segment", cmd_segment, argparse.Namespace(workspace=str(ws), truth=None)))
    if args.mode == "text":
        steps.append(("textlayer", cmd_textlayer, argparse.Namespace(workspace=str(ws), name=args.name, page_image=args.page_image)))
    elif args.mode == "substitute":
        steps.append(("substitute", cmd_substitute, argparse.Namespace(workspace=str(ws), font=args.font, name=args.name,
                                                                       background_dpi=150)))
    else:
        steps += [
            ("cluster", cmd_cluster, argparse.Namespace(workspace=str(ws), threshold=0.25)),
            ("fonts", cmd_fonts, argparse.Namespace(workspace=str(ws), family=None, threshold=0.45)),
            ("traced", cmd_traced, argparse.Namespace(workspace=str(ws), name=args.name, background_dpi=150, min_conf=60.0)),
        ]
    for name, fn, ns in steps:
        print(f"== {name}")
        fn(ns)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from .review import serve

    server = serve(Path(args.workspace), port=args.port)
    print(f"Reviewing {args.workspace} at http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    """After corrections: segmentation through the output PDF again."""
    ws = str(args.workspace)
    if args.mode == "text":
        return cmd_textlayer(argparse.Namespace(workspace=ws, name=args.name, page_image=args.page_image))
    cmd_segment(argparse.Namespace(workspace=ws, truth=None))
    if args.mode == "substitute":
        return cmd_substitute(argparse.Namespace(workspace=ws, font=args.font, name=args.name, background_dpi=150))
    cmd_cluster(argparse.Namespace(workspace=ws, threshold=0.25))
    cmd_fonts(argparse.Namespace(workspace=ws, family=None, threshold=0.45))
    return cmd_traced(argparse.Namespace(workspace=ws, name=args.name, background_dpi=150, min_conf=60.0))


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ectype", description=__doc__)
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

    p = sub.add_parser("preprocess", help="orient, split spreads and deskew a scan into one image per book page")
    p.add_argument("input", help="scanned PDF or page image")
    p.add_argument("-o", "--output", help="workspace folder (default reports/work-<name>); pages go to <workspace>/pages")
    p.add_argument("--keep-spreads", action="store_true", help="don't split two-page spreads")
    p.add_argument("--overrides", help="JSON of per-page corrections: rotate, split, skew")
    p.add_argument("--pages", help="1-based source pages, e.g. 1-3,5")
    p.add_argument("--no-deskew", action="store_true")
    p.add_argument("--dpi", type=float, default=300, help="render resolution for pages that aren't a single image")
    p.set_defaults(func=cmd_preprocess)

    p = sub.add_parser("ocr", help="OCR <workspace>/pages into <workspace>/ocr")
    p.add_argument("workspace")
    p.add_argument("--engine", default="tesseract", help="tesseract (default) or kraken")
    p.add_argument("--lang", help="engine language(s), e.g. eng or eng+grc (default: the project's, else eng)")
    p.set_defaults(func=cmd_ocr)

    p = sub.add_parser("segment", help="tie OCR characters to their ink: <workspace>/segment")
    p.add_argument("workspace")
    p.add_argument("--truth", help="synthetic truth folder (default <workspace>/truth if present)")
    p.set_defaults(func=cmd_segment)

    p = sub.add_parser("substitute", help="build the substitute-mode PDF: <workspace>/substitute")
    p.add_argument("workspace")
    p.add_argument("--font", help="font file or family name (default: EB Garamond, else a system serif)")
    p.add_argument("--name", help="output file name without .pdf (default: the workspace name)")
    p.add_argument("--background-dpi", type=float, default=150, help="resolution of the erased background image")
    p.set_defaults(func=cmd_substitute)

    p = sub.add_parser("cluster", help="group glyphs by style and average them: <workspace>/cluster")
    p.add_argument("workspace")
    p.add_argument("--threshold", type=float, default=0.25, help="shape distance (1 - correlation) that splits a group")
    p.set_defaults(func=cmd_cluster)

    p = sub.add_parser("fonts", help="trace composites and build one font per style: <workspace>/fonts")
    p.add_argument("workspace")
    p.add_argument("--family", help="font family name (default: Ectype <workspace>)")
    p.add_argument("--threshold", type=float, default=0.45, help="coverage at which a composite pixel counts as ink")
    p.set_defaults(func=cmd_fonts)

    p = sub.add_parser("traced", help="build the traced-mode PDF: <workspace>/traced")
    p.add_argument("workspace")
    p.add_argument("--name", help="output file name without .pdf (default: the workspace name)")
    p.add_argument("--background-dpi", type=float, default=150)
    p.add_argument("--min-conf", type=float, default=60.0, help="words below this OCR confidence stay as image")
    p.set_defaults(func=cmd_traced)

    p = sub.add_parser("run", help="every stage, scan to PDF")
    p.add_argument("input", help="scanned PDF or page image")
    p.add_argument("-o", "--output", help="workspace folder (default reports/work-<name>)")
    p.add_argument("--mode", choices=["traced", "substitute", "text"], default="traced",
                   help="text: just the scan with an invisible OCR text layer (no tracing)")
    p.add_argument("--font", help="substitute mode: font file or family name")
    p.add_argument("--name", help="output file name without .pdf")
    p.add_argument("--lang", default="eng", help="OCR languages, e.g. eng or eng+grc (see: ectype languages)")
    p.add_argument("--page-image", choices=["gray", "bw"], default="gray", help="text mode: grayscale or black-and-white page images")
    p.add_argument("--keep-spreads", action="store_true")
    p.add_argument("--overrides", help="JSON of per-page preprocessing corrections")
    p.add_argument("--pages", help="1-based source pages, e.g. 1-3,5")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("textlayer", help="build the text-layer PDF (the scan + invisible OCR text): <workspace>/textlayer")
    p.add_argument("workspace")
    p.add_argument("--name", help="output file name without .pdf (default: the workspace name)")
    p.add_argument("--page-image", choices=["gray", "bw"], default="gray", help="grayscale JPEG, or black and white (CCITT G4)")
    p.set_defaults(func=cmd_textlayer)

    p = sub.add_parser("languages", help="list the OCR languages, or download more")
    p.add_argument("--add", nargs="+", metavar="CODE", help="download these languages, e.g. grc lat heb")
    p.add_argument("--all", action="store_true", help="also list the languages that can be added")
    p.set_defaults(func=cmd_languages)

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

    p = sub.add_parser("review", help="correct OCR in the browser; corrections become training data")
    p.add_argument("workspace")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("rebuild", help="after corrections: segment, cluster, fonts and the PDF again")
    p.add_argument("workspace")
    p.add_argument("--mode", choices=["traced", "substitute", "text"], default="traced")
    p.add_argument("--page-image", choices=["gray", "bw"], default="gray")
    p.add_argument("--font", help="substitute mode: font file or family name")
    p.add_argument("--name", help="output file name without .pdf")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("train", help="train the OCR languages' models from their pools (with a project's corrections added)")
    p.add_argument("workspace", nargs="?", help="a project: its corrections are added to the pools first")
    p.add_argument("--lang", nargs="+", help="languages to train, without a project (e.g. heb grc kraken_7051645)")
    p.add_argument("--iterations", type=int, help="Tesseract: training iterations (default: by the pool's size)")
    p.add_argument("--epochs", type=int, help="Kraken: most epochs (default 15)")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("train-synthetic", help="add lines set in your fonts from known text to the training pools")
    p.add_argument("--fonts", required=True, help="folder of fonts (.ttf, .otf; subfolders included)")
    p.add_argument("--script", nargs="+", default=["hebrew", "greek"], choices=["hebrew", "greek"])
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
    for stream in (sys.stdout, sys.stderr):  # a Windows console may not have every character we print
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.credits:
        return cmd_credits()
    if not args.command:
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
