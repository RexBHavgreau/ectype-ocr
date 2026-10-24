# Phase 3 results: glyph grouping and averaging

`ectype cluster <workspace>` groups every glyph instance in the document by style and shape, and averages each group into one composite. Phase 3's exit test is: on the Young chapter, one composite per character per style, italic and roman separated, and specks gone.

```
ectype cluster reports/work-young
```

## How it works

1. **Instances.** Each glyph unit with ink is cut out of its page as coverage (0 = paper, 1 = ink), with neighbouring letters masked off. It is positioned by its line's fitted baseline and its own ink centroid, and scaled by its line's x-height. Lines are put into size classes (body, footnotes, headings) by x-height relative to the document.
2. **Slant.** Each word gets a measured slant: the shear that makes its stroke columns sharpest. Book italics lean about 8–15°, and the Young chapter's italic words measure 8–12°. So 7–18° counts as italic. Steeper readings come from words made mostly of diagonals ("very", "way" read as 22–24°). One- and two-letter words follow their neighbours.
3. **Shape clusters.** Within each (character, size class, slant), instances are grouped by average-linkage clustering on correlation distance between normalised 24-row shape features, cut at 0.25. Groups of one or two instances join their nearest large group if close enough.
4. **Styles.** Letter clusters that occur together in words (at least twice) are joined into styles, only within one size class and slant. Punctuation and digits join the style they most often share a word with, preferring their own slant.
5. **Averaging.** Each cluster (up to 400 instances, sampled) is placed at 4× resolution by baseline and centroid. Each instance is then refined by phase correlation against the running mean (two passes) and averaged. Thresholding the composite at one half gives the glyph Phase 4 will trace.
6. **Spacing data.** Ink gaps between neighbouring glyphs in words are recorded per cluster pair, for advance widths and sidebearings in Phase 4.
7. **Probable OCR misreadings.** These are glyphs other than a character's main one that look more like another character's main glyph than their own, plus rare characters whose only glyph does.

## Results on the Young chapter

50,012 glyph instances became 442 averaged glyphs.

| Style | What it is | Glyphs | Instances |
| --- | --- | --- | --- |
| 0 | Body roman | 144 | 48,710 |
| 1 | Body italic (titles such as *Iliad*, *Odyssey*) | 25 | 528 |
| 2 | Footnote roman (size 0.7) | 47 | 261 |
| 3 | Footnote italic (size 0.7) | 26 | 136 |
| 4 | Running heads, sans serif (size 1.2) | 20 | 112 |
| 5 and up | Small groups: chapter opening, headings | — | — |

- **Specks are gone.** The composites are clean letters, with no specks or grain (sheets in `reports/work-young/cluster/index.html`).
- **Italic and roman are separated**, in both the body and the footnotes.
- **One composite per character per style:** almost. In the body roman style, eight characters have a second glyph of three or more instances ("." "2" "S" "T" "b" "e" "“" "”"). Looking at them, nearly all are misreadings, not real variants:
  - "T" 3 is an "I" (Tesseract read "Ibid." as "Tbid.").
  - "." 14 is a comma.
  - The second "“" and "”" glyphs are single quotes.
  - "S" 4 has a quote mark fused to it.
  - "e" 3 is a bold sans "e".

  The suspect list flags these.
- **57 probable misreadings** are listed in the report with page and unit, for example "R" read as "B", "p" as "b", "v" as "u", "fir" as "m".

On synthetic pages, the averaged "e" has the same ink area as the font's "e" (within 8%). It comes out about a pixel short, because thin curves averaged from blurred instances fall just under the threshold.

## For manual review

- `reports/work-young/cluster/index.html`: one glyph sheet per style. Check that each letter looks right, and that no italic letter sits in a roman style or the reverse.
- The "Probable OCR misreadings" table there. Correcting those words (once the review report can save corrections) will also fix their glyphs.
