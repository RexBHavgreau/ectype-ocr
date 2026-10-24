# Credits

Ectype OCR builds on other people's work. This file is updated in the same commit as anything borrowed. See `PLAN.md` for the attribution rule.

## Code copied or adapted

None yet.

## Ideas and methods

| Source | What we follow | Link |
| --- | --- | --- |
| Adobe Acrobat ClearScan | The target output: glyphs traced from the scan into synthesized CID-keyed CFF fonts, painted as visible text over a background with the text erased (as analyzed in `PLAN.md`) | — |
| Scribe OCR (AGPL-3.0) | A per-document overlay font fitted to OCR measurements; per-word `Tc`/`TJ` placement. Read for ideas only, no code copied (see `docs/spikes/scribe-ocr.md`) | https://github.com/scribeocr/scribeocr |
| smoothscan (GPL-3.0) | Grouping repeated symbols, vectorizing them and embedding them as a font | https://github.com/ncraun/smoothscan |
| Leptonica jbclass and jbig2enc | Symbol classification by correlation; averaging every instance of a class into one composite template | http://www.leptonica.org/ , https://github.com/agl/jbig2enc |
| Ocular OCR; DeepVecFont, Im2Vec | Learned font models and vector-font reconstruction, for later work on damaged or rare glyphs | — |

## Dependencies

| Dependency | Used for | License |
| --- | --- | --- |
| pikepdf (and QPDF) | Reading and writing PDF structure | MPL-2.0 (QPDF: Apache-2.0) |
| pypdfium2 (and PDFium) | Rendering PDF pages; text extraction | Apache-2.0 or BSD-3-Clause (PDFium: Apache-2.0 / BSD-3-Clause) |
| fontTools | Reading embedded fonts; later, building CFF fonts | MIT |
| NumPy | Image arrays | BSD-3-Clause |
| OpenCV (opencv-python-headless) | Image processing | Apache-2.0 (wheels: MIT) |
| Pillow | Image I/O; drawing synthetic pages | MIT-CMU (HPND) |
| scikit-image | SSIM for visual fidelity | BSD-3-Clause |
| RapidFuzz | Edit distance for character error rate | MIT |
| vtracer | Glyph tracing (from Phase 4) | MIT |
| pytesseract | Tesseract wrapper (from Phase 1) | Apache-2.0 |
| Tesseract OCR | OCR engine (command-line tool, installed separately) | Apache-2.0 |
| Leptonica | Symbol classification and composites, through ctypes (system library, installed separately) | BSD-2-Clause |
| pytest | Test runner (development only) | MIT |

## Models, datasets, and fonts

| Item | Use | Authors / source | License |
| --- | --- | --- | --- |
| E. J. Young, "Some Reflections upon Inspiration" (scan in `corpus/`) | Primary test case | Scanned by the project author | Test material only; not redistributed in releases |
| The same chapter after Acrobat ClearScan (`corpus/E J. Young Inspiration-1 - Copy.pdf`) | Reference result (see `docs/acrobat-baseline.md`) | Produced by the project author with Adobe Acrobat 26 | Test material only; not redistributed in releases |

Fonts used for synthetic test pages are taken from the local system at run time and are not bundled; each generated page records the font file and its hash in its `truth.json`.
