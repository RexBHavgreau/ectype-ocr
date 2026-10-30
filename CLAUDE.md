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
- **Windows text files:** write list/box/gt files with `newline="\n"`; read tool output as UTF-8.
- **Qt in tests:** free dialogs on the interface thread (close, deleteLater, processEvents) or a
  worker thread's garbage collection crashes the run.

## Decisions

- One OCR model per language (not per book), trained on a shared pool of every project's
  corrections, lines set in fonts, and scans matched to known text. Typeface-family models are
  chosen automatically per book; Kraken family models are stored as small packs over the general
  model. A new model replaces the one in use only if it does better.
- Known texts: Hebrew Bible (WLC, accents stripped, points kept) and Greek New Testament (SBLGNT).
  More languages need a reliable public-domain text the app can download.
- Kraken is an optional add-on (its own download), for non-Latin scripts.
- Training from fonts is in the app (Training → Train from fonts…) as well as the batch file
  `Train Hebrew and Greek.bat` (`ectype train-all`).

## Planned

- **0.5.0: the Ectype icon** from `assets/ectype-icon` (`ectype.ico` 16–256 px, PNGs 16–1024, SVG):
  the exe (PyInstaller `icon=`), the app window and taskbar (Qt window icon; Windows
  AppUserModelID), the installer and its uninstall entry (Inno `SetupIconFile`), and the Kraken
  engine exe. Not before 0.5.0.
- Possibly English or Latin known texts for Train from fonts.
