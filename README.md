# ectype-ocr

Ectype OCR is an experimental OCR application. It turns a scanned book page into a PDF whose text is real, visible, searchable vector type traced from the scan itself, like Acrobat's ClearScan, but free and scriptable. See [`PLAN.md`](PLAN.md) for the design and the phased build plan.

**Status: Phases 0–3 are done**: baseline and harness; preprocessing, OCR and tying each character to its ink ([results](docs/phase1.md)); substitute mode ([results](docs/phase2.md)); glyph grouping and averaging ([results](docs/phase3.md)). There are tools to take PDFs apart, make test pages with known ground truth, and compare results. The Acrobat ClearScan baseline for the Young chapter is in [`docs/acrobat-baseline.md`](docs/acrobat-baseline.md).

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

This dumps a PDF's fonts, grouped by shared embedded program (type, embedded program, glyph count, ToUnicode coverage, repeated variants, ligatures, U+FFFD glyphs, private keys), SVG glyph sheets, page structure (images and their dpi, text operators, render modes, per-word `Tc`, ActualText spans), a file-size breakdown and the extracted images. The output is an `index.html` plus `analysis.json`.

```
ectype preprocess "corpus/E J. Young Inspiration-1.pdf" [--keep-spreads] [--overrides fixes.json]
```

This turns a scan into one upright, deskewed grayscale PNG per book page. Orientation comes from the text itself (Tesseract), not from the PDF's `/Rotate`. Two-page spreads are split at the fold, which is found from the text-free gutter and the fold shadow. Each page gets a JSON sidecar with the transforms applied. The review report shows each split and flags pages to check. To correct a page, put it in an overrides file, e.g. `{"13": {"split": 1700}, "7": {"rotate": 180, "skew": 0.2}, "9": {"split": false}}`.

```
ectype ocr reports/work-young           # Tesseract: words, lines, baselines, character boxes
ectype segment reports/work-young       # tie each character to its ink
```

`ocr` writes `<workspace>/ocr/<page>.ocr.json` plus overlays. `segment` writes `<workspace>/segment/<page>.seg.json`: glyph units, each a Unicode string with its ink components (a multi-character unit where letters touch or a ligature was printed). It also writes a label image and an overlay that colours each letter's ink, which is the view for checking by eye.

```
ectype substitute reports/work-young [--font "EB Garamond"]
```

This writes the substitute-mode PDF: each OCR word set in a real font at its scanned position (size and baseline per line, `Tz`/`Tc` per word so word ends land on the scanned ink), over the scan with the text erased. The report measures, word by word, how far the rendered text lands from the scan.

```
ectype cluster reports/work-young
```

This groups every glyph instance by size, slant and shape, joins groups into styles, and averages each group into a 4× composite. It writes glyph sheets per style and a list of probable OCR misreadings.

```
ectype synth -o reports/synth [--font "EB Garamond"] [--clean]
```

This makes a synthetic test page: known text, set in a known font, then degraded like a scan (skew, blur, noise, specks, JPEG). It writes `scan.pdf`, `scan.png`, `clean.png`, `truth.txt` and `truth.json`. The JSON gives every line, word and character with its baseline origin and ink box.

```
ectype compare A.pdf B.pdf [--truth reports/synth/truth.json] [--labels Acrobat,Ectype] [--pages 1-3] [--align]
```

This compares two documents page by page. You get side-by-side renders, a difference overlay, SSIM and ink disagreement, file size per page, and the character error rate (CER) of extracted text when ground truth is given. Page images (PNG/TIFF/JPEG) are accepted as well as PDFs. `--align` registers B onto A (best quarter turn, then an affine fit) for output that was turned, deskewed or rescaled, as Acrobat's is.

`ectype synth --set 10 -o reports/work-synth` makes a workspace of ten varied synthetic pages with truth. `ocr` and `segment` run on it like any other workspace, and `segment` then reports how many characters own the right ink.

`ectype --credits` prints [`CREDITS.md`](CREDITS.md).

## Layout

- `src/ectype/`: the library and CLI.
  - `analyze`, `synth` and `compare` are the Phase 0 tools.
  - `preprocess`, `ocr` and `segment` are Phase 1.
  - `background`, `pdfwrite` and `substitute` are Phase 2. `pdfwrite` is shared with traced mode.
  - `cluster` is Phase 3.
  - `lept` holds ctypes bindings to Leptonica's symbol classifier.
- `spikes/` and `docs/spikes/`: exploratory scripts and their write-ups (Leptonica composites, Scribe OCR's font approach).
- `corpus/`: test scans. Never modified.
- `tests/`: the pytest suite.

## License

Apache 2.0. See [`LICENSE`](LICENSE), [`NOTICE`](NOTICE) and [`CREDITS.md`](CREDITS.md).
