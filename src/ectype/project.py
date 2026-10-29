"""A book project: a scan, its workspace, settings and per-page corrections.

The GUI drives everything through this class, and so can scripts; nothing here
depends on Qt. Settings live in <workspace>/project.json, preprocessing overrides
(split position, no split, rotation, skew) in <workspace>/overrides.json, the same
file `ectype preprocess --overrides` reads.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import report

Progress = Callable[[str, str], None]  # (stage, message)

STAGES_TRACED = ["preprocess", "ocr", "segment", "cluster", "fonts", "traced"]
STAGES_SUBSTITUTE = ["preprocess", "ocr", "segment", "substitute"]
STAGES_TEXT = ["preprocess", "ocr", "textlayer"]
STAGES = {"traced": STAGES_TRACED, "substitute": STAGES_SUBSTITUTE, "text": STAGES_TEXT}
# Rough share of a run's time per stage, for the progress bar (OCR takes the most).
STAGE_WEIGHT = {"preprocess": 12, "ocr": 50, "segment": 12, "cluster": 8, "fonts": 6, "traced": 12,
                "substitute": 12, "textlayer": 10}
OUTPUT_FOLDER = {"traced": "traced", "substitute": "substitute", "text": "textlayer"}


@dataclass
class Settings:
    source: str = ""
    mode: str = "traced"  # "traced", "substitute" or "text" (the scan with an invisible text layer)
    split_spreads: bool = True
    font: str = ""  # substitute mode; empty = default serif
    lang: str = "eng"  # Tesseract languages, e.g. "eng+grc"
    page_image: str = "gray"  # text mode: "gray" or "bw" (black and white, smallest)
    declined_languages: list = field(default_factory=list)  # languages the user chose not to add when offered
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

    def unread_scripts(self) -> dict[str, int]:
        """Words found in scripts none of the OCR languages covers, by script (last OCR run)."""
        path = self.workspace / "ocr" / "ocr.json"
        return json.loads(path.read_text(encoding="utf-8")).get("unread_scripts", {}) if path.is_file() else {}

    def language_suggestions(self) -> list[str]:
        """Languages to offer for those scripts (not ones chosen, or declined for this project)."""
        from .scripts import suggest

        path = self.workspace / "ocr" / "ocr.json"
        pages = [r.get("unread_scripts") or {} for r in json.loads(path.read_text(encoding="utf-8"))["pages"]] \
            if path.is_file() else []
        return [lang for lang in suggest(self.unread_scripts(), self.languages(), pages)
                if lang not in self.settings.declined_languages]

    def decline_languages(self, codes: list[str]) -> None:
        self.settings.declined_languages = sorted(set(self.settings.declined_languages) | set(codes))
        self.save_settings()

    def corrected_lines(self) -> int:
        """Lines corrected in Review: the training data for the OCR model."""
        from .train import training_pairs

        return len(training_pairs(self.workspace))

    def base_language(self) -> str:
        """The main OCR language, as a stock model (a trained one's base)."""
        from .train import trained_models

        main = self.languages()[0]
        return trained_models().get(main, {}).get("base", main)

    def trainable(self) -> list[tuple[str, str, int]]:
        """What training can run on now: (stock language, "lines" or "words", count) for the main
        language with enough corrected lines, and each other-script language with enough
        corrected words (Hebrew, Greek... are trained on words, their lines mixing scripts)."""
        from .scripts import script_of
        from .train import MIN_LINES, MIN_WORDS, script_words, trained_models

        out = []
        lines = self.corrected_lines()
        if lines >= MIN_LINES:
            out.append((self.base_language(), "lines", lines))
        models = trained_models()
        for code in self.languages()[1:]:
            base = models.get(code, {}).get("base", code)
            if script_of(base) != "Latin":
                n = len(script_words(self.workspace, base))
                if n >= MIN_WORDS:
                    out.append((base, "words", n))
        return out

    def corrected_words(self) -> int:
        """Words corrected into another script than the main text's, saved for training."""
        from .train import training_pairs

        return len(training_pairs(self.workspace, words=True))

    def train_model(self, progress: Progress | None = None) -> list:
        """Fine-tune each language there is enough correction for (see trainable), and use the
        new models for this book from the next reading on."""
        from .train import MIN_LINES, TrainingError, train, trained_models

        progress = progress or (lambda stage, msg: None)
        plan = self.trainable()
        if not plan:
            raise TrainingError(f"Correct at least {MIN_LINES} lines in Review first ({self.corrected_lines()} so far). "
                                "Each corrected line becomes training data.")
        book = self.settings.name or self.workspace.name
        results = [train(self.workspace, base, book, progress=lambda msg: progress("train", msg), words=unit == "words")
                   for base, unit, _ in plan]
        models = trained_models()
        new = {r.base: r.model for r in results}
        codes = []
        for code in self.languages():
            code = new.get(models.get(code, {}).get("base", code), code)
            if code not in codes:
                codes.append(code)
        self.set_languages(codes)
        return results

    def has_corrections(self) -> bool:
        return any((self.workspace / "corrections").glob("*.json"))

    def run(self, start: str = "preprocess", progress: Progress | None = None,
            fraction: Callable[[float], None] | None = None) -> Path | None:
        """Run the pipeline from `start` to the output PDF. Stages before it keep their results.
        `fraction(f)` reports how much of the run is done, from 0 to 1, page by page."""
        progress = progress or (lambda stage, msg: None)
        fraction = fraction or (lambda f: None)
        stages = self.stages()
        if start not in stages:
            raise ValueError(f"unknown stage {start!r}; choose from {', '.join(stages)}")
        ws = self.workspace
        s = self.settings
        running = stages[stages.index(start):]
        whole = sum(STAGE_WEIGHT.get(st, 10) for st in running)
        before = 0.0
        fraction(0.0)
        for stage in running:
            weight = STAGE_WEIGHT.get(stage, 10)

            def on_page(done: int, total: int, stage=stage, weight=weight, before=before) -> None:
                fraction((before + weight * done / max(1, total)) / whole)
                if stage in ("ocr", "segment") or done == total:
                    progress(stage, f"page {done} of {total}")

            progress(stage, "running")
            if stage == "preprocess":
                if not s.source or not Path(s.source).is_file():
                    raise FileNotFoundError("no scan to preprocess; open a scan first")
                from .preprocess import preprocess

                out = preprocess(Path(s.source), ws, keep_spreads=not s.split_spreads, overrides=self.overrides(), on_page=on_page)
                msg = f"{len(out['sources'])} source pages -> {len(out['pages'])} pages"
            elif stage == "ocr":
                from .ocr import ocr_pages

                out = ocr_pages(ws / "pages", ws / "ocr", lang=s.lang, on_page=on_page)
                msg = f"{sum(r['words'] for r in out['pages'])} words"
                if out["unread_scripts"]:
                    from .languages import name

                    found = ", ".join(f"{script} ({n} words)" for script, n in out["unread_scripts"].items())
                    msg += f"; words in scripts the OCR languages don't cover: {found}"
                    if out["suggest_languages"]:
                        msg += "; add " + ", ".join(name(c) for c in out["suggest_languages"])
            elif stage == "segment":
                from .segment import segment_workspace

                out = segment_workspace(ws, on_page=on_page)
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

                out = textlayer_workspace(ws, out_name=s.name or None, image=s.page_image, on_page=on_page)
                msg = f"{out['bytes']:,} bytes; {out['words']} words as an invisible text layer"
            elif stage == "traced":
                from .traced import traced_workspace

                out = traced_workspace(ws, out_name=s.name or None, on_page=on_page)
                msg = f"{out['bytes']:,} bytes; {out['words']} words as text, {out['fallback_words']} left as image"
            else:
                from .substitute import substitute_workspace
                from .synth import find_font

                out = substitute_workspace(ws, find_font(s.font or None), out_name=s.name or None, on_page=on_page)
                msg = f"{out['bytes']:,} bytes"
            progress(stage, msg)
            before += weight
            fraction(before / whole)
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
        """Export to dest (an .idml path): the export folder is dest's name beside it,
        holding the IDML and its Links, as docx-to-idml writes them."""
        from .idml import export_idml

        dest = Path(dest)
        prepared = prepared or self.prepare_idml(progress)
        if progress:
            progress("idml", "reflow: writing the manuscript and running docx-to-idml" if options is None or
                     options.mode == "reflow" else "facsimile: laying out the pages")
        out = export_idml(self.workspace, dest.parent, dest.stem, options, prepared["layout"], prepared["matches"],
                          lang=self.settings.lang)
        if progress:
            progress("idml", out["idml"])
            for line in out.get("report", []):
                progress("idml", line)
        return out

    def export(self, dest: Path) -> Path:
        pdf = self.output_pdf()
        if pdf is None:
            raise FileNotFoundError("nothing to export yet; run the pipeline first")
        dest = Path(dest)
        shutil.copyfile(pdf, dest)
        return dest
