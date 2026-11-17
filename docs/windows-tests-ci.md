2026-11-17: the test suite on Windows (the `windows` job in `.github/workflows/ci.yml`)

## Setup the job uses

- GitHub's `windows-latest` runner, Python 3.12.10 (the version release.yml builds with), Qt offscreen
  (`QT_QPA_PLATFORM=offscreen`), `PYTHONFAULTHANDLER=1`.
- Tesseract 5.5.3 from Chocolatey (English and osd only), `ECTYPE_TESSERACT` set. Tesseract's training
  tools (`unicharset_extractor`, `combine_lang_model`, `merge_unicharsets`) from MSYS2 UCRT64, as release.yml does.
- FriBiDi from MSYS2 copied beside Python and Pillow, so Pillow's raqm layout works (raqm True).
- Fonts: GNU FreeFont 20120503, Liberation 2.1.5, DejaVu 2.37, put in the user's font folder (downloads retried).
- Ectype IDML cloned and run as `python -m ectype_idml`; Temurin Java 17 with the element-attribute limit lifted.
- `pip install -e ".[dev,gui]"`: numpy 2.5.5, opencv 5.0.0.93, Pillow 13.0.0, PySide6 6.12.0, vtracer 0.6.15.
- The job was `continue-on-error: true` until the suite passed whole; it is required now.

## Last run

Run 44006436508 (commit cc6abc0), 8 minutes in the Tests step:
https://github.com/RexBHavgreau/ectype-ocr/actions/runs/44006436508

289 passed, 1 failed, 3 skipped, 0 crashed.

Skipped (not Windows faults): `test_lept.py` (Leptonica not installed here), two in `test_kraken.py`
(need the Kraken engine and a model; the Linux `kraken` job runs them).

## The one failure (fixed afterwards: see the end)

`tests/test_gui.py::test_buttons_wrap_and_stay_visible_in_a_narrow_window`
- Last line: `assert 83 > 83` ("the buttons wrapped onto more rows").
- Cause: the main window cannot be made narrower than 1582 px on this runner (Linux offscreen: 771 px), so
  resizing to 520 changes nothing and the button row has the same height at both widths. The job's
  diagnostic shows the widgets that set that minimum: the Review tabs. In `ManuscriptReviewTab` a `QLabel`
  has a minimum width of 1116 px and in `ReviewTab` a label inside a widget has 1032 px (the labels are
  built in `gui_manuscript.py` around lines 98-100 and in the Review tab; the text is long and the label
  does not wrap). The runner's Qt font is "Sans Serif 9", which on Windows falls back to a wider face than
  on Linux, so every unwrapped label is wider.
- Kind: the program (a label that cannot wrap makes the window's minimum width follow its text), shown by
  the test on a machine whose font is wide. Whether Beaux's Segoe UI also gives a minimum above 520 is not
  known; the buttons themselves do wrap (3 rows at 1582 px). The fix would be word wrap (or an elided label)
  on those labels; not done here, since it is a layout change in `src/`.

## Leads from the first Windows run (docs/windows-tests-2026-11-16.md)

- Access violation in vtracer (`trace.trace_bitmap`, `tests/test_traced.py`): did not happen on Python 3.12
  (all traced tests passed). So it belongs to Python 3.14 (or that PC), not to the code.
- `test_latin_lines_with_italic_words` (29 of 60): passes here, with the fonts installed. That PC lacked
  fonts: a setup problem.
- Most of that run's 80 failures were setup (no Tesseract, no fonts, no training tools, Python 3.14).

## Afterwards: the one failure fixed, the job required

The two Review legends now wrap and Manuscript Review's buttons flow onto more rows; the test compares
a very wide window with a narrow one. Run 44008560195 (commit 13ee978): the whole suite passed on
Windows (pytest exit code 0), and the window's least width on the runner went from 1582 to 884 px
(Linux: 771 to 536). https://github.com/RexBHavgreau/ectype-ocr/actions/runs/44008560195
The `windows` job is no longer allowed to fail.
