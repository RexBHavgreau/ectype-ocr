# ectype-ocr

Ectype OCR is an experimental OCR application. It turns a scanned book page into a PDF whose text is real, visible, searchable vector type traced from the scan itself, like Acrobat's ClearScan, but free and scriptable. The original design is kept in [`docs/history/original-plan.md`](docs/history/original-plan.md); current work is in [`TASKS.md`](TASKS.md).

**What it does:** **Run** can make a Traced PDF, with each glyph drawn from a typeface traced from the scan; a Substitute PDF, with OCR words set in a real typeface at their scanned positions; a Text layer only PDF, which keeps the scan and adds invisible, searchable OCR text; a Plain text only file, which saves the OCR text as a `.txt` file without making a PDF; or Pages only, which splits spreads, straightens pages and turns them upright (any of the three) with no OCR. **Review** shows the scan and output side by side, flags uncertain words and likely glyph misreadings, and lets you correct or confirm them; corrections become training data. **IDML export** creates an InDesign document either reflowed as a book or laid out to match the scan. **Kraken** can reread Greek, Hebrew and other scripts with a downloaded model after Tesseract reads the page. **Training** learns from Review corrections, text set in your fonts and scans matched to known texts, and installs a model only when it reads better. **Manuscript Mode** uses Kraken's line-finding and named hands to read handwritten books, compare readings, correct lines and train the hands.

Early results, by build phase: [Preprocessing, OCR and tying each character to its ink](docs/phase1.md) · [Substitute mode](docs/phase2.md) · [Glyph grouping and averaging](docs/phase3.md) · [Font synthesis and traced mode](docs/phase4-5.md)

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

Choose **Select PDF…** to pick the scanned book (a PDF, or page images), then press **Run**. The toolbar shows the selected PDF and its page count, and the Spreads tab previews it until it has been read. Only the results, such as the PDF, go beside the scan. A book is kept as a project (its page images, readings and corrections, in Ectype's data folder, `projects/`) only when you want it for Review and training: the first correction you save, training, or **File > Save project** keeps it; otherwise its work is deleted when you open another book or close the app. A new book starts with the choices you made last. A project made by an earlier version is still used. The toolbar chooses what Run makes, and how the pages are prepared (**Split two-page spreads**, **Straighten pages**, **Turn pages upright**, each on or off):
- **Traced:** fonts traced from the scan.
- **Substitute:** a real font in place of the scan's type.
- **Text layer only:** the scan with invisible, searchable OCR text over it. This is the fastest, with no glyph tracing. Its page image is **as scanned** by default: the scan's own pictures, unchanged (pages split, straightened and turned without coding them again), so the PDF is the scan's size plus its text. Grayscale or black and white code the pages again.
- **Pages only:** no OCR; the pages split, straightened and turned upright, as ticked, saved beside the scan as "<name> - pages.pdf", also keeping the scan's own pictures.
- **Plain text only (no PDF):** just the OCR text, as a `.txt` file beside the scan with the scan's name (`Treatise 01.pdf` gives `Treatise 01.txt`).

**Export > Export text…** (Ctrl+T) saves the text as corrected in Review: paragraphs (lines joined, hyphens removed) or lines as on the page; with page markers like `{p. 172 · PDF 90}` (the printed page and the page of the PDF, to find it in a viewer); vowel points and accents are taken off by default so a search finds the words (a box keeps them). **Export > Make text files for a folder of scans…** does this for every PDF or page image in a folder: scans that already have a `.txt` are skipped, and a text file Ectype didn't write is never overwritten.

**Languages** lists the installed OCR languages; tick as many as the book uses (English, Greek and Hebrew together, for instance). If the scan has words in a script none of them reads, the app says so after the run and offers to add the language. **Add languages…** at the bottom of that list downloads more from the Tesseract project's language data. They are stored in your user folder and shared by every project. Changing the languages reads the pages again on the next Run.

The app has four tabs:
- **Spreads:** each scan page, upright, with its split line. Drag the red line to where the pages meet, untick "Split this spread" to keep a page whole, or rotate a page. "Split two-page spreads" in the toolbar sets the default for the whole document. Changes apply on the next Run.
- **Review:** the scan and the output side by side, scrolling and zooming together (Ctrl+wheel). Words are boxed in red for low OCR confidence and orange where a glyph looks like another character. Click a word, type the fix, press Enter; saved unchanged, a flagged word is confirmed (green). **B** and ***I*** beside the box (Ctrl+B, Ctrl+I, and Ctrl+Shift+K for small capitals) set the word's style over what was detected; it follows into the PDF (the word's letters grouped with those of its style), IDML and Word. Punctuation at a word's start or end has a box of its own, corrected apart from the letters. Where the OCR left a letter out of a word's box ("DMG." for "ZDMG."), drag the selected word's dashed edges over it and type the whole word; **Add a word** draws a box around a word the OCR missed altogether. Then press **Update after corrections**, which reruns only segmentation through the PDF (a stretched box takes the missed letter's ink into the word, so it is traced, not left in the background).
- **Glyphs:** the averaged glyph sheets per style, and the probable misreadings. Double-click one to correct it.
- **Log:** stage-by-stage progress.

**Export PDF…** saves the result. **Export IDML…** lays the OCR text out as an InDesign document, in one of two ways ([details](docs/idml.md)):
- **Reflow:** the text laid out as a book by [Ectype IDML (called docx-to-idml before its 1.0.0)](https://github.com/RexBHavgreau/ectype-idml), with masters, running heads, footnotes and the fonts of its layout profile. Ectype writes the reading text as a Word manuscript (kept beside the IDML) and runs the installed converter. Running heads are dropped, paragraphs split by a page break are joined, words the OCR was unsure of are marked "Check OCR", and optionally the scan's page numbers are marked as {p. 172}.
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
ectype run scan.pdf [-o workspace] [--mode traced|substitute] [--font …] [--keep]
```

This runs every stage below in one workspace: preprocess, ocr, segment, then either cluster, fonts and traced, or substitute. `--mode text` runs just preprocess, ocr and the text layer (`--page-image original|gray|lossless|bw`; original, the scan's own pictures, is the default); `--mode plaintext` runs preprocess, ocr and writes the text file beside the scan; `--mode pages` only prepares the pages (`--keep-spreads`, `--no-deskew`, `--no-orient` leave out a step). A run works in a temporary folder and keeps nothing but the result (`--keep` keeps the book as a project for Review and training); a traced or substitute run of a book already kept uses its project. `--lang eng+grc` sets the OCR languages.
Repeat `--kraken LANG=MODEL` (for example, `--kraken heb=7051645`) to read a language with a Kraken model (repeatable; see `ectype kraken list`).

```
ectype languages [--all]              # installed OCR languages (--all: and those that can be added)
ectype kraken list                    # the installed models: number, scripts, title, size
ectype kraken available [--script SCRIPT] [--printed]   # models in Kraken's repository (needs the Kraken engine)
ectype kraken add MODEL               # download and install a model (needs the Kraken engine)
ectype kraken remove MODEL            # remove an installed model
ectype data-folder [--move FOLDER]    # show where Ectype keeps its data and how big it is; --move puts it elsewhere
ectype free-space [--cache] [--font-lines [LANG ...]]   # show what takes space in Ectype's data folder; --cache and --font-lines free some
ectype update [--install]             # check for a newer version (and install it, with --install)
ectype gui [target]                   # open the desktop app (a scan to open, or a project folder)
ectype languages --add grc lat heb    # download languages
ectype train reports/work-young       # train the languages' models from their pools, with this project's corrections
ectype train-synthetic --fonts C:\Fonts --script hebrew greek transliteration   # lines set from known text in your fonts
ectype train-scans letteris.pdf --text hebrew-bible --edition "Letteris 1866"   # a scanned edition, matched to its text
ectype train-all                      # all of it, from training.ini (what Train Hebrew and Greek.bat runs)
ectype models                         # the trained models and pools; --remove, --remove-pool, --remove-legacy
ectype textlayer reports/work-young   # the scan with an invisible OCR text layer
ectype text reports/work-young [-o book.txt] [--lines] [--no-page-markers] [--keep-heads] [--keep-marks]   # the OCR text as a .txt
ectype text-files scans/ [more folders or PDFs] [--lang eng+grc+heb] [--recursive] [--redo] [--keep] [--keep-marks]   # a .txt beside each scan
ectype idml reports/work-young --mode reflow|facsimile [-o out/book.idml] [--page-markers] [--profile NAME]
ectype pages scan.pdf [more scans or folders] [--split] [--deskew] [--orient]   # only fix the pages, no OCR: "scan - pages.pdf" (none given: all three)
ectype strip-text book.pdf [more.pdf] [-o out.pdf] [--all]   # a copy without its hidden (OCR) text layer, as "book - no text.pdf"; the PDF itself is never changed
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
ectype rebuild reports/work-young   # after corrections: segment, cluster, fonts and the PDF again
```

Correct the OCR in the desktop app's Review tab (the old browser review, `ectype review`, was removed in 0.8.0). It boxes every word: red for low OCR confidence, orange where a glyph looks like a different character than the OCR says, green once corrected. Each correction updates `ocr/`, is logged in `corrections/`, and saves the corrected line as a training pair in `training/` (`.png` + `.gt.txt`, as Kraken's `ketos train` reads them).

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

- `src/ectype/`: the library and command-line interface.
  - `analyze` dumps a PDF's fonts, glyph sheets and page structure; `compare` compares renderings of the same pages; `synth` makes synthetic test pages with known text; `report` supplies helpers for the HTML reports.
  - `preprocess` orients pages, splits spreads and deskews scans; `ocr` runs the OCR engines to produce words, lines, baselines and characters; `segment` ties each OCR character to its ink; `scripts` finds words in scripts the chosen OCR languages do not cover and reads them again; `raised` rereads raised characters such as note numbers and verse parts; `imagefiles` reads and writes images even when paths contain characters outside Windows' old code page.
  - `background` erases text from a page image while leaving the paper and figures; `substitute` sets scanned words in a real typeface at their scanned positions; `pdfwrite` writes positioned glyph runs over background images; `cluster` groups glyph instances by style and averages each group; `trace` turns averaged composites into Bezier contours; `fontbuild` builds a font for each style; `traced` draws each glyph from its own cluster.
  - `fontmatch` chooses installed fonts that look like the scan; `layout` turns each page into text blocks, paragraphs, lines and styled words; `textlayer` puts invisible OCR text over the scan; `plaintext` writes the OCR text as a plain text file; `textfiles` makes a text file beside each scan in a folder; `docx` writes the Word manuscript for Reflow; `idml` exports the OCR text as an InDesign document; `converter` runs the installed Ectype IDML converter; `idmlkit` is the IDML writer used by Facsimile.
  - `project` holds a book's scan, workspace, settings and corrections; `gui` is the desktop app; `gui_manuscript` supplies Manuscript Mode's Kraken view and dialogs; `review` keeps OCR corrections as training data; `manuscript` reads handwritten books with Kraken and user-named hands; `characters` supplies the special characters for typing corrections; `stopping` handles the Stop button; `crashlog` records what the app was doing before a crash.
  - `kraken` runs the second OCR engine on scripts such as Greek and Hebrew; `kraken_worker` runs that engine in its own process; `languages` lists OCR languages and downloads more; `models` manages the trained models and chooses among them; `families` identifies typeface families so books can be read with a suitable model.
  - `pool` keeps the shared training material for each language; `training` trains the general and typeface-family models; `train` fits OCR models from that pool; `trainall` runs the complete training process from a settings file; `synthtrain` makes training lines from known text in your fonts; `align` makes training lines from scans whose text is known; `texts` supplies the known texts used for training; `translit` makes transliteration text for training the Latin-script model.
  - `resources` locates Ectype's tools and data; `datafolder` moves the data folder to another drive; `freespace` frees space in that folder; `updates` checks GitHub for a newer release and installs it; `verify` checks downloads against a known fingerprint; `stamps` decides whether saved results are still current by their contents; `lept` holds ctypes bindings to Leptonica's symbol classifier; `cli` is the command-line interface.
- `spikes/` and `docs/spikes/`: exploratory scripts and their write-ups (Leptonica composites, Scribe OCR's font approach).
- `corpus/`: test scans. Never modified.
- `tests/`: the pytest suite.

## License

Apache 2.0. See [`LICENSE`](LICENSE), [`NOTICE`](NOTICE) and [`CREDITS.md`](CREDITS.md).
