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
   suite once (`AGENTS.md` has the command).
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

### T-003 · docs/releasing.md: match what the release actually does
- **Status:** Waiting (for Python 3.12 in the test environment)
- **Who:** any agent · **Size:** S
- **Why:** `docs/releasing.md` describes the first release (0.1.0). The release now also
  builds a second download, the Kraken engine, and tests more.
- **Read first:** `docs/releasing.md`; `.github/workflows/release.yml` (read every step's
  `name:` line and its comments).
- **Files to change:** `docs/releasing.md` only.
- **Steps:**
  1. Rewrite the numbered list under "The workflow then:" so it has one item per step of
     the first job in `release.yml`, in the same order, each in one plain sentence.
  2. After it, add a short second list for the second job (the Kraken engine): what it
     builds, how it is tested, and the name of the file it attaches
     (`Ectype-OCR-Kraken-<version>-win64.zip`).
  3. Add one paragraph: the release offers two files, the installer and the Kraken engine
     zip; the app downloads the engine itself when the user asks for Kraken.
  4. Change the example version numbers `v0.1.0` / `0.1.0` to `vX.Y.Z` / `X.Y.Z`.
  5. Add one sentence near the top: "Beaux pushes the release tags; agents never do."
  6. Keep every other paragraph as it is unless `release.yml` contradicts it; if it does,
     fix the sentence and name it in your report.
- **Done when:**
  - Each step `name:` in `release.yml` is covered by an item in the doc (list the pairing
    in your report).
  - `grep -n "0\.1\.0" docs/releasing.md` prints nothing.
- **Report:**

Report (ChatGPT GitHub, 2026-11-16):
- Did: No change to `docs/releasing.md`; stopped at the required setup check.
- Checks: `python --version`: 3.13.5 (required: 3.12); `tesseract --version`: 5.5.0; task checks and whole suite not run.
- Not done / unsure: Install and activate Python 3.12 in the test environment, then resume T-003.
- For Beaux to try on Windows: nothing.
### T-004 · README: describe the project as it is now, not by build phases
- **Status:** Ready
- **Who:** any agent · **Size:** M
- **Why:** The README opens with "Phases 0–5 are done, and Phase 6 is under way", which was
  true in October 2026. The app is far past that, and readers can't tell what it does today.
- **Read first:** `README.md` (top paragraph, "Status", and the "Layout" section at the end);
  the first docstring line of each file in `src/ectype/*.py`.
- **Files to change:** `README.md` only.
- **Steps:**
  1. Leave the first paragraph (it already points to `TASKS.md`) as it is.
  2. Replace the paragraph that starts "**Status: Phases 0–5 are done**" and its four
     bullet points with one paragraph headed "**What it does**" that lists, in one sentence
     each: the four things Run can make (take them from the README's own list under "The
     desktop app": Traced, Substitute, Text layer only, Plain text only); Review; IDML
     export; Kraken; training; Manuscript Mode. Use only facts already stated lower in the
     README. Then one line: "Early results, by build phase:" followed by the four existing
     links (`docs/phase1.md` … `docs/phase4-5.md`) on one line.
  3. In "Layout", remove the phrases "are the Phase 0 tools", "are Phase 1", "are Phase 2",
     "is Phase 3", "are Phase 4", "is Phase 5", "are Phase 6" and say instead what each
     group does, in a few words, from the modules' docstrings.
  4. Still in "Layout": list every `.py` file in `src/ectype/` that the section doesn't
     mention, each with a few words taken from the first line of its docstring. Do not
     describe a module from its name alone; open it.
  5. Do not change the "Commands", "Install" or "The desktop app" sections.
- **Done when:**
  - `grep -n "Phase [0-9]" README.md` prints nothing.
  - For every file `src/ectype/<name>.py` (except `__init__.py`), `<name>` appears in the
    "Layout" section.
- **Report:**

### T-010 · Program output read as UTF-8, with a test that keeps it so
- **Status:** Ready
- **Who:** any agent that can run the tests · **Size:** S
- **Why:** On Windows, Python reads another program's output in the old Windows encoding
  unless told otherwise, so an accented letter or a dash in it comes back wrong. Six places
  still don't say which encoding to use. A new test will catch any that are added later.
- **Read first:** the six lines named in step 2.
- **Files to change:** `src/ectype/resources.py`, `src/ectype/scripts.py`,
  `src/ectype/synth.py`, `tools/kraken_eval.py`, `tools/make_fingerprints.py`; new file
  `tests/test_text_encoding.py`. Nothing else.
- **Steps:**
  1. Create `tests/test_text_encoding.py` with exactly this content:

     ```python
     """Text files are read and written as UTF-8, said outright. Left to itself, Python on Windows
     uses the old ANSI code page, so a dash or a Hebrew letter comes back as "?" or stops the run."""
     import ast
     from pathlib import Path

     ROOT = Path(__file__).resolve().parents[1]
     FOLDERS = ["src/ectype", "tools"]
     # Places left as they are on purpose: (file, function called). Each needs a reason.
     ALLOWED = {
         ("src/ectype/converter.py", "Popen"),  # Ectype IDML's own output: its encoding is decided in T-013
     }


     def _mode(call):
         if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
             return str(call.args[1].value)
         for k in call.keywords:
             if k.arg == "mode" and isinstance(k.value, ast.Constant):
                 return str(k.value.value)
         return "r"


     def offenders(path):
         found = []
         for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
             if not isinstance(node, ast.Call):
                 continue
             given = {k.arg for k in node.keywords}
             f = node.func
             name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
             if "encoding" in given:
                 continue
             if name in ("read_text", "write_text"):
                 # a file's own read_text / write_text, not Ectype's functions of the same name
                 ours = isinstance(f, ast.Attribute) and ast.unparse(f.value).endswith("project")  # Project.write_text
                 if isinstance(f, ast.Attribute) and not ours and given <= {"errors", "newline"} and len(node.args) == (name == "write_text"):
                     found.append((node.lineno, name))
             elif name == "open" and isinstance(f, ast.Name) and "b" not in _mode(node):
                 found.append((node.lineno, name))
             elif name in ("run", "Popen", "check_output") and given & {"text", "universal_newlines"}:
                 found.append((node.lineno, name))
         return found


     def test_text_is_read_and_written_as_utf8():
         bad = []
         for folder in FOLDERS:
             for path in sorted((ROOT / folder).rglob("*.py")):
                 rel = path.relative_to(ROOT).as_posix()
                 bad += [f"{rel}:{line} {name}" for line, name in offenders(path) if (rel, name) not in ALLOWED]
         assert not bad, "no encoding given:\n" + "\n".join(bad)
     ```

  2. Run `python -m pytest -q tests/test_text_encoding.py`. It fails and lists six places
     (line numbers may have moved a little): `src/ectype/resources.py:96`,
     `src/ectype/scripts.py:119`, `src/ectype/synth.py:78`, `tools/kraken_eval.py:67` and
     `:105`, `tools/make_fingerprints.py:117`. If it lists others, stop and report them.
  3. In each of the six calls, which all have `text=True`, add
     `encoding="utf-8", errors="replace"` right after `text=True`. Change nothing else in
     the call.
  4. Do **not** change `src/ectype/converter.py` (it is left out of the test on purpose;
     T-013), and do not change the test you created.
- **Done when:**
  - `python -m pytest -q tests/test_text_encoding.py` passes.
  - `git diff --stat` shows only the five files above changed and the one test file added.
  - The whole suite passes.
- **Report:**

### T-011 · Tests read and write text as UTF-8
- **Status:** Waiting (for T-010 to be merged: it adds the test this task extends)
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

---

## For Claude

These need the project's history or judgement about risk. Other agents: do not take them.

### T-007 · Training's kept results judged by content, not file times
- **Status:** Ready
- **Who:** Claude · **Size:** M (split by file before starting)
- **Why:** Everything else in Ectype reuses a kept result only when its inputs' contents are
  unchanged (`stamps.py`). Training still trusts file times and hand-raised version numbers:
  `pool.py` (`items`), `synthtrain.py` (font stamps, `SCRIPT_VERSION`, `VERSION`,
  `UNPOINTED_VERSION`), `trainall.py`, `training.py`, `manuscript._stamp`.
- **Care:** a new stamp format must not make Beaux's existing work look stale. Their Hebrew
  pool has 112,000+ lines set from fonts (over an hour to remake) and Greek 149,600. Accept
  the old stamp once and rewrite it in the new form, as `ocr.stamp` does for readings made
  before sidecars. Model files are large: hash them once and keep the digest, don't hash on
  every read.

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
- Say what the worker's machine must have, and that it stops if it doesn't. (T-006 was run
  on Python 3.14 with no Tesseract: 78 failures that meant nothing. T-001 was done by an
  agent that couldn't run a command at all.)
- Before writing a task that changes a value, search `tests/` for that value. (T-005's
  check could only pass by changing a test that said the download was off; the worker had
  to choose between two rules.) If a test must change, say so in the task and say how.
- For code tasks, write the failing test yourself first, or describe it exactly, and make
  "the test passes" the check.
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
