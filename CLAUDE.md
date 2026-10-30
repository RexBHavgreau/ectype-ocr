# Ectype OCR: notes for Claude

Read this first in every session. It carries what earlier sessions learned, so nothing has to be
rediscovered. Background: `PLAN.md` (goals), `docs/releasing.md` (releases), `docs/idml.md`
(training, IDML), `README.md`.

## Keep this file current (standing rule)

Whenever a major problem or decision comes up, add it here **at the time**, in the same commit as
the fix or change, not at the end of the session. "Major" means anything a later session would
otherwise have to rediscover: a failure's real cause, a workaround, a design choice and why, a
convention, the user's preferences, plans for a coming version. Keep entries short (what, why,
where in the code). Update or remove an entry when it stops being true; move finished plans out
of "Planned".

## Working with Beaux

- Beaux is an editor and a Hebrew / Old Testament scholar, not a programmer. Reports: short,
  plain, no jargon; say what it means for them and what they need to do.
- Develop on the session's branch; commit and push there. Open a PR to `main` when work is ready;
  **merge only when Beaux says so.**
- Beaux pushes the release tags (`vX.Y.Z`) themself. A new feature is a minor version (0.4.0 added
  Train from fonts); fixes alone are a patch version. The version comes from the tag: no version
  number to edit in the code.
- No force pushes. Commit messages and PRs carry no model names.
- Beaux likes to keep tweaking on the branch before merging; they test on their own Windows
  machine (a cloud session can't attach a Windows folder) and correct real books in Review.
- UX: the Log is a record, not the main view. Work must not switch to the Log tab (progress has
  its own window); a finished run brings Review forward.

## Releasing

Merge to `main`, Beaux tags it, `release.yml` builds and attaches `Ectype-OCR-<v>-Setup.exe` and
`Ectype-OCR-Kraken-<v>-win64.zip`. The Windows installer workflow also runs on every branch push:
check it passes (bundling, packaged-app smoke test, installer, Kraken engine) before asking to
merge. The `Tests` workflow's `kraken` job takes about 20 minutes.

## Lessons learned (problems and their causes)

- **Tesseract's training tools on Windows** (0.3.1): in the UB Mannheim package,
  `unicharset_extractor` and `combine_lang_model` can't start: its ICU needs a C++ runtime entry
  point (`_ZSt15__get_once_callv`) its own `libstdc++-6.dll` lacks, and no other runtime fits both
  it and Tesseract. Without them Hebrew's vowel points can't be added ("Too few images could be
  prepared"). Fix: `release.yml` tests each tool with only its folder and Windows' on PATH, and
  takes failing ones from MSYS2's Tesseract into `tesseract\training` with their DLLs;
  `train.tool()` looks there first. The smoke test trains Hebrew to prove it.
- **The app runs Tesseract's tools with a restricted PATH** and Windows error boxes off
  (`resources.tesseract_env`, `quiet_windows_errors`), so a user's other programs' DLLs can't
  interfere and a missing DLL fails quietly instead of with popups.
- **Updates must install over the existing copy wherever it is** (0.4.0): Beaux's install is in
  `D:\Program Files\Ectype OCR`. The updater used to decide all-users vs per-user from whether the
  app was under C:\Program Files and put a second copy on C:. It now reads the installer's
  uninstall entry (HKLM / HKCU) and passes `/DIR=` (`updates.installer_command`).
- **Training pool: the same lines set again must be kept** (0.4.0): fonts found twice (two
  folders) had all their lines deleted, because `Writer.remove` left the origin mapped and the
  identical new images were taken as present. Fixed in `pool.Writer.remove`; a font counts as done
  only if its images exist. Fonts are also deduplicated by name (`synthtrain.fonts_in`).
- **Fonts without Hebrew vowel points** count as *Partial* support (`synthtrain.level`): their
  lines are set without the points, since the typeface also appears in unpointed books.
- **Kraken release check** uses PP-OCRv6 small (`packaging/kraken_smoke.py` PREFERRED); training
  PP-OCRv6 tiny crashed without a message and is only tried as a diagnostic.
- **English reads only é among accented letters** (0.5.0): tessdata_best's `eng` has 112
  characters, é the only one beyond ASCII, so ḥ ā š ü ə came out as plain letters, silently.
  Fix: Train from fonts → Transliteration (`translit.py`: English prose with SBL Hebrew made from
  the WLC, other Semitic, IPA, European words) trains `eng`, adding the letters. `train._foreign`
  keeps modifier letters (ʾ ʿ ː ᵉ) and IPA θ β χ in English lines; Latin counts a letter with a
  combining mark (p̄) as one character (`_graphemes`). A partial font sets only the lines it can.
- **False "add a language" offers** (0.5.0): on Driver's Treatise the script detector's wrong
  guesses on smudged words (Devanagari 16, Cyrillic 9, Arabic 7 over 26 pages, three on one
  page) made the app offer Hindi and Arabic. `scripts.suggest` now needs three on a page on
  one page in 20 of the book (two to three pages).
- **Review couldn't confirm a correct flagged word** (0.5.0): Save ignored unchanged text. Now
  it confirms (green, "Confirmed", training line saved).
- **Italic i's lost their dots in the traced type** (0.5.0, Treatise "I gıve", "ıs sellıng"): the
  dot leans right, over the gap or the next letter, so `_clusters` (dot over a stem) left it
  out, joined it to the m, or alignment took it for a letter. `segment._float_marks` sets
  floating marks aside before alignment (not in words with quotes, ʾ ʿ or superscripts) and
  `_join_marks` gives them to the nearest i/j/accented letter; it also joins a broken-off
  piece beside a letter (the v's arm that made "give" look like "gize").
- **Footnote text copied interleaved with the next line** (0.5.0): a baseline fitted to
  old-style figures (3 4 5 7 9 descend) sloped 5 pt across the line. `NO_DESCENDER` keeps only
  0 1 2 6 8; a fitted slope over 0.01 from Tesseract's is replaced by Tesseract's
  (`fit_baselines`).
- **Glyph grouping crashed** ("all input arrays must have the same shape"): the feature width
  was rounded per instance from xh * s, which cancels out but not in floating point
  (`cluster._feature`).
- **IDML export stopped: "OSError: invalid reference"** (0.5.0): one of Beaux's installed
  Windows fonts opens but fails when FreeType draws a letter. Font matching
  (`fontmatch._distance`, `_horizontal_scale`) now passes over such a face.
- **Windows text files:** write list/box/gt files with `newline="\n"`; read tool output as UTF-8.
- **Qt in tests:** free dialogs on the interface thread (close, deleteLater, processEvents) or a
  worker thread's garbage collection crashes the run.

## Decisions

- One OCR model per language (not per book), trained on a shared pool of every project's
  corrections, lines set in fonts, and scans matched to known text. Typeface-family models are
  chosen automatically per book; Kraken family models are stored as small packs over the general
  model. A new model replaces the one in use only if it does better.
- Training (0.5.0): "Train OCR model" does a **quick update** when little is new
  (`training.plan`): the model in use trained further on the new images plus 3x earlier ones
  (replay, so nothing is forgotten), few iterations; only the families the new images touch are
  retrained (Kraken packs of the others keep their old general model as `base`). Training
  afresh (`--full`, a box in the dialog) is for now and then. Beaux's complaint: two dozen
  corrections took as long as a whole training. Also: `.lstmf` cache, parallel prep and checks,
  the model in use's per-image errors kept (`<which>.errors.json`).
  Models and the pool live in the user folder (`%LOCALAPPDATA%\Ectype OCR`), so upgrades keep
  them; a model trained before 0.5.0 (no `general.items.json`) is taken to know the pool images
  older than its training time, so the first training after upgrading can be quick too.
- Known texts: Hebrew Bible (WLC) and Greek New Testament (SBLGNT). More languages need a
  reliable public-domain text the app can download. Hebrew lines set from fonts (0.5.0,
  `synthtrain.hebrew_form`, VERSION 2) come in three forms: the Codex's accents (35%), as
  grammars print it (30%: points, and ֫ ole over a stress before the last syllable, found
  from the Codex's accent, `texts.grammar_style`), points only (the rest, and every line of
  a font without the accents). Beaux: the stress mark is common in grammars; accents too.
  Matching scans to the text (`align.py`) still uses it without accents.
- Kraken is an optional add-on (its own download), for non-Latin scripts.
- Review (0.5.0): a punctuation mark at a word's start or end is a box of its own
  (`review.parts`), since typed beside Hebrew it jumps about in the text box; the correction log
  still holds the whole word's text, so `carry_corrections` works unchanged. Saving a word
  unchanged confirms it (green, and its line becomes training data).
- Review (0.5.0): a missed letter is taken in by stretching the word's box (`BoxEditor`, dashed,
  drag an edge), a missed word by drawing one ("Add a word", `review.add_word`). Segmentation
  gives a word the ink inside its box, so the letter is then traced and erased from the
  background. Letters that stay keep their boxes (`review.place_chars`). Log entries now carry
  the word's box ("resized", "added"); adding a word shifts the line's later entries and word
  training images along one (`_make_room`).
- Traced mode draws every word as type (0.5.0; Beaux: "faking it is not the answer"). A word
  read with confidence under 60 (`cluster.VERBATIM_CONF`) has each letter traced from its own
  ink, a cluster of its own, so a misreading can't draw a letter the page lacks; characters
  with no ink stay in the text via ActualText. Only a word with no glyph at all stays image
  (Treatise: 329 image words -> 30; PDF 1.55 -> 1.71 MB). `--min-conf` is gone.
- Review's special characters (0.5.0, Beaux's design): an Ω button beside the correction box
  opens the characters used last (`characters.recent`, `characters.json` in the user folder)
  and "More characters…", a floating, non-modal `CharacterPane` (sets: superscripts,
  transliteration, accented Latin, combining marks, IPA, Greek, Hebrew points, Hebrew accents,
  punctuation; search by Unicode name). A click inserts at the cursor; combining marks are
  shown on ◌ and inserted without it.
- IDML: Facsimile is the static export (frames where the text stands, lines kept, notes as
  text in their frames); Reflow is the live one (docx-to-idml: real paragraphs, Word
  footnotes become InDesign footnotes where a superscript reference matching the note's
  number was found on its page, the rest gathered under "Notes"). Unicode superscripts from
  the OCR go to both as plain characters with superscript formatting (`plain_superscripts`);
  the PDF's text keeps the Unicode ones. The dialog asks Reflow or Facsimile **first**
  (0.5.0): it used to come after the layout and font matching, so in 0.4.0 Beaux never saw
  the choice (and a damaged font stopped the export before it); Reflow skips font matching.
- Training from fonts is in the app (Training → Train from fonts…) as well as the batch file
  `Train Hebrew and Greek.bat` (`ectype train-all`).
- Transliteration is a choice in Train from fonts that trains `eng` (not a language of its
  own), off by default; Hebrew in SBL academic style is generated from the WLC by rules of
  thumb (`translit.hebrew`), plus some in Gesenius' older style (ḳ, qᵉṭal). Generated IPA is
  made-up words, so it reads worst. Its lines also carry superscripts as Unicode characters
  (note numbers word¹, verse parts 3ᵃ; Treatise read them as "!", "?", "%").

## Test material and expected results

- `corpus/Pages from Treatise 01.pdf`: 26 pages of Driver's *Treatise on the Use of the Tenses
  in Hebrew*, typical scan quality; the hard test (English, italic, Greek, pointed Hebrew with
  stress accents, transliteration, footnotes). Stock eng+grc+heb, 207 s for a full run: English
  roman nearly exact; Greek good even in footnotes; italic often wrong ("faken", "zs seliing");
  superscript letters wrong (3ᵃ "3%"); transliteration wrong (Noʿŏmi "Nodmi"); pointed Hebrew
  poor (כָּרָה "173"). `corpus/E J. Young Inspiration-1.pdf`: 17 pages, English; a full traced
  run 106 s.
- Transliteration training (300 lines in each of 4 fonts, 3,000 iterations): eng's character
  errors on a 5th font 14.8% -> 4.5% (qūdšāʾ ʾabbāʾ read exactly; stock: "qiidSa' 'abba'").
- Quick update (Hebrew, 480 lines in 4 fonts, then 27 "corrections"): afresh 75 s, quick 40 s;
  the gain grows with the pool, since a quick update's size doesn't.
- A stretched box (Review): a word with its first letter left out of its box came out
  "xclamation" with the "e" left in the background; stretched and retyped, "exclamation" with
  nothing left behind.
- Update after corrections on Young (17 pages), 69 s: segment 2 s (only corrected pages,
  already), cluster 43 s (collect 16, averaging 21, grouping 12), fonts 4 s, traced 20 s.

## Planned

- **0.5.0: the Ectype icon** from `assets/ectype-icon` (`ectype.ico` 16–256 px, PNGs 16–1024, SVG):
  the exe (PyInstaller `icon=`), the app window and taskbar (Qt window icon; Windows
  AppUserModelID), the installer and its uninstall entry (Inno `SetupIconFile`), and the Kraken
  engine exe. Not before 0.5.0.
- Possibly English or Latin known texts for Train from fonts.
- **Faster "Update after corrections"**: keep per-page cluster instances (collect), composites
  of clusters whose members are unchanged, groupings of letters whose instances are unchanged,
  and the erased backgrounds of untouched pages; same output. Expect ~70 s -> 15-20 s on Young.
- **Better reading of Driver's Treatise**: train Hebrew on lines in fonts like Driver's and
  measure on the Treatise (accents and the stress mark are in the training lines now); set
  some Train-from-fonts lines in each
  font's italic (English, transliteration); superscript letters and figures. A checked
  transcription of a few Treatise pages (Beaux can correct or confirm one) would give real
  error rates.
