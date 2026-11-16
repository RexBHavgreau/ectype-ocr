# Windows test run - 2026-11-16

## Environment and result

- Windows 11 Pro, version 10.0.26300, build 26300.
- Python 3.14.4 (`py -3.14`), pytest 9.2.0, PySide6 6.12.0. The `python` command points to LibreOffice's Python 3.13.17, which has neither pytest nor PySide6; Python 3.12 is not installed.
- Tesseract is not installed or on PATH. `tesseract --version` could not run, so there is no version to report. English (`eng`) is also absent from the installed language data.
- Pillow in this Python installation lacks RAQM.
- Each test command used `QT_QPA_PLATFORM=offscreen` and a new empty `ECTYPE_DATA_DIR`.

The full collection contained 275 tests. Across the first full run and the follow-up batches needed after it crashed: **188 passed, 32 failed, 46 errored, 7 skipped, and 2 crashed the Python process**. The two process crashes were Windows access violations in traced-font tests. No code or tests were changed.

The requested `python -m pytest -q -p no:cacheprovider` could not run under the default `python`: pytest is not installed there. The suite was run with the available project environment instead:

```powershell
py -3.14 -m pytest -q -p no:cacheprovider
```

The first invocation stopped at `tests/test_traced.py::test_trace_ring_winding` with exit code `-1073741819` (`0xC0000005`). The remaining tests were run in separate batches so every collected test was attempted. A second traced test, `tests/test_traced.py::test_a_shape_too_big_for_a_font_is_left_out`, caused the same access violation.

## Errors (46)

**Final error:** `ectype.ocr.EngineUnavailable: Tesseract not found; install it (e.g. apt install tesseract-ocr), or set ECTYPE_TESSERACT to its path`

`tests/test_cluster.py::test_one_glyph_per_common_character`, `test_composite_matches_the_font`, `test_kept_results_make_the_same_glyphs`, `test_style_set_in_review_groups_the_word_so`; `tests/test_cluster_progress.py::test_cluster_reports_progress_and_is_unchanged`, `test_cluster_stops_then_completes`, `test_stop_leaves_an_earlier_result_alone`, `test_fonts_report_stop_and_complete`; `tests/test_export.py::test_text_layer`, `test_layout`, `test_facsimile`, `test_reflow_manuscript`, `test_reflow_through_docx_to_idml`, `test_project_prepares_idml_once`; `tests/test_ocr_segment.py::test_tesseract_reads_synthetic_page`, `test_segmentation_owns_ink`; `tests/test_pdf_meta.py::test_textlayer_modes_and_size`, `test_traced_and_substitute_modes`; `tests/test_review.py::test_apply_correction_keeps_or_spreads_boxes`, `test_correction_saves_the_text_and_a_training_pair`; `tests/test_stamps.py::test_page_cache_round_trip`, `test_page_cache_reads_nothing_odd`, `test_second_run_computes_nothing`, `test_changed_reading_with_old_size_and_time_is_made_again`, `test_planted_pickles_are_never_loaded`, `test_stale_cluster_follows_the_readings`, `test_stale_cluster_without_inputs`, `test_segment_stamp`, `test_review_edit_redoes_that_page`, `test_ocr_writes_its_stamp`; `tests/test_substitute.py::test_substitute_same_font_lands_on_the_ink`; `tests/test_traced.py::test_fonts_validate`, `test_traced_pdf`.

**Final error:** `pytesseract.pytesseract.TesseractNotFoundError: tesseract is not installed or it's not in your PATH. See README file for more information.`

`tests/test_gui.py::test_project_runs_and_splits`, `test_overrides_and_rerun`, `test_window_review_and_correction`, `test_languages_text_mode_and_idml_dialog`, `test_help_menu_and_update_check`, `test_progress_window_during_a_run`, `test_training_shows_how_far_along_and_for_how_long`, `test_work_leaves_the_tab_in_view`, `test_special_characters_go_into_the_correction`, `test_bold_italic_and_small_capitals_set_in_review`, `test_delete_a_word_box_and_stretch_with_handles`, `test_export_idml_says_where_it_went`, `test_review_notes_the_word_clicked_in_the_crash_report`.

## Failed tests (32)

**Final error:** `pytesseract.pytesseract.TesseractNotFoundError: tesseract is not installed or it's not in your PATH. See README file for more information.`

`tests/test_preprocess.py::test_orientation_and_gutter`, `test_preprocess_spread`, `test_overrides_and_keep_spreads`; `tests/test_projects.py::test_a_failing_run_deletes_its_temporary_folder`, `test_the_app_run_stops_for_a_missing_language`; `tests/test_stopping.py::test_run_stops_at_next_page_and_carries_on`, `test_stop_during_ocr_keeps_whole_pages`.

**Final error:** `ValueError: Not installed: English (eng). Add it first: ectype languages --add eng`

`tests/test_plaintext.py::test_batch_makes_a_companion_per_scan`, `test_stopped_book_leaves_no_text_file`; `tests/test_projects.py::test_the_folder_command_says_which_books_need_another_language`.

**Final error:** `RuntimeError: Setting Hebrew or Syriac needs Pillow's raqm layout (for right-to-left text, joined letters and vowel points), which this installation lacks.`

`tests/test_hebrew_prose.py::test_a_font_done_for_pointed_hebrew_gets_only_the_unpointed_set`, `test_without_the_prose_the_bible_without_points_stands_in`, `test_a_font_without_points_takes_most_unpointed_lines_from_the_prose`; `tests/test_training.py::test_syriac_from_the_peshitta`, `test_arabic_from_the_quran_and_the_van_dyck_bible`.

**Final error:** `AssertionError: the shipped languages are copied beside it` (the installed list contained `lat`, but not `eng`).

`tests/test_export.py::test_language_codes_and_download`.

**Final error:** `AssertionError: assert 83 > 83` (the narrow window did not produce more button rows).

`tests/test_gui.py::test_buttons_wrap_and_stay_visible_in_a_narrow_window`.

**Final error:** `AssertionError: Regex pattern did not match.` The test expected an error about a model reading several scripts; it received the Tesseract-not-found error instead.

`tests/test_kraken_choice.py::test_reading_stops_in_plain_words_for_a_many_script_model_alone`.

**Final error:** `PermissionError: [WinError 5] Access is denied` while replacing the temporary PDF with `t.pdf`.

`tests/test_pdf_meta.py::test_lossless_background_is_the_same_pixels`.

**Final error:** `AssertionError: assert 2 == 0` (the command returned exit code 2).

`tests/test_plaintext.py::test_text_files_dialog_and_cli`, `test_cli_text_of_a_scan_as_beaux_typed_it`.

**Final error:** `AssertionError: assert 1 == 0` (the command returned exit code 1).

`tests/test_plaintext.py::test_cli_run_puts_the_pdf_beside_the_scan`; `tests/test_projects.py::test_results_only_runs_keep_nothing`, `test_keep_leaves_the_project_in_the_projects_folder`, `test_a_pdf_making_step_given_a_scan_runs_it`.

**Final error:** `AssertionError: assert 1 == 3` (the command returned exit code 1).

`tests/test_projects.py::test_a_run_stops_before_the_slow_steps_when_a_script_isnt_read`.

**Final error:** The output listed the missing-language command as `ectype languages --add eng xyz`, while the test expected `ectype languages --add xyz`.

`tests/test_plaintext.py::test_cli_refuses_a_language_not_installed`.

**Final error:** The expected text retained an en dash and em dash, but the actual text contained replacement characters (`U+FFFD`).

`tests/test_synth_compare.py::test_synth_truth_matches_ink`.

**Final error:** `AssertionError: assert (3 == 3 and 0 == 36)` (expected 36 new Greek lines; got 0).

`tests/test_training.py::test_lines_set_in_fonts`.

**Final error:** `AssertionError: expected 12 transliteration lines; got 0`.

`tests/test_training.py::test_transliteration_lines_set_in_fonts`.

**Final error:** `AssertionError: expected 8 Latin lines; got 0`.

`tests/test_training.py::test_latin_from_the_vulgate`.

**Final error:** `AssertionError: expected 60 lines with italic words; got 29`.

`tests/test_training.py::test_latin_lines_with_italic_words`.

## Crashed tests (2)

**Final error:** `Windows fatal exception: access violation` in `src/ectype/trace.py:27`, `trace_bitmap`.

- `tests/test_traced.py::test_trace_ring_winding`
- `tests/test_traced.py::test_a_shape_too_big_for_a_font_is_left_out` (also passed through `src/ectype/fontbuild.py:123`, `build_style_font`)

## Captured full-suite output

The initial invocation's progress output and complete Python traceback are preserved below. The two dots after the traceback were buffered results for the two tests that finished just before the crash.

```text
....EE.E.EEEEE............EFE.EEE...E............EEEEE.EE.EEE.EEF..E.F.F [ 26%]
F........s.s....................F.................EE.....FEE............ [ 52%]
.FFF...FF..F..F.FF..FFF.FFFF.E..E....s..s.s.EE.EEEEEEE.E...FF....EsF.... [ 78%]
Windows fatal exception: access violation
Current thread (most recent call first):
  File "<repo>\src\ectype\trace.py", line 27 in trace_bitmap
  File "<repo>\tests\test_traced.py", line 25 in test_trace_ring_winding
  File "<Python>\site-packages\_pytest\python.py", line 187 in pytest_pyfunc_call
  File "<Python>\site-packages\pluggy\_execution.py", line 147 in _multicall
  File "<Python>\site-packages\pluggy\_manager.py", line 183 in _hookexec
  File "<Python>\site-packages\pluggy\_caller.py", line 197 in __call__
  File "<Python>\site-packages\_pytest\python.py", line 1798 in runtest
  File "<Python>\site-packages\_pytest\runner.py", line 184 in pytest_runtest_call
  File "<Python>\site-packages\pluggy\_execution.py", line 147 in _multicall
  File "<Python>\site-packages\pluggy\_manager.py", line 183 in _hookexec
  File "<Python>\site-packages\pluggy\_caller.py", line 197 in __call__
  File "<Python>\site-packages\_pytest\runner.py", line 250 in <lambda>
  File "<Python>\site-packages\_pytest\runner.py", line 361 in from_call
  File "<Python>\site-packages\_pytest\runner.py", line 249 in call_and_report
  File "<Python>\site-packages\_pytest\runner.py", line 139 in runtestprotocol
  File "<Python>\site-packages\_pytest\runner.py", line 118 in pytest_runtest_protocol
  File "<Python>\site-packages\pluggy\_execution.py", line 147 in _multicall
  File "<Python>\site-packages\pluggy\_manager.py", line 183 in _hookexec
  File "<Python>\site-packages\pluggy\_caller.py", line 197 in __call__
  File "<Python>\site-packages\_pytest\main.py", line 411 in pytest_runtestloop
  File "<Python>\site-packages\pluggy\_execution.py", line 147 in _multicall
  File "<Python>\site-packages\pluggy\_manager.py", line 183 in _hookexec
  File "<Python>\site-packages\pluggy\_caller.py", line 197 in __call__
  File "<Python>\site-packages\_pytest\main.py", line 387 in _main
  File "<Python>\site-packages\_pytest\main.py", line 333 in wrap_session
  File "<Python>\site-packages\_pytest\main.py", line 380 in pytest_cmdline_main
  File "<Python>\site-packages\pluggy\_execution.py", line 147 in _multicall
  File "<Python>\site-packages\pluggy\_manager.py", line 183 in _hookexec
  File "<Python>\site-packages\pluggy\_caller.py", line 197 in __call__
  File "<Python>\site-packages\_pytest\config\__init__.py", line 262 in _main
  File "<Python>\site-packages\_pytest\config\__init__.py", line 286 in _console_main
  File "<Python>\site-packages\pytest\__main__.py", line 9 in <module>
  File "<frozen runpy>", line 88 in _run_code
  File "<frozen runpy>", line 198 in _run_module_as_main

Current thread's C stack trace (most recent call first):
  <cannot get C stack on this system>
..
```
