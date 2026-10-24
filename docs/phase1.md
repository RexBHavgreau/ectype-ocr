# Phase 1 results: OCR and segmentation

Phase 1's exit test is that on 10 test pages at least 98% of characters own the right ink. This page records where things stand. To reproduce:

```
ectype synth --set 10 -o reports/work-synth && ectype ocr reports/work-synth && ectype segment reports/work-synth
ectype preprocess "corpus/E J. Young Inspiration-1.pdf" -o reports/work-young && ectype ocr reports/work-young && ectype segment reports/work-young
```

## Synthetic pages (exact ground truth)

There are ten pages in Liberation Serif, DejaVu Serif and FreeSerif at 9.5–12 pt, each with random skew, blur, noise, specks and JPEG artifacts. Every fourth page also has its ink thickened by a pixel. A character "owns the right ink" when the glyph unit holding it covers at least 90% of its true ink, and at most 5% of that unit's ink belongs to other characters.

| Page | Font | Thickened ink | Owned |
| --- | --- | --- | --- |
| s01 | Liberation Serif | | 99.74% |
| s02 | DejaVu Serif | | 99.66% |
| s03 | FreeSerif | | 99.14% |
| s04 | Liberation Serif | yes | 62.78% |
| s05 | DejaVu Serif | | 99.57% |
| s06 | FreeSerif | | 99.66% |
| s07 | Liberation Serif | | 99.74% |
| s08 | DejaVu Serif | yes | 91.17% |
| s09 | FreeSerif | | 99.23% |
| s10 | Liberation Serif | | 99.49% |
| **All** | | | **95.02%** |

- **The eight normally inked pages all clear 98%** (99.1–99.7%).
- **The two thickened-ink pages don't.** On s04, whole words fuse into single blobs: 138 blobs hold 3 or more letters. That is far heavier than the Young scan, but old books with heavy ink would look like this. Two approaches were tried and dropped:
  - Cutting blobs at the thinnest column: it misassigns overhanging parts, such as the f-hook in "fi", and lowered the overall score.
  - What remains is a curved-path ("drop-fall") cut between letters, a known technique not yet built.
- What the 1–2% residue on clean pages is made of: OCR misreadings, a few i-dots still separated from their stems, and quote marks split from their pairs.

## How segmentation works

1. **Clusters.** The page is binarized, and connected components are assigned to OCR words by overlap. Within each word, components form left-to-right clusters:
   - A small piece stacked above or below a larger one (i-dots, colons, accents) joins its single best partner.
   - Lone fragments within a pixel or two of a letter join that letter.
2. **Alignment.** Characters are matched to clusters:
   - **One-to-one when the counts match.** Tesseract's character boxes drift by up to a whole letter, so they can't be trusted to place a merge.
   - **Otherwise, by dynamic programming.** The main cost is how well each cluster's ink width fits the usual width of its characters, learned from the same page's one-to-one words. Box position is a weak, capped tie-breaker.
3. **Ligature joining.** A dotless i or j under an overhanging neighbour becomes part of a "fi" (or "fj") unit.

## The Young chapter (no ground truth yet)

- The 13 spreads become 26 pages: 2 blank, one title page, and 23 text pages averaging about 2,200 glyph units each.
- Tesseract's mean word confidence is 94–96% per page.
- Low-confidence words that sit wholly outside the text column (specks in the gutter shadow) are dropped and listed.
- Multi-character units are under 1% of units. They are what you'd expect: printed ligatures (fi, fl, and "ct"), and letters touching punctuation ("r.", "y.").

**For manual review:** open `reports/work-young/segment/index.html`. Each letter's ink should be one colour. Magenta boxes are multi-character units, red ink belongs to no character, and red boxes are characters with no ink found. The OCR report (`reports/work-young/ocr/index.html`) lists low-confidence words per page. Recurring misreadings include "Tbid." for "Ibid." and footnote markers fused to words.
