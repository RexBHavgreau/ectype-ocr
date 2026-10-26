"""A book project: a scan, its workspace, settings and per-page corrections.

The GUI drives everything through this class, and so can scripts; nothing here
depends on Qt. Settings live in <workspace>/project.json, preprocessing overrides
(split position, no split, rotation, skew) in <workspace>/overrides.json, the same
file `ectype preprocess --overrides` reads.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import report

Progress = Callable[[str, str], None]  # (stage, message)

STAGES_TRACED = ["preprocess", "ocr", "segment", "cluster", "fonts", "traced"]
STAGES_SUBSTITUTE = ["preprocess", "ocr", "segment", "substitute"]
STAGES_TEXT = ["preprocess", "ocr", "textlayer"]
STAGES = {"traced": STAGES_TRACED, "substitute": STAGES_SUBSTITUTE, "text": STAGES_TEXT}
OUTPUT_FOLDER = {"traced": "traced", "substitute": "substitute", "text": "textlayer"}


@dataclass
class Settings:
    source: str = ""
    mode: str = "traced"  # "traced", "substitute" or "text" (the scan with an invisible text layer)
    split_spreads: bool = True
    font: str = ""  # substitute mode; empty = default serif
    lang: str = "eng"  # Tesseract languages, e.g. "eng+grc"
    page_image: str = "gray"  # text mode: "gray" or "bw" (black and white, smallest)
    name: str = ""  # output file name without .pdf


class Project:
    def __init__(self, workspace: Path, source: Path | None = None):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.settings = self._load_settings()
        if source is not None:
            self.settings.source = str(Path(source).resolve())
            if not self.settings.name:
                self.settings.name = Path(source).stem
            self.save_settings()

    @classmethod
    def for_scan(cls, scan: Path) -> "Project":
        """A project whose workspace sits beside the scan: <scan name>.ectype/."""
        scan = Path(scan)
        return cls(scan.with_name(scan.stem + ".ectype"), scan)

    # -- settings and overrides -------------------------------------------------

    def _load_settings(self) -> Settings:
        path = self.workspace / "project.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return Settings(**{k: v for k, v in data.items() if k in Settings.__dataclass_fields__})
        return Settings()

    def save_settings(self) -> None:
        report.write_json(self.workspace / "project.json", asdict(self.settings))

    @property
    def overrides_path(self) -> Path:
        return self.workspace / "overrides.json"

    def overrides(self) -> dict[str, dict]:
        if self.overrides_path.is_file():
            return json.loads(self.overrides_path.read_text(encoding="utf-8"))
        return {}

    def _set_override(self, page: int, key: str, value) -> None:
        data = self.overrides()
        entry = data.setdefault(str(page), {})
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
        if not entry:
            data.pop(str(page))
        report.write_json(self.overrides_path, data)

    def set_split(self, page: int, x: int | bool | None) -> None:
        """Split source page at x (in the upright image), False for no split, None for automatic."""
        self._set_override(page, "split", x)

    def set_rotation(self, page: int, clockwise: int | None) -> None:
        self._set_override(page, "rotate", None if clockwise is None else clockwise % 360)

    def reset_page(self, page: int) -> None:
        data = self.overrides()
        data.pop(str(page), None)
        report.write_json(self.overrides_path, data)

    # -- what the workspace holds ----------------------------------------------

    def sources(self) -> list[dict]:
        """Source pages as preprocessing saw them (orientation, split, review thumbnail)."""
        path = self.workspace / "preprocess" / "preprocess.json"
        return json.loads(path.read_text(encoding="utf-8"))["sources"] if path.is_file() else []

    def pages(self) -> list[str]:
        return sorted(p.stem for p in (self.workspace / "pages").glob("p*.png"))

    def page_image(self, page: str) -> Path:
        return self.workspace / "pages" / f"{page}.png"

    def ocr(self, page: str):
        from .ocr import OcrPage

        path = self.workspace / "ocr" / f"{page}.ocr.json"
        return OcrPage.from_json(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else None

    def output_pdf(self) -> Path | None:
        folder = OUTPUT_FOLDER.get(self.settings.mode, "traced")
        name = (self.settings.name or self.workspace.name) + ".pdf"
        path = self.workspace / folder / name
        return path if path.is_file() else None

    def corrected(self, page: str) -> set[tuple[int, int]]:
        path = self.workspace / "corrections" / f"{page}.json"
        if not path.is_file():
            return set()
        return {(e["line"], e["word"]) for e in json.loads(path.read_text(encoding="utf-8"))}

    def suspects(self) -> list[dict]:
        path = self.workspace / "cluster" / "clusters.json"
        return json.loads(path.read_text(encoding="utf-8")).get("suspects", []) if path.is_file() else []

    def suspect_words(self) -> dict[str, set[tuple[int, int]]]:
        from .review import _flags

        return _flags(self.workspace)

    def style_sheets(self) -> list[Path]:
        return sorted((self.workspace / "cluster").glob("style-*.png"), key=lambda p: int(p.stem.split("-")[1]))

    # -- actions -----------------------------------------------------------------

    def correct(self, page: str, line: int, word: int, text: str) -> dict:
        from .review import correct

        return correct(self.workspace, page, line, word, text)

    def stages(self) -> list[str]:
        return STAGES.get(self.settings.mode, STAGES_TRACED)

    def update_stage(self) -> str:
        """Where to start again after OCR corrections: the stage after OCR."""
        stages = self.stages()
        return stages[stages.index("ocr") + 1]

    def languages(self) -> list[str]:
        from .languages import split

        return split(self.settings.lang)

    def set_languages(self, codes: list[str]) -> None:
        """OCR languages, in order (the first is the main one). Pages are read again on
        the next run."""
        from .languages import join

        self.settings.lang = join(codes)
        self.save_settings()

    def has_corrections(self) -> bool:
        return any((self.workspace / "corrections").glob("*.json"))

    def run(self, start: str = "preprocess", progress: Progress | None = None) -> Path | None:
        """Run the pipeline from `start` to the output PDF. Stages before it keep their results."""
        progress = progress or (lambda stage, msg: None)
        stages = self.stages()
        if start not in stages:
            raise ValueError(f"unknown stage {start!r}; choose from {', '.join(stages)}")
        ws = self.workspace
        s = self.settings
        for stage in stages[stages.index(start):]:
            progress(stage, "running")
            if stage == "preprocess":
                if not s.source or not Path(s.source).is_file():
                    raise FileNotFoundError("no scan to preprocess; open a scan first")
                from .preprocess import preprocess

                out = preprocess(Path(s.source), ws, keep_spreads=not s.split_spreads, overrides=self.overrides())
                msg = f"{len(out['sources'])} source pages -> {len(out['pages'])} pages"
            elif stage == "ocr":
                from .ocr import ocr_pages

                out = ocr_pages(ws / "pages", ws / "ocr", lang=s.lang)
                msg = f"{sum(r['words'] for r in out['pages'])} words"
            elif stage == "segment":
                from .segment import segment_workspace

                out = segment_workspace(ws)
                msg = f"{sum(r['units'] for r in out['pages'])} glyph units"
            elif stage == "cluster":
                from .cluster import cluster_workspace

                out = cluster_workspace(ws)
                msg = f"{len(out['glyphs'])} glyphs in {len(out['styles'])} styles; {len(out['suspects'])} probable misreadings"
            elif stage == "fonts":
                from .fontbuild import build_workspace_fonts

                out = build_workspace_fonts(ws, family=f"Ectype {s.name or ws.name}")
                bad = [st["style"] for st in out["styles"] if st["validation"]["ots"] not in ("pass", "not installed")]
                msg = f"{len(out['styles'])} fonts" + (f"; validation problems in styles {bad}" if bad else ", all valid")
            elif stage == "textlayer":
                from .textlayer import textlayer_workspace

                out = textlayer_workspace(ws, out_name=s.name or None, image=s.page_image)
                msg = f"{out['bytes']:,} bytes; {out['words']} words as an invisible text layer"
            elif stage == "traced":
                from .traced import traced_workspace

                out = traced_workspace(ws, out_name=s.name or None)
                msg = f"{out['bytes']:,} bytes; {out['words']} words as text, {out['fallback_words']} left as image"
            else:
                from .substitute import substitute_workspace
                from .synth import find_font

                out = substitute_workspace(ws, find_font(s.font or None), out_name=s.name or None)
                msg = f"{out['bytes']:,} bytes"
            progress(stage, msg)
        return self.output_pdf()

    # -- IDML ---------------------------------------------------------------------

    def _stale(self, stage: str) -> bool:
        """True when a stage's results are missing or older than the OCR they come from."""
        ocr = [p.stat().st_mtime for p in (self.workspace / "ocr").glob("*.ocr.json")]
        if stage == "segment":
            outs = list((self.workspace / "segment").glob("*.seg.json"))
            if len(outs) < len(ocr):
                return True
        else:
            outs = [self.workspace / "cluster" / "clusters.json"]
            seg = [p.stat().st_mtime for p in (self.workspace / "segment").glob("*.seg.json")]
            ocr = ocr + seg
            if not outs[0].is_file():
                return True
        return bool(ocr) and min(p.stat().st_mtime for p in outs) < max(ocr)

    def prepare_idml(self, progress: Progress | None = None) -> dict:
        """What an IDML export needs beyond the OCR: segmentation and glyph styles (for
        slant, running heads and font matching), then the page layout and the fonts most
        like the scan's. No glyph tracing. Returns {"layout", "matches"}."""
        progress = progress or (lambda stage, msg: None)
        if not self.pages() or not any((self.workspace / "ocr").glob("*.ocr.json")):
            raise FileNotFoundError("no OCR yet; run the pipeline first")
        ws = self.workspace
        if self._stale("segment"):
            from .segment import segment_workspace

            progress("segment", "running")
            out = segment_workspace(ws)
            progress("segment", f"{sum(r['units'] for r in out['pages'])} glyph units")
        if self._stale("cluster"):
            from .cluster import cluster_workspace

            progress("cluster", "running")
            out = cluster_workspace(ws)
            progress("cluster", f"{len(out['glyphs'])} glyphs in {len(out['styles'])} styles")
        from .fontmatch import match_styles, write_matches
        from .layout import build_layout

        progress("layout", "running")
        layout = build_layout(ws)
        progress("layout", f"{sum(len(p.blocks) for p in layout.pages)} text blocks on {len(layout.pages)} pages")
        progress("fonts", "comparing the scan's letters with the installed fonts")
        matches = match_styles(ws, layout, progress=lambda msg: progress("fonts", msg))
        write_matches(ws / "cluster" / "font-matches.json", matches)
        return {"layout": layout, "matches": matches}

    def export_idml(self, dest: Path, options=None, prepared: dict | None = None,
                    progress: Progress | None = None) -> dict:
        from .idml import export_idml

        prepared = prepared or self.prepare_idml(progress)
        out = export_idml(self.workspace, Path(dest), options, prepared["layout"], prepared["matches"])
        if progress:
            progress("idml", f"{out['pages']} pages -> {dest}")
        return out

    def export(self, dest: Path) -> Path:
        pdf = self.output_pdf()
        if pdf is None:
            raise FileNotFoundError("nothing to export yet; run the pipeline first")
        dest = Path(dest)
        shutil.copyfile(pdf, dest)
        return dest
