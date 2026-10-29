# Text layer, languages and IDML export

Three additions for using Ectype when the end product is an InDesign document rather than a PDF:
- **Text-layer mode** gives a searchable PDF quickly, with no glyph tracing.
- **OCR languages** can be chosen and downloaded.
- **IDML export** lays the OCR text out, reflowed or in facsimile.

## Text-layer mode

`ectype run scan.pdf --mode text`, or **Text layer only** in the app. This runs preprocessing and OCR, then writes a PDF of the upright, deskewed pages with every OCR word in invisible text (render mode 3) over its scanned position.

- **Font.** The text uses a "glyphless" font made on the fly: one empty glyph per character, mapped to Unicode by the ToUnicode table. Any script the OCR reads is covered without needing a font that has it.
- **Placement.** Each word is scaled horizontally to its scanned width and sized so a selection covers its line.
- **Page image.** Grayscale JPEG at the page's resolution (quality 60), or black and white (CCITT Group 4).

On the Young chapter (26 pages), text extraction matches the OCR to within line endings (character error rate 0.3%):

| Page image | Size |
| --- | --- |
| Grayscale | 10.4 MB |
| Black and white | 1.3 MB |

## Languages

The **Languages** menu lists the installed Tesseract languages, each with a checkbox; several can be ticked. **Add languages…** at the bottom opens a searchable list of Tesseract's 122 languages and downloads the chosen ones from the `tessdata` repository (`raw.githubusercontent.com/tesseract-ocr/tessdata`).

- **Where they go.** Tesseract reads from one folder, so the first download creates the user's own folder and copies the shipped languages and Tesseract's output configs beside the new ones. The folder is `%LOCALAPPDATA%\Ectype OCR\tessdata` on Windows, `~/.local/share/ectype/tessdata` on Linux, or `ECTYPE_DATA_DIR` if set.
- **Command line:** `ectype languages --add grc lat`.
- **Changing languages** reads the pages again on the next run. Corrections made in the Review tab are carried over to the new reading: each goes to the new word at the same place on the page (`review.carry_corrections`).

### Other scripts in the text

A book in English with Greek and Hebrew words needs all three languages ticked; Tesseract then reads each page with them together. On a test page this reads unpointed Hebrew and most polytonic Greek correctly. Two things help further (`scripts.py`):

- **Each word Tesseract was unsure of** (confidence under 60, at most 40 a page) goes to Tesseract's script detector, the orientation and script data the app ships, with the word's image repeated so the detector has enough to go on.
  - A word in a script a chosen language covers, but read as something else, is read again by itself in that language alone. Pointed Hebrew read as "O°7>8" becomes Hebrew. It is then marked unsure, for Review.
  - Words in a script no chosen language covers are counted. When at least two are on one page, the app offers to add the language: Greek → Ancient Greek, Hebrew → Hebrew, Cyrillic → Russian, and so on. It downloads the language if needed and reads the pages again; "Not for this book" stops the offer for that project. The command line prints the suggestion. Isolated guesses on smudged English words are ignored: the English Young chapter gets no suggestion.
- **Right-to-left words** (Hebrew, Arabic, Syriac) come from Tesseract in reading order, with character boxes running right to left.
  - Segmentation pairs their characters with the ink in visual order.
  - In the PDFs, the glyphs are set in visual order with the word's text in reading order attached (ActualText), so the word copies and searches correctly.
  - In substitute mode, a word the chosen font can't set (Hebrew in EB Garamond) is left as the scan, with invisible searchable text over it.
  - Some viewers still reverse the order of the words in a multi-word Hebrew phrase when copying, or mirror a bracket next to it. The OCR text, the Word manuscript and the IDML have it right.
- Tesseract's direction marks (U+200E around right-to-left words) are removed from the text.

## Kraken: Greek, Hebrew and other scripts

[Kraken](https://kraken.re) is the second OCR engine, as planned (PLAN.md): Tesseract for the main text, Kraken for Greek, Hebrew, Syriac and the other scripts its models cover. Kraken is reputed to read these better, and its models are trained and shared by scholars and libraries in its repository on Zenodo.

- **The engine.** Kraken needs PyTorch, which is large, so it runs in a process of its own (`kraken_worker.py`; `kraken.py` talks to it). In the Windows app it is an optional download, the Kraken engine (`Ectype-OCR-Kraken-<version>-win64.zip`, a few hundred megabytes), from the app's own release, checked against the release's SHA-256 digest and unpacked into the user's folder. From source it is the `kraken` extra, or any environment named by `ECTYPE_KRAKEN`.
- **Models.** **Languages → Add languages… → Kraken models** lists the repository's recognition models (through Kraken's own repository client; the list is kept for a week). They can be filtered by script and to printed text, and searched; each model's description, authors, languages, reported error rate and licence are shown. The user chooses: no model is built in or preferred, since the right one depends on the book (period, typeface, how dark and clean the scan is). A downloaded model is an OCR language with a code like `kraken_7051645`, named e.g. "Hebrew (Kraken): <title>".
- **Reading.** With a Kraken model ticked, Tesseract reads the page with the Tesseract languages ticked. Then words in the model's script are read again by Kraken, a run of neighbouring words at a time, as one line (Kraken reads lines). A word is in the script when most of its letters are, or when the script detector puts it there. With a Kraken model chosen, the detector also checks words Tesseract read confidently but that aren't plain words, since Tesseract without Hebrew reads it as confident figures ("13723" for וּבָרְכוּ). A figure or mark between two Hebrew words goes with them. Ticking Tesseract's Hebrew too helps find the Hebrew words: the combined reading gets most of them into Hebrew letters, and Kraken then reads them again.
- **Training.** Words corrected into a Kraken model's script (20 or more) fine-tune that model with Kraken's trainer, `ketos train`, continuing from it (`--resize union` adds characters it lacks). A fifth is kept aside to compare the old and new model. The result is a Kraken model of its own, "Hebrew (Kraken), trained on <book>", replacing the one it came from for the book. On a small test model trained here from synthetic lines, two epochs on 32 corrected words cut the error rate on the 8 kept aside from 51% to 10%.
- **Comparing models.** `tools/kraken_eval.py` (run in CI, which can reach Zenodo) reads Genesis 1:1–4, pointed and unpointed, and John 1:1–5 in several fonts with each printed Greek and Hebrew model and with Tesseract's `heb` and `grc`, and prints the character error rates. Set type isn't a book's type, so this checks the models work and how they compare; it doesn't choose one.

  First results (October 29; character error rates, %; lines lightly blurred and speckled; 52 recognition models in the repository):

  | | Polytonic Greek (5 fonts) | Hebrew, unpointed (4 fonts) | Hebrew, pointed (4 fonts) |
  | --- | --- | --- | --- |
  | Tesseract `grc` / `heb` | 0.4–3.9 | 2.8–3.3 | 22.6–28.8 |
  | CLLG Polytonic Greek (printed) | 0.4–1.1 | | |
  | PP-OCRv6 multilingual, medium (printed and handwritten) | 0.4 in every font | 2.8–3.3 | 39.7–41.4 |
  | PP-OCRv6 multilingual, small / tiny | 0.0–0.7 / 1.1–3.2 | 2.8–4.4 / 2.8–6.6 | 39.7–45.9 / 40.4–44.9 |
  | BiblIA medieval Hebrew manuscripts (4 models) | | 12.2–47.5 | 45.2–65.4 |

  - **Greek:** the Kraken models read polytonic Greek at least as well as Tesseract, and more evenly across typefaces (Tesseract: 3.9% in GFS Didot).
  - **Unpointed Hebrew:** the printed multilingual models are level with Tesseract. The manuscript models don't suit print.
  - **Pointed Hebrew:** no model in the repository reads vowel points in print; they drop them (about 40% of the characters). Tesseract does better but lacks four of the points. For a pointed text, a model has to be trained on the book itself: correct its Hebrew words in Review and train, either engine.
  - Two "Party" models (whole-page recognition) can't be used to read lines; the app refuses them with a message.

## Training the OCR model

**Train OCR model…** in the app (it shows how many lines and other-script words you have corrected), or `ectype train <workspace>`. Each word corrected in Review saves its line, image and corrected text, in `training/`. Training fine-tunes a copy of the book's main language on those lines (`train.py`):

1. **Base model.** The trainable float model of the language (from `tessdata_best`, about 15 MB) is downloaded once. The shipped models are integer models and can't be trained further.
2. **Training lines.** The corrected lines, plus as many lines the OCR read with high confidence (every word at 90 or more), so the model keeps what it already does well. Lines with letters of another script, such as Hebrew in an English line, are left out and counted. Each line becomes Tesseract's training file (`.lstmf`) from its image and a box file with its text.
   - **New characters.** Characters of the model's own script that it lacks are added to it, as tesstrain does: a new character set (the model's plus the new ones) and a starter model with the old model's word lists, which training continues from (`--old_traineddata`).
3. **Training.** A fifth of the corrected lines (at least two) is kept aside. `lstmtraining` continues the base model on the rest: 30 iterations a line, between 300 and 3,000.
4. **Check.** The lines kept aside are read with the old and the new model, and the report gives both character error rates.
5. **Install.** The new model is saved as a language of its own, e.g. `eng_young` ("English, trained on Young"), beside the downloaded ones. It replaces the main language for this book, and can be chosen for others in the Languages menu. The app offers to read the pages again with it, keeping the corrections.

At least 10 corrected lines are needed. Training again later starts from the stock model with all the corrections so far.

### Hebrew, Greek and other scripts in the book

Their words are trained on separately, word by word, since their lines mix scripts. A word corrected into Hebrew (or Greek…) is also saved by itself in `training/words/`. With 20 or more for a language that is ticked, training fine-tunes that language too, on those words and on words of its script read with confidence 90 or more, e.g. `heb_treatise` ("Hebrew, trained on Treatise"). It replaces the stock language for the book.

This is the route for the pointed Hebrew in the Treatise (`corpus/Treatise 01.pdf`). The stock Hebrew model has no holam, tsere, hataf segol or shin dot at all, so it can't give אֱלֹהִים, and it reads this book's type poorly: short pointed words come out as Latin look-alikes in the combined reading ("3223)") and as wrong letters when read again in Hebrew alone. Script detection can only send a word to the right model; only training on this book's own Hebrew will fix the reading.

Right-to-left text is trained in visual order, its characters reversed: Tesseract's Hebrew model reads that way and turns its readings round. Trained in reading order, it learns the words backwards. Like tesstrain, the character set is built with normalisation mode 3 and a pass-through recoder, as the stock model has.

On a test of 150 synthetic pointed Hebrew words and phrases (FreeSerif), training on 120 cut the character error rate on the 30 kept aside from 32% to 16%. The new model reads tsere and the other added points (וּרְאֵה, וּמַלְּטִי); dropped points cause most of the remaining errors.

On a test of 80 synthetic lines in an italic face, training on 64 took 32 seconds and cut the character error rate on the 16 kept aside from 1.1% to 0.16%.

Training needs Tesseract's training tools (`lstmtraining`, `combine_tessdata`; to add characters, also `unicharset_extractor`, `merge_unicharsets`, `combine_lang_model` and `dawg2wordlist`). They come with Tesseract in Linux packages; the Windows build log says whether they are in the app.

## IDML export

**Export IDML…** in the app, or `ectype idml <workspace> --mode reflow|facsimile`.

Each export is a folder of its own, as docx-to-idml writes them: `<name>/<name>.idml`, with the images in `<name>/Links/`, linked relative to the IDML (`file:Links/p001.jpg`). The folder can be moved or sent on without relinking.

The export first makes sure segmentation and glyph grouping are current, and runs them if not. That analysis is what finds italics, running heads, superscripts and the scan's typefaces. No glyph tracing is involved. The export then builds a page layout (`layout.py`):
- **Blocks:** lines of one zone and size that overlap, with a normal baseline pitch.
- **Paragraphs:** a new one starts at an indented line or after a short line.
- **Alignment:** justified, centred, flush left or right, read from the line ends.
- **Folios:** each page's printed number, read from its running head or foot, and inferred for pages without one (the Young chapter: 168–193).

### Reflow: through docx-to-idml

Reflow is laid out by [docx-to-idml](https://github.com/RexBHavgreau/docx-to-idml), as decided for it on October 24 (its `docs/ENGINE.md`). Ectype writes the reading text as a Word manuscript (`docx.py`) and runs the installed converter's `convert` command (`converter.py`). It reads the converter's location from `program.json` in docx-to-idml's settings folder, or from `ECTYPE_DOCX2IDML` (a command line, e.g. `python -m docx2idml` to run it from source). The converter gives the book its masters, running heads, front matter and footnotes, in the fonts of its layout profile (chosen in the export dialog). Its report is shown in the Log. If docx-to-idml isn't installed, the dialog says so and links to its releases.

The manuscript (kept beside the IDML):
- **Headings:** Heading 1 (the largest on its page, a chapter title), Heading 2 and 3, and an all-capitals line under a chapter title as Author.
- **Body paragraphs** as Normal; set-off passages (quotations, verse) as Quote; centred and right-aligned lines kept so.
- **Italics** as italic runs; raised figures as superscripts.
- **Notes:** Word footnotes where their reference was found, i.e. a superscript figure on the same page matching the note's printed number. The others are gathered at the end under "Notes" and counted in the report, because the OCR often misses small reference figures. On the Young chapter it missed the "2" after "Inspiration." and the whole "2 Ibid., 230–31." line.
- **"Check OCR":** a character style on words read with confidence under 70 and not corrected in Review.
- **"Page Marker":** optionally, `{p. 172}` where each page of the scan begins, inside a paragraph when the page breaks in one.
- **Left out:** running heads and folios. Paragraphs split by a page break are joined, and line-end hyphens removed where they only split a word (the hyphenated form is kept when it occurs elsewhere in the book).

On the Young chapter, docx-to-idml lays the manuscript out as 33 pages: about 30 of text plus front matter, with placeholders such as [Book title] and [ISBN] that its report names.

### Facsimile

Every text block of every page goes in its own frame where it stands on the scan:
- **Lines** are kept with forced line breaks, and paragraphs keep their indents and alignment.
- **First baseline:** one leading below the frame's top, on the scanned baseline.
- **Pages:** facing pages as in the book (left-hand pages are versos), numbered from the folios.
- **The scan** is placed under the text on a layer of its own, "Scan", locked and non-printing.

It is written with docx-to-idml's IDML writer, copied into `src/ectype/idmlkit/` unchanged (MIT). That writer brings InDesign's own base settings and styles, and pages, spreads, sections and links as InDesign writes them. Its output has been opened in InDesign and Affinity Publisher. Ectype subclasses it for frames placed freely on the page, a leading per paragraph, and the scan layer.

Fonts are chosen per glyph style (`fontmatch.py`), and each becomes a character style named for what it is on the scan ("Text", "Text Italic", "Notes", "Running Head"…), so a font is changed in one place in InDesign:
- **Comparison.** Each style's averaged glyphs are compared with every installed font of the same slant, drawn at the same x-height on a 72-row canvas.
- **Weight.** The body text is taken to be regular weight. Other styles match bold fonts only if their strokes are clearly heavier, because scanned ink spreads and would otherwise always pick bold.
- **Family.** Text styles prefer the body's family slightly, as books set notes and italics in the body face.
- **Size.** Point sizes follow from the scanned x-height and the font's.
- **Width.** A horizontal scale is measured per style. Each block is narrowed further, locally, if any of its lines would overflow its measure in the replacement font, which would push lines out of the frame.

On a synthetic page set in Liberation Serif at 11 pt, the font match picks Liberation Serif Regular at 10.98 pt.

### Checking

Every IDML export, Reflow and Facsimile, is checked in the tests with docx-to-idml's `tools/idml_check.py`. It checks:
- the package structure
- the links between parts: frames, stories, pages, masters and sections
- Adobe's IDML schema (DOM 21.5), with jing

CI clones docx-to-idml for this and runs Reflow through it. Both exports of the Young chapter pass.

The first version of this export (October 26) wrote its own IDML. It failed the schema (wrong DOM version, frame insets written as attributes), set `PagesPerDocument` to the page count (InDesign then adds that many pages at the start) and had no page descriptors. All three were things docx-to-idml had already found and fixed against InDesign, so that writer was replaced.

**Still to check in InDesign:** a Facsimile export (line fit with InDesign's composer, the scan layer and links) and a Reflow export of a real chapter.
