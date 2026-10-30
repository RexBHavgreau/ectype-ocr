"""The trained models: one general model for each OCR language, with typeface-family models
beside it, chosen by Ectype rather than the user.

The user chooses languages ("English", "Hebrew", a Kraken model for Greek); the Languages menu
shows nothing else. Behind each chosen language:
- the stock model (Tesseract's language, or the Kraken model downloaded);
- the general model, trained from the stock model on everything in the language's pool
  (`pool.py`), replacing the stock model once there is one;
- family models, for typeface families with enough training data (`families.py`): for
  Tesseract a model of its own (a few megabytes), for Kraken a pack of the changes from the
  general model, laid over it when reading (`kraken_worker.py`).

    <user folder>/models/registry.json
    <user folder>/models/tesseract/<code>/general.traineddata     float form, the base for families
    <user folder>/tessdata/<code>_ectype.traineddata              the general model, as Tesseract reads it
    <user folder>/tessdata/<code>_ectype_f<n>.traineddata         family models
    <user folder>/models/kraken/<code>/general.safetensors
    <user folder>/models/kraken/<code>/f<n>.pack.safetensors

Reading a book (`resolve`): the model the book was last found to read best with, measured on
its corrections; else the family model nearest the book's typeface fingerprint; else the
general model; else the stock one.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .resources import user_data_dir, user_tessdata_dir

INTERNAL = "_ectype"


@dataclass
class Resolved:
    code: str  # the language chosen
    engine: str  # "tesseract" or "kraken"
    model: str  # Tesseract: the language name to read with; Kraken: the model file
    pack: str | None = None  # Kraken: a family pack laid over the model
    which: str = "stock"  # "stock", "general" or a family ("f1"...)

    def key(self) -> str:
        return f"{self.code}={self.which}"


def root() -> Path:
    return user_data_dir() / "models"


def _registry_path() -> Path:
    return root() / "registry.json"


def registry() -> dict:
    try:
        return json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_registry(data: dict) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def engine_of(code: str) -> str:
    from .kraken import is_model

    return "kraken" if is_model(code) else "tesseract"


def is_internal(name: str) -> bool:
    """A model Ectype keeps for itself, not a language to choose: the general and family
    models, and the per-book models of earlier versions."""
    if INTERNAL in name:
        return True
    from .train import trained_models

    if name in trained_models():
        return True
    from .kraken import installed_models

    return bool(installed_models().get(name, {}).get("book"))


def stock_code(code: str) -> str:
    """A language as chosen, from a per-book model of an earlier version: its stock language."""
    from .kraken import installed_models
    from .train import trained_models

    return trained_models().get(code, {}).get("base") or installed_models().get(code, {}).get("base") or code


def tesseract_name(code: str, which: str) -> str:
    return f"{code}{INTERNAL}" if which == "general" else f"{code}{INTERNAL}_{which}"


def tesseract_dir(code: str) -> Path:
    return root() / "tesseract" / code


def kraken_dir(code: str) -> Path:
    return root() / "kraken" / code


def entry(code: str) -> dict:
    return registry().get(code, {})


def _present(code: str, which: str, info: dict) -> bool:
    if engine_of(code) == "tesseract":
        return (user_tessdata_dir() / f"{tesseract_name(code, which)}.traineddata").is_file()
    path = info.get("pack") or info.get("file")
    return bool(path) and Path(path).is_file()


def choices(code: str) -> dict[str, dict]:
    """The models there are for a language: "general" and families, with their records."""
    e = entry(code)
    out = {}
    if e.get("general") and _present(code, "general", e["general"]):
        out["general"] = e["general"]
    for fid, info in (e.get("families") or {}).items():
        if _present(code, fid, info) and "general" in out:
            out[fid] = info
    return out


def _resolved(code: str, which: str) -> Resolved:
    if engine_of(code) == "tesseract":
        name = code if which == "stock" else tesseract_name(code, which)
        return Resolved(code, "tesseract", name, None, which)
    from .kraken import installed_models

    stock = installed_models().get(code, {}).get("file", "")
    if which == "stock":
        return Resolved(code, "kraken", stock, None, which)
    e = entry(code)
    general = e["general"]["file"]
    if which == "general":
        return Resolved(code, "kraken", general, None, which)
    info = e["families"][which]
    # A pack made over an earlier general model (kept by a quick update) is laid over that one.
    return Resolved(code, "kraken", info.get("base") or general, info["pack"], which)


def resolve(code: str, fingerprints: dict[str, list[float]] | None = None, chosen: dict[str, str] | None = None) -> Resolved:
    """The model to read a language with, for a book with these typeface fingerprints (script ->
    fingerprint) and, if any, the model found best on its corrections (chosen: code -> which)."""
    from .families import nearest
    from .scripts import script_of

    code = stock_code(code)
    have = choices(code)
    pick = (chosen or {}).get(code) or {}
    # The model found best on the book's corrections, unless the models were trained again since.
    if pick.get("generation") == entry(code).get("generation", 0) and pick.get("which") in have:
        return _resolved(code, pick["which"])
    families = {f: info["centroid"] for f, info in have.items() if f != "general" and info.get("centroid")}
    fp = (fingerprints or {}).get(script_of(code))
    family = nearest(fp, families) if fp else None
    if family:
        return _resolved(code, family)
    if "general" in have:
        return _resolved(code, "general")
    return _resolved(code, "stock")


def workspace_state(workspace: Path) -> dict:
    """What Ectype knows of a project's type: {"fingerprints": {script: fp}, "chosen": {code: which}}."""
    try:
        return json.loads((Path(workspace) / "ocr" / "models.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_workspace_state(workspace: Path, **changes) -> None:
    path = Path(workspace) / "ocr" / "models.json"
    data = {**workspace_state(workspace), **changes}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def resolve_all(codes: list[str], workspace: Path | None = None) -> list[Resolved]:
    state = workspace_state(workspace) if workspace else {}
    return [resolve(c, state.get("fingerprints"), state.get("chosen")) for c in codes]


def has_families(codes: list[str]) -> bool:
    return any(len(choices(stock_code(c))) > 1 for c in codes)


def describe(r: Resolved) -> str:
    from .languages import name

    if r.which == "stock":
        return f"{name(r.code)} (stock model)"
    if r.which == "general":
        return f"{name(r.code)} (trained)"
    books = entry(r.code).get("families", {}).get(r.which, {}).get("books") or []
    like = f", like {', '.join(books[:2])}" if books else ""
    return f"{name(r.code)} (trained, typeface family {r.which[1:]}{like})"


# -- managing ---------------------------------------------------------------------------


def size_of(code: str) -> int:
    total = 0
    for folder in (tesseract_dir(code), kraken_dir(code)):
        if folder.is_dir():
            total += sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    for p in user_tessdata_dir().glob(f"{code}{INTERNAL}*.traineddata"):
        total += p.stat().st_size
    return total


def remove(code: str, which: str | None = None) -> None:
    """Remove a language's trained models (all, or one family), going back to the one before."""
    data = registry()
    e = data.get(code, {})
    targets = [which] if which else ["general", *(e.get("families") or {})]
    for w in targets:
        info = e.get("families", {}).pop(w, None) if w != "general" else e.pop("general", None)
        if engine_of(code) == "tesseract":
            (user_tessdata_dir() / f"{tesseract_name(code, w)}.traineddata").unlink(missing_ok=True)
        elif info:
            for key in ("file", "pack"):
                if info.get(key) and (w != "general" or key == "file"):
                    Path(info[key]).unlink(missing_ok=True)
    if which is None:
        data.pop(code, None)
        shutil.rmtree(tesseract_dir(code), ignore_errors=True)
        shutil.rmtree(kraken_dir(code), ignore_errors=True)
    save_registry(data)


def legacy() -> list[str]:
    """Per-book models made by earlier versions ("English, trained on Young"), to be removed."""
    from .kraken import installed_models
    from .train import trained_models

    return sorted([*trained_models(), *(c for c, m in installed_models().items() if m.get("book"))])


def remove_legacy(code: str) -> None:
    from . import kraken
    from .train import forget_trained

    forget_trained(code)
    (user_tessdata_dir() / f"{code}.traineddata").unlink(missing_ok=True)
    model = kraken.installed_models().get(code)
    if model:
        shutil.rmtree(Path(model["file"]).parent, ignore_errors=True)
        kraken.unregister(code)
