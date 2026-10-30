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
- **Changing languages** reads the pages again on the next run. Corrections made in the Review tab are carried over to the new reading: each goes to the new word at the same place on the page (`review.carry_corrections`), with its box if it was stretched; a word added by hand that the new reading still misses is added again.

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
- **Training.** A Kraken model learns from its script's pool like Tesseract's language does (see Training below), with Kraken's trainer, `ketos train` (`--resize union` adds characters it lacks). Its typeface families' models are packs of their changes from the general model (below).
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

## Training the OCR models

Training improves the models from book to book: what is learned from one project carries over to the next, and nothing is lost when another is added.

### One pool per language

Every language has a training pool in the user's folder (`pool.py`), shared by all projects and both engines. Into it go:
- **Corrections** from every project: a corrected line goes to the main language's pool, a word corrected into Hebrew or Greek to that language's pool. They are added when a project is trained (`Project.sync_pool`); a line corrected again replaces its earlier version.
- **Confidently read lines** of each project (every word at 90 or more, at most 300 a project): trained on, never tested on, so training on corrections doesn't unlearn what a model already reads well.
- **Lines set in your fonts** (below).
- **Lines of scanned editions matched to their known text** (below).

A fifth of each source's items is kept aside for testing, chosen by the item's id, so it never changes. For lines set in fonts, whole fonts are kept aside instead (a fifth of them, at least one of two or more), so the test is on type the model never saw.

### One model per language, with typeface families beside it

The Languages menu shows languages, not models: "Hebrew", "Greek (Ancient)", a Kraken model. Behind each one (`models.py`) is:
- the **stock model** (Tesseract's language, or the Kraken model downloaded);
- a **general model**, trained on the language's whole pool. It replaces the stock model once there is one;
- **family models**, for typeface families with enough material.

**Typeface families** (`families.py`): a book's type in one script gets a fingerprint, measured on its letters scaled to one height: stroke weight, thick-thin contrast, how much of each letter is ink, width and slant. Books and fonts whose fingerprints lie close together form a family. On the fonts at hand the serifs fall together, the sans faces together, and bold and italic apart. The same font at another size stays in its family; heavy blur, like dark ink, makes type look bolder and can move it.

**Which model reads a book** is Ectype's choice, not the user's:
1. the model found best on the book's own corrections (each language's models are tried on them after training);
2. else the family model nearest the book's fingerprint, if one is near enough (a book's first reading measures its type on its first three pages, then reads with the model that suits it);
3. else the general model;
4. else the stock model.

The Log says which was used.

**Storage.** A Tesseract family model is a model of its own in compact (integer) form, a few megabytes. A Kraken family model is a *pack*: only its changes from the general model, each layer's difference in 8-bit steps with a scale for each row (`kraken_worker.make_pack`). When reading, the engine lays the pack over the general model in memory. On a test model a pack was a quarter of a full copy (4.0 against 16 MB), and the rebuilt model read 30 test lines exactly as the full one did. Compressing packs further would save only 13%.

### Training a language

**Train OCR model…** in the app (`ectype train <project>`, or `ectype train --lang heb grc` without a project) trains each chosen language with at least 20 images in its pool (`training.py`):
1. **General model.** Trained afresh from the stock model on a balanced sample of the whole pool: at most 4,000 images, every source represented, confidently read lines no more than a third. Starting from the stock model each time keeps it from drifting as books are added; the sample keeps the time bounded as the pool grows.
2. **The check.** The new model and the one in use read every source's test images. The new one is installed only if it makes fewer errors overall and no source does clearly worse (more than 10%, or half a point). Otherwise the old one stays, and the report says which source got worse.
3. **Families.** A family with 200 or more training images gets a model trained further from the general model on its images. It is kept only if it makes at least 3% fewer errors on the family's test images than the general model does. When fonts in a family weren't kept aside, a tenth of its images is held back for this.
4. **This book.** The language's models are tried on the book's own corrections, and the best is remembered for it. Training again ends that choice, since the models have changed.

**A quick update** is what Train OCR model does when little is new (up to a quarter of what the model in use was trained with, or 200 images): a couple dozen corrections shouldn't cost a whole training. The general model in use is trained further on the new images, with three times as many earlier ones (200 at least) so that it doesn't forget them, for 400 to 3,000 iterations (Kraken: 5 epochs). It is checked as above and installed only if it does better. Only the typeface families with a book among the new images are trained further, from their own models; for Kraken, the other families' packs stay laid over the general model they were made over. The dialog's **Train afresh** box (`ectype train --full`) starts again from the stock model on the whole pool, worth doing now and then after much has been added. `general.items.json` beside the models lists the images the general model knew, which is how the new ones are found.

Every training is also quicker than it was: each line's training file is made once and kept (`training-cache` in the user's folder), lines are prepared and test images read several at a time (one processor each), and the model in use keeps its errors on each test image, so a check reads only the new model and new test images.

The report goes to the Log; the app offers to read the pages again.

**Trained models…** in the Languages menu lists each language's models and pool, and can remove a language's trained models (back to the stock model; the pool stays) or empty its pool. It also removes the per-book models made by earlier versions ("English, trained on Young"). Projects that used one read with the language's trained model instead. `ectype models` does the same from the command line.

### Training without correcting by hand

**Lines set in your fonts** (`synthtrain.py`, `ectype train-synthetic --fonts <folder> --script hebrew greek transliteration`). For each font in the folder (and its subfolders) that has the script's letters:
- Passages of a known text are cut into lines of 3 to 9 words and set in the font at several sizes. Hebrew is set right to left, with its points placed by Pillow's raqm layout.
- Each line is made to look scanned: a slight tilt, blur, ink lighter or heavier, paper tone and noise, specks.
- The image and its exact text go into the pool as the font's own source, with its typeface fingerprint, so fonts and real books fall into families together.
- Fonts are read where they are and never copied.
- Running again replaces a font's lines; fonts that haven't changed are skipped.

**Known texts** (`texts.py`), downloaded once into the user's folder:
- the Hebrew Bible: the Westminster Leningrad Codex, public domain, via the Open Scriptures Hebrew Bible, CC BY 4.0;
- the Greek New Testament: the SBL Greek New Testament, CC BY 4.0;
- the Latin Vulgate (Clementine), public domain, from the one-file text of `lukesmithxyz/vul`; it trains Latin (`lat`, Train from fonts → Latin).
- any UTF-8 text file of an edition (for example Westcott–Hort), one passage a line.

**Transliteration** (`translit.py`, `--script transliteration`) trains English, not a language of its own. Tesseract's English model knows one accented letter, é: it reads ḥ as h and ā as a, drops ʾ and ʿ, and nothing shows that anything was lost. The text is generated: English sentences of scholarly prose with transliterated words among them, sometimes in brackets or quotes, with an occasional reference or page number:
- Hebrew in SBL academic style, made from the Hebrew Bible (bərēʾšîṯ bārāʾ ʾĕlōhîm, rûaḥ, YHWH), some in the older style of Gesenius' grammar (ḳ, qᵉṭal);
- Arabic (DMG letters such as ǧ ḫ ṯ ḏ ġ ḍ ẓ, and the kh/dh/sh spellings), Akkadian, Ugaritic, Aramaic and Syriac words;
- IPA transcriptions in slashes or brackets (made-up words: every symbol, stress, length, some diacritics);
- European words with accents (German, French, Latin with macrons, Spanish, Scandinavian, Czech, Polish, Hungarian, Turkish, Romanian, Portuguese, Icelandic);
- superscripts: notes' numbers after words (word¹, word¹²) and verses' parts in references (Isa. 1, 3ᵃ), stored as the Unicode superscript characters; the stock model reads them as "!", "?" or "%".

A font supports it fully with nine in ten of these letters, partly with a–z and half of them; a partial font sets only the lines it has every letter of. Training keeps the modifier letters (ʾ ʿ ʰ ː ᵉ) and IPA's θ β χ as English characters, and adds the letters the model lacks, a letter with a combining mark (p̄, ɛ̃) counting as one.

On the fonts at hand, 300 lines in each of four fonts (3,000 iterations) brought English's character errors on a fifth font it never saw from 14.8% to 4.5%: the stock model read *qūdšāʾ ʾabbāʾ* as "qiidSa’ ‘abba’", the trained one exactly. SBL Hebrew and the accented letters came out nearly clean; made-up IPA, with no words to lean on, is the weakest.

Hebrew accents (cantillation, meteg, paseq) are left out. Consonants, vowel points, dagesh, shin and sin dots, maqaf and sof pasuq are kept. Greek keeps its accents and breathings.

**Scanned editions matched to their text** (`align.py`, `ectype train-scans <scan> --text hebrew-bible --edition "Letteris 1866"`). The scan is read as a project of its own in the user's folder, with the language's current model. Then each line the OCR found is looked for in the text:
- Its words, reduced to bare letters, vote for where in the text they stand.
- They are paired with the text's words there. Unpaired words at the ends, such as verse and chapter numbers, are cut off the image.
- The line is kept only if 85% of its letters agree. The known text, with its points, is then the line's true text.
- Doubtful lines are skipped, not guessed, since an edition may differ from the known text in places.

On test pages set in FreeSerif, 23 of 24 Greek lines (Matthew 1) and 21 of 25 Hebrew lines (Genesis 1, pointed) were matched, with their verse numbers cut off.

**The batch file.** `Train Hebrew and Greek.bat`, in the program folder and the Start menu, runs all of it: `ectype train-all --config training.ini`.
- The first time, it makes the settings file in the user's folder (`%LOCALAPPDATA%\Ectype OCR\training.ini`) and opens it in Notepad. There you give the fonts folder, the scripts, the lines per font (400 by default), and any scanned editions with their texts.
- It then sets the lines, matches each edition (skipping those already matched), and trains each language of those scripts: Tesseract's, and every Kraken model installed for them.
- It writes `training-report.txt` beside the settings.
- Steps already done are skipped, so it can be stopped and run again.

On the fonts at hand, 40 lines in each of seven Greek fonts trained a general Greek model in under a minute. It read the held-out font with 1.0% character errors. The family models made no further gain on these clean, computer-set lines, so none were kept. The gains will come from real books' type.

### How Tesseract and Kraken are trained (`train.py`)

- **Tesseract** continues its float model (`tessdata_best`, downloaded once, about 15 MB; the shipped models are integer models and can't be trained further). Each image becomes a training file (`.lstmf`) with a box file of its text. Images with letters of another script (Hebrew in an English line) are left out.
  - **New characters.** Characters of the model's own script that it lacks are added, as tesstrain does: the stock Hebrew model has no holam, tsere, hataf segol or shin dot. Training continues from a starter model with the new character set and the old word lists (`--old_traineddata`).
  - **Right-to-left text** is trained in visual order, its characters reversed: Tesseract's Hebrew model reads that way and turns its readings round. It uses normalisation mode 3 and a pass-through recoder, as the stock model has.
  - **Saving.** The result is saved in float form (to train further) and integer form (to read with; smaller).
- **Kraken** uses `ketos train`, run by the Kraken engine. It loads data in its own process (no worker processes, which a packaged program can't easily start), on all but one core, and doesn't compile the network (that needs a C++ compiler).

Earlier tests: 150 generated pointed Hebrew words in FreeSerif. Training on 120 cut errors on the 30 held out from 32% to 16%, and the model read the added points (וּרְאֵה, וּמַלְּטִי). On a small Kraken model, two epochs on 32 words cut errors on 8 held out from 51% to 10%.

Training needs Tesseract's training tools: `lstmtraining` and `combine_tessdata`, and to add characters also `unicharset_extractor`, `merge_unicharsets`, `combine_lang_model` and `dawg2wordlist`. They come with Tesseract in Linux packages, and in the Windows app.

## IDML export

**Export IDML…** in the app, or `ectype idml <workspace> --mode reflow|facsimile`. The app asks first: Reflow (live) or Facsimile (static), with Reflow's options. It then prepares the layout; for Facsimile it also compares the scan's typefaces with the installed fonts and shows the fonts chosen, to confirm or change, before asking where to save.

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
- **Italics** as italic runs; raised figures as superscripts. Unicode superscripts the OCR reads (¹, ᵃ, from a model trained on them) are written as their plain characters set as superscripts, in Word and InDesign alike (`layout.plain_superscripts`), so they take the book's font and a note's ¹ matches its reference.
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
