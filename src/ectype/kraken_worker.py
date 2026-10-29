"""Ectype's Kraken engine, run as a process of its own.

Kraken (https://kraken.re, Apache 2.0) reads Greek, Hebrew and other scripts with models
from its repository on Zenodo, but brings PyTorch, which is large. So it runs apart from the
app: from source as `python -m ectype.kraken_worker`, and in the Windows app as
`ectype-kraken.exe`, an optional download (`kraken.py` finds and talks to it). This module
imports only Kraken and the standard library at the top, so the add-on stays self-contained.

Commands (results as JSON on stdout; progress as "progress <done> <total>" lines on stderr):

    version                    Kraken's and PyTorch's versions, and the protocol version
    list                       the repository's recognition models
    get <doi> <folder>         download a model's files
    serve                      read lines: one JSON request a line on stdin, one answer a
                               line on stdout, until stdin closes (see `_read`)
    train <ketos args...>      `ketos train` with the given arguments
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROTOCOL = 1


def _out(data) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _progress(done: int, total: int) -> None:
    sys.stderr.write(f"progress {done} {total}\n")
    sys.stderr.flush()


def version() -> dict:
    import importlib.metadata as md

    return {"protocol": PROTOCOL, "kraken": md.version("kraken"), "torch": md.version("torch")}


def _record(doi: str, rec) -> dict | None:
    """What the app needs to know of a repository record; None if it isn't a recognition model."""
    get = (lambda key, default=None: getattr(rec, key, default))
    kinds = get("model_type") or []
    if isinstance(kinds, str):
        kinds = [kinds]
    if kinds and "recognition" not in kinds:
        return None
    metrics = get("metrics") or {}
    cer = metrics.get("cer") if isinstance(metrics, dict) else None
    return {
        "doi": str(get("doi") or doi), "concept_doi": str(get("concept_doi") or doi),
        "summary": str(get("summary") or get("name") or ""), "description": str(get("description") or "")[:4000],
        "language": list(get("language") or []), "script": list(get("script") or []),
        "keywords": list(get("keywords") or []), "license": str(get("license") or ""),
        "cer": float(cer) if cer is not None else None,
        "date": str(get("publication_date") or "")[:10],
        "creators": [str(c.get("name", c) if isinstance(c, dict) else c) for c in (get("creators") or [])][:6],
    }


def list_models() -> list[dict]:
    from kraken.repo import get_listing

    seen = [0]

    def callback(total, advance):
        seen[0] += advance
        _progress(seen[0], total)

    out = []
    for concept, versions in get_listing(callback).items():
        if not versions:
            continue
        rec = versions[0]  # the newest deposit of each model
        item = _record(str(getattr(rec, "doi", concept)), rec)
        if item:
            item["concept_doi"] = concept
            out.append(item)
    return out


def get(doi: str, folder: str) -> dict:
    from htrmopo import get_model

    seen = [0]

    def callback(total, advance):
        seen[0] += advance
        if total:
            _progress(seen[0], total)

    path = Path(get_model(doi, path=folder, callback=callback))
    files = sorted(str(p) for p in path.iterdir() if p.is_file())
    weights = [f for f in files if f.endswith((".mlmodel", ".safetensors"))]
    return {"folder": str(path), "files": files, "model": weights[0] if weights else None}


class Reader:
    """Recognition models, loaded once each and kept."""

    def __init__(self):
        self.models = {}

    def model(self, path: str):
        if path not in self.models:
            from kraken.tasks import RecognitionTaskModel

            self.models[path] = RecognitionTaskModel.load_model(path)
        return self.models[path]

    def read(self, request: dict) -> dict:
        """Read boxed lines of one image with one model. Request: {"model", "image", "lines":
        [[x0, y0, x1, y1], ...]}. Answer: {"lines": [{"text", "chars": [[char, [x0, y0, x1, y1],
        confidence 0-100], ...]}, ...]}, the text in reading order."""
        from kraken.configs import RecognitionInferenceConfig
        from kraken.containers import BBoxLine, Segmentation
        from PIL import Image

        model = self.model(request["model"])
        image = Image.open(request["image"])
        lines = [BBoxLine(id=f"l{i}", bbox=tuple(int(v) for v in box)) for i, box in enumerate(request["lines"])]
        seg = Segmentation(type="bbox", imagename=request["image"], text_direction="horizontal-lr",
                           script_detection=False, lines=lines)
        out = []
        for record in model.predict(image, seg, RecognitionInferenceConfig(num_line_workers=0)):
            chars = []
            for char, cut, conf in record:
                xs, ys = [p[0] for p in cut], [p[1] for p in cut]
                chars.append([char, [min(xs), min(ys), max(xs), max(ys)], round(100 * float(conf), 1)])
            out.append({"text": record.prediction, "chars": chars})
        return {"lines": out}


def serve() -> None:
    reader = Reader()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            _out(reader.read(json.loads(line)))
        except Exception as exc:  # one bad request doesn't end the session
            _out({"error": f"{type(exc).__name__}: {exc}"})


def train(args: list[str]) -> int:
    """`ketos train`, its output sent to stderr so stdout holds only the JSON answer."""
    import contextlib

    from kraken.ketos import cli

    code = 0
    with contextlib.redirect_stdout(sys.stderr):
        try:
            # On the CPU, loading data in this process (no worker processes, which a frozen
            # program would have to start as copies of itself), with all cores for the maths.
            threads = str(max(1, (os.cpu_count() or 2) - 1))
            cli.main(args=["--device", "cpu", "--workers", "0", "--threads", threads, "--seed", "0", "train", *args],
                     prog_name="ketos", standalone_mode=False)
        except SystemExit as exc:
            code = int(exc.code or 0)
    _out({"returncode": code})
    return code


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    command, rest = argv[0], argv[1:]
    try:
        if command == "version":
            _out(version())
        elif command == "list":
            _out(list_models())
        elif command == "get":
            _out(get(rest[0], rest[1]))
        elif command == "serve":
            serve()
        elif command == "train":
            return train(rest)
        else:
            print(f"unknown command {command!r}", file=sys.stderr)
            return 2
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
