# ectype-ocr

Ectype OCR is an experimental OCR application. It turns a scanned book page into a PDF whose text is real, visible, searchable vector type traced from the scan itself, like Acrobat's ClearScan, but free and scriptable. See [`PLAN.md`](PLAN.md) for the design and the phased build plan.

**Status: Phases 0–5 are done, and Phase 6 is under way:** the desktop app, parallel processing, caching, and corrections saved as training data. `ectype run scan.pdf` goes from a scan to a PDF whose visible text is set in fonts traced from the scan itself. Results by phase:
- preprocessing, OCR and tying each character to its ink ([results](docs/phase1.md))
- substitute mode ([results](docs/phase2.md))
- glyph grouping and averaging ([results](docs/phase3.md))
- font synthesis and traced mode ([results](docs/phase4-5.md))

On the Young chapter, the traced PDF is 619 KB against Acrobat ClearScan's 914 KB, and its text is searchable and copies out. There are tools to take PDFs apart, make test pages with known ground truth, and compare results. The Acrobat ClearScan baseline for the Young chapter is in [`docs/acrobat-baseline.md`](docs/acrobat-baseline.md).

## Install

On Windows, download `Ectype-OCR-<version>-Setup.exe` from the [releases page](https://github.com/RexBHavgreau/ectype-ocr/releases) and run it. Tesseract and a default font come with it. How releases are built: [`docs/releasing.md`](docs/releasing.md).

## Setup from source

```
sudo apt install tesseract-ocr libleptonica-dev   # system tools (Leptonica is used through ctypes)
pip install -e ".[dev,gui]"
pytest
```

## The desktop app

```
ectype gui [scan.pdf | project folder]       # or: ectype-gui
```

Open a scan and press **Run**. The project folder is created beside the scan (`<scan>.ectype/`). The toolbar chooses what Run makes:
- **Traced:** fonts traced from the scan.
- **Substitute:** a real font in place of the scan's type.
- **Text layer only:** the scan with invisible, searchable OCR text over it. This is the fastest, with no glyph tracing. Its page image can be grayscale, or black and white for the smallest file.

**Languages** lists the installed OCR languages; tick as many as the book uses. **Add languages…** at the bottom of that list downloads more from the Tesseract project's language data. They are stored in your user folder and shared by every project. Changing the languages reads the pages again on the next Run.

The app has four tabs:
- **Spreads:** each scan page, upright, with its split line. Drag the red line to where the pages meet, untick "Split this spread" to keep a page whole, or rotate a page. "Split two-page spreads" in the toolbar sets the default for the whole document. Changes apply on the next Run.
- **Review:** the scan and the output side by side, scrolling and zooming together (Ctrl+wheel). Words are boxed in red for low OCR confidence and orange where a glyph looks like another character. Click a word, type the fix, press Enter. Then press **Update after corrections**, which reruns only segmentation through the PDF.
- **Glyphs:** the averaged glyph sheets per style, and the probable misreadings. Double-click one to correct it.
- **Log:** stage-by-stage progress.

**Export PDF…** saves the result. **Export IDML…** lays the OCR text out as an InDesign document, in one of two ways ([details](docs/idml.md)):
- **Reflow:** the text flows onto a fresh layout in the fonts you choose. Running heads are dropped, paragraphs split by a page break are joined, and notes are gathered at the end.
- **Facsimile:** each text block sits where it stands on the scan, line for line, in the installed fonts most like the scan's. They are chosen automatically and shown before the export, and you can change them. The scan goes on a locked layer underneath that doesn't print.

IDML export needs only the OCR, plus the analysis that finds italics, running heads and the fonts (segmentation and glyph grouping, run if needed). It doesn't need glyph tracing, so text-layer mode is enough. Edits made in InDesign don't come back to Ectype, so they don't add training data; corrections made in the Review tab still do.

Everything the app does is also available on the command line.

## Commands

All reports are written to `reports/`, which git ignores.

```
ectype run scan.pdf [-o workspace] [--mode traced|substitute] [--font …]
```

This runs every stage below in one workspace: preprocess, ocr, segment, then either cluster, fonts and traced, or substitute. `--mode text` runs just preprocess, ocr and the text layer (`--page-image gray|bw`). `--lang eng+grc` sets the OCR languages.

```
ectype languages [--all]              # installed OCR languages (--all: and those that can be added)
ectype languages --add grc lat heb    # download languages
ectype textlayer reports/work-young   # the scan with an invisible OCR text layer
ectype idml reports/work-young --mode reflow|facsimile [-o book.idml] [--font "EB Garamond"] [--size 11]
```

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
ectype fonts reports/work-young     # trace composites, one CID-keyed CFF font per style (validated with OTS)
ectype traced reports/work-young    # the traced-mode PDF: every glyph drawn from its own cluster
```

```
ectype review reports/work-young    # correct OCR in the browser at http://127.0.0.1:8765/
ectype rebuild reports/work-young   # after corrections: segment, cluster, fonts and the PDF again
```

The review page boxes every word: red for low OCR confidence, orange where a glyph looks like a different character than the OCR says, green once corrected. Each correction updates `ocr/`, is logged in `corrections/`, and saves the corrected line as a training pair in `training/` (`.png` + `.gt.txt`, as Kraken's `ketos train` reads them).

OCR and segmentation run in parallel across pages (one worker per CPU) and reuse a page's result when it's newer than its inputs.

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
  - `trace` and `fontbuild` are Phase 4.
  - `traced` is Phase 5.
  - `project` (a book project: settings, overrides, running stages, corrections, export), `gui` (the desktop app) and `review` (the browser review) are Phase 6.
  - `textlayer` (the scan with an invisible OCR text layer), `languages` (OCR languages and their download), `layout` (text blocks and paragraphs), `fontmatch` (installed fonts most like the scan's) and `idml` (the InDesign export).
  - `lept` holds ctypes bindings to Leptonica's symbol classifier.
- `spikes/` and `docs/spikes/`: exploratory scripts and their write-ups (Leptonica composites, Scribe OCR's font approach).
- `corpus/`: test scans. Never modified.
- `tests/`: the pytest suite.

## License

Apache 2.0. See [`LICENSE`](LICENSE), [`NOTICE`](NOTICE) and [`CREDITS.md`](CREDITS.md).
