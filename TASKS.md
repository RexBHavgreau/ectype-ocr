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
  `any agent on Beaux's PC` = needs Windows, the installed app, or a website the cloud can't
  reach. `Claude` = needs the project's history or careful judgement; others should not take it.
  `Beaux` = only a person can do it.
- **Size**: S = under an hour, one or two files. M = a few files, a few hours. Tasks larger
  than M are split before they are listed as Ready.
- **Status**: `Ready`, `In progress (who, date)`, `For review (branch)`, `Waiting (for what)`.

---

## Ready

### T-001 · README: add the commands it doesn't mention
- **Status:** Ready
- **Who:** any agent · **Size:** S
- **Why:** The README's "Commands" section was written before several commands existed, so
  users can't find them.
- **Read first:** `README.md` (the "Commands" section); the output of `ectype --help`,
  `ectype kraken --help`, `ectype data-folder --help`, `ectype free-space --help`,
  `ectype update --help`.
- **Files to change:** `README.md` only.
- **Steps:**
  1. Run `ectype --help` and list every command name.
  2. For each command name, search the README's "Commands" section. The ones known to be
     missing are `kraken` (with `list`, `available`, `add`, `remove`), `data-folder`,
     `free-space` and `update`; add any others you find missing.
  3. Add them to the second code block in "Commands" (the one that starts with
     `ectype languages [--all]`), one line each, in the same style: the command with its
     main options, then `#` and a short description. Take options and descriptions from each
     command's `--help`; do not invent any.
  4. Also add the option `--kraken LANG=MODEL` to the `ectype run` line's explanation
     paragraph, in one sentence, using the wording of `ectype run --help`.
- **Done when:**
  - Every command printed by `ectype --help` appears somewhere in `README.md`
    (check each with `grep -c "ectype <name>" README.md`).
  - No other part of the README changed (`git diff --stat` shows only `README.md`, and the
    diff only adds lines in "Commands" and one sentence after the `ectype run` block).
- **Report:**

### T-002 · Docs: docx-to-idml is now called Ectype IDML
- **Status:** For review (task/T-002-ectype-idml)
- **Who:** any agent · **Size:** S
- **Why:** The companion program was renamed at its version 1.0.0 (November 2026). The app
  already uses the new name; `README.md` and `docs/idml.md` still use the old one and link
  to the old address.
- **Read first:** `README.md`, `docs/idml.md`. Facts to use: new name **Ectype IDML**; new
  address `https://github.com/RexBHavgreau/ectype-idml`; program file
  `Ectype-IDML-X.Y.Z.exe`; run from source with `python -m ectype_idml` (was
  `python -m docx2idml`); settings folder `%APPDATA%\Ectype IDML`; the button in Export IDML
  is "Find Ectype IDML…".
- **Files to change:** `README.md`, `docs/idml.md` only.
- **Steps:**
  1. In each file, the **first** mention becomes "Ectype IDML (called docx-to-idml before
     its 1.0.0)", linked to the new address.
  2. Every later mention of the program becomes "Ectype IDML". Update links and the
     `python -m` command as given above.
  3. **Leave unchanged:** the environment variable names `ECTYPE_DOCX2IDML` and
     `ECTYPE_DOCX2IDML_SOURCE` (they kept their names); the folder name `src/ectype/idmlkit/`;
     the file name `LICENSE-docx-to-idml`; and sentences about the past that give a date
     (for example "The first version of this export (October 26)…"): there, write
     "docx-to-idml (now Ectype IDML)".
  4. Do not touch `CREDITS.md`, `CLAUDE.md`, or any `.py` file.
- **Done when:**
  - `grep -n "RexBHavgreau/docx-to-idml" README.md docs/idml.md` prints nothing.
  - `grep -n "python -m docx2idml" README.md docs/idml.md` prints nothing.
  - Every remaining `docx-to-idml` in the two files is one of the cases in step 1 or 3
    (list them in your report).
- **Report:**
  Report (ChatGPT, 2026-11-16):
  - Did: Updated `README.md` and `docs/idml.md` to identify the companion as Ectype IDML, use its new repository address, and document its executable, source command, settings folder and discovery button. Kept `ECTYPE_DOCX2IDML` and `src/ectype/idmlkit/` unchanged.
  - Checks: `grep -n "RexBHavgreau/docx-to-idml" README.md docs/idml.md` — no matches; `grep -n "python -m docx2idml" README.md docs/idml.md` — no matches. Remaining `docx-to-idml`: `README.md:52` and `docs/idml.md:182` (first mentions); `docs/idml.md:192` and `docs/idml.md:233` (dated historical references). Whole suite: 275 passed, 2 skipped (`pytest -q`; [GitHub Actions run](https://github.com/RexBHavgreau/ectype-ocr/actions/runs/43937235639)).
  - Not done / unsure: No local checkout was available; the full suite was verified in GitHub Actions instead.
  - For Beaux to try on Windows: nothing; documentation only.

### T-003 · docs/releasing.md: match what the release actually does
- **Status:** Ready
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

### T-004 · README: describe the project as it is now, not by build phases
- **Status:** Ready
- **Who:** any agent · **Size:** M
- **Why:** The README opens with "Phases 0–5 are done, and Phase 6 is under way", which was
  true in October 2026. The app is far past that, and readers can't tell what it does today.
- **Read first:** `README.md` (top paragraph, "Status", and the "Layout" section at the end);
  the first docstring line of each file in `src/ectype/*.py`.
- **Files to change:** `README.md` only. Do T-001 and T-002 first, or work around their lines.
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

### T-005 · Arabic: record the fingerprint of the Van Dyck Bible download
- **Status:** Ready
- **Who:** any agent on Beaux's PC (the cloud can't reach ebible.org) · **Size:** S
- **Why:** Ectype only downloads files whose exact size and SHA-256 are on record. The
  Arabic Smith–Van Dyck Bible has no record yet, so that download is switched off and Arabic
  trains from the Qurʾān alone.
- **Read first:** `src/ectype/texts.py`: the comment above `VAN_DYCK_URL`, and the functions
  `_cached` and `_van_dyck`.
- **Files to change:** `src/ectype/texts.py` (one line and its comment); `CREDITS.md` (in the
  Van Dyck row, remove only the words "; its download is off until a fingerprint is recorded
  for it").
- **Steps:**
  1. Download `https://ebible.org/Scriptures/arb-vd_vpl.zip` to a temporary folder outside
     the repository.
  2. Check it is the right file: in Python, read its bytes and call
     `ectype.texts._van_dyck(data)`. It must return more than 30,000 verses, and the first
     must be Genesis 1:1 in Arabic (it begins "فِي الْبَدْءِ" or the same words without vowel
     marks). If not, stop and report what you got.
  3. Work out the file's size in bytes and its SHA-256 (Python: `len(data)` and
     `hashlib.sha256(data).hexdigest()`).
  4. Set `VAN_DYCK_FINGERPRINT = {"size": <size>, "sha256": "<sha256>"}` in `texts.py`, and
     shorten the comment above it to say the download is checked against this fingerprint,
     recorded on <date>.
  5. Download the file a second time and confirm the size and SHA-256 are the same. If they
     differ (the site rebuilds the zip), undo step 4, stop and report both values.
- **Done when:**
  - `python -m pytest -q tests/test_verify.py tests/test_scripts_train.py` passes.
  - In Python, with `ECTYPE_DATA_DIR` set to a new empty temporary folder,
    `ectype.texts._cached("van-dyck", print)` downloads the text and returns more than
    30,000 lines.
- **Report:**

### T-006 · Run the test suite on Windows and report what fails
- **Status:** Ready
- **Who:** any agent on Beaux's PC · **Size:** S
- **Why:** The automatic tests run on Linux only, but the app is used on Windows. Nobody has
  a list of which tests fail there. This task only makes the list; it fixes nothing.
- **Read first:** `AGENTS.md` (setup on Windows).
- **Files to change:** none in `src/` or `tests/`. Write the result to a new file
  `docs/windows-tests-<date>.md`.
- **Steps:**
  1. Set up as `AGENTS.md` says. Note the Windows version, Python version and Tesseract
     version (`tesseract --version`).
  2. Run `python -m pytest -q -p no:cacheprovider` with `QT_QPA_PLATFORM=offscreen` and
     with `ECTYPE_DATA_DIR` set to a new empty temporary folder (so Beaux's real data folder
     is never touched). Save the full output.
  3. In the new file, write: the versions; the totals (passed, failed, skipped); then for
     each failed test its name and the last line of its error. Group failures that have the
     same error line.
  4. Do **not** change any code or test, even if a fix looks obvious.
- **Done when:** the file exists with the totals and one entry per failure (or says "no
  failures").
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
- [ ] **Try a Kraken model download from Zenodo** in the installed app and say whether it
  worked (the download check has never met the real Zenodo).
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
- **Rashi script.** No stock model; needs transcribed pages to train one.
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
