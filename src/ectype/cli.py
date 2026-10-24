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
    s = ocr_pages(ws / "pages", ws / "ocr", engine=args.engine, lang=args.lang)
    words = sum(r["words"] for r in s["pages"])
    print(f"{len(s['pages'])} pages, {words} words. Report: {ws / 'ocr' / 'index.html'}")
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
    bad = [st["style"] for st in s["styles"] if st["validation"]["ots"] != "pass" or st["validation"]["fonttools"] != "pass"]
    print(f"{len(s['styles'])} fonts, {sum(st['glyphs'] for st in s['styles'])} glyphs; "
          + ("all pass OTS and fontTools" if not bad else f"validation problems in styles {bad}"))
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


def cmd_run(args: argparse.Namespace) -> int:
    """Every stage, scan to PDF, in one workspace."""
    ws = Path(args.output) if args.output else _default_out("work", Path(args.input))
    steps = [
        ("preprocess", cmd_preprocess, argparse.Namespace(input=args.input, output=str(ws), keep_spreads=args.keep_spreads,
                                                          overrides=args.overrides, pages=args.pages, no_deskew=False, dpi=300)),
        ("ocr", cmd_ocr, argparse.Namespace(workspace=str(ws), engine="tesseract", lang=args.lang)),
        ("segment", cmd_segment, argparse.Namespace(workspace=str(ws), truth=None)),
    ]
    if args.mode == "substitute":
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
    for candidate in (Path(__file__).resolve().parents[2] / "CREDITS.md", Path("CREDITS.md")):
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
    p.add_argument("--lang", default="eng", help="engine language(s), e.g. eng or eng+grc")
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
    p.add_argument("--mode", choices=["traced", "substitute"], default="traced")
    p.add_argument("--font", help="substitute mode: font file or family name")
    p.add_argument("--name", help="output file name without .pdf")
    p.add_argument("--lang", default="eng")
    p.add_argument("--keep-spreads", action="store_true")
    p.add_argument("--overrides", help="JSON of per-page preprocessing corrections")
    p.add_argument("--pages", help="1-based source pages, e.g. 1-3,5")
    p.set_defaults(func=cmd_run)

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
