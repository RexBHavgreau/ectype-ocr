"""Smoke test of the packaged Kraken engine (release workflow).

    python packaging/kraken_smoke.py <ectype-kraken.exe> <work folder>

Lists the repository's models, downloads a Greek one, reads a line with it through `serve`,
and trains it for one epoch on a few lines, as the app does; then finds the lines of a page,
reads them, and trains the line-finding (Manuscript Mode). Needs only Pillow besides the engine.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LINES = ["Ἐν ἀρχῇ ἦν ὁ λόγος", "καὶ ὁ λόγος ἦν πρὸς τὸν θεόν", "καὶ θεὸς ἦν ὁ λόγος", "οὗτος ἦν ἐν ἀρχῇ",
         "πάντα δι’ αὐτοῦ ἐγένετο", "ἐν αὐτῷ ζωὴ ἦν", "τὸ φῶς τῶν ἀνθρώπων", "ἡ σκοτία αὐτὸ οὐ κατέλαβεν"]


PREFERRED = "10.5281/zenodo.21788405"  # PP-OCRv6 (small) multilingual
TINY = "10.5281/zenodo.21788403"  # PP-OCRv6 (tiny) multilingual


def run(engine: str, *args: str, stdin: str | None = None) -> object:
    out = subprocess.run([engine, *args], input=stdin, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        sys.exit(f"{args[0]} failed:\n{out.stderr[-3000:]}")
    lines = [line for line in out.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1])


def main() -> int:
    engine, work = sys.argv[1], Path(sys.argv[2])
    work.mkdir(parents=True, exist_ok=True)
    records = run(engine, "list")
    print(f"{len(records)} recognition models")
    # The same model every time (the listing's order varies), so the check is repeatable:
    # PP-OCRv6 small, which reads Greek well; else the first printed Greek model.
    preferred = [r for r in records if r["doi"] == PREFERRED]
    greek = [r for r in records if "Grek" in (r.get("script") or [])]
    printed = [r for r in greek if "print" in (r.get("summary", "") + " ".join(r.get("keywords", []))).lower()]
    record = (preferred or printed or greek or records)[0]
    print("model:", record["doi"], record.get("summary"))
    got = run(engine, "get", record["doi"], str(work / "model"))
    model = got["model"]
    print("downloaded:", model)

    font = ImageFont.truetype(r"C:\Windows\Fonts\times.ttf", 44)
    data = work / "lines"
    data.mkdir(exist_ok=True)
    for i, text in enumerate(LINES):
        im = Image.new("L", (int(font.getlength(text)) + 40, 72), 255)
        ImageDraw.Draw(im).text((20, 52), text, font=font, fill=0, anchor="ls")
        im.save(data / f"l{i}.png")
        (data / f"l{i}.gt.txt").write_text(text, encoding="utf-8", newline="\n")

    request = json.dumps({"model": model, "image": str(data / "l0.png"), "lines": [[0, 0, *Image.open(data / "l0.png").size]]})
    answer = run(engine, "serve", stdin=request + "\n")
    print("read:", answer)
    if "lines" not in answer or not answer["lines"][0]["text"].strip():
        sys.exit("no reading")

    (work / "train.txt").write_text("\n".join(str(data / f"l{i}.png") for i in range(6)) + "\n", encoding="utf-8", newline="\n")
    (work / "eval.txt").write_text("\n".join(str(data / f"l{i}.png") for i in range(6, 8)) + "\n", encoding="utf-8", newline="\n")
    result = run(engine, "train", "-f", "path", "-i", model, "--resize", "union", "-o", str(work / "trained"),
                 "-t", str(work / "train.txt"), "-e", str(work / "eval.txt"), "-N", "1", "-q", "fixed")
    weights = sorted((work / "trained").glob("*.safetensors"))
    print("trained:", result, [w.name for w in weights])
    if result.get("returncode") or not weights:
        sys.exit("training saved no model")

    # A family's model as a pack of changes over the general one, and read with it laid over.
    base = model if model.endswith(".safetensors") else str(weights[0])
    info = run(engine, "pack", base, str(weights[-1]), str(work / "f1.pack.safetensors"))
    print("pack:", info)
    request = json.dumps({"model": base, "pack": str(work / "f1.pack.safetensors"), "image": str(data / "l0.png"),
                          "lines": [[0, 0, *Image.open(data / "l0.png").size]]})
    packed = run(engine, "serve", stdin=request + "\n")
    print("read with the pack:", packed)
    if "lines" not in packed:
        sys.exit("couldn't read with the pack")

    # Manuscript Mode: Kraken's own line-finding (the model that comes with Kraken must be in
    # the engine), reading the lines it found, and training the line-finding for one round.
    page = Image.new("L", (1200, 700), 255)
    d = ImageDraw.Draw(page)
    for i, text in enumerate(LINES[:6]):
        d.text((60, 90 + 100 * i), text, font=font, fill=0, anchor="ls")
    page.save(work / "page.png")
    seg = run(engine, "serve", stdin=json.dumps({"op": "segment", "image": str(work / "page.png"),
                                                 "direction": "horizontal-lr"}) + "\n")
    print("lines found:", len(seg.get("lines", [])), seg.get("error", ""))
    if not seg.get("lines"):
        sys.exit("the line-finding found no lines")
    read = run(engine, "serve", stdin=json.dumps({"model": model, "image": str(work / "page.png"),
                                                  "direction": "horizontal-lr", "baselines": seg["lines"]}) + "\n")
    print("read the lines found:", [l["text"] for l in read.get("lines", [])][:3], read.get("error", ""))
    if "lines" not in read:
        sys.exit("couldn't read the lines found")
    pts = (lambda ps: " ".join(f"{x},{y}" for x, y in ps))
    body = "".join(f'<TextLine id="l{i}"><Coords points="{pts(l["boundary"])}"/><Baseline points="{pts(l["baseline"])}"/>'
                   "</TextLine>" for i, l in enumerate(seg["lines"]))
    xml = ('<?xml version="1.0" encoding="UTF-8"?><PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/'
           '2019-07-15"><Metadata><Creator>smoke</Creator><Created>2026-01-01T00:00:00</Created><LastChange>'
           f'2026-01-01T00:00:00</LastChange></Metadata><Page imageFilename="{work / "page.png"}" imageWidth="1200" '
           f'imageHeight="700"><TextRegion id="r0"><Coords points="0,0 1200,0 1200,700 0,700"/>{body}</TextRegion>'
           "</Page></PcGts>")
    (work / "page.xml").write_text(xml, encoding="utf-8")
    (work / "seg.txt").write_text(str(work / "page.xml") + "\n", encoding="utf-8", newline="\n")
    result = run(engine, "segtrain", "-i", "default", "-o", str(work / "seg"), "-f", "page", "-t", str(work / "seg.txt"),
                 "-e", str(work / "seg.txt"), "-N", "1", "-q", "fixed", "--resize", "union")
    seg_weights = sorted((work / "seg").glob("*.safetensors"))
    print("line-finding trained:", result, [w.name for w in seg_weights])
    if result.get("returncode") or not seg_weights:
        sys.exit("training the line-finding saved no model")

    # Not part of the check: training PP-OCRv6 tiny once crashed here without a message.
    # Try it, and print what happens (the engine prints a trace if PyTorch crashes).
    if any(r["doi"] == TINY for r in records):
        try:
            got = run(engine, "get", TINY, str(work / "tiny"))
            out = subprocess.run([engine, "train", "-f", "path", "-i", got["model"], "--resize", "union",
                                  "-o", str(work / "tiny-trained"), "-t", str(work / "train.txt"), "-e",
                                  str(work / "eval.txt"), "-N", "1", "-q", "fixed"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace")
            print(f"diagnostic: training PP-OCRv6 tiny ended with code {out.returncode}")
            if out.returncode:
                print(out.stderr[-6000:])
        except SystemExit as exc:
            print(f"diagnostic: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
