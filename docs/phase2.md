# Phase 2 results: substitute mode

`ectype substitute <workspace> [--font …]` sets every OCR word in a real typeface at its scanned position, over the scan with the text erased. Phase 2's exit test asked three things of the Young chapter:
- it renders cleanly in the substitute font
- search and copy work
- text overlays the original within about 1 px at word ends

```
ectype substitute reports/work-young --name young-substitute
```

## How words are placed

- **Size.** Each scanned x-height letter (a, c, e, m, n, o, r, s, u, v, w, x, z) is divided by the same letter's height in the substitute font, so round letters' overshoot cancels out. A trimmed mean of those ratios gives the type size for the page. A line takes its own size only when it is clearly different (more than 12% off): footnotes and headings.
- **Baseline.** Per line, a robust straight-line fit to the bottoms of letters without descenders.
- **Width.** One horizontal scale (`Tz`) for the page brings the font's letter widths to the scan's. Each word may adjust that by up to ±7%. A per-word character spacing (`Tc`) absorbs the rest, so the word's ink starts and ends exactly on the scanned word's ink. This replaces letterspacing that was visible when substituting a narrower face.
- **Background.** The ink that became glyph units, plus specks, is masked, dilated 2 px and filled from a paper estimate (a grayscale closing). Figures and other non-text ink are never touched. The background is stored as a 150 dpi JPEG, as Acrobat does.
- **Fonts.** The substitute font is subset and embedded once per document as Type0 / Identity-H, with a ToUnicode map. Each word carries a trailing space glyph so copied text has word breaks, as Acrobat's does.

## Results

**Synthetic pages, set in the font they were printed in.** This isolates placement from font differences.

| Page | Font | Size found / true (pt) | Word ends within 1 px (left / right) |
| --- | --- | --- | --- |
| s01 | Liberation Serif | 10.48 / 10.56 | 100% / 100% |
| s03 | FreeSerif | 10.72 / 10.56 | 99.6% / 98.8% |
| s06 | FreeSerif | 11.23 / 11.04 | 98.8% / 97.6% |
| s07 | Liberation Serif | 10.48 / 10.56 | 100% / 99.2% |
| s09 | FreeSerif | 9.70 / 9.60 | 99.2% / 98.8% |
| s10 | Liberation Serif | 9.98 / 9.60 | 98.0% / 97.6% |
| s02 | DejaVu Serif | 10.52 / 10.08 | 93.2% / 92.4% |

The DejaVu pages are the blurriest. Blur fattens the binarized ink by about half a pixel, which makes the type about 4% large. Two fixes for this were tried and dropped, because each made other pages worse:
- estimating that growth per page from letter heights
- measuring edges at subpixel precision from the grayscale

The `growth` parameter of `place_words` remains as a knob for this.

**The Young chapter, in Liberation Serif.** EB Garamond isn't installed here, and Liberation is a Times-like face.

- **Position:** word ends land within 1 px of the scanned ink for 98.3% of words on the left and 98.2% on the right (at least 95% on every full page). Word tops match for only about 25% of words, which is expected: Liberation's ascenders and capitals aren't Sabon's.
- **Text:** the extracted text equals the OCR text. The only differences are words hyphenated across lines, which pdfium joins up. A search for 240 sampled words found all 240.
- **Size:** 813 KB for 26 book pages (31 KB per page). Acrobat's ClearScan file is 914 KB for the same 13 spreads.

## For manual review

- `reports/work-young/substitute/index.html` has per-page numbers, and overlays with the text layer in red over the scan.
- `reports/work-young/substitute/young-substitute.pdf` is the output itself.
- Things to look at: how the substituted text reads, whether the erased background is clean (faint halos or leftover marks), and footnote and heading sizes.
