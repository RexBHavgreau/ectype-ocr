# Spike: how Scribe OCR builds its overlay font

This is a summary from reading [scribe.js](https://github.com/scribeocr/scribe.js) at commit `66367f2` (June 2026), the engine behind [Scribe OCR](https://github.com/scribeocr/scribeocr). Scribe is AGPL-3.0, so we copy no code from it. Everything below describes ideas only.

**Short answer:** Scribe does **not** trace glyphs from the scan. It starts from a bundled typeface and reshapes it to match measurements taken from the OCR. This settles the open question in `PLAN.md` ("docs don't say whether glyph outlines come from the scan"). Our traced mode is a different technique; Scribe is closer to our substitute mode.

## How it works

1. **Measure the scan.** `fontStatistics.js` and the page converters collect, for each character and style, its width, its height and pair kerning from OCR boxes. Every measurement is normalized by the x-height of its line. The per-page statistics are merged into document-level `charMetrics` for each style (regular, italic, bold, bold italic).
2. **Pick the closest typeface.** `fontEval.js` renders OCR words in each candidate font and scores the result against the binarized scan. The candidates are Carlito, Century, Palatino, Garamond, Nimbus Roman/Sans/Mono and URW Gothic, shipped as WOFF in `fonts/latin`. A cheaper pre-filter compares OCR character widths with each font's advances by RMS difference (`calcFontWidthRMSD`).
3. **Reshape the chosen font** (`worker/optimizeFontModule.js`, `optimizeFont`):
   - It optionally rescales the em size.
   - It scales each glyph horizontally to the measured width, clamped to ±30% (±10% for single-stem letters like i, l, t, I). Single-stem letters are scaled about their stem centre so the stem stays put.
   - It resets left bearings for punctuation.
   - It enforces one advance for monospace fonts.
   - It scales capitals vertically when cap height has at least 10 observations, and adjusts J and the descenders of g, p, q, stretching only the part below the baseline.
   - It derives kerning pairs from measured gaps, capped at ±10% of the em (with special cases for quotes and ff/fi/fl).
   - It removes the GSUB table, so no ligatures are applied.
4. **Write the PDF** (`export/pdf/`).
   - Fonts are subset to the characters used and embedded as Type0 with Identity-H, a CIDFontType0 descendant and FontFile3/OpenType, plus a ToUnicode CMap (`createEmbeddedFontType0`). Multi-character ToUnicode values such as "fi" are supported.
   - Text is written one line at a time with `Tm` (rotated for skewed lines) and `TJ` arrays.
   - Per-word stretch goes into `Tc`. When the needed `Tc` would be too large, Scribe switches to a *width-scaled variant* of the font: the same font file with a scaled `/W` array, at 1.1× to 1.5×. The variant shares the FontDescriptor, FontFile and ToUnicode objects with the base font.
   - Output modes are `ebook` (visible text only), `proof` and `eval` (text over the image at reduced opacity), `invis` (invisible text over the image) and `annot`.

## What we take from it

- **Normalize every measurement by line x-height**, and pool measurements per style across the document. We'll need the same thing for advances and sidebearings in Phase 4.
- **Absorb per-word stretch in `Tc`, with a cap.** Width-scaled `/W` variants that share one font program are a cheap way to avoid large `Tc` values. Worth trying in Phase 2.
- **Substitute mode can choose its font automatically**, by rendering candidate fonts against the scan. This could be a later option for picking a substitute font.
- **Clamp corrections to plausible ranges**, and require a minimum number of observations before trusting a statistic. Both rules carry over to glyph averaging (singletons and rare glyphs).

## Where we differ

- Our glyph outlines come from averaged scan composites, not from a stock font.
- We keep ligatures as glyphs with correct ToUnicode. Scribe removes GSUB and treats "fi" as two letters.
- We use CID-keyed CFF (FontFile3/CIDFontType0C) as Acrobat does. Scribe embeds OpenType inside FontFile3.
