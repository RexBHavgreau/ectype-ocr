"""Compare Kraken's repository models for Greek and Hebrew with Tesseract, on test lines.

    python tools/kraken_eval.py <out folder> [--scripts Grek Hebr] [--max 12]

Lists the repository's recognition models (through the Kraken engine), downloads those for
the scripts asked for that aren't marked as handwriting, and reads the same test lines with each
and with Tesseract's language for the script: Genesis 1:1-4 (pointed and unpointed) and John
1:1-5 (polytonic), set in the fonts at hand, slightly blurred and speckled like a scan. Prints
a table of character error rates and writes it, with each model's details, to results.json.

This is a sanity check across models, not a verdict: set type isn't a book's type, and the
right model for a book is the one trained on type like it (the app shows each model's
description so the user can judge).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ectype import kraken  # noqa: E402
from ectype.resources import tesseract_cmd  # noqa: E402

HEBREW = [
    "בְּרֵאשִׁית בָּרָא אֱלֹהִים אֵת הַשָּׁמַיִם וְאֵת הָאָרֶץ",
    "וְהָאָרֶץ הָיְתָה תֹהוּ וָבֹהוּ וְחֹשֶׁךְ עַל־פְּנֵי תְהוֹם",
    "וְרוּחַ אֱלֹהִים מְרַחֶפֶת עַל־פְּנֵי הַמָּיִם",
    "וַיֹּאמֶר אֱלֹהִים יְהִי אוֹר וַיְהִי־אוֹר",
    "וַיַּרְא אֱלֹהִים אֶת־הָאוֹר כִּי־טוֹב",
    "וַיַּבְדֵּל אֱלֹהִים בֵּין הָאוֹר וּבֵין הַחֹשֶׁךְ",
]
GREEK = [
    "Ἐν ἀρχῇ ἦν ὁ λόγος, καὶ ὁ λόγος ἦν πρὸς τὸν θεόν,",
    "καὶ θεὸς ἦν ὁ λόγος. οὗτος ἦν ἐν ἀρχῇ πρὸς τὸν θεόν.",
    "πάντα δι’ αὐτοῦ ἐγένετο, καὶ χωρὶς αὐτοῦ ἐγένετο",
    "οὐδὲ ἕν ὃ γέγονεν. ἐν αὐτῷ ζωὴ ἦν, καὶ ἡ ζωὴ ἦν",
    "τὸ φῶς τῶν ἀνθρώπων· καὶ τὸ φῶς ἐν τῇ σκοτίᾳ φαίνει,",
    "καὶ ἡ σκοτία αὐτὸ οὐ κατέλαβεν.",
]
FONTS = {"Hebr": ["FreeSerif", "DejaVu Sans", "Noto Serif Hebrew", "Ezra SIL", "SBL Hebrew"],
         "Grek": ["FreeSerif", "DejaVu Serif", "Noto Serif", "GFS Didot", "Gentium Plus"]}
TESSERACT = {"Hebr": "heb", "Grek": "grc"}


def unpointed(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def test_sets(script: str) -> dict[str, list[str]]:
    if script == "Hebr":
        return {"pointed": HEBREW, "unpointed": [unpointed(t) for t in HEBREW]}
    return {"polytonic": GREEK}


def font_path(family: str) -> Path | None:
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}|%{family}", family], capture_output=True, text=True).stdout
    except OSError:
        return None
    file, _, found = out.partition("|")
    return Path(file) if file and family.split()[0].lower() in found.lower() else None


def render(lines: list[str], font: Path, rtl: bool, folder: Path, seed: int = 0) -> list[tuple[Path, str]]:
    folder.mkdir(parents=True, exist_ok=True)
    face = ImageFont.truetype(str(font), 46)
    rng = np.random.default_rng(seed)
    out = []
    for i, text in enumerate(lines):
        w = int(face.getlength(text, direction="rtl" if rtl else "ltr", language="he" if rtl else None)) + 60
        im = Image.new("L", (w, 90), 255)
        ImageDraw.Draw(im).text((30, 64), text, font=face, fill=0, anchor="ls", direction="rtl" if rtl else "ltr")
        im = im.filter(ImageFilter.GaussianBlur(0.7))
        a = np.asarray(im).astype(float) + rng.normal(0, 8, (im.height, im.width))
        png = folder / f"l{i:02d}.png"
        Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(png, dpi=(300, 300))
        out.append((png, text))
    return out


def cer(pairs: list[tuple[str, str]]) -> float:
    from rapidfuzz.distance import Levenshtein

    errors = sum(Levenshtein.distance(unicodedata.normalize("NFC", got.strip()), unicodedata.normalize("NFC", want))
                 for got, want in pairs)
    return round(100 * errors / max(1, sum(len(w) for _, w in pairs)), 2)


def tesseract_reads(lang: str, lines: list[tuple[Path, str]]) -> list[str] | None:
    cmd = tesseract_cmd()
    if not cmd:
        return None
    out = []
    for png, _ in lines:
        r = subprocess.run([cmd, str(png), "-", "--psm", "7", "-l", lang], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        out.append(_clean(r.stdout))
    return out


def _clean(text: str) -> str:
    from ectype.scripts import clean

    return clean(text).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--scripts", nargs="+", default=["Grek", "Hebr"])
    ap.add_argument("--max", type=int, default=12, help="models a script, newest first")
    args = ap.parse_args()
    if not features.check("raqm"):
        print("Pillow has no raqm layout: Hebrew can't be set", file=sys.stderr)
    args.out.mkdir(parents=True, exist_ok=True)
    records = kraken.list_models(refresh=True)
    print(f"{len(records)} recognition models in the repository")
    results = []
    for script in args.scripts:
        rtl = script in ("Hebr", "Arab", "Syrc")
        sets = {}
        for family in FONTS[script]:
            font = font_path(family)
            if font is None:
                continue
            for name, lines in test_sets(script).items():
                sets[f"{name}, {family}"] = render(lines, font, rtl, args.out / "lines" / script / f"{name}-{family}")
        candidates = [r for r in records if script in (r.get("script") or []) and kraken.printed(r) is not False]
        candidates.sort(key=lambda r: r.get("date", ""), reverse=True)
        rows = []
        lang = TESSERACT[script]
        row = {"model": f"Tesseract {lang}", "doi": "", "cer": {}}
        for label, lines in sets.items():
            got = tesseract_reads(lang, lines)
            if got is not None:
                row["cer"][label] = cer(list(zip(got, (t for _, t in lines))))
        rows.append(row)
        for r in candidates[: args.max]:
            row = {"model": r.get("summary") or r["doi"], "doi": r["doi"], "date": r.get("date"),
                   "license": r.get("license"), "reported_cer": r.get("cer"), "cer": {}}
            try:
                code = kraken.install_model(r)
                file = kraken.installed_models()[code]["file"]
                row["file"] = file
                with kraken.Session() as session:
                    for label, lines in sets.items():
                        got = [session.read(file, png, [[0, 0, *Image.open(png).size]])[0]["text"] for png, _ in lines]
                        row["cer"][label] = cer(list(zip(got, (t for _, t in lines))))
            except Exception as exc:
                row["error"] = str(exc)[:300]
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        results.append({"script": script, "sets": list(sets), "rows": rows})
        print(f"\n## {script}\n")
        print("| Model | DOI | " + " | ".join(sets) + " |")
        print("|---|---|" + "---|" * len(sets))
        for row in rows:
            cells = [f"{row['cer'][s]:.1f}" if s in row["cer"] else (row.get("error", "")[:40] or "—") for s in sets]
            print(f"| {row['model'][:60]} | {row['doi']} | " + " | ".join(cells) + " |")
    (args.out / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
