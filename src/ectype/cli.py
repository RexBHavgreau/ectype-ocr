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
                truth=Path(args.truth) if args.truth else None, labels=labels)
    m = s["mean"]
    print(f"{s['pages_compared']} pages · mean SSIM {m['ssim']} · ink disagreement {m['ink_disagreement']}")
    if s["truth"]:
        print(f"CER vs truth: {labels[0]} {m['cer_a']} · {labels[1]} {m['cer_b']}")
    print(f"Report: {out / 'index.html'}")
    return 0


def cmd_synth(args: argparse.Namespace) -> int:
    from .synth import DEFAULT_TEXT, Degrade, find_font, make_page

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
    p.set_defaults(func=cmd_compare)

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
