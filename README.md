# ectype-ocr

Ectype OCR is an experimental OCR application. It turns a scanned book page into a PDF whose text is real, visible, searchable vector type traced from the scan itself, like Acrobat's ClearScan, but free and scriptable. See [`PLAN.md`](PLAN.md) for the design and the phased build plan.

**Status: Phase 0 (baseline and harness).** No OCR output yet. There are tools to take PDFs apart, make test pages with known ground truth, and compare results.

## Setup

```
sudo apt install tesseract-ocr libleptonica-dev   # system tools (Leptonica is used through ctypes)
pip install -e ".[dev]"
pytest
```

## Commands

All reports are written to `reports/`, which git ignores.

```
ectype analyze "corpus/E J. Young Inspiration-1.pdf"
```

This dumps a PDF's fonts (type, embedded program, glyph count, ToUnicode coverage, repeated variants, ligatures, U+FFFD glyphs, private keys), SVG glyph sheets, page structure (images and their dpi, text operators, render modes, per-word `Tc`, ActualText spans), a file-size breakdown and the extracted images. The output is an `index.html` plus `analysis.json`.

```
ectype synth -o reports/synth [--font "EB Garamond"] [--clean]
```

This makes a synthetic test page: known text, set in a known font, then degraded like a scan (skew, blur, noise, specks, JPEG). It writes `scan.pdf`, `scan.png`, `clean.png`, `truth.txt` and `truth.json`. The JSON gives every line, word and character with its baseline origin and ink box.

```
ectype compare A.pdf B.pdf [--truth reports/synth/truth.json] [--labels Acrobat,Ectype] [--pages 1-3]
```

This compares two documents page by page. You get side-by-side renders, a difference overlay, SSIM and ink disagreement, file size per page, and the character error rate (CER) of extracted text when ground truth is given. Page images (PNG/TIFF/JPEG) are accepted as well as PDFs.

`ectype --credits` prints [`CREDITS.md`](CREDITS.md).

## Layout

- `src/ectype/`: the library and CLI.
  - `analyze`, `synth` and `compare` are the Phase 0 tools.
  - `lept` holds ctypes bindings to Leptonica's symbol classifier.
- `spikes/` and `docs/spikes/`: exploratory scripts and their write-ups (Leptonica composites, Scribe OCR's font approach).
- `corpus/`: test scans. Never modified.
- `tests/`: the pytest suite.

## License

Apache 2.0. See [`LICENSE`](LICENSE), [`NOTICE`](NOTICE) and [`CREDITS.md`](CREDITS.md).
