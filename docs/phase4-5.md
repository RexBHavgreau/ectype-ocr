# Phases 4 and 5 results: fonts traced from the scan, and traced mode

```
ectype fonts reports/work-young --family "Ectype Young"   # Phase 4
ectype traced reports/work-young --name young-traced      # Phase 5
ectype run scan.pdf                                        # every stage, scan to PDF
```

## Phase 4: tracing and font synthesis

- **Tracing.** Each averaged composite (4× resolution) is thresholded at 0.45 coverage and traced with vtracer into cubic Bézier contours. Contours under about 1 px² at scan resolution are dropped. Winding is fixed for CFF: outer contours counterclockwise, holes clockwise, decided by nesting.

  Why 0.45: at 0.5, thin strokes averaged from blurred instances come out about 5% too light (the traced text carried 95% of the scan's ink). At 0.45 it carries 101%.
- **Fonts.** One font per style. Glyph IDs (CIDs) are ranked by frequency, as in Acrobat's fonts. The x-height is 500 units of a 1,000-unit em. Each glyph's ToUnicode entry is its text, so "fi" or "ct" units copy out as two letters.
- **Spacing.** Sidebearings are solved by least squares from about 50,000 measured gaps between neighbouring glyphs (gap = right sidebearing of the first + left sidebearing of the second), with a weak prior toward equal sides.
- **Format.** Fonts are written as OpenType (`fonts/style-N.otf`, for inspection and validation) and embedded in PDFs as bare CID-keyed CFF (FontFile3 / CIDFontType0C, ROS Adobe-Identity-0), Acrobat's format.

**Exit check: fonts validate.** FontForge isn't installed in this environment. Instead, every font is checked with OTS (the OpenType Sanitizer that Chrome and Firefox use, which is stricter than most tools) and a fontTools round trip that decompiles and draws every glyph. All 15 Young fonts (405 glyphs) pass both. pdfium renders them in the output PDF. Opening `reports/work-young/fonts/style-0.otf` in FontForge is a good manual check.

## Phase 5: traced mode end to end

- **Placement.** Every glyph unit is drawn with the glyph synthesized from its own cluster, in its style's font, over the scan with that text erased. Glyphs are placed one at a time. Each word starts at its first unit's ink, and a `TJ` adjustment before every later glyph puts it where that instance was printed, so the original's kerning and spacing survive.
- **Image fallback.** Words with OCR confidence under 60, or with a unit that has no glyph, stay in the image. Their ink is left in the background and their text is laid over it invisibly (render mode 3), so they can still be found by search.
- **Background.** Paper grain in the erased background is median-filtered (5×5) away from any remaining ink. That cut background JPEG size by about 60% with no visible change, and no stroke is touched.

## Against the success criteria (Young chapter)

| Criterion | Result |
| --- | --- |
| Hard to tell from ClearScan at 100–200%, search and copy work | Rendered pages are nearly indistinguishable from the scan (see `reports/work-young/traced/young-traced.pdf`). Search found 240 of 240 sampled words. Extracted text matches the OCR text (mean difference 0.15%, from line-end hyphenation). |
| One glyph per character per style, not 17 versions of "i" | Body roman: 135 glyphs, including digits, punctuation, ligatures (fi, fl, ct, tt) and touching pairs (ty, tw, ry). Other characters have one glyph each, apart from misreadings listed for review. Acrobat's main font has 393 glyphs, with 17 "i" and 24 ".". |
| File size at or below Acrobat's | **619 KB** for 26 book pages, against **914 KB** for Acrobat's 13 spreads. Fonts: 97 KB (Acrobat 232 KB). |
| Command line only, no Adobe software | `ectype run scan.pdf` |

Placement: 97.2% of words' left ink edges and 92.3% of right ink edges land within 1 px of the scan's. 10,897 words are drawn as text, and 22 (low OCR confidence) are left as image.

## Known limits

- **Misreadings.** A misread character still gets a glyph shaped like what was printed, but with the wrong text behind it. The cluster report lists the likely ones (35 in the Young chapter); `ectype review` shows them in orange so they can be corrected, and `ectype rebuild` regenerates the PDF.
- **Heavily inked pages.** Where letters fuse into whole-word blobs (synthetic pages s04 and s08), segmentation falls below target. Those letters become multi-character glyphs, or the word falls back to the image.
- **Blurry pages.** Fonts carry about 2 px of tracing tolerance; very blurry scans give slightly rounder corners.
