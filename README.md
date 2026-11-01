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

From 0.3.0 the app keeps itself up to date. At startup (switched off under **Help**) and from **Help → Check for updates…**, it asks GitHub for the latest release. If there's a newer one, it offers to install it: it downloads the installer, checks it against the release's SHA-256 digest, runs it over the existing install and opens the new version. You can also choose "Skip this version" or "Not now". `ectype update [--install]` does the same from the command line. Versions before 0.3.0 have no update check: install 0.3.0 by hand once.

## Setup from source

```
sudo apt install tesseract-ocr libleptonica-dev   # system tools (Leptonica is used through ctypes)
pip install -e ".[dev,gui]"
pytest
```

Kraken, the second engine, is optional: `pip install torch --index-url https://download.pytorch.org/whl/cpu` and `pip install -e ".[kraken]"`, or set `ECTYPE_KRAKEN` to a command running `python -m ectype.kraken_worker` in another environment.

## The desktop app

```
ectype gui [scan.pdf | project folder]       # or: ectype-gui
```

Choose **Select PDF…** to pick the scanned book (a PDF, or page images), then press **Run**. The toolbar shows the selected PDF and its page count, and the Spreads tab previews it until it has been read. The project folder is created beside the scan (`<scan>.ectype/`). The toolbar chooses what Run makes:
- **Traced:** fonts traced from the scan.
- **Substitute:** a real font in place of the scan's type.
- **Text layer only:** the scan with invisible, searchable OCR text over it. This is the fastest, with no glyph tracing. Its page image can be grayscale, or black and white for the smallest file.

**Languages** lists the installed OCR languages; tick as many as the book uses (English, Greek and Hebrew together, for instance). If the scan has words in a script none of them reads, the app says so after the run and offers to add the language. **Add languages…** at the bottom of that list downloads more from the Tesseract project's language data. They are stored in your user folder and shared by every project. Changing the languages reads the pages again on the next Run.

The app has four tabs:
- **Spreads:** each scan page, upright, with its split line. Drag the red line to where the pages meet, untick "Split this spread" to keep a page whole, or rotate a page. "Split two-page spreads" in the toolbar sets the default for the whole document. Changes apply on the next Run.
- **Review:** the scan and the output side by side, scrolling and zooming together (Ctrl+wheel). Words are boxed in red for low OCR confidence and orange where a glyph looks like another character. Click a word, type the fix, press Enter; saved unchanged, a flagged word is confirmed (green). **B** and ***I*** beside the box (Ctrl+B, Ctrl+I, and Ctrl+Shift+K for small capitals) set the word's style over what was detected; it follows into the PDF (the word's letters grouped with those of its style), IDML and Word. Punctuation at a word's start or end has a box of its own, corrected apart from the letters. Where the OCR left a letter out of a word's box ("DMG." for "ZDMG."), drag the selected word's dashed edges over it and type the whole word; **Add a word** draws a box around a word the OCR missed altogether. Then press **Update after corrections**, which reruns only segmentation through the PDF (a stretched box takes the missed letter's ink into the word, so it is traced, not left in the background).
- **Glyphs:** the averaged glyph sheets per style, and the probable misreadings. Double-click one to correct it.
- **Log:** stage-by-stage progress.

**Export PDF…** saves the result. **Export IDML…** lays the OCR text out as an InDesign document, in one of two ways ([details](docs/idml.md)):
- **Reflow:** the text laid out as a book by [docx-to-idml](https://github.com/RexBHavgreau/docx-to-idml), with masters, running heads, footnotes and the fonts of its layout profile. Ectype writes the reading text as a Word manuscript (kept beside the IDML) and runs the installed converter. Running heads are dropped, paragraphs split by a page break are joined, words the OCR was unsure of are marked "Check OCR", and optionally the scan's page numbers are marked as {p. 172}.
- **Facsimile:** each text block sits where it stands on the scan, line for line, in the installed fonts most like the scan's. They are chosen automatically and shown before the export, and you can change them. The scan goes on a locked layer underneath that doesn't print.

Each export is a folder of its own, holding the IDML and its `Links`. IDML export needs only the OCR, plus the analysis that finds italics, running heads and the fonts (segmentation and glyph grouping, run if needed). It doesn't need glyph tracing, so text-layer mode is enough. Edits made in InDesign don't come back to Ectype, so they don't add training data; corrections made in the Review tab still do.

**Kraken** reads Greek, Hebrew and other scripts better than Tesseract does. Under **Languages → Add languages… → Kraken models**, download the Kraken engine once (an optional download that includes PyTorch), then pick a model from Kraken's repository on Zenodo, filtered by script and to printed text; each model's description says what it was trained on. A Kraken model is then an OCR language like the others: tick it with English and Tesseract reads the page, after which Kraken reads again each run of words in its script. Details: [`docs/idml.md`](docs/idml.md#kraken-greek-hebrew-and-other-scripts).

**Train OCR model…** improves the OCR from book to book. Every correction you make in Review, in any project, goes into its language's training pool, with lines set from known text in your own fonts and lines of scanned editions matched to their text (no correcting by hand: `Train Hebrew and Greek.bat`). Each language has one model trained on its whole pool, checked on material kept aside and installed only if it does better; typeface families with enough material get models of their own beside it, and Ectype picks the one that reads each book best. **Training → Train from fonts…** does the font part from the app: choose the languages, then the fonts (those installed on the computer, and any folders you add), each shown with the chosen languages it supports. Its **Transliteration** choice teaches the English model the letters of transliterated and accented words (ḥ ṭ ṣ š ʾ ʿ ā ê ə, Arabic and Akkadian ḫ ẓ ḍ ġ, IPA, ü ç ø č ō), which the stock model reads as plain letters. The Languages menu shows only languages. Details: [`docs/idml.md`](docs/idml.md#training-the-ocr-models).

**Manuscript Mode** is for handwritten books, especially those with no digital text yet. **Languages → Switch to Manuscript Mode** (and back with **Switch to Print Mode**) gives a list of its own: Hebrew, Greek, Syriac, Arabic, Latin. Under each language are its **hands**: Kraken models you name ("Sephardi square", "Masorah hand"), made with **New hand…** from an installed handwriting model (Add languages… → Kraken models, with "Printed only" unticked) or from another hand. Tick the hands a manuscript is written in. With two or more, choose whether every line is read by every hand, or the other hands read only the lines the first is unsure of; where the hands read a line differently, it is flagged. No known text is used at all, so the reading isn't pulled towards the familiar text. Run finds each page's lines with Kraken's own line-finding and makes a searchable PDF (the scan with its text).

Review then shows the **Kraken view**: each line found is a band, red when read unsurely, orange when the hands disagree, green once corrected. Click a band, correct the whole line, press Enter to go on to the next; the Ω menu has editorial marks ([ ] ⟨ ⟩ ⟦ ⟧, a dot under an uncertain letter). Drag the dashed box to stretch a line over strokes it missed, join a line Kraken split, cut one it ran together, delete a line, or draw one it missed; then **Read this page again**. The list on the left can show pages, the **least sure lines first**, or the **sample pages** of **Training → Train for this book…** (a few pages chosen by you, or at random away from the front and back matter, to correct first). **Train OCR model** then trains each hand on its corrected lines (with warped, blurred and faded copies of them; the hand is replaced only if it reads lines kept aside better) and the book's line-finding on the lines you changed. **Settings → Read manuscripts with all processor cores** reads with several Kraken readers side by side; off, one reader leaves the computer free.

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
ectype train reports/work-young       # train the languages' models from their pools, with this project's corrections
ectype train-synthetic --fonts C:\Fonts --script hebrew greek transliteration   # lines set from known text in your fonts
ectype train-scans letteris.pdf --text hebrew-bible --edition "Letteris 1866"   # a scanned edition, matched to its text
ectype train-all                      # all of it, from training.ini (what Train Hebrew and Greek.bat runs)
ectype models                         # the trained models and pools; --remove, --remove-pool, --remove-legacy
ectype textlayer reports/work-young   # the scan with an invisible OCR text layer
ectype idml reports/work-young --mode reflow|facsimile [-o out/book.idml] [--page-markers] [--profile NAME]
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
  - `textlayer` (the scan with an invisible OCR text layer), `languages` (OCR languages and their download), `layout` (text blocks and paragraphs), `fontmatch` (installed fonts most like the scan's), `idml` (the InDesign export), `docx` and `converter` (Reflow's Word manuscript and running docx-to-idml), and `idmlkit` (docx-to-idml's IDML writer, copied, used by Facsimile).
  - `lept` holds ctypes bindings to Leptonica's symbol classifier.
- `spikes/` and `docs/spikes/`: exploratory scripts and their write-ups (Leptonica composites, Scribe OCR's font approach).
- `corpus/`: test scans. Never modified.
- `tests/`: the pytest suite.

## License

Apache 2.0. See [`LICENSE`](LICENSE), [`NOTICE`](NOTICE) and [`CREDITS.md`](CREDITS.md).
