# Ectype OCR: tasks

This is the project's working list: what needs doing, who can do it, and exactly how. It took
the place of `PLAN.md` (the original 2026 design, now kept as history in
[`docs/history/original-plan.md`](docs/history/original-plan.md)).

- **Beaux** owns the project, decides what is wanted, tests on Windows, merges and releases.
- **The manager** (a Claude session) keeps this file: writes the tasks, checks finished work,
  and does the tasks marked for Claude. Its guide is at the end of this file.
- **Workers** (any coding agent: Copilot, Qwen, a smaller Claude) each do one task and report.

Any agent: read [`AGENTS.md`](AGENTS.md) first. It has the setup, the test command and the rules.

## How to do a task

1. **Pick** the task you were told to do. Check its **Status** is `Ready` and its **Who**
   line fits you. If not, stop and say so.
2. **Branch**: make a branch from the newest `main` named `task/<ID>-<two-or-three-words>`,
   for example `task/T-003-releasing-doc`. (If your tool makes its own branch, use that.)
3. **Claim**: in this file, change the task's Status to `In progress (<your tool>, <date>)`
   and commit that alone, first.
4. **Read** every file listed under **Read first**, and nothing else unless you need it.
5. **Do** the numbered **Steps**, in order. Change only the files under **Files to change**.
6. **Check**: run every command under **Done when**. All must pass. Then run the whole test
   suite once (`AGENTS.md` has the command), unless the task changed only documents (`.md`
   files): then no tests are needed.
7. **Report**: fill in the task's **Report** (template below), set Status to
   `For review (<branch name>)`, commit, push your branch, and open a pull request to `main`
   titled `<ID>: <task title>`. If you cannot push or open a pull request, tell Beaux the
   branch name.
8. **Stop.** Do not merge. Do not start another task unless told to.

**When to stop early and report instead of finishing:** the steps don't match the code you
find; a check fails and the fix is outside the files you may change; the task needs a
decision (two reasonable ways to do it); you have changed more than about 200 lines; or you
have tried the same fix three times. A clear "I stopped because…" is a good result. A guess
that looks finished is a bad one.

**Report template** (keep it short; plain words):

```
Report (<tool>, <date>):
- Did: <what changed, one or two lines>
- Checks: <each "Done when" command and its result; whole suite: N passed, N skipped>
- Not done / unsure: <anything left, or "nothing">
- For Beaux to try on Windows: <what to click or run, or "nothing">
```

## What the fields mean

- **Who**: `any agent` = a small model can do it from the task text alone.
  `any agent that can run the tests` = it must have the repository checked out and set up
  as `AGENTS.md` says; an agent that can only edit files should not take it.
  `any agent on Beaux's PC` = needs Windows, the installed app, or a website the cloud can't
  reach. `Claude` = needs the project's history or careful judgement; others should not take it.
  `Beaux` = only a person can do it.
- **Size**: S = under an hour, one or two files. M = a few files, a few hours. Tasks larger
  than M are split before they are listed as Ready.
- **Status**: `Ready`, `In progress (who, date)`, `For review (branch)`, `Waiting (for what)`.

---

## Ready

### T-011 · Tests read and write text as UTF-8
- **Status:** Ready
- **Who:** any agent that can run the tests · **Size:** M
- **Why:** On Windows one test fails only because the test itself reads a file without
  saying it is UTF-8 (`tests/test_synth_compare.py`, line 14: a dash comes back as "?").
  About 36 places in `tests/` have the same habit.
- **Read first:** `tests/test_text_encoding.py`.
- **Files to change:** files in `tests/` only. **This task is the one exception to "never
  change a test": you may add an `encoding=` argument and nothing else.**
- **Steps:**
  1. In `tests/test_text_encoding.py`, change `FOLDERS = ["src/ectype", "tools"]` to
     `FOLDERS = ["src/ectype", "tools", "tests"]`.
  2. Run `python -m pytest -q tests/test_text_encoding.py`. It lists every place.
  3. For each `read_text()` add `encoding="utf-8"`. For each `write_text(...)` add
     `encoding="utf-8"` as the last argument. For each `run`/`Popen`/`check_output` with
     `text=True` add `encoding="utf-8", errors="replace"` after it. For each `open(...)` in
     text mode add `encoding="utf-8"`.
  4. Change no assertion, no test name, no expected value, and no line the test did not
     list. If a listed line is not a file being read or written (for example a function of
     Ectype's own that happens to be called `write_text`), leave it, and add it to `ALLOWED`
     in `tests/test_text_encoding.py` with a comment saying what it is.
- **Done when:**
  - `python -m pytest -q tests/test_text_encoding.py` passes.
  - `git diff` shows only added `encoding=` (and `errors=`) arguments, the one-line change
    to `FOLDERS`, and any `ALLOWED` lines from step 4.
  - The whole suite passes, with the same number of tests passed as before you began (run
    it first and note the number).
- **Report:**

### T-014 · `ectype strip-text`: take a bad OCR text layer out of a PDF
- **Status:** Ready
- **Who:** any agent that can run the tests · **Size:** M
- **Why:** Beaux has PDFs whose hidden OCR text (put there by a scanner or another program)
  is bad, and wants a copy with no text layer at all: the page images only. Command line
  only; nothing in the app's window.
- **What "hidden text" means:** in a PDF, text drawn in text mode 3 (the operator `3 Tr`) is
  invisible; OCR programs lay it over the page picture so it can be searched. Text in any
  other mode is visible type that is part of how the page looks.
- **The tests are already written:** `tests/test_striptext.py` (10 tests). They are skipped
  until `src/ectype/striptext.py` exists; your work is done when all 10 pass. **Do not
  change that file.** Read it first: it shows exactly what is wanted.
- **Read first:** `tests/test_striptext.py`; in `src/ectype/analyze.py`, the loop that
  starts `for operands, op in pikepdf.parse_content_stream(stream_owner):` (it shows how to
  follow `q`, `Q` and `Tr`); in `src/ectype/cli.py`, `cmd_data_folder` and the lines that
  begin `p = sub.add_parser("data-folder"` (the pattern for a command).
- **Files to change:** new `src/ectype/striptext.py`; `src/ectype/cli.py` (one new function
  and one new `add_parser` block, placed just before the `data-folder` ones); `README.md`
  (one line). Nothing else. No new dependency: `pikepdf` is already used.
- **Steps:**
  1. In `src/ectype/striptext.py` write
     `strip_text(src, dest=None, everything=False) -> dict`:
     - Open `src` with `pikepdf.open`. For each page, read its content with
       `pikepdf.parse_content_stream(page)`. Go through the operators in order, keeping the
       current text mode: it starts at 0; `Tr` sets it; `q` saves it and `Q` puts the saved
       one back.
     - Collect each text block (from `BT` to `ET`). The operators that show text are `Tj`,
       `TJ`, `'` and `"`. One is *hidden* when the text mode at that point is 3, or always
       when `everything` is true.
     - If every text-showing operator in a block is hidden, remove those operators (leave
       the rest of the block). If a block has both hidden and visible ones, leave the whole
       block unchanged and count it as `mixed` (the visible words' place depends on the
       hidden ones before them). Visible ones left are counted in `visible_left`.
     - If a page changed, write it back:
       `page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(instructions))`.
     - Do the same for each form on the page: every entry of the page's
       `/Resources` `/XObject` whose `/Subtype` is `/Form` (read it with
       `parse_content_stream(xobject)`, write it back with `xobject.write(data)`). Do each
       form once even if several pages use it (remember its `objgen`). Its text mode
       starts at 0.
     - Where to save: if `dest` is None, beside `src` as `<name> - no text.pdf`, or
       `<name> - no text (2).pdf`, `(3)` … if that exists. If `dest` is given and is the
       same file as `src`, raise `ValueError`; if it already exists, raise
       `FileExistsError`. Never write to `src`.
     - Save only if something was removed: to `<dest>.part` first, then `os.replace` it to
       `dest`. If nothing was removed, write no file.
     - Return `{"output": dest or None, "pages": …, "pages_changed": …, "removed": …,
       "visible_left": …, "mixed": …}` (`removed` counts text-showing operators removed).
  2. In `src/ectype/cli.py` add the command `strip-text`: one or more PDFs (`pdfs`,
     `nargs="+"`; do **not** call the argument `workspace`), `-o/--output` (only with one
     PDF; with more, say so and return 2), `--all` (sets `everything`). For each PDF print,
     in plain words: the result's path and how many of its pages had hidden text removed;
     or that the PDF has "no hidden text layer" and nothing was written; and, when
     `visible_left` is above 0, one line saying it also has visible text that was left and
     that `--all` removes it too. A PDF that doesn't exist: say so and return a number
     other than 0, with no Python traceback. Help text for `--all` must warn that it
     removes type that is part of how the page looks (Acrobat ClearScan and Ectype's own
     traced PDFs are made of such type).
  3. In `README.md`, in the code block under "Commands" that starts with
     `ectype languages [--all]`, add one line:
     `ectype strip-text book.pdf [more.pdf] [-o out.pdf] [--all]   # a copy without its hidden (OCR) text layer`
  4. Leave alone: password-protected PDFs (let pikepdf's error through; `cli.main` already
     turns it into a plain message), the PDF's title and other details, bookmarks, and
     fonts no longer used (a later task, if the files turn out too big).
- **Done when:**
  - `python -m pytest -q tests/test_striptext.py` gives `10 passed` (not skipped).
  - `git diff --stat main` shows only the three files above (and this task's lines here).
  - `python -m pytest -q tests/test_text_encoding.py` still passes, if that file exists.
  - The whole suite passes.
- **For the report, "For Beaux to try on Windows":**
  `ectype strip-text "D:\path\to\a book with bad OCR.pdf"`, then open the new
  "… - no text.pdf": it should look the same, and searching it should find nothing.
- **Report:**

### T-015 · Move the old build-phase write-ups into docs/history
- **Status:** Ready
- **Who:** any agent · **Size:** S · documents only: no setup and no tests needed
- **Why:** `docs/phase1.md`, `phase2.md`, `phase3.md` and `phase4-5.md` are results from
  October 2026, when the app was first built. They sit beside the current guides and look
  current. The original plan already lives in `docs/history/`; these belong with it.
- **Read first:** the four files' first lines; `README.md` (the line that starts "Early
  results, by build phase:").
- **Files to change:** the four files (moved, not edited apart from step 2); `README.md`
  (four links); any other `.md` file that links to them (step 3). Not `CLAUDE.md`.
- **Steps:**
  1. Move the four files with `git mv` into `docs/history/`, keeping their names.
  2. At the top of each, under its title, add one line:
     `> History: results from the first build (October 2026), kept as written.`
  3. Find every link to them: `grep -rn "phase[0-9]" --include="*.md" .` Change each link
     so it still opens the moved file. Links **inside** the four files to other documents
     (for example to `acrobat-baseline.md` or to pictures) must be fixed too, because the
     files are now one folder deeper (`../` in front).
  4. Change nothing else in any file.
- **Done when:**
  - `ls docs/phase*.md` finds nothing and `ls docs/history/` shows the four files and
    `original-plan.md`.
  - Every relative link in the four moved files and every link to them points to a file
    that exists (list each link and its target in your report).
- **Report:**

### T-016 · List where docs/idml.md no longer matches the app (a list only)
- **Status:** Ready
- **Who:** any agent · **Size:** S · documents only: no setup and no tests needed
- **Why:** `docs/idml.md` is the users' guide to languages, Kraken, training and the
  InDesign export. Parts were written several versions ago. Before anyone rewrites it, the
  manager needs a list of what is out of date. This task makes that list and changes
  nothing else.
- **Read first:** `docs/idml.md` (all of it); `README.md` (the section "The desktop app",
  which is current); the output of `ectype --help` if you can run it (if not, the
  "Commands" section of `README.md`, which is current).
- **Files to change:** one new file, `docs/idml-review-<date>.md`. Do **not** edit
  `docs/idml.md`.
- **Steps:**
  1. Go through `docs/idml.md` one heading at a time.
  2. For every statement about a menu, a button, a command, an option, a file name or a
     folder, check it against `README.md` (and `ectype --help`). Note it only if the two
     disagree, or if `README.md` describes something on the same subject that
     `docs/idml.md` leaves out.
  3. Write the new file as a table with four columns: the heading in `docs/idml.md`; the
     sentence there (quoted, shortened if long); what `README.md` or `--help` says instead
     (quoted); and "differs" or "missing".
  4. Do not judge which is right, do not propose wording, and do not list matters of style.
     If you are not sure two statements disagree, list it and write "unsure" in the last column.
- **Done when:** the file exists; every row quotes both sources; `git diff --stat main`
  shows only that new file (and this task's lines here).
- **Report:**

### T-017 · Report which parts of the program the tests don't reach (a list only)
- **Status:** Ready
- **Who:** any agent that can run the tests · **Size:** S
- **Why:** The manager writes test tasks for other agents. To choose them well it needs to
  know which parts of the program no test runs. This task measures that and writes it
  down. It changes no code and no test.
- **Read first:** `AGENTS.md` (setup, running the tests).
- **Files to change:** one new file, `docs/test-coverage-<date>.md`. Nothing else: in
  particular do **not** add `pytest-cov` to `pyproject.toml` (install it for yourself only).
- **Steps:**
  1. `python -m pip install pytest-cov` (in your own environment only).
  2. Run the whole suite with coverage:
     `python -m pytest -q --cov=ectype --cov-report=term-missing` (with
     `QT_QPA_PLATFORM=offscreen`, as `AGENTS.md` says). Save the output.
  3. In the new file write: the date, your Python version, the totals (tests passed,
     skipped; overall percentage); then a table of every file in `src/ectype/` with its
     number of lines, lines not run, and percentage, sorted with the lowest percentage first.
  4. Below the table, for each of the ten files with the most lines not run, list the
     **functions** that no test runs at all (name and line number). Find them from the
     "Missing" line ranges: a function counts if every line of its body is missing.
  5. Say which tests were skipped and why (the skip reasons pytest prints with `-rs`).
  6. Do not write tests, and do not say what should be tested: the list is the result.
- **Done when:** the file exists with the totals, the table and the ten lists;
  `git diff --stat main` shows only that new file (and this task's lines here); nothing in
  `pyproject.toml` changed.
- **Report:**

---

## For Claude

These need the project's history or judgement about risk. Other agents: do not take them.

### T-012 · The tests run on Windows too, automatically
- **Status:** Ready
- **Who:** Claude · **Size:** M
- **Why:** The app is Windows-only but its tests run on Linux only. T-006 (a run on Beaux's
  PC, `docs/windows-tests-2026-11-16.md`) could not give a clean list: that PC had Python
  3.14 and no Tesseract, so 78 of 85 failures were the missing setup. A `windows-latest`
  job in `ci.yml` (Python 3.12, Tesseract as `release.yml` installs it, the test fonts:
  Liberation, DejaVu, FreeFont; FriBiDi for Pillow's raqm) gives the real list on every
  push. Start it allowed to fail, fix what it finds, then make it required.
- **Leads from T-006 to check there** (may be the setup, may be real): the traced-font tests
  ended in an access violation inside `trace.trace_bitmap` (vtracer) on Python 3.14: does it
  happen on 3.12, which the app ships? `test_buttons_wrap_and_stay_visible_in_a_narrow_window`
  (83 > 83: the buttons didn't wrap); `test_latin_lines_with_italic_words` (29 of 60).

### T-013 · Ectype IDML's notes shown with the right letters
- **Status:** Ready
- **Who:** Claude · **Size:** S
- **Why:** `converter.convert` reads Ectype IDML's output with Windows' default encoding
  (`Popen(text=True)` with no encoding). Its notes quote running heads, which can be Hebrew
  or Greek. Which encoding the packaged converter writes must be found out first (it is a
  PyInstaller program; Ectype IDML's side may need `PYTHONUTF8` or to write UTF-8 itself),
  so this isn't a one-line change. Then remove the line for it in
  `tests/test_text_encoding.py`'s `ALLOWED`.

### T-008 · The Windows crash when clicking a word in Review
- **Status:** Waiting (for a crash report from Beaux's PC made by 0.11.1 or later, which
  names the page and the word clicked)
- **Who:** Claude, with Beaux · **Size:** unknown
- **Why:** "Windows fatal exception: access violation" in Review (0.11.0, the Hebrew
  grammar). Not reproduced on Linux. Background: search `CLAUDE.md` for "access violation".

### T-009 · Real error rates from checked pages
- **Status:** Waiting (for Beaux to check the transcriptions, see below)
- **Who:** Claude · **Size:** M
- **Why:** Decisions such as "should Kraken be the main reader" need error rates measured
  against pages a person has checked, on pages kept out of training. Report letters and
  words wrong; for Hebrew, consonants apart from points and accents; by reader (Tesseract,
  Tesseract + Kraken). Extend the existing tools (`compare`, `tools/kraken_eval.py`); don't
  build a second framework. Truth files live in `corpus/truth/`.

---

## Waiting for Beaux

Things only Beaux can do. Each unblocks the task named.

- [ ] **Check the transcription** in `corpus/truth/` (the Hebrew grammar, PDF page 5; made by
  Claude from the scan, never checked). Unblocks T-009.
- [ ] **Transcribe or confirm two or three pages of Driver's *Treatise*** (one with pointed
  Hebrew, one with footnotes and Greek). Unblocks T-009.
- [ ] **Send the next crash report** if Review crashes again (at its next start the app says
  it kept a report and offers Open folder; the file is `crash-<date>-<time>.txt`). Unblocks T-008.
- [ ] **After the next release: Training → Train from fonts → Arabic**, and say whether it
  downloaded the Arabic Bible (its download was switched on in T-005) or said "(no text)".
- [ ] **Try a Hebrew manuscript model** (BiblIA, from Zenodo) in Manuscript Mode.

---

## Later (not ready: needs a decision or a measurement first)

The manager turns one of these into Ready tasks when its condition is met. Workers: not yours.

- **Kraken as the main reader.** Beaux wants it ("more accurate and much stronger"). First
  T-009's measurements on the Treatise and Young. Also to settle: the engine is a ~290 MB
  download, and traced PDFs need each letter's place.
- **Party** (the page-reading model). Needs its own package; check speed on CPU, size, and
  whether it gives each letter's place, before offering it.
- **Ectype against Acrobat on Hebrew.** Beaux finds Ectype's traced Hebrew better than
  Adobe's. A fixed comparison (same scans through both: look, search and copy, file size,
  time) would protect that as the code changes.
- **Better reading of Driver's *Treatise*.** Train Hebrew on fonts like Driver's and measure
  on the checked pages.
- **Rashi script.** Beaux has trained Hebrew on at least one Rashi font (Train from fonts),
  and Ectype read the Rashi type in the Ben Ze'ev grammar. Not yet measured: a checked page
  of Rashi type would say how well, and whether more Rashi fonts or corrections are needed.
- **Syriac with vowels.** The Peshitta text used for training has none.
- **Manuscripts:** straightening curved pages (Beaux: not now); training on Intel graphics.
- **An English known text** for Train from fonts (Beaux: only a maybe).

## Not to be done

Beaux has already said no, or not now, to these. Do not propose or build them. The list and
the reasons are in `CLAUDE.md` under "Decisions Beaux made about proposals". In short: no
applying one correction everywhere the same misreading occurs; no faint boxes for words
dropped as noise; no work on lone punctuation left as image; no XML/TEI export, library
search index, or "combine text files"; no whole-page Kraken reading as the default for
printed books; no platform other than Windows.

---

## Done

Newest first. The manager moves a task here when its pull request is merged, as one line,
and deletes the task's full text above. Older history: `CLAUDE.md`, "What each version brought".

- 2026-11-17 · T-007 · Training's kept results are judged by content (fonts' set lines,
  matched editions, Manuscript Mode's page images and hand models, a model's kept checking
  errors); what was kept under the old stamps is accepted once and rewritten. Two places
  deliberately stay on file times (see `CLAUDE.md`). Done by a cheaper Claude agent, checked
  by the manager.
- 2026-11-16 · T-010 · Other programs' output is read as UTF-8 in six places, and
  `tests/test_text_encoding.py` keeps it so (Copilot on Beaux's PC).
- 2026-11-16 · T-004 · README says what the app does now and lists every module (ChatGPT).
- 2026-11-16 · T-003 · `docs/releasing.md` matches both build jobs (ChatGPT, second try).
- 2026-11-16 · Kraken models download from Zenodo in the installed app (Beaux: the model
  they read with was installed through Ectype), so the download check works on the real thing.
- 2026-11-16 · `corpus/arb-vd_vpl.txt` and `.xml` removed (Beaux: uploaded, not needed).
- 2026-11-16 · T-006 · Tests run on Beaux's PC and written up (`docs/windows-tests-2026-11-16.md`).
  The PC lacked Python 3.12 and Tesseract, so most failures were the setup; led to T-010 to T-012.
- 2026-11-16 · T-005 · The Arabic Smith–Van Dyck Bible's download is switched on (its
  fingerprint recorded).
- 2026-11-16 · T-001 · README lists every command.
- 2026-11-16 · T-002 · README and `docs/idml.md` say Ectype IDML, not docx-to-idml.
- 2026-11-16 · This file and `AGENTS.md` set up; the original plan moved to `docs/history/`.

---

## The manager's guide

For the Claude session that keeps this file. Workers can skip this.

**At the start of a session:** read this file's Ready, For Claude and Waiting sections; list
open pull requests whose title starts with a task ID; check any task marked `In progress`
for more than a few days (ask Beaux, then set it back to Ready).

**Checking a finished task** (Status `For review`):
1. Read the pull request's whole diff, not the report. Did it change only the files allowed?
2. Run the task's "Done when" commands yourself, and the whole suite.
3. Look for the usual cheap-model faults: a test changed or skipped to pass; an invented
   fact in a doc; an error swallowed with a bare `except`; code copied instead of reused;
   something outside the task "tidied".
4. Good: add what a later session needs to `CLAUDE.md` (same pull request), move the task to
   Done, tell Beaux in plain words what it means for them and what to try. Not good: either
   fix it yourself if that is quicker than explaining, or set it back to Ready with a
   **Review note** saying exactly what was wrong, so the next attempt doesn't repeat it.
5. Never merge. Beaux merges.

**Writing a task a small model can do.** Assume the worker has not read `CLAUDE.md`, will not
remember anything, and will do literally what the words say.
- One outcome per task; S or M only. If you can't write "Done when" as commands whose
  result is yes or no, the task isn't ready: split it or keep it for Claude.
- Name the files to read and the files to change. Give function names, not "the part that…".
- Put facts the worker needs in the task (names, addresses, numbers); don't send it to
  search for them.
- Say what to leave alone; that is where small models wander.
- Ask of the worker's machine only what the task needs. (T-003, one document, was stopped
  by a "Python must be 3.12" rule that was both needless for a document and wrong: 3.10 to
  3.13 all work.) Mark document tasks "documents only: no setup and no tests needed".
- Say what the worker's machine must have, and that it stops if it doesn't. (T-006 was run
  on Python 3.14 with no Tesseract: 78 failures that meant nothing. T-001 was done by an
  agent that couldn't run a command at all.)
- Before writing a task that changes a value, search `tests/` for that value. (T-005's
  check could only pass by changing a test that said the download was off; the worker had
  to choose between two rules.) If a test must change, say so in the task and say how.
- For code tasks, write the tests yourself first and make "they pass" the check. For a new
  module, commit the test file with `pytest.importorskip("ectype.<module>")` at its top:
  it is skipped until the worker adds the module, and the worker is told not to touch it
  (T-014, `tests/test_striptext.py`). Try the tests against a rough version of your own
  before committing them, then throw the rough version away.
- Keep for Claude: anything touching kept data on Beaux's PC (pools, models, stamps,
  projects, the data folder), downloads and their checks, the updater and installer, threads
  and stopping, Qt object lifetimes, and anything whose check is "Beaux looks at it".

**What suits which worker.** Small models do well: documentation brought in line with the
code, tests for behaviour that already exists, mechanical renames, running something and
reporting. An agent on Beaux's PC can do what the cloud can't: run on Windows, reach blocked
sites (ebible.org, Zenodo), try the installed app. Give those to it.

**Keeping the file short.** Ready should hold what could be started today, about ten tasks
at most. Finished tasks become one line. Ideas with no decision go under Later, one
paragraph each. When something here stops being true, change it in the same commit as the
work that changed it.
