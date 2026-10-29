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
    pack <base> <trained> <out>     a family's model as its changes from the general one
    compose <base> <pack> <out>     the family's model rebuilt from them
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

    return {"protocol": PROTOCOL, "kraken": md.version("kraken"), "torch": md.version("torch"),
            "utf8_mode": bool(sys.flags.utf8_mode)}


def _utf8_by_default() -> None:
    """Open text files as UTF-8 unless told otherwise. Kraken's repository client (htrmopo)
    reads its data files without naming an encoding, which fails in Windows' own code page;
    UTF-8 mode would fix that, but a packaged program can't always be started in it."""
    import builtins
    import io

    if sys.flags.utf8_mode or getattr(builtins.open, "_ectype_utf8", False):
        return
    original = builtins.open

    def utf8_open(file, mode="r", buffering=-1, encoding=None, *args, **kwargs):
        if "b" not in mode and encoding is None:
            encoding = "utf-8"
        return original(file, mode, buffering, encoding, *args, **kwargs)

    utf8_open._ectype_utf8 = True
    builtins.open = utf8_open
    io.open = utf8_open


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
    if weights:
        # Some repository models (Party's page models) need other software: say so now.
        from kraken.tasks import RecognitionTaskModel

        try:
            RecognitionTaskModel.load_model(weights[0])
        except Exception as exc:
            raise ValueError(f"Kraken can't use this model to read lines ({str(exc).splitlines()[0][:200]})") from exc
    return {"folder": str(path), "files": files, "model": weights[0] if weights else None}


PACK_FORMAT = "1"


def _split_name(name: str) -> tuple[str, str]:
    """A layer's name without the model's own id that Kraken puts in front: (id, rest)."""
    head, _, rest = name.partition(".")
    return (head, rest) if rest and len(head) >= 32 and head.count("-") == 4 else ("", name)


def make_pack(base: str, trained: str, out: str) -> dict:
    """A typeface family's model as the changes from the general model it was trained from:
    each changed layer as 8-bit steps of the difference (one scale a row), layers whose shape
    changed (new characters) whole, and unchanged layers left out. A small fraction of a
    full copy; `compose` rebuilds the model from the general one and the pack."""
    import torch
    from safetensors import safe_open
    from safetensors.torch import load_file, save_file

    old = {_split_name(n)[1]: t for n, t in load_file(base).items()}
    new = load_file(trained)
    with safe_open(trained, framework="pt") as f:
        metadata = f.metadata() or {}
    tensors, delta, whole, prefix = {}, 0, 0, ""
    for name, t in new.items():
        prefix, rest = _split_name(name)
        b = old.get(rest)
        if b is None or b.shape != t.shape or not t.is_floating_point():
            tensors[f"{rest}::full"] = t.contiguous()
            whole += 1
            continue
        d = t.float() - b.float()
        if float(d.abs().max()) < 1e-8:
            tensors[f"{rest}::same"] = torch.zeros(1, dtype=torch.int8)
            continue
        rows = d.reshape(d.shape[0], -1) if d.dim() > 1 else d.reshape(1, -1)
        scale = rows.abs().amax(dim=1, keepdim=True).clamp_min(1e-12) / 127
        q = torch.round(rows / scale).clamp(-127, 127).to(torch.int8)
        tensors[f"{rest}::q"] = q.contiguous()
        tensors[f"{rest}::s"] = scale.float().contiguous()
        delta += 1
    save_file(tensors, out, metadata={"ectype_pack": PACK_FORMAT, "prefix": prefix,
                                      "model_metadata": json.dumps(metadata)})
    return {"bytes": os.path.getsize(out), "layers_changed": delta, "layers_whole": whole,
            "full_bytes": os.path.getsize(trained)}


def compose(base: str, pack: str, out: str) -> str:
    """The family model, rebuilt from the general model and its pack (see make_pack)."""
    from safetensors import safe_open
    from safetensors.torch import load_file, save_file

    old = {_split_name(n)[1]: t for n, t in load_file(base).items()}
    tensors = {}
    with safe_open(pack, framework="pt") as f:
        meta = f.metadata() or {}
        prefix = meta.get("prefix", "")
        full = (lambda rest: f"{prefix}.{rest}" if prefix else rest)  # noqa: E731
        for key in f.keys():
            rest, kind = key.rsplit("::", 1)
            if kind == "full":
                tensors[full(rest)] = f.get_tensor(key)
            elif kind == "same":
                tensors[full(rest)] = old[rest]
            elif kind == "q":
                b = old[rest]
                q, scale = f.get_tensor(key).float(), f.get_tensor(f"{rest}::s")
                tensors[full(rest)] = (b.float() + (q * scale).reshape(b.shape)).to(b.dtype).contiguous()
    save_file(tensors, out, metadata=json.loads(meta.get("model_metadata") or "{}"))
    return out


class Reader:
    """Recognition models, loaded once each and kept (a family's: the general model with the
    family's pack laid over it)."""

    def __init__(self):
        self.models = {}
        self.tmp = None

    def model(self, path: str, pack: str | None = None):
        key = (path, pack)
        if key not in self.models:
            from kraken.tasks import RecognitionTaskModel

            if pack:
                import tempfile

                self.tmp = self.tmp or tempfile.TemporaryDirectory(prefix="ectype-kraken-")
                path = compose(path, pack, os.path.join(self.tmp.name, f"m{len(self.models)}.safetensors"))
            self.models[key] = RecognitionTaskModel.load_model(path)
        return self.models[key]

    def read(self, request: dict) -> dict:
        """Read boxed lines of one image with one model. Request: {"model", "image", "lines":
        [[x0, y0, x1, y1], ...]}. Answer: {"lines": [{"text", "chars": [[char, [x0, y0, x1, y1],
        confidence 0-100], ...]}, ...]}, the text in reading order."""
        from kraken.configs import RecognitionInferenceConfig
        from kraken.containers import BBoxLine, Segmentation
        from PIL import Image

        model = self.model(request["model"], request.get("pack"))
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
    # No compiling of the network (torch.compile): it needs a C++ compiler, which users don't have.
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
    _utf8_by_default()
    for stream in (sys.stdout, sys.stderr, sys.stdin):  # JSON on the pipes is UTF-8 everywhere
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
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
        elif command == "pack":
            _out(make_pack(rest[0], rest[1], rest[2]))
        elif command == "compose":
            _out({"model": compose(rest[0], rest[1], rest[2])})
        else:
            print(f"unknown command {command!r}", file=sys.stderr)
            return 2
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
