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
STAGES_MANUSCRIPT = ["preprocess", "read", "textlayer"]  # Kraken's lines read with the hands, a searchable PDF
# Rough share of a run's time per stage, for the progress bar (OCR takes the most).
STAGE_WEIGHT = {"preprocess": 12, "ocr": 50, "read": 75, "segment": 12, "cluster": 8, "fonts": 6, "traced": 12,
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
    script: str = "print"  # "print", or "manuscript" (Manuscript Mode: manuscript.py)
    ms_languages: list = field(default_factory=list)  # Manuscript Mode's languages, in order
    hands: dict = field(default_factory=dict)  # Manuscript Mode: language -> its hands (ids) to read with, in order
    hand_reading: str = "unsure"  # several hands: "every" line with every hand, or others only on "unsure" lines
    sample: list = field(default_factory=list)  # "Train for this book": the pages chosen to correct first


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
        folder = "textlayer" if self.manuscript() else OUTPUT_FOLDER.get(self.settings.mode, "traced")
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

    def correct(self, page: str, line: int, word: int, text: str, span: tuple[int, int] | None = None,
                box: list[int] | None = None, style: dict | None = None) -> dict:
        from .review import correct

        return correct(self.workspace, page, line, word, text, span, box, style)

    def word_styles(self, page: str) -> dict[tuple[int, int], dict]:
        """Each word's style on a page: italic as detected (the glyph grouping's), and what was
        set in Review over it (italic, bold, small capitals)."""
        from .review import word_styles

        out: dict[tuple[int, int], dict] = {}
        clusters, seg = self.workspace / "cluster" / "clusters.json", self.workspace / "segment" / f"{page}.seg.json"
        if clusters.is_file() and seg.is_file():
            italic = {u: g["italic"] for g in json.loads(clusters.read_text(encoding="utf-8"))["glyphs"]
                      for p, u in g["instances"] if p == page}
            votes: dict[tuple[int, int], list[bool]] = {}
            for u in json.loads(seg.read_text(encoding="utf-8"))["units"]:
                if u["id"] in italic and u["text"].isalpha():
                    votes.setdefault((u["line"], u["word"]), []).append(italic[u["id"]])
            out = {k: {"italic": sum(v) * 2 > len(v)} for k, v in votes.items()}
        for k, st in word_styles(self.workspace, page).items():
            out.setdefault(k, {}).update(st)
        return out

    def add_word(self, page: str, box: list[int], text: str) -> dict:
        from .review import add_word

        return add_word(self.workspace, page, box, text)

    def stages(self) -> list[str]:
        if self.manuscript():
            return STAGES_MANUSCRIPT
        return STAGES.get(self.settings.mode, STAGES_TRACED)

    def update_stage(self) -> str:
        """Where to start again after OCR corrections: the stage after OCR (reading)."""
        stages = self.stages()
        return stages[stages.index("read" if self.manuscript() else "ocr") + 1]

    # -- Manuscript Mode (manuscript.py) -------------------------------------------------

    def manuscript(self) -> bool:
        return self.settings.script == "manuscript"

    def set_script(self, script: str) -> None:
        """Print or Manuscript Mode. Each keeps its own languages; pages are read again on the next run."""
        self.settings.script = "manuscript" if script == "manuscript" else "print"
        self.save_settings()

    def set_hands(self, lang: str, hand_ids: list[str]) -> None:
        """The hands a manuscript language is read with, in order; the language is chosen while it has one."""
        s = self.settings
        s.hands = {**s.hands, lang: list(hand_ids)}
        if hand_ids and lang not in s.ms_languages:
            s.ms_languages = s.ms_languages + [lang]
        if not hand_ids:
            s.hands.pop(lang, None)
            s.ms_languages = [c for c in s.ms_languages if c != lang]
        self.save_settings()

    def set_hand_reading(self, how: str) -> None:
        from .manuscript import EVERY, UNSURE

        self.settings.hand_reading = EVERY if how == EVERY else UNSURE
        self.save_settings()

    def correct_line(self, page: str, index: int, text: str) -> dict:
        from .manuscript import correct_line

        return correct_line(self.workspace, page, index, text, self.settings, self.book())

    def manuscript_corrections(self) -> int:
        """Lines corrected or confirmed in this manuscript."""
        from .manuscript import corrections_of

        return sum(len(corrections_of(self.workspace, p)) for p in self.pages())

    def train_manuscript(self, progress: Progress | None = None) -> list[str]:
        """Train the book's hands on their corrected lines and the line-finding on its changed lines."""
        from .manuscript import train_book
        from .training import Progress as Bar

        progress = progress or (lambda stage, msg: None)
        bar = Bar(lambda msg: progress("train", msg), lambda f: progress("fraction", f"{f:.4f}"))
        return train_book(self.workspace, self.settings, bar)

    def choose_sample(self, n: int = 5, pages: list[str] | None = None) -> list[str]:
        """"Train for this book": the pages to correct first (given, or n at random from the middle)."""
        from .manuscript import sample_pages

        self.settings.sample = sorted(pages) if pages is not None else sample_pages(self.pages(), n)
        self.save_settings()
        return self.settings.sample

    def read_pages(self, pages: list[str], progress: Progress | None = None) -> dict:
        """Read some pages again (after their lines were changed in Review), and remake their OCR files."""
        from . import updates
        from .manuscript import read_workspace

        return read_workspace(self.workspace, self.settings, all_cores=updates.all_cores(updates.load_settings()),
                              pages=pages)

    def languages(self) -> list[str]:
        """The OCR languages chosen. A per-book model of an earlier version counts as the
        language it was trained for: the trained models now stand behind the languages."""
        from .languages import split
        from .models import stock_code

        out = []
        for code in split(self.settings.lang):
            code = stock_code(code)
            if code not in out:
                out.append(code)
        return out

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
        """Lines corrected in Review."""
        from .train import training_pairs

        return len(training_pairs(self.workspace))

    def corrected_words(self) -> int:
        """Words corrected into another script than the main text's."""
        from .train import training_pairs

        return len(training_pairs(self.workspace, words=True))

    def base_language(self) -> str:
        """The main OCR language."""
        return self.languages()[0]

    def book(self) -> str:
        return self.settings.name or self.workspace.name

    def sync_pool(self) -> dict[str, int]:
        """Add this project's corrections to the languages' training pools (pool.py)."""
        from .pool import sync_workspace

        return sync_workspace(self.workspace, self.book(), self.languages())

    def trainable(self) -> list[tuple[str, int]]:
        """The chosen languages with enough in their pools to train: (code, images)."""
        from .training import MIN_ITEMS, trainable

        self.sync_pool()
        out = []
        for code in self.languages():
            n = trainable(code)
            if n >= MIN_ITEMS:
                out.append((code, n))
        return out

    def train_model(self, progress: Progress | None = None, mode: str = "auto") -> list[dict]:
        """Train the chosen languages' models from their pools (training.py): each language's
        general model, and its typeface families' (mode: "auto", a quick update when little is
        new; "full", afresh). Then find which reads this book best."""
        from .train import TrainingError
        from .training import MIN_ITEMS, Progress, choose_for_workspace, train_languages

        progress = progress or (lambda stage, msg: None)
        say = lambda msg: progress("train", msg)  # noqa: E731
        plan = self.trainable()
        if not plan:
            raise TrainingError(f"Nothing to train yet: each language needs at least {MIN_ITEMS} corrected lines or "
                                "words in its pool. Correct more in Review (every project's corrections count).")
        bar = Progress(say, lambda f: progress("fraction", f"{f:.4f}"))
        done = train_languages([code for code, _ in plan], bar, mode=mode)  # side by side
        failed = {code: r for code, r in done.items() if isinstance(r, Exception)}
        if len(failed) == len(done):
            raise next(iter(failed.values()))
        reports = [r if not isinstance(r, Exception) else {"code": code, "mode": mode, "general": {"installed": False},
                                                           "families": {}, "lines": [f"{code}: {r}"]}
                   for code, r in done.items()]
        chosen = choose_for_workspace(self.workspace, self.book(), self.languages(), say)
        for r in reports:
            r["chosen"] = chosen.get(r["code"])
        return reports

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
            elif stage == "read":
                from . import updates
                from .manuscript import read_workspace

                out = read_workspace(ws, s, on_page=on_page, all_cores=updates.all_cores(updates.load_settings()))
                msg = (f"{out['lines']} lines on {out['pages']} pages; {out['unsure']} unsure"
                       + (f", {out['disagree']} read differently by the hands" if out["disagree"] else ""))
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

    def prepare_idml(self, progress: Progress | None = None, match_fonts: bool = True) -> dict:
        """What an IDML export needs beyond the OCR: segmentation and glyph styles (for
        slant, running heads and font matching), then the page layout and (match_fonts, for
        Facsimile) the fonts most like the scan's. No glyph tracing. Returns {"layout", "matches"}."""
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
        if not match_fonts:
            return {"layout": layout, "matches": None}
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
