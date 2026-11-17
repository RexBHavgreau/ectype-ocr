# Test coverage report

- Date: 2026-11-16
- Python: 3.13.17
- Pytest outcome: 22 failed, 211 passed, 15 skipped, 27 errors in 207.52s (0:03:27)
- Coverage totals: 16,746 statements, 7,513 missed, 55.1% overall
- Notes: This run used `QT_QPA_PLATFORM=offscreen` and `PYTHONPATH=src`; the suite is not clean in this environment.

## Coverage by file

| File | Statements | Missed | Coverage |
| --- | ---: | ---: | ---: |
| `src/ectype/characters.py` | 62 | 62 | 0.0% |
| `src/ectype/crashlog.py` | 150 | 150 | 0.0% |
| `src/ectype/gui_manuscript.py` | 373 | 373 | 0.0% |
| `src/ectype/gui.py` | 2863 | 2857 | 0.2% |
| `src/ectype/idmlkit/images.py` | 46 | 42 | 8.7% |
| `src/ectype/idmlkit/writer.py` | 719 | 650 | 9.6% |
| `src/ectype/traced.py` | 164 | 143 | 12.8% |
| `src/ectype/kraken_worker.py` | 285 | 242 | 15.1% |
| `src/ectype/idml.py` | 288 | 225 | 21.9% |
| `src/ectype/fontmatch.py` | 224 | 162 | 27.7% |
| `src/ectype/lept.py` | 88 | 59 | 33.0% |
| `src/ectype/cluster.py` | 703 | 455 | 35.3% |
| `src/ectype/align.py` | 123 | 64 | 48.0% |
| `src/ectype/cli.py` | 916 | 368 | 59.8% |
| `src/ectype/project.py` | 523 | 194 | 62.9% |
| `src/ectype/stopping.py` | 72 | 26 | 63.9% |
| `src/ectype/trainall.py` | 106 | 36 | 66.0% |
| `src/ectype/train.py` | 345 | 109 | 68.4% |
| `src/ectype/kraken.py` | 378 | 119 | 68.5% |
| `src/ectype/converter.py` | 125 | 38 | 69.6% |
| `src/ectype/fontbuild.py` | 214 | 62 | 71.0% |
| `src/ectype/models.py` | 160 | 45 | 71.9% |
| `src/ectype/resources.py` | 160 | 45 | 71.9% |
| `src/ectype/synthtrain.py` | 369 | 98 | 73.4% |
| `src/ectype/docx.py` | 206 | 51 | 75.2% |
| `src/ectype/training.py` | 548 | 134 | 75.5% |
| `src/ectype/families.py` | 132 | 32 | 75.8% |
| `src/ectype/manuscript.py` | 555 | 103 | 81.4% |
| `src/ectype/datafolder.py` | 173 | 30 | 82.7% |
| `src/ectype/review.py` | 350 | 60 | 82.9% |
| `src/ectype/pdfwrite.py` | 278 | 42 | 84.9% |
| `src/ectype/synth.py` | 207 | 29 | 86.0% |
| `src/ectype/updates.py` | 129 | 17 | 86.8% |
| `src/ectype/pool.py` | 222 | 28 | 87.4% |
| `src/ectype/idmlkit/model.py` | 137 | 17 | 87.6% |
| `src/ectype/compare.py` | 206 | 24 | 88.3% |
| `src/ectype/preprocess.py` | 310 | 34 | 89.0% |
| `src/ectype/raised.py` | 101 | 11 | 89.1% |
| `src/ectype/substitute.py` | 362 | 39 | 89.2% |
| `src/ectype/verify.py` | 136 | 14 | 89.7% |
| `src/ectype/scripts.py` | 125 | 12 | 90.4% |
| `src/ectype/imagefiles.py` | 21 | 2 | 90.5% |
| `src/ectype/layout.py` | 330 | 31 | 90.6% |
| `src/ectype/stamps.py` | 66 | 6 | 90.9% |
| `src/ectype/analyze.py` | 439 | 40 | 90.9% |
| `src/ectype/freespace.py` | 159 | 14 | 91.2% |
| `src/ectype/striptext.py` | 103 | 8 | 92.2% |
| `src/ectype/ocr.py` | 319 | 24 | 92.5% |
| `src/ectype/trace.py` | 122 | 9 | 92.6% |
| `src/ectype/segment.py` | 556 | 41 | 92.6% |
| `src/ectype/textlayer.py` | 85 | 5 | 94.1% |
| `src/ectype/languages.py` | 177 | 9 | 94.9% |
| `src/ectype/texts.py` | 252 | 12 | 95.2% |
| `src/ectype/plaintext.py` | 155 | 7 | 95.5% |
| `src/ectype/report.py` | 34 | 1 | 97.1% |
| `src/ectype/textfiles.py` | 104 | 2 | 98.1% |
| `src/ectype/translit.py` | 161 | 1 | 99.4% |
| `src/ectype/__init__.py` | 1 | 0 | 100.0% |
| `src/ectype/background.py` | 29 | 0 | 100.0% |

## Ten files with the most lines not run

### 1. `src/ectype/gui.py` (2857 missed / 2863 statements / 0.2% coverage)
- `__init__` (line 57)
- `start` (line 95)
- `_forget` (line 99)
- `report` (line 104)
- `__init__` (line 136)
- `seconds` (line 166)
- `_stop` (line 169)
- `_tick` (line 174)
- `add` (line 181)
- `set_fraction` (line 185)
- `stop_clock` (line 189)
- `finish` (line 195)
- `__init__` (line 213)
- `start_drawing` (line 222)
- `stop_drawing` (line 227)
- `mousePressEvent` (line 232)
- `mouseMoveEvent` (line 241)
- `mouseReleaseEvent` (line 247)
- `show_image` (line 269)
- `fit` (line 276)
- `__init__` (line 329)
- `itemChange` (line 338)
- `mouseReleaseEvent` (line 343)
- `__init__` (line 351)
- `_current` (line 415)
- `drop_gutter` (line 446)
- `rotate` (line 467)
- `reset` (line 476)
- `_relabel` (line 485)
- `set_status` (line 508)
- `mousePressEvent` (line 514)
- `__init__` (line 532)
- `_scale` (line 542)
- `box` (line 554)
- `changed` (line 558)
- `boundingRect` (line 568)
- `shape` (line 572)
- `paint` (line 579)
- `_edges_at` (line 587)
- `mousePressEvent` (line 610)
- `mouseMoveEvent` (line 618)
- `mouseReleaseEvent` (line 633)
- `__init__` (line 645)
- `_follow` (line 809)
- `refresh` (line 819)
- `show_pane` (line 896)
- `_drop_editor` (line 912)
- `_show_editor` (line 917)
- `_box_changed` (line 924)
- `start_adding` (line 946)
- `_drawn` (line 952)
- `_rect` (line 1025)
- `select_word` (line 1029)
- `_style_words` (line 1075)
- `__init__` (line 1087)
- `_jump` (line 1140)
- `__init__` (line 1163)
- `toggle_hand` (line 1194)
- `_reader_chosen` (line 1263)
- `_toggled` (line 1272)
- `_label` (line 1286)
- `_kraken_installed` (line 1354)
- `_filter` (line 1358)
- `work` (line 1376)
- `_progress` (line 1385)
- `_done` (line 1392)
- `_failed` (line 1396)
- `_add` (line 1432)
- `_update_total` (line 1474)
- `free` (line 1484)
- `_sized` (line 1531)
- `free_space` (line 1535)
- `choose` (line 1552)
- `_confirm` (line 1635)
- `work` (line 1832)
- `_load_when_free` (line 1846)
- `selected` (line 1903)
- `work` (line 1924)
- `_busy` (line 1935)
- `_run` (line 1943)
- `_progress` (line 1950)
- `_failed` (line 1964)
- `_sync` (line 2100)
- `_browse` (line 2103)
- `_mode_changed` (line 2187)
- `scripts` (line 2380)
- `add_folder` (line 2388)
- `remove_folders` (line 2394)
- `_scanned` (line 2435)
- `_shown` (line 2442)
- `_check_all` (line 2493)
- `_scripts_changed` (line 2520)
- `chosen_fonts` (line 2524)
- `_accept` (line 2528)
- `reject` (line 2532)
- `__init__` (line 2543)
- `addItem` (line 2549)
- `count` (line 2552)
- `itemAt` (line 2555)
- `takeAt` (line 2558)
- `expandingDirections` (line 2561)
- `hasHeightForWidth` (line 2564)
- `heightForWidth` (line 2567)
- `setGeometry` (line 2570)
- `sizeHint` (line 2574)
- `minimumSize` (line 2577)
- `place_row` (line 2592)
- `_separator` (line 2625)
- `__init__` (line 2634)
- `action` (line 2652)
- `load` (line 2779)
- `_hands_changed` (line 2822)
- `_data_folder_dialog` (line 2840)
- `free_space_dialog` (line 2846)
- `work` (line 2859)
- `_space_freed` (line 2873)
- `work` (line 2886)
- `_all_cores_changed` (line 2906)
- `_mode_changed` (line 2957)
- `_page_image_changed` (line 2980)
- `_languages_changed` (line 2986)
- `_progress_line` (line 3044)
- `_close_progress` (line 3054)
- `_enable` (line 3062)
- `_split_changed` (line 3192)
- `_jump` (line 3197)
- `open_project` (line 3224)
- `run` (line 3241)
- `_finished` (line 3259)
- `added` (line 3315)
- `_failed` (line 3333)
- `line` (line 3372)
- `_check_at_start_changed` (line 3505)
- `step` (line 3570)
- `done` (line 3579)
- `failed` (line 3584)
- `export` (line 3593)

### 2. `src/ectype/idmlkit/writer.py` (650 missed / 719 statements / 9.6% coverage)
- `visit` (line 533)

### 3. `src/ectype/cluster.py` (455 missed / 703 statements / 35.3% coverage)
- `dpi_of` (line 361)
- `find` (line 647)

### 4. `src/ectype/gui_manuscript.py` (373 missed / 373 statements / 0.0% coverage)
- `set_selected` (line 36)
- `mousePressEvent` (line 43)
- `view_key` (line 131)
- `show_view` (line 134)
- `_row` (line 169)
- `_picked` (line 173)
- `show_page` (line 187)
- `_draw_lines` (line 196)
- `_clicked` (line 210)
- `_drop_editor` (line 248)
- `_changed` (line 289)
- `join` (line 293)
- `start_split` (line 303)
- `delete` (line 310)
- `start_drawing` (line 318)
- `create` (line 369)
- `choice` (line 436)

### 5. `src/ectype/cli.py` (368 missed / 916 statements / 59.8% coverage)
- `show` (line 172)
- `show` (line 292)

### 6. `src/ectype/kraken_worker.py` (242 missed / 285 statements / 15.1% coverage)
- `utf8_open` (line 63)
- `callback` (line 99)

### 7. `src/ectype/idml.py` (225 missed / 288 statements / 21.9% coverage)
- `typeset` (line 245)
- `style_for` (line 332)

### 8. `src/ectype/project.py` (194 missed / 523 statements / 62.9% coverage)
- `on_work` (line 721)

### 9. `src/ectype/fontmatch.py` (162 missed / 224 statements / 27.7% coverage)
- No function body was entirely missing from the coverage data.

### 10. `src/ectype/crashlog.py` (150 missed / 150 statements / 0.0% coverage)
- `crash_dir` (line 35)
- `_write` (line 44)
- `_prune` (line 115)
- `_fatal_to_disk` (line 123)
- `_qt_handler` (line 132)
- `hook` (line 174)

## Skipped tests and reasons

- `tests/test_gui.py:12` ? could not import `PySide6.QtWidgets`: No module named `PySide6`
- `tests/test_lept.py:11` ? Leptonica not installed
- `tests/test_datafolder.py:127` ? could not import `PySide6.QtWidgets`: No module named `PySide6`
- `tests/test_freespace.py:131` and `tests/test_freespace.py:155` ? could not import `PySide6.QtWidgets`: No module named `PySide6`
- `tests/test_kraken.py:144`, `tests/test_kraken.py:250` ? could not import `PySide6.QtWidgets`: No module named `PySide6`
- `tests/test_kraken.py:194` and `tests/test_kraken.py:215` ? needs the Kraken engine and a model (`ECTYPE_KRAKEN_TEST_MODEL`)
- `tests/test_plaintext.py:244` and `tests/test_plaintext.py:368` ? could not import `PySide6.QtWidgets`: No module named `PySide6`
- `tests/test_scripts_train.py:58` and `tests/test_scripts_train.py:153` ? right-to-left text needs Pillow's raqm layout
- `tests/test_training.py:459` and `tests/test_training.py:510` ? could not import `PySide6.QtWidgets`: No module named `PySide6`
