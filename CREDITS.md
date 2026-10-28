# Credits

Ectype OCR builds on other people's work. This file is updated in the same commit as anything borrowed. See `PLAN.md` for the attribution rule.

## Code copied or adapted

| Source | What | License |
| --- | --- | --- |
| docx-to-idml (https://github.com/RexBHavgreau/docx-to-idml), commit dff3874 | `src/ectype/idmlkit/`: its IDML writer (`writer.py`, `model.py`, `images.py`) and `base/` (InDesign's own preferences, colours, fonts and root styles), copied unchanged; Ectype's facsimile export subclasses the writer. The .docx it writes for Reflow follows docx-to-idml's `docs/ENGINE.md` and its test helper's WordprocessingML | MIT (`src/ectype/idmlkit/LICENSE-docx-to-idml`) |
| docx-to-idml's updater (`docx2idml/updates.py`) | `src/ectype/updates.py` follows it: the GitHub latest-release check, the release file picked by name, the download through a ".part" file checked against size and SHA-256 digest, and the offer/skip settings. Changed to download and run an installer instead of saving a new .exe | MIT (`LICENSES/docx-to-idml-MIT.txt`) |

## Ideas and methods

| Source | What we follow | Link |
| --- | --- | --- |
| Adobe Acrobat ClearScan | The target output: glyphs traced from the scan into synthesized CID-keyed CFF fonts, painted as visible text over a background with the text erased (as analyzed in `PLAN.md`) | — |
| Scribe OCR (AGPL-3.0) | A per-document overlay font fitted to OCR measurements; per-word `Tc`/`TJ` placement. Read for ideas only, no code copied (see `docs/spikes/scribe-ocr.md`) | https://github.com/scribeocr/scribeocr |
| smoothscan (GPL-3.0) | Grouping repeated symbols, vectorizing them and embedding them as a font | https://github.com/ncraun/smoothscan |
| Leptonica jbclass and jbig2enc | Symbol classification by correlation; averaging every instance of a class into one composite template | http://www.leptonica.org/ , https://github.com/agl/jbig2enc |
| OCRmyPDF and Tesseract's PDF renderer ("GlyphLessFont") | An invisible text layer set in a font of empty glyphs, mapped to Unicode | https://github.com/ocrmypdf/OCRmyPDF |
| Ocular OCR; DeepVecFont, Im2Vec | Learned font models and vector-font reconstruction, for later work on damaged or rare glyphs | — |

## Dependencies

| Dependency | Used for | License |
| --- | --- | --- |
| pikepdf (and QPDF) | Reading and writing PDF structure | MPL-2.0 (QPDF: Apache-2.0) |
| pypdfium2 (and PDFium) | Rendering PDF pages; text extraction | Apache-2.0 or BSD-3-Clause (PDFium: Apache-2.0 / BSD-3-Clause) |
| fontTools | Reading embedded fonts; later, building CFF fonts | MIT |
| NumPy | Image arrays | BSD-3-Clause |
| SciPy | Component bounding boxes (`ndimage`) | BSD-3-Clause |
| OpenCV (opencv-python-headless) | Image processing | Apache-2.0 (wheels: MIT) |
| Pillow | Image I/O; drawing synthetic pages | MIT-CMU (HPND) |
| scikit-image | SSIM for visual fidelity | BSD-3-Clause |
| RapidFuzz | Edit distance for character error rate | MIT |
| vtracer | Tracing averaged glyph composites into Bézier outlines | MIT |
| pytesseract | Tesseract wrapper (from Phase 1) | Apache-2.0 |
| Tesseract OCR | OCR engine (command-line tool, installed separately) | Apache-2.0 |
| Leptonica | Symbol classification and composites, through ctypes (system library, installed separately) | BSD-2-Clause |
| PySide6 (Qt for Python) | The desktop app (optional `gui` extra), used as an unmodified library | LGPL-3.0 |
| docx-to-idml | Reflow's layout: run as a separate installed program, not shipped; in the tests, run from source, and its checker validates every IDML export against Adobe's IDML schema (jing) | MIT |
| lxml | Writing IDML (docx-to-idml's writer) | BSD-3-Clause |
| PyInstaller | Packaging the Windows app (build only; its bootloader is GPL-2.0 with an exception allowing distribution of packaged programs) | GPL-2.0 with bootloader exception |
| Inno Setup | Building the Windows installer (build only) | Inno Setup licence (free, including commercial use) |
| pytest | Test runner (development only) | MIT |
| opentype-sanitizer (OTS) | Validating synthesized fonts (development) | BSD-3-Clause |

## Models, datasets, and fonts

| Item | Use | Authors / source | License |
| --- | --- | --- | --- |
| E. J. Young, "Some Reflections upon Inspiration" (scan in `corpus/`) | Primary test case | Scanned by the project author | Test material only; not redistributed in releases |
| The same chapter after Acrobat ClearScan (`corpus/E J. Young Inspiration-1 - Copy.pdf`) | Reference result (see `docs/acrobat-baseline.md`) | Produced by the project author with Adobe Acrobat 26 | Test material only; not redistributed in releases |

| EB Garamond (bundled with the Windows app; the default substitute font) | Substitute mode | Georg Duffner, Octavio Pardo | SIL OFL 1.1 (`fonts/EBGaramond-OFL.txt` in the app) |
| Tesseract and its libraries, with `eng` and `osd` data (bundled with the Windows app, UB Mannheim build) | OCR | Tesseract contributors; Leptonica, libpng, libjpeg, libtiff, zlib and others | Apache-2.0 (Tesseract); each library's licence is in the app's `tesseract` folder |
| Tesseract language data (tessdata), downloaded on request | OCR in further languages | Tesseract contributors | Apache-2.0 |
| Liberation Serif (default substitute font when EB Garamond isn't installed) | Substitute mode; synthetic pages | Steve Matteson, Ascender Corp.; Red Hat | SIL OFL 1.1 |
| DejaVu Serif, FreeSerif | Synthetic test pages | DejaVu fonts team; GNU FreeFont (Primož Peterlin et al.) | Bitstream Vera license; GPL-3.0 with font exception |

From source, fonts are taken from the local system at run time; only the Windows app bundles one (EB Garamond). Each synthetic page records the font file and its hash in its `truth.json`; a substitute-mode PDF embeds a subset of the chosen font, which these licenses permit.
