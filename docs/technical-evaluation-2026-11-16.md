# Ectype OCR: Technical Evaluation

**Review date:** 16 November 2026  
**Repository reviewed:** [`RexBHavgreau/ectype-ocr`](https://github.com/RexBHavgreau/ectype-ocr)  
**Reference state:** `main` at commit [`25117777f75a543b3a6195144d9789ebee5cf1ed`](https://github.com/RexBHavgreau/ectype-ocr/commit/25117777f75a543b3a6195144d9789ebee5cf1ed), the 16 November 2026 merge of PR #23. This commit brings the changes documented under 0.11.1 in `CLAUDE.md` onto `main`. Ectype releases are tagged separately, so this evaluation identifies the inspected source state rather than assuming that a `v0.11.1` release tag has been published.

## Executive assessment

Ectype has developed into a substantially more capable application than a conventional OCR front end. Its distinctive achievement is the reconstruction of scanned type as visible, searchable vector text, while retaining the source page's appearance. Around that core it now has a desktop workflow, a correction interface, script-specific OCR readers, font-based and correction-based training, a manuscript workflow, and PDF and IDML output modes.

The architecture should be developed incrementally, not replaced. Several improvements that might be obvious suggestions for an ordinary OCR application are already implemented in Ectype: line-level manuscript correction, manual word-box repair, script-specific Kraken reading, shared training pools, typeface-family models, model checks before replacement, font-based synthetic training, and both facsimile and reflow IDML exports.

The best next steps are consequently **validation, reliability, and closing specific documented gaps**, not a larger feature set. In particular, Ectype needs stronger ground-truth evaluation on real historical books, a conclusive resolution of the remaining Windows crash report, and clearer public documentation of what the current application actually does.

## Scope and qualifications

This review is based on the repository's `CLAUDE.md`, `PLAN.md`, `README.md`, and `docs/idml.md`, including the implementation history and planned-work sections. It is a review of the documented repository state, not a line-by-line code audit or an independent test run of the Windows application.

Performance figures below are figures recorded by the project. They should not be treated as independently reproduced results. The observation that Ectype's Hebrew tracing currently looks better than Adobe's is the user's report from practical testing. It is an important result, but it is not yet a controlled, published comparison across a defined Hebrew test corpus.

## What is already implemented

The following capabilities should not be treated as missing features or proposed from scratch.

### OCR, scripts, and reading modes

- Tesseract is the primary printed-page reader. Optional Kraken models can reread words in their respective scripts; the user can select the reader for a script rather than having every Kraken model treated as a single language.
- Manuscript Mode uses Kraken's page-line detection and supports multiple named hands, line-level review, joining and splitting lines, drawing missed lines, deleting lines, and training from corrected samples.
- The application supports mixed-script pages and right-to-left text, including Hebrew, Arabic, and Syriac. The PDF output uses visual glyph order while retaining logical text order for search and copying.
- OCR corrections can be carried into a subsequent reading. Review supports confirming a correct word, correcting text and style, adding or deleting words, stretching word boxes to include missed ink, and inserting special characters. Punctuation at word edges can be corrected independently.

### Training and model selection

- Corrections from projects feed shared language-specific training pools.
- Train from fonts creates synthetic lines in installed typefaces using known texts. The documented known-text material includes the WLC, unpointed Hebrew prose from Project Ben-Yehuda, the SBL Greek New Testament, the Clementine Vulgate, the unvowelled Peshitta, and Arabic material from the Qur'an and Smith–Van Dyck Bible.
- Hebrew synthetic training includes pointed and unpointed lines. Its pointed material includes forms intended to represent biblical accents and the stress-mark conventions used in grammars.
- Models can be trained generally and, where sufficient material exists, for typeface families. Ectype compares candidate models against held-aside test material and does not install a candidate merely because it is newer.
- Quick updates, cached training material, and model packs reduce the cost of incorporating new corrections.

### Output and project workflow

- PDF output includes traced, substitute-font, and invisible searchable-text-layer modes, as well as plain-text extraction.
- Traced mode groups and averages glyph instances, builds fonts from the scan, and places the resulting type at the scanned positions. Low-confidence words can be traced letter by letter rather than representing a doubtful reading with a mismatched glyph.
- IDML export has two different purposes: Reflow produces live, reflowable text, while Facsimile places text in frames in the positions and lines of the scan.
- The application has a desktop interface and command-line commands. Project workspaces, correction logs, caching, progress reporting, stop handling, and a configurable data location are already part of the design.

These capabilities are described across [`CLAUDE.md`](../CLAUDE.md), [`README.md`](../README.md), and [`docs/idml.md`](idml.md). The implementation history in `CLAUDE.md` is especially important: it records specific problems discovered through use on real books and the fixes made in response.

## Traced Hebrew is a strength to protect

The user's practical assessment is that Ectype's reconstruction of Hebrew type is already better than Adobe's in the material tested. That changes the recommendation: the tracing engine should be treated as a competitive strength to preserve, not as a subsystem that needs speculative redesign.

There is supporting evidence that the implementation is addressing the difficult geometry correctly. In the 0.11.1 work merged into `main` in the reference commit, Kraken's letter positions were found to be narrow reference columns rather than full letter widths. This had caused Hebrew word boxes to cut off the first letter. The revised span calculation reduced the measured ink outside all word boxes from 28.2% to 0.3% on the 17-page grammar sample. That is a meaningful, specific correction to the character-to-ink relationship needed by traced output.

The useful next step is not to change tracing for its own sake. It is to document and protect its current quality with repeatable comparisons. Keep OCR recognition accuracy and visual reconstruction fidelity as separate measurements: a correctly drawn glyph can still carry an incorrect Unicode reading, and a correct OCR reading can still be placed or rendered poorly.

A useful comparative test would process the same Hebrew scans through Ectype and Adobe ClearScan, then compare:

1. Visual fidelity at normal reading size and at high magnification, including the relationship of consonants to vowel points and other marks.
2. Search and copy accuracy, including logical RTL order and combining-mark sequences.
3. Page image quality, background erasure, and preservation of non-text detail.
4. Output file size and processing time.

Include several representative print conditions and typefaces. A Rashi-script sample would be especially informative, but it should be treated as a separate recognition challenge: the project's plan notes that Rashi has no stock model and would require suitable ground truth for training. Traced mode can reproduce its shapes once they are recognized and mapped to ink; recognition itself is the harder part.

## Recommended priorities

### 1. Extend the existing evaluation tools with verified ground truth

**Highest priority.** The repository already has OCR comparison tools, model checks, corpus material, and held-aside training samples. The next improvement is to make the evaluation more representative and more authoritative, not to build a second benchmark framework.

The project notes explicitly identify the need for a checked transcription of several pages of Driver's *Treatise on the Use of the Tenses in Hebrew* to establish real error rates. The grammar experiments so far include useful vocabulary and model comparisons, but some of those measurements are not substitutes for a manually checked, complete transcription.

Build a small, carefully proofread reference corpus first. Include pointed Hebrew, unpointed Hebrew prose, scholarly transliteration, Greek, and mixed-script pages. Add Rashi script when a reliable sample and ground truth are available. Keep evaluation pages separate from training material, and report at least:

- Character error rate and word error rate.
- Hebrew consonant accuracy separately from vowel-point and accent accuracy.
- Errors in punctuation, superscripts, and scholarly transliteration.
- Results by typeface, scan condition, and OCR reader.
- Results before and after training on a different set of pages from the same book or typeface family.

This would establish whether improvements transfer to unseen pages and books, rather than merely showing that a model can learn the material it has already encountered.

### 2. Resolve the remaining Windows stability issue

The 0.11.1 notes describe a Windows access violation that can occur after a run or while interacting with Review. The initial menu-lifecycle theory was not confirmed; the crash was not reproduced in the offscreen Linux test environment. The new diagnostics record pages, clicked words, and character code points, which should help identify the trigger.

This deserves focused testing on Windows with real Hebrew pages and repeated Review interaction. It should be closed with a reproducible test or a clear root-cause explanation before the application is relied upon by a broader group of users. The release workflow's packaged-app smoke tests are valuable, but they cannot by themselves reproduce every interactive Windows failure.

### 3. Make training and cache invalidation fully content-based

The development notes identify a remaining exception to Ectype's otherwise careful content-based cache validation: some training stamps still rely on file times and manually maintained version constants. The named areas include `pool.py`, `synthtrain.py`, `trainall.py`, `training.py`, and `manuscript._stamp`.

The rest of the pipeline already has a strong principle: reuse results only when the actual inputs and relevant code are unchanged. Applying that principle consistently to training inputs would reduce the risk of silently reusing stale training data after a file changes while retaining its timestamp, or needlessly rebuilding data after an unrelated change.

This is a targeted maintenance improvement, not a reason to redesign the training system.

### 4. Bring the README and user-facing documentation up to date

The public README still describes Phases 0–5 as complete and Phase 6 as underway, and some of its command examples and workflow descriptions reflect an earlier project structure. That does not adequately represent the present application documented in `CLAUDE.md`, including the later training, manuscript, Kraken-reader, and project-workflow improvements.

Update the README to describe the current user-visible capabilities, current project-folder behavior, current CLI entry points, and the distinction between Print Mode and Manuscript Mode. Separate stable user instructions from historical development notes. The detailed technical history can remain in `CLAUDE.md`; ordinary users should not have to infer current behavior from it.

### 5. Close a few explicitly documented script-specific gaps

Avoid broad claims of uniform multilingual support. The scripts have different evidence and different remaining limitations:

- **Pointed Hebrew:** Current stock models remain weak on some vowel points. The documentation recommends training on the book's own corrected lines. The grammar-style stress mark is represented in synthetic training, but the planned evaluation on checked pages remains important.
- **Rashi script:** The project plan identifies the lack of a stock model and the need for model training on suitable ground truth. Treat it as a distinct evaluation and training task, not an assumed consequence of square-Hebrew support.
- **Syriac:** The known Peshitta text used for synthetic training is unvowelled. Vowelled Syriac is currently learned from corrections, so representative vowelled samples would be useful for evaluation and future training.
- **Arabic:** The Qur'an source is available through the tested repository copy, while the Smith–Van Dyck download is still noted as untested in the development log. Verify it on the target Windows installation before relying on it as a training source.

These are bounded tasks. They are preferable to adding more OCR engines without evidence that another engine will solve a demonstrated problem.

### 6. Keep improving IDML fidelity where real books expose gaps

The two IDML modes already serve distinct purposes, and the export path is considerably more developed than a simple OCR-to-IDML converter. Further work should be driven by books that expose specific structural failures.

The README notes that Reflow joins paragraphs divided by page breaks, marks uncertain OCR for checking, and does not carry the scan's running heads through as source text. Those may be reasonable choices for a reflowed edition, but the trade-offs should be documented clearly. If users regularly need to preserve a particular feature—such as marginal notes, multi-level headings, or source footnote placement—add a correction or export option for that feature rather than attempting a wholesale layout-reconstruction system.

## Recommendations not to pursue now

Several ideas raised in an earlier informal review should be set aside because they duplicate existing work, conflict with recorded project decisions, or are not justified by current evidence:

- Do not redesign Ectype around a new canonical-transcription architecture without first identifying a concrete limitation in the current project, OCR, and correction data model.
- Do not rebuild the correction interface from scratch. It already supports important word- and line-level repairs; improve particular friction points found in use.
- Do not make whole-page Kraken recognition the default for printed books. The recorded page-level experiment found that Tesseract line detection followed by Kraken rereading was slightly more accurate and much faster on the tested grammar page. Revisit whole-page recognition for layouts where Tesseract's line detection actually fails.
- Do not add TEI/XML export, a built-in library-wide full-text database, or a combine-text-files feature unless the intended workflow changes. The development notes record a deliberate decision to keep companion text files simple for the user's external searching workflow.
- Do not apply one correction automatically everywhere the same misreading occurs. This was considered and rejected because a repeated correction can be wrong in another context.
- Do not prioritize recovering words dropped as noise through faint Review boxes; this was explicitly deferred.
- Do not spend time fixing isolated punctuation glyphs that remain rasterized unless they become a recurring practical problem. The development notes explicitly classify this as nonessential for now.

## Overall conclusion

Ectype's core direction is sound. Its combination of scan-derived vector typography, Unicode-aware text handling, script-specific OCR, iterative correction, and reusable training is unusual and potentially valuable for historical and scholarly books.

The near-term goal should be to make the existing strengths demonstrably reliable across real material. Prioritize a verified multilingual and historical-print test corpus, close the outstanding Windows stability question, finish content-based training-cache validation, and bring the public documentation into line with the current application. Continue improving individual scripts where measured tests reveal a weakness.

Most importantly, preserve the traced-PDF technology while it is already performing well on Hebrew. Establish its advantage through controlled comparisons before changing the tracing process. Ectype does not need to become a much broader application; it needs to become exceptionally dependable at the difficult digitization work it already does.

## Repository references

- [`CLAUDE.md`](../CLAUDE.md) — implementation history, decisions, lessons, and planned work.
- [`docs/history/original-plan.md`](history/original-plan.md) (was `PLAN.md` when this review was written) — original architecture, goals, and non-goals.
- [`README.md`](../README.md) — public-facing capabilities and usage.
- [`docs/idml.md`](idml.md) — OCR languages, Kraken, training, and IDML export.
- [`corpus/`](../corpus/) — project test material.
