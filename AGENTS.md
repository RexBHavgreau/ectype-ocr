# Ectype OCR: instructions for every coding agent

This file is for any AI coding agent working in this repository (Claude, GitHub Copilot, Qwen,
or another). It is short on purpose. Read all of it before you change anything.

## What this project is

Ectype OCR is a Windows desktop app and command-line tool, written in Python, that reads
scanned books (OCR) and writes PDFs, text files and InDesign files. The code is in
`src/ectype/`, the tests in `tests/`. The owner, Beaux, is an editor and scholar, not a
programmer.

## What you do here

1. You work on **one task** from [`TASKS.md`](TASKS.md). Beaux or the managing agent tells
   you which one (for example "do T-003"). If nobody named a task, take the first task under
   "Ready" in `TASKS.md` whose **Who** line fits you, and nothing else.
2. Follow the steps under "How to do a task" in `TASKS.md` exactly.
3. If the task is unclear, or turns out bigger than it says, **stop and report**. Do not guess
   and do not widen the task.

## Setup (only for tasks that change code or tests)

On Linux (the same as the project's automatic tests):

```
sudo apt-get update
sudo apt-get install -y tesseract-ocr libleptonica-dev fonts-liberation fonts-dejavu-core fonts-freefont-ttf libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3
pip install -e ".[dev,gui]"
```

On Windows (PowerShell, in the repository's folder):

```
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,gui]"
tesseract --version
```

**Which Python.** Ectype runs on Python 3.10, 3.11, 3.12 and 3.13; any of them is fine.
(3.12 is only what the released app is built with, so `py -3.12` above may be `py -3.13`
and so on.) Python 3.14 is not ready: some tests crash on it on Windows.

**Check before you run the tests.** `python --version` must say 3.10 to 3.13, and
`tesseract --version` must print a version (with English installed). If not, **stop and
tell Beaux what to install**: without them most tests fail for that reason alone and the
result means nothing.

**Tasks that only change documents** (their "Files to change" lists only `.md` files) need
none of this setup and no test run. Do them with whatever you have, and say in your report
that no tests were run because only documents changed.

## Running the tests

```
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

(On Windows PowerShell: `$env:QT_QPA_PLATFORM="offscreen"; python -m pytest -q`.)

- The whole suite takes about five minutes (about 270 tests). While working, run only the test file for the code
  you changed, for example `python -m pytest -q tests/test_review.py`. Run the whole suite
  once before you finish.
- Some tests skip themselves when a tool is missing (Kraken, the Ectype IDML converter, Java).
  Skipped tests are fine. Failed tests are not.
- If a test already fails before you change anything, say so in your report. Do not fix it
  unless your task says to.

## Rules (these are not negotiable)

1. **Never** change, delete, skip or weaken a test to make it pass. If a test seems wrong,
   stop and report.
2. **Never** modify anything in `corpus/` (the test scans).
3. **Never** push to `main`, merge a pull request, force-push, or create a release tag.
4. Do not add a dependency (anything new in `pyproject.toml`) unless the task says to.
5. Change only the files the task names. If you think another file must change, stop and
   report why.
6. Do not edit `CLAUDE.md`. Do not edit `TASKS.md` except the one task you are doing (its
   **Status** line and its **Report**).
7. Never give OpenCV a file path (`cv2.imread`, `cv2.imwrite`): use
   `ectype.imagefiles.imread` / `imwrite`. Paths with accented letters fail on Windows.
8. Write text files with `encoding="utf-8"`; list, box and `.gt.txt` files also with
   `newline="\n"`.
9. No `pickle` anywhere in `src/`.
10. Messages the user sees are in plain English with no programming words. Copy the tone of
    the messages already in the file you are editing.
11. Commit messages: one plain sentence saying what changed and why. Do not put the name of
    an AI model in commit messages, pull requests or code comments.

## Where things are

| You need | Look in |
| --- | --- |
| The task list and how to do a task | `TASKS.md` |
| What the app does, for users | `README.md`, `docs/idml.md` |
| Why something is the way it is (long; search it, don't read it all) | `CLAUDE.md` |
| How releases are made | `docs/releasing.md` |
| The original 2026 design (history only, not current) | `docs/history/original-plan.md` |

`CLAUDE.md` is the project's memory: about 900 lines of past problems, causes and decisions.
You do not need to read it. When a task depends on something in it, the task says which words
to search for.
