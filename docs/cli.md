# Ectype OCR on the command line: a guide

Every command starts with `ectype`. Then comes the command, then what it works on (a scan, a
folder, a project folder), then its options. Put any path with spaces in quotes.

To see one command's options at any time: `ectype <command> --help` (e.g. `ectype run --help`).
`ectype --help` lists all commands.

Language codes join with `+`: `eng` English, `spa` Spanish, `lat` Latin, `grc` ancient Greek,
`heb` Hebrew, `syr` Syriac, `deu` German, `fra` French, `ita` Italian. With no `--lang`, a scan is
read as English. `ectype languages` shows what is installed.

---

## The one you were looking for: a text layer only (no project, no tracing)

Adds an invisible, searchable OCR text layer to the scan and writes the result beside it as
"`<scan> - OCR.pdf`". No glyph tracing, no fonts, and **no project folder is kept** (a temporary
one is used and deleted).

```
ectype run "D:\Library\Book.pdf" --mode text --lang eng
ectype run "D:\Library\Book.pdf" --mode text --lang eng+grc+heb
```

Variations:

```
ectype run "Book.pdf" --mode text --lang spa --page-image bw       smaller file: black and white pages
ectype run "Book.pdf" --mode text --lang spa --page-image lossless  no JPEG loss (larger)
ectype run "Book.pdf" --mode text --lang spa --pages 1-20           only source pages 1-20
ectype run "Book.pdf" --mode text --lang spa --keep                 keep the project, to correct it in Review
ectype run "Book.pdf" --mode text --lang spa --name "Book searchable"   output file name
ectype textlayer "Book.pdf" --lang spa                              same as the first line (a shortcut)
```

Notes:
- The pages are straightened and two-page spreads are split first (as in the app). Add
  `--keep-spreads` to leave spreads whole.
- The original scan is never changed, and a file of that name that Ectype didn't make is never
  overwritten (the next free name is used).
- Several books at once: use `text-files` (below) for .txt files; for PDFs, run the command once per
  book (a loop in your shell).
- If the book has a script your `--lang` doesn't cover, the run says so at the end and tells you
  what to add.

---

## Commands for a whole book (a scan in, a result out)

| Command | What it does |
|---|---|
| `ectype run SCAN` | Every stage, scan to PDF. Default mode is **traced** (every word drawn as type; slow). |
| `ectype run SCAN --mode text` | Scan + invisible text layer. No tracing. No project kept. |
| `ectype run SCAN --mode plaintext` | No PDF: the OCR text as a .txt beside the scan. |
| `ectype run SCAN --mode substitute` | PDF with the text set in an installed font (`--font NAME`). |
| `ectype text SCAN_OR_PROJECT` | A .txt of the book (paragraphs, page markers). |
| `ectype text-files FOLDER...` | A .txt beside every scan in the folders. |
| `ectype strip-text PDF...` | A copy of a PDF without its bad hidden text layer. |

### run

```
ectype run SCAN [options]
  --mode traced|substitute|text|plaintext   (default traced)
  --lang CODES        e.g. eng or eng+grc+heb (default eng)
  --pages 1-3,5       only these source pages
  --page-image gray|lossless|bw   page background (bw: text mode only; default gray)
  --name NAME         output file name, without .pdf
  --font NAME         substitute mode: font file or family name
  --keep              text/plaintext modes: keep the project for Review
  --keep-spreads      don't split two-page spreads
  --overrides FILE    JSON of per-page fixes: rotate, split, skew
  --kraken LANG=MODEL read a language with an installed Kraken model (repeatable)
  --anyway            go on even if the book has a script --lang doesn't read
  -o FOLDER           put the project folder here
```

Traced and substitute runs keep the book's project (in Ectype's data folder, `projects/`), so you
can correct it in the app's Review and run `rebuild`.

### text

```
ectype text "Book.pdf" --lang eng+grc+heb
ectype text "Book.pdf" --lang eng --lines          every line as printed
ectype text "Book.pdf" --keep-marks                keep Hebrew points / Greek accents
ectype text "Book.pdf" --no-page-markers
ectype text "Book.pdf" --keep-heads                keep running heads and page numbers
ectype text "Book.pdf" -o "C:\out\Book.txt"
ectype text "C:\...\Book.ectype"                   from a project folder (corrections included)
```

### text-files

```
ectype text-files "D:\Library" --recursive --lang spa
ectype text-files "D:\Library" --redo --lang spa     make again (only files Ectype made are replaced)
ectype text-files "D:\Library" --keep-marks
ectype text-files "A.pdf" "B.pdf" --lang eng         individual scans
  --keep      keep each book's work folder
```

### strip-text

```
ectype strip-text "Old scan.pdf"                 writes "Old scan - no text.pdf" beside it
ectype strip-text "A.pdf" "B.pdf"                several
ectype strip-text "A.pdf" -o "C:\out\A clean.pdf"
ectype strip-text "A.pdf" --all                  remove visible text too (careful: ClearScan / traced PDFs)
```

---

## Step commands (work on a book's *project folder*, not a scan)

For a scan, use the commands above. These run one stage of the pipeline on a project folder (the
`.ectype` folder). Given a scan they refuse and say what to use instead (except `textlayer`,
`traced`, `substitute`, which then act as `run --mode …`).

| Command | Stage |
|---|---|
| `ectype preprocess INPUT -o WORKSPACE` | Orient, split spreads, deskew (`--keep-spreads`, `--no-deskew`, `--pages`, `--dpi`, `--overrides`). |
| `ectype ocr WORKSPACE` | OCR (`--lang`, `--engine tesseract\|kraken`, `--kraken LANG=MODEL`). |
| `ectype segment WORKSPACE` | Tie OCR characters to their ink. |
| `ectype cluster WORKSPACE` | Group glyphs by style (`--threshold`). |
| `ectype fonts WORKSPACE` | Build a font per style (`--family`, `--threshold`). |
| `ectype traced WORKSPACE` | Traced PDF (`--name`, `--page-image`, `--background-dpi`). |
| `ectype substitute WORKSPACE` | Substitute-font PDF (`--font`, `--name`, `--page-image`). |
| `ectype textlayer WORKSPACE` | Text-layer PDF (`--name`, `--page-image gray\|lossless\|bw`). |
| `ectype rebuild WORKSPACE` | After corrections in Review: segment through the PDF again (`--mode`, `--font`, `--name`, `--page-image`). |
| `ectype idml WORKSPACE` | InDesign export (`--mode reflow\|facsimile`, `-o`, `--profile`, `--page-markers`, `--no-check-ocr`, `--no-scan`). |

---

## Languages and Kraken models

```
ectype languages                       what is installed
ectype languages --all                 also what can be added
ectype languages --add spa lat grc     download languages

ectype kraken list                     installed Kraken models
ectype kraken available                models you can add (needs the Kraken engine)
ectype kraken available --script Hebrew --printed
ectype kraken add 7051645              install one
ectype kraken remove 7051645

ectype text "Grammar.pdf" --lang eng+heb --kraken heb=7051645     read Hebrew with that model
```

## Training

```
ectype train                       (no project: use --lang)
ectype train PROJECT               add the project's corrections to the pools, then train
ectype train --lang heb grc        train these languages
  --full                           train afresh (default: quick update when little is new)
  --iterations N                   Tesseract iterations
  --epochs N                       Kraken epochs (default 15)
ectype train-synthetic --fonts "C:\Fonts" --script hebrew greek latin syriac arabic transliteration --lines 400
ectype train-scans SCAN --text hebrew-bible --edition "Letteris 1866" --pages 1-40
ectype train-all [--config FILE] [--write-config PATH]    everything a settings file asks for
ectype models                      list trained models and pools
ectype models --remove heb         back to the stock model
ectype models --remove-pool heb    empty a pool
ectype models --remove-legacy
```

## Housekeeping

```
ectype data-folder                         where Ectype keeps its data, and how big it is
ectype data-folder --move "D:\Ectype OCR"  move it all there
ectype free-space                          what takes space
ectype free-space --cache                  remove caches, leftovers, projects' working files
ectype free-space --font-lines heb grc     remove lines set from fonts (Train from fonts remakes them)
ectype update                              check for a newer version
ectype update --install                    download and run the installer (installed app)
ectype gui [SCAN_OR_PROJECT]               open the desktop app
ectype --version        ectype --credits
```

Your corrections, models and settings are never removed by `free-space`.

## Developer and testing commands

```
ectype analyze PDF [-o DIR] [--no-images] [--no-glyphs]     fonts, glyph sheets, structure of a PDF
ectype compare A.pdf B.pdf [--dpi N] [--pages 1-3] [--truth FILE] [--labels A,B] [--align] [-o DIR]
ectype synth [-o DIR] [--font F] [--text FILE] [--dpi N] [--size PT] [--clean] [--skew D] [--blur S]
             [--noise S] [--speckles N] [--jpeg Q] [--ink-spread PX] [--seed N] [--set N]
```

## If a command fails

Ectype prints "Ectype stopped: …" and saves the details in
`<data folder>\logs\last-command-error.txt`; send that file if you need help.
