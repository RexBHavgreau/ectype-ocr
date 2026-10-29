"""Smoke test of the packaged Kraken engine (release workflow).

    python packaging/kraken_smoke.py <ectype-kraken.exe> <work folder>

Lists the repository's models, downloads a Greek one, reads a line with it through `serve`,
and trains it for one epoch on a few lines, as the app does. Needs only Pillow besides the engine.
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
