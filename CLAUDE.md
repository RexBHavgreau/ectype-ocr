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

## What each version brought (for release notes and for knowing what is where)

**0.5.0** (this session, from Beaux's testing of 0.4.0 on Driver's *Treatise*):
- *Reading more scripts and characters.* Train from fonts now has Latin (Clementine Vulgate),
  Syriac (Peshitta OT, ETCBC), Arabic (Qurʾān from Tanzil.net and the Smith–Van Dyck Bible)
  and Transliteration (trains English: SBL Hebrew made from the WLC, other Semitic, IPA,
  European accented letters, superscript note numbers and verse parts). Hebrew lines come with
  the Codex's accents, as grammars print them (֫ stress mark), or pointed only.
- *Training.* Quick update when little is new (minutes, not hours); Train afresh as a box;
  prepared lines cached, preparation and checks run side by side; upgrades keep models and
  the first training after upgrading can be quick.
- *Review.* Save unchanged confirms a word (green); punctuation at a word's edges is a box of
  its own; bigger correction text; stretch a word's box over a missed letter; Add a word;
  special characters (Ω: recent ones, and a floating character pane with search).
- *Traced PDF.* Every word drawn as type (doubtful words traced letter by letter, not left as
  grey images); italic i dots and broken letters kept; level baselines with old-style
  figures (footnote text no longer copies interleaved).
- *IDML.* Reflow (live) or Facsimile (static) asked first; Reflow skips font matching;
  Facsimile shows its fonts to confirm; Unicode superscripts set as superscript formatting;
  a damaged installed font no longer stops the export.
- *Smaller.* No false "add Hindi/Arabic" offers; work doesn't switch to the Log tab; a known
  text that can't be downloaded leaves only its language unavailable.
- *The Ectype icon* (`assets/ectype-icon` is the master; the PNGs 16–512 and the .ico are
  copied to `src/ectype/icons` as package data): both exes and the Kraken engine's
  (PyInstaller `icon=`), the window and taskbar (`gui.app_icon`, AppUserModelID
  `Ectype.EctypeOCR`), the installer (`SetupIconFile`), its uninstall entry (the exe's icon),
  and the Train Hebrew and Greek shortcut. Changing the icon: replace both copies.

**0.4.0**: Train from fonts in the app; the updater installs over the existing copy wherever it
is; the training pool keeps lines set again.

**0.5.1's work shipped as 0.5.0**: Beaux tagged main as v0.5.0 after both merges (0.5.0 had
not been tagged), so the release "0.5.0" holds everything below too.

**0.6.0** (style set in Review is a new feature, so a minor version):
- *Italic words drawn half upright* (Treatise p. 1: *past*, *future*, *perfect* came out with
  letters from upright and italic shapes mixed): `word_italics` took only slants of 7-18
  degrees for italic, tuned on Young (8-12) to keep upright "very" (22-24) out; Driver's italic
  measures 16-28, so it was "upright", and its letters joined upright clusters or stayed apart.
  The upper limit now follows the book (`cluster.italic_limit`: median of its leaning words
  + 10, at least 18; Young 20, Treatise 28); slants are kept per page and italics decided in
  `collect` for the whole book (`CACHE_VERSION` 2).
- *Style set in Review* (Beaux: the correction box had no formatting): **B** and ***I*** buttons
  and Ctrl+B / Ctrl+I, small capitals by Ctrl+Shift+K only (Beaux: rare, no button). The box
  shows the word in its style. Saved as the log entry's `style` ({italic, bold, smallcaps},
  `review.word_styles`, last wins), over detection: `cluster.collect` sets the word's instances
  (bold and small capitals are part of the grouping key, so they group apart; `Glyph.bold`,
  `.smallcaps`), layout's `LWord.bold/.smallcaps`, IDML `FontStyle` Bold / `Capitalization`
  SmallCaps, Word `<w:b/>`, `<w:smallCaps/>`. Detected italic is shown on selecting a word
  (`Project.word_styles`).

**0.6.1** (a fix):
- *Arabic not offered for any font* (Beaux, 0.5.0: Times New Roman showed "—" for Arabic): the
  Arabic texts hadn't downloaded (Tanzil's form, untested from the cloud), and the window gave
  every font "—" without saying why. The Qurʾān now comes first from a verbatim copy of
  Tanzil's "simple" text in a public repository (`texts.QURAN_URL`, rizaumami/quran-epub at a
  fixed commit; SHA-1 16d8abb3, Tanzil's 2019 download), tested from here; Tanzil's form is the
  fallback. Tanzil's notice is kept in the cached copy (its terms), skipped when read. The
  Qurʾān uses only U+0621-0652, which Times New Roman has. A language whose text couldn't be
  downloaded is marked "(no text)" in the font table, and the status line says why
  (`support(failed=)`). Van Dyck (eBible) is still untested; Arabic uses the Qurʾān alone if
  it fails.

**0.5.1 work** (in the v0.5.0 release; gaps filled, no new whole features):
- *Faster "Update after corrections"* (69 s -> ~21 s on Young, same output): glyph grouping
  keeps its work in `cluster/cache/` (`cluster.Cache`): each page's instances while its image,
  reading and segmentation are unchanged (`collect`), each letter's grouping and each
  composite by the fingerprints of the instances in it (`Instance.fingerprint`); results not
  used in a run are removed. Traced's check pictures (`*.overlay.png`) are saved with fast
  compression (they took half its time). Erased backgrounds are not kept: about 1 s a book.
- *Italic in training lines* (Treatise italic was often misread: "faken", "zs seliing"): for
  Transliteration and Latin, a font's italic face of the same weight (`synthtrain.italic_of`,
  among the chosen and installed fonts) sets a run of one to four words in 30% of lines and
  whole lines in 8% (`_italic_runs`, `render_runs`); the text is unchanged. Their lines are
  made again (`SCRIPT_VERSION` 3); fingerprints come from all-roman lines only.
- *Raised characters read again* (`raised.py`, after check_words in `ocr._ocr_one`): note
  numbers and verse parts the model read as "!", "?", "%", ">". A run of up to three at a word's
  end (before closing punctuation), or a lone mark of one or two starting a line, is raised when
  every separate piece of ink centred in its box ends 0.3 x-heights above the baseline (whole
  pieces: Tesseract's box can hold only the top of a y's arm or a t's crossbar, which looked
  raised; x-height = hOCR x_size - ascenders - descenders). Letters only a-h after a figure.
  Read again enlarged x3/x5, plain/sharpened, digits and a-h only, until two readings agree;
  stored as ¹ ᵃ. Treatise: all note marks and most verse parts right (13ᵇ, 547ᵈ, 27ᵇ); 171ᵃ
  read 171⁴, who³ unchanged. About 1.8 s a page.

## Decisions Beaux made about proposals (so they aren't offered again)

- Rejected: applying one correction everywhere the same misreading occurs ("too much can go
  wrong").
- Not for now: showing words dropped as noise as faint boxes in Review to restore them.
- Not build work, in Beaux's view: lone punctuation left as image, the odd "2" of the
  Treatise's "12.", the browser Review lacking the app's new tools.
- An English known text for Train from fonts: only a maybe (corrections already teach plain
  English; special characters are Transliteration's).

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
- Known texts: Hebrew Bible (WLC), Greek New Testament (SBLGNT), Latin Vulgate (Clementine,
  public domain, one TSV from github.com/lukesmithxyz/vul; Train from fonts → Latin trains
  `lat`, a quarter of its lines with word-final æ). More languages need a reliable
  public-domain text the app can download. Syriac: the Peshitta OT (with deuterocanon, 46
  books, unvowelled) from ETCBC/peshitta's `plain/0.2/<Book>.txt` (Beaux chose it; its text is
  CC BY-NC 4.0, credited in CREDITS.md, downloaded on the user's machine for their own
  training; the repo's MIT licence covers its programs only). Train from fonts → Syriac
  trains `syr` (and installed Syriac Kraken models), set right to left with raqm's Syriac
  shaping. Arabic (Beaux's choice, both): the Qurʾān from Tanzil.net (POST form
  `download.php`, quranType=simple, outType=txt-2, agree=true; CC BY 3.0, unchanged, credit)
  and the Smith–Van Dyck Bible (`ebible.org/Scriptures/arb-vd_vpl.zip`, public domain),
  verses interleaved; trains `ara`. **Untested downloads**: tanzil.net, ebible.org and
  peshitta.eu are blocked from the cloud session, so Beaux's machine is the first to fetch
  them; a failing download now leaves only that script unavailable (`synthtrain.support`,
  `generate`), where it used to stop the Train from fonts window. Hebrew lines set from fonts (0.5.0,
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
- Update after corrections on Young (17 pages): 69 s in 0.5.0 (segment 2, cluster 43, fonts
  4, traced 20); about 21 s since (cluster 4-6, traced 11), with identical output.

## Planned

- Possibly an English known text for Train from fonts (Latin is done: the Vulgate).
- **Better reading of Driver's Treatise** (accents, the stress mark and superscripts are in
  the training lines since 0.5.0): train Hebrew on lines in fonts like Driver's and measure on
  the Treatise. A checked transcription of a few Treatise
  pages (Beaux can correct or confirm one) would give real error rates.
- **Check the untested download** on Beaux's machine: eBible.org's Van Dyck (Arabic), blocked
  from the cloud session (the Qurʾān now comes from a repository copy).
- **Lone punctuation left as image**: the 30 words the Treatise still draws as image are
  nearly all ; : , with no ink tied to them (`traced.place_page`, "no glyph").
- Syriac with vowels: the Peshitta text is unvowelled; vowelled Syriac is learned only from
  corrections.
