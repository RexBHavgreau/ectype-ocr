# Ectype OCR: Project Plan

Oct 24, 2026 · @Beaux

## Goal and scope

Build a free, scriptable tool that turns a scanned book page into a PDF whose text is real, visible, searchable vector type traced from the scan itself, matching what Acrobat's ClearScan produces, with cleaner glyphs and smaller files.

**Name.** Ectype OCR: PyPI package `ectype-ocr`, GitHub `ectype-ocr` (confirm free when creating), import name and command `ectype`. An ectype (Greek *ektypos*, "struck in relief") is a copy made from an original, the counterpart of an archetype. Not to be confused with the Ectype JavaScript type checker on npm or the ectype.online media collection.

**Output modes**

1. **Traced mode (the ClearScan equivalent).** Glyphs are traced from the scan, grouped and averaged, built into embedded fonts, and placed where the original letters were.
2. **Substitute mode.** Same positions, but the text is set in a real digital typeface the user picks (for example EB Garamond). This is the low-bloat route and reuses everything except font synthesis.
3. **Searchable-image mode** for comparison only. OCRmyPDF already does this well, so the project never rebuilds it.

**Success means**

- A page from the Young chapter, viewed at 100–200%, is hard to tell apart from Acrobat's ClearScan output, and text search and copy work.
- One glyph per character per style in each font, not 17 versions of "i".
- File size at or below Acrobat's for the same pages.
- The pipeline runs from the command line with no Adobe software.

**Non-goals for version 1**

- Handwriting, tables, and math. Mixed scripts on one page (Greek or Hebrew inside English text) are in scope, since biblical and classical books need them.
- Syriac and Arabic. The data model supports them from the start (glyph units, right-to-left, ActualText), but they ship after Latin, Greek, and Hebrew.
- A GUI before Phase 6. The CLI and HTML review report come first; the GUI is built on the same core in Phase 6.

## What Acrobat ClearScan actually does

Acrobat 26's ClearScan traces glyphs from the scan into synthesized fonts, paints them as visible text over a background image with the text erased, and labels each font with the nearest system font name. Findings come from the Young chapter PDF (17 pages, producer string "Adobe Acrobat (64-bit) 26 Paper Capture Plug-in with ClearScan").

| Aspect | What the file contains | Implication for us |
| --- | --- | --- |
| Font type | Type0 fonts with CIDFontType0 (CFF) descendants, Identity-H encoding, embedded as FontFile3 | fontTools can write exactly this |
| Font names | `*Times New Roman-19350`, `*Comic Sans MS-86859`, etc.; the asterisk marks a synthesized font, the name is only a style label | Names are cosmetic; pick our own |
| Outlines | Traced from the scan (Sabon/Garamond-like), scan specks traced in | Clean before tracing |
| Glyph count | Up to 393 glyphs per font; 17 variants of "i", 24 of ".", ligatures like fi and fr, unknowns mapped to U+FFFD | Acrobat clusters loosely; we can merge harder |
| Glyph order | Roughly by frequency (e, o, a, s, i, n, t, r…) | Order is free; frequency is fine |
| Text mapping | ToUnicode CMap per font | Required for search and copy |
| Font sharing | 38 fonts reused across pages, about 230 KB compressed for 17 pages | Build fonts per document, not per page |
| Page structure | One grayscale background image per page with text pixels erased to faint residue, plus small strip images for the gutter shadow | Need a text-erasing (inpainting) step |
| Text placement | Visible fill (render mode 0), one `Td` per word with a per-word `Tc` character-spacing adjustment | Simple to reproduce with pikepdf or raw content streams |
| Oddities | A private `/Bezier 300` key in each font dictionary | Ignore |

The extracted glyph sheet and background image are in the working files from this conversation; regenerate them in Claude Code from the original PDF as a baseline.

## Prior art

No open-source tool does the whole job, but most stages already exist in mature form; the new work is the glue and the glyph-to-font step. Build on these rather than from scratch.

| Project | What it already does | What it lacks | How to use it |
| --- | --- | --- | --- |
| [Scribe OCR](https://github.com/scribeocr/scribeocr) (AGPL-3.0, JavaScript) | Generates a custom overlay font per document from OCR data and exports a text-only "Ebook" PDF; reads Tesseract hOCR; includes a proofreading UI | [Docs](https://docs.scribeocr.com/) don't say whether glyph outlines come from the scan or a built-in font with adjusted metrics | Closest end-to-end relative. Read its font-optimization and PDF-export code first; borrow ideas (AGPL limits copying code into a non-AGPL project) |
| [smoothscan](https://github.com/ncraun/smoothscan/) (GPL-3.0) | Groups repeated symbols, vectorizes them, builds a TrueType font, embeds it in a PDF | No OCR mapping (symbols map to arbitrary letters); 1-bit input only; 4 commits, dormant | Proof of concept for the font-embedding half; read, don't fork |
| [Leptonica jbclass](https://fossies.org/linux/leptonica/src/jbclass.c) (BSD-2) | Symbol classification by correlation or rank Hausdorff; `jbAccumulateComposites` and `jbTemplatesFromComposites` average every instance of a class into one 8-bit template | Knows shapes, not characters | The grouping-and-averaging engine. Same code [jbig2enc](https://github.com/agl/jbig2enc) uses |
| Tesseract 5 | OCR with word and per-symbol boxes in hOCR, ALTO, or its API | Character boxes can be loose; ligature handling varies | Character identity and positions |
| [OCRmyPDF](https://ocrmypdf.readthedocs.io/en/stable/_sources/plugins.md.txt) 17 | Preprocessing (deskew, clean, rotate), Tesseract orchestration, PDF/A output, plugin hooks including a structured `OcrElement` result in v17 | Text layer is invisible; no hook replaces the renderer | Use as the front end, or borrow its preprocessing; not the core |
| Potrace | Bitmap-to-Bézier tracing; also FontForge's autotrace engine | Potrace is GPL; vtracer is MIT. Both trace one bitmap at a time | Glyph tracing with vtracer; Potrace as an optional add-on |
| fontTools | Builds CFF/CID and TrueType fonts, subsetting, ToUnicode | No tracing | Font assembly |
| PyMuPDF | Writes pages, images, text in embedded fonts with per-word placement | Not a typesetter | Reference only (AGPL); use pikepdf (MPL-2.0) to write PDFs and pypdfium2 (Apache/BSD) to render them |
| ScanTailor Advanced, unpaper | Splitting spreads, deskew, dewarp, despeckle, binarize | Not OCR | Optional cleanup before the pipeline |

Two more things to know:

- **Patent.** Amazon's [US9684986](https://patents.google.com/patent/US9684986), "Constructing fonts from scanned images for rendering text" (filed 2015), covers building a font from scanned glyphs with baseline offsets. Google Patents lists it as expired for unpaid fees; worth confirming before any commercial release.
- **Research, from memory (verify).** Berkeley's Ocular OCR learns a font model from historical prints; deep vector-font work (DeepVecFont, Im2Vec) can reconstruct outlines. Useful later for damaged or rare glyphs, not for v1.

## Biggest obstacles

The hardest problem is tying each OCR character to the exact ink that forms it; nearly every other obstacle depends on getting that link right. Ranked by risk:

1. **Character-to-ink alignment.** Tesseract's per-symbol boxes are approximate, and touching letters (rn, ri, ft), ligatures, and broken letters (a split "e") give one blob for two characters or two blobs for one.
   - Mitigation: segment ink into connected components yourself, then assign components to OCR characters by overlap within each word box. Treat multi-character blobs as ligature glyphs (map fi, fl, ffi to Unicode ligatures or a multi-character ToUnicode entry), and merge components that one character box covers.
2. **Grouping by style, not just by letter.** Roman, italic, bold, small caps, and each point size need separate groups, or an italic "a" gets averaged into a roman one. Tesseract's font attributes are unreliable.
   - Mitigation: cluster within each character by shape (Leptonica correlation, or a size-and-shape feature vector), then derive styles from which clusters co-occur in the same words. Size groups come from x-height per line.
3. **Averaging without blur or drift.** Instances must be aligned to sub-pixel accuracy before averaging, or the composite thickens.
   - Mitigation: align on centroid then refine by cross-correlation; average in grayscale at 2–4× upsampled resolution; threshold once; then trace. Leptonica already aligns by centroid.
4. **Metrics and spacing.** A traced glyph has no advance width or sidebearings, and word positions must match the scan.
   - Mitigation: set advance from measured median ink-to-ink spacing per glyph; place each word at its scanned baseline origin and use per-word character spacing (as Acrobat does) or a `TJ` array to absorb error.
5. **Baselines on warped or skewed pages.** Curved lines near the gutter break a single baseline per line.
   - Mitigation: deskew and dewarp first (ScanTailor Advanced, or OpenCV); v1 handles flat pages only and flags curved lines for image fallback.
6. **Erasing text from the background.** Leaving ghosts (as Acrobat does) looks dirty; over-erasing removes figure detail.
   - Mitigation: build a mask from the exact components that became glyphs, dilate 1–2 px, fill with local background (OpenCV inpaint or median paper color). Never touch regions classified as figures.
7. **Specks and noise.** Dirt attached to letters gets traced in.
   - Mitigation: averaging over many instances largely erases specks; also drop components below an area threshold and smooth with the tracer's equivalent of Potrace's `turdsize` and `alphamax` settings.
8. **Rare glyphs and OCR errors.** A character seen once can't be averaged; a misread character puts the wrong shape behind the right Unicode, or the reverse.
   - Mitigation: trace singletons directly with heavier smoothing; mark low-confidence words for review; keep unknown shapes as glyphs with U+FFFD only as a last resort, and report them.
9. **Scale of work.** A 300-page book at 600 dpi yields roughly a million glyph instances.
   - Mitigation: process pages in parallel for extraction, then one document-wide clustering pass; cache intermediate results per page.

## Script support

Traced mode handles every target script well because each glyph is placed at its scanned position, so no OpenType shaping is needed; the hard work moves to OCR quality per script and to making copied text come out in the right logical order.

**Target scripts**

| Script | OCR starting point | Glyph unit to cluster | Special issues |
| --- | --- | --- | --- |
| Latin: English, Spanish, French, Latin | Tesseract `eng`, `spa`, `fra`, `lat` | Letter; accents as separate mark glyphs | Accented letters often split into base + accent components; small caps; old ligatures (ct, st) in older books |
| German Fraktur and other blackletter | Tesseract `frk` / `deu_latf`; historical models trained on GT4HistOCR (OCR-D) | Letter, plus Fraktur ligatures (ch, ck, tz, ſt) | Long s (ſ), r rotunda (ꝛ), superscript e for umlaut; choose faithful vs. normalized text (see below) |
| Hebrew with niqqud (and cantillation) | Tesseract `heb` (weak with points); [Kraken](https://kraken.re/6.0.0/index.html) models | Consonant (with dagesh/mappiq as part of the letter shape) and each vowel or accent as its own mark glyph | Right to left; marks must attach to the right consonant; Unicode mark order (canonical reordering breaks some Hebrew sequences, so do not blindly normalize to NFC) |
| Rashi script | No stock model; train a Kraken model on your own ground truth (prior research: [Auto-ML for Rashi OCR](https://arxiv.org/pdf/1811.01290)) | Same as Hebrew | Same Unicode as square Hebrew, so traced mode supplies the Rashi shapes for free; substitute mode needs a Rashi font |
| Polytonic Greek | Tesseract `grc`; Kraken models | Base letter; breathings, accents, and iota subscript as mark glyphs (or precomposed units where the ink is joined) | Mixed Greek-Latin pages are common; on 19th-century commentaries [Kraken scored about 7% character error on Greek vs. 13% for Tesseract](https://ar5iv.arxiv.org/html/2110.06817) |
| Syriac (later) | Tesseract `syr`; Kraken | Connected run of letters, not single letters | Cursive joining; three styles (Estrangela, Serto, East Syriac); vowel points; right to left |
| Arabic (later) | Tesseract `ara`; Kraken | Connected component (part of a word), with dots as marks | Cursive; letter segmentation is unreliable, so cluster whole connected shapes; right to left |

**What this changes in the design**

1. **Glyph unit = an ink unit mapped to a Unicode string.** A glyph can stand for one character, a letter plus marks, a ligature, or a connected Arabic or Syriac run. The data model stores a string per glyph, never a single character.
2. **Marks are their own clusters.** Niqqud, Greek accents, and Latin accents are clustered and placed independently of their base letters. This keeps fonts small: one qamets glyph serves every consonant.
3. **OCR is pluggable per region.** Kraken becomes a first-class engine beside Tesseract (Apache 2.0; gives baselines and per-character cuts; supports right-to-left and bidirectional text). Each region or line carries a script and language tag, detected or set by the user.
4. **Logical text via ActualText.** Glyphs are drawn in visual order; each word is wrapped in a marked-content span with an `/ActualText` string in logical order. This is what makes copied Hebrew, Arabic, and Syriac come out right, and lets combining sequences copy correctly. Test extraction in Acrobat, pdf.js, Poppler, and MuPDF, since they differ.
5. **Faithful vs. normalized text is a user option.** For Fraktur ſ, Hebrew mark order, and Greek precomposed vs. decomposed forms, the glyph's ToUnicode can be faithful while ActualText carries the normalized form, or the reverse.
6. **Substitute mode needs shaping for complex scripts.** Setting Hebrew with niqqud, Arabic, or Syriac in a real font requires HarfBuzz shaping and mark positioning, so substitute mode is harder than traced mode for these scripts. It also needs a font per script (for example EB Garamond, SBL Hebrew or Taamey Frank, GFS Didot, UnifrakturMaguntia, Noto Sans Syriac, Amiri).

## Proposed architecture

A Python pipeline of separate stages that pass plain files between them; traced and substitute modes differ only in where the font comes from.

&#91;embedded content: pipeline · 9 stages, 2 output modes\]

The glyph mask from segmentation drives the erasing, so only ink that became text is removed; word positions and baselines from OCR feed assembly in both modes.

| Between stages | Format |
| --- | --- |
| Preprocessed page | PNG, grayscale, 300–600 dpi, with a JSON of the transforms applied |
| OCR result | JSON: words, lines, baselines, characters with boxes and confidence (converted from hOCR or ALTO) |
| Segmentation | JSON linking each character to component IDs, plus a label-map PNG |
| Clusters | JSON of classes (character, style, instance list) plus composite PNGs at 4× scale |
| Glyph outlines | SVG path per class |
| Fonts | OTF (CID-keyed CFF) per style, plus a JSON map of glyph ID to Unicode and advance |
| Background | PNG per page with text erased |
| Output | PDF |

## Phased build plan

Build substitute mode first: it proves OCR, positioning, background erasing, and PDF writing without touching font synthesis, and is useful on its own.

**Phase 0 — Baseline and harness**

- Analysis script that dumps any PDF's fonts, glyph sheets, page structure, and background images (generalize what was done on the Young chapter).
- Test corpus in place (see next section) and a comparison report: side-by-side renders, a pixel-difference overlay, file size, text-extraction accuracy.
- Exit: the harness runs on Acrobat's output and on the original scan.

**Phase 1 — OCR and segmentation**

- Preprocessing: page rotation detection from the image itself (never trust `/Rotate`; the Young scan's is wrong), deskew and cleanup, and automatic splitting of two-page spreads (find the gutter, which is often off-center, rather than cutting at 50%), with a per-page override in the review report. Output is one page per book page by default; `--keep-spreads` keeps each spread as one page, as Acrobat does.

* Tesseract run producing word, line, and symbol boxes with baselines; connected-component extraction; component-to-character assignment.
* Exit: on 10 test pages, at least 98% of characters own the right ink, checked through an overlay view.

**Phase 2 — Substitute mode**

- Erase text from the background; place words in a chosen font at scanned baselines with per-word spacing; ToUnicode correct.
- Exit: Young chapter pages render cleanly in EB Garamond, search and copy work, and text overlays the original within about 1 px at word ends.

**Phase 3 — Glyph grouping and averaging**

- Per-character clustering by style; aligned averaging into grayscale composites; review sheet of every cluster with instance counts.
- Exit: one composite per character per style on the Young chapter, with italic and roman separated, and specks gone.

**Phase 4 — Tracing and font synthesis**

- Trace composites with vtracer (Potrace optional); build CID-keyed CFF fonts with fontTools (advances, ligatures, ToUnicode); one font per style per document.
- Exit: fonts open cleanly in FontForge, and pass a fontTools validation check.

**Phase 5 — Traced mode end to end**

- Swap substitute font for synthesized fonts; image fallback for low-confidence words and figures.
- Exit: the success criteria in the goal section, measured on the Young chapter against Acrobat.

**Phase 5b — Scripts beyond English**

- In order: Spanish, French, and Latin (accents as marks); polytonic Greek with Kraken; Hebrew with niqqud (marks, right to left, ActualText); Fraktur; Rashi script (train a Kraken model on 20–50 transcribed pages).
- Exit per script: a test page copies out as correct Unicode in logical order in Acrobat and pdf.js, with one glyph per letter or mark per style.
- Syriac and Arabic come after Phase 6, using connected-shape glyph units.

**Phase 6 — Hardening**

- Batch CLI, parallel extraction, caching, a review report listing low-confidence words, and an option to run as an OCRmyPDF plugin or after it.
- GUI (PySide6/Qt): open a scan, run the pipeline, review pages side by side, correct OCR, and export. Spread handling in the GUI: a split-spreads switch for the document (on by default), and per page a draggable gutter line, a "don't split this page" toggle, and rotate buttons; these write the same overrides file the CLI reads. Later: dewarping.

**Phase 7 — Contribute pathway (optional for users)**

- **Correction capture** (built into the review report from Phase 2 on, not deferred): every OCR correction saves the line image, baseline, and corrected text as training data.
- **Rights per source:** each book is marked public domain, licensed for sharing, or restricted; restricted sources train local models but are never exported.
- **Fine-tune and measure:** one command wraps `ketos train` from a chosen base model, holds back a test set, and records accuracy before and after.
- **Generated paperwork:** Kraken repository metadata and model card (languages, ISO 15924 script codes such as Hebr, Grek, Latf, base-model DOI, metrics, training-data summary, license) prefilled for review; a PAGE XML or ALTO dataset export with a draft HTR-United catalog entry; a contribution packet (model, card, accuracy report, sample pages) for emailing original model authors.
- **Guarded publishing:** optional `ketos publish` with a dry-run summary, explicit confirmation (Zenodo deposits are permanent), the token kept in the system keychain, and personal file paths stripped from metadata.
- Exit: a fine-tuned model and its training data, from public-domain pages, packaged and ready to publish without hand-editing files.

## Test corpus and evaluation

Keep the original scan of the Young chapter as the primary test case, since it has an Acrobat ClearScan version to compare against.

**Corpus**

| Set | Purpose |
| --- | --- |
| Young chapter, original scan (re-download it) | Primary comparison against Acrobat |
| Young chapter, Acrobat ClearScan output | Reference result |
| 2–3 more clean book scans at 300 and 600 dpi | Generalization; resolution effects |
| Pages with italics, small caps, footnotes, block quotes | Style grouping |
| Pages with gutter curvature or skew | Dewarp handling and fallback |
| A page with a figure or ornament | Background erasing must leave figures alone |
| Synthetic pages: known text set in a known font, printed or rendered, degraded | Ground truth for every metric. Include synthetic pages for each script: Fraktur, polytonic Greek, pointed Hebrew, Rashi, and mixed English-Greek-Hebrew |

**Metrics**

| Metric | How it's measured |
| --- | --- |
| Text accuracy | Character error rate of extracted text against ground truth |
| Visual fidelity | Rendered output vs. original scan: SSIM, plus a difference overlay for review |
| Position accuracy | Distance between each placed word's box and its scanned box |
| Glyph cleanliness | Glyphs per character per style (target 1); count of U+FFFD glyphs |
| File size | Bytes per page, against Acrobat and against the scan |
| Speed | Seconds per page at 300 and 600 dpi |

The synthetic set matters most for development: with a known font behind the scan, glyph shapes, spacing, and text can all be checked exactly.

## Open decisions

All decisions are settled as of October 24, 2026.

| Decision | Options | Decided |
| --- | --- | --- |
| License | MIT/BSD (permissive) vs. GPL/AGPL (lets you reuse Scribe OCR or smoothscan code) | Apache 2.0 (permissive, with an explicit patent grant); read Scribe for ideas only |
| Language | Python throughout vs. Python with a small C helper | Python; C only if clustering is too slow |
| Leptonica access | ctypes bindings to the jbclass functions vs. reimplementing clustering and averaging in NumPy/OpenCV | Try ctypes first; NumPy fallback is about a day of work |
| OCR engine | Tesseract only vs. a pluggable interface (Kraken, Apple Vision, cloud) | Pluggable interface with Tesseract and Kraken from the start; Kraken for Greek, Hebrew, Rashi, Syriac, and Arabic |
| Font format | CID-keyed CFF (like Acrobat) vs. TrueType | CFF: fewer points, matches Acrobat, PDF-native |
| Fonts per document | One per detected style vs. one per style per size | Per style; scale handles size unless glyphs differ visibly (optical sizes) |
| Ligatures | Keep as single glyphs vs. split into letters | Keep, with correct ToUnicode |
| Relation to OCRmyPDF | Run after it, as a plugin, or standalone | Standalone. No dependency on OCRmyPDF: our own preprocessing module calls the same underlying tools directly (Tesseract orientation detection, Leptonica deskew, OpenCV cleanup), so it can be improved freely. OCRmyPDF (MPL-2.0) is a reference; any copied code stays in its own MPL-licensed files. Accept OCRmyPDF-processed PDFs as input; a Kraken engine plugin for OCRmyPDF is a later side project |
| Platform | CLI only, GUI only, or both | Both: a cross-platform CLI and a cross-platform GUI (PySide6/Qt) over the same core library; the GUI is the primary interface for the author |

**Dependency licenses.** Every bundled dependency must be permissive (MIT, BSD, Apache, MPL) or LGPL used as a library. GPL and AGPL tools are allowed only as optional add-ons the user installs separately. This rules out PyMuPDF (AGPL) and Potrace (GPL) as core dependencies; pikepdf, pypdfium2, and vtracer replace them. Recheck licenses when adding any dependency.

## Handoff notes for Claude Code

Give Claude Code this doc (export it as Markdown into the repo as `PLAN.md`) and start with Phase 0; don't ask it to build the whole pipeline in one session.

**Suggested repo layout**

```
ectype-ocr/
  PLAN.md                  this doc
  CREDITS.md               attribution for code, ideas, dependencies, models, fonts
  LICENSES/                full license texts of third-party code
  pyproject.toml
  src/ectype/
    analyze.py             dump fonts, glyph sheets, page structure of any PDF
    ocr.py                 OCR engine interface + Tesseract implementation
    segment.py             connected components, component-to-character assignment
    cluster.py             per-character style clustering and averaging
    trace.py               vtracer wrapper (Potrace optional), outline cleanup
    fontbuild.py           fontTools CID-CFF builder, ToUnicode, metrics
    background.py          text erasing / inpainting
    pdfwrite.py            page assembly with pikepdf; rendering via pypdfium2
    cli.py
  tests/
  corpus/                  scans and Acrobat reference (git-ignored if large)
  reports/                 HTML comparison reports
```

**First tasks, in order**

- [ ] Add the Apache 2.0 `LICENSE`, a `NOTICE` file, `CREDITS.md`, and `LICENSES/` in the first commit.

* [ ] Set up the repo, dependencies (pikepdf, pypdfium2, fonttools, opencv-python, numpy, vtracer, pytesseract or tesserocr), and a test runner.
* [ ] Write `analyze.py` and run it on the Acrobat PDF; confirm it reproduces the findings in the Acrobat section.
* [ ] Build the comparison report (side-by-side render, difference overlay, size, text accuracy).
* [ ] Make one synthetic test page with known text and font.
* [ ] Spike: call Leptonica's `jbCorrelationInit`, `jbAddPage`, `jbAccumulateComposites`, `jbTemplatesFromComposites` through ctypes on one page and save the composites as images.
* [ ] Spike: read Scribe OCR's font-optimization and PDF-export code and write a half-page summary of how it builds its overlay font.

**Conventions to tell Claude Code**

- Each stage reads and writes plain files (PNG, JSON) so stages can be rerun and inspected alone.
- Every stage produces a visual check (overlay or sheet) in `reports/`.
- Keep coordinates in scan pixels until PDF writing; convert to points in one place.
- Never modify files in `corpus/`.

* Save every OCR correction made in the review report as a line-level training pair from the first version that has a review report; the Contribute pathway (Phase 7) depends on it.

**Attribution (a project rule, beyond what licenses require)**

- Keep a `CREDITS.md` from the first commit, updated in the same commit as anything borrowed. It has four parts:
  - **Code copied or adapted:** source project, URL, commit or version, license, and the files in this repo that contain it.
  - **Ideas and methods:** projects and papers whose approach we followed without copying code (smoothscan, Scribe OCR, jbig2enc and Leptonica's composites, the Ocular and vector-font research, Acrobat ClearScan as analyzed here).
  - **Dependencies:** every library, engine, and command-line tool used, with its license.
  - **Models, datasets, and fonts:** each OCR model and training set with its authors and DOI, and each font used in substitute mode with its designer and license.
- Files containing copied code carry a header naming the source, URL, and license; borrowed code from copyleft-per-file licenses (MPL) stays in its own files.
- Full license texts of third-party code go in `LICENSES/`.
- The GUI's About window and `ectype --credits` show the credits, and the Contribute pathway's model cards credit base models and their training data.
