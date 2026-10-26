# Phase 3 results: glyph grouping and averaging

`ectype cluster <workspace>` groups every glyph instance in the document by style and shape, and averages each group into one composite. Phase 3's exit test is: on the Young chapter, one composite per character per style, italic and roman separated, and specks gone.

```
ectype cluster reports/work-young
```

## How it works

1. **Instances.** Each glyph unit with ink is cut out of its page as coverage (0 = paper, 1 = ink), with neighbouring letters masked off. It is positioned by its line's fitted baseline and its own ink centroid, and scaled by its line's x-height. Lines are put into size classes (body, footnotes, headings) by x-height relative to the document.
   - **Running heads and feet.** These are kept apart in their own zone. A page's top band is a running head when two things hold. First, it stands apart from the text below it: the gap is at least 1.5× the page's usual gap between lines. Second, its letters recur (fuzzy match ≥ 80) in the top band of at least three pages, or it has no letters at all, as with a lone folio. The bottom band is treated the same way. Books often set running heads in another typeface at the body's size. On the Young chapter, the sans-serif upright "Edward J. Young," is too close in shape to the body's roman to split off by shape alone (correlation distance 0.04–0.10 against the 0.25 cut), so it was averaged into the serif letters.
2. **Slant.** Each word gets a measured slant: the shear that makes its stroke columns sharpest. Book italics lean about 8–15°, and the Young chapter's italic words measure 8–12°. So 7–18° counts as italic. Steeper readings come from words made mostly of diagonals ("very", "way" read as 22–24°). One- and two-letter words follow their neighbours. Words measuring 3–7° are settled by shape instead. Each letter votes for whichever average it is closer to: the upright one or the italic one (same size and zone, from words whose slant was clear). The sans-serif italic "“Some" measures only 4–6°, because its round letters barely lean. An upright "for" measures 4–6° because of its f.
3. **Shape clusters.** Within each (character, size class, slant, zone), instances are grouped by average-linkage clustering on correlation distance between normalised 24-row shape features, cut at 0.25. Groups of one or two instances join their nearest large group if close enough.
4. **Styles.** Letter clusters that occur together in words (at least twice) are joined into styles, only within one size class, slant and zone. In running heads, letters on the same line also join, since "Edward" and "Young" share no letters. Punctuation and digits join the style they most often share a word with. Failing that, they join the style they most often share a line with, then the largest style in their zone, preferring their own slant.
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
- **35 probable misreadings** are listed in the report with page and unit, for example "R" read as "B", "u" as "n", "I" as "l", and a quote mark read as a period. (A looser cutoff listed about 90, but flagged too many sound words.)

On synthetic pages, the averaged "e" has the same ink area as the font's "e" (within 8%). It comes out about a pixel short, because thin curves averaged from blurred instances fall just under the threshold.

### Running heads (after the fix for the first export)

In the first export from the app, the odd-page running head came out in serif roman ("Edward J. Young, “Some") instead of the scan's sans serif. "Some" also came out upright instead of italic. Now 50,150 instances become 438 glyphs in 17 styles. The running heads on all 22 text pages have their own two styles:
- upright sans serif, with the folios, which are serif in the scan too: 220 instances
- italic sans serif: 462 instances

The body's italic no longer borrows the running head's sans-serif italic letters (three of its italic "n"s did). One other word changed slant: "Reason" in a footnote's italic book title (*The Bible, The Church and The Reason*) is now italic.

## For manual review

- `reports/work-young/cluster/index.html`: one glyph sheet per style. Check that each letter looks right, and that no italic letter sits in a roman style or the reverse.
- The "Probable OCR misreadings" table there. Correcting those words (once the review report can save corrections) will also fix their glyphs.
