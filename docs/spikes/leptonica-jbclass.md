# Spike: Leptonica jbclass through ctypes

**Result: it works.** `src/ectype/lept.py` calls `jbCorrelationInit`, `jbAddPage`, `jbAccumulateComposites` and `jbTemplatesFromComposites` from the system Leptonica (1.82.0) through ctypes. No compiled helper is needed, so the NumPy fallback isn't needed yet.

Run it with:

```
python spikes/leptonica_jbclass.py "corpus/E J. Young Inspiration-1.pdf" --page 6 --half top --rotate 90 --thresh 0.85
```

Output goes to `reports/spike-jbclass/`. It writes `binarized.png`, a `templates.png` sheet of every class's averaged template sorted by instance count, and `stats.json`.

## How the binding works

- The only struct we mirror is `JbClasser` from `jbclass.h`. Its header is public and its layout hasn't changed in years. We read three fields from it: `pixaa` (the instances of each class), `naclass` (the class of each component) and `baseindex`.
- Images cross the boundary as PNG files that Leptonica reads and writes itself, via `pixRead` and `pixWrite`. That keeps the binding independent of `Pix` internals, which 1.82 no longer exposes. Each page and each template costs one temp file. That is negligible next to classification, but it could be replaced with `pixReadMem`/`pixWriteMem` later.
- `jbTemplatesFromComposites` returns 8-bit templates where 255 means a pixel was ink in every instance. The binding flips this so 0 = ink, matching the rest of the pipeline.

## Findings on one page (Young, p. 178, 300 dpi, Otsu binarization)

The page has 2,432 connected components. Classification takes about 0.5 s.

| Threshold | Classes | Singletons | Largest classes |
| --- | --- | --- | --- |
| 0.95 | 2,228 | 2,115 | 17, 16, 10, 8, 6 |
| 0.85 | 611 | 251 | 50, 39, 38, 36, 35 |
| 0.75 | 215 | 66 | 119, 81, 76, 72, 67 |
| 0.65 | 112 | 20 | 207, 201, 153, 83, 82 |

(The weight factor is 0.5 throughout.)

- **The composites are clean.** With 10 or more instances, the averaged templates are smooth grayscale letters, and specks don't survive averaging. This confirms obstacle 7 in the plan, that averaging largely erases noise.
- **At 0.85, each letter splits into many classes** (several "e", "t" and "o" classes). This is the same loose clustering Acrobat shows. At 0.65, most letters drop to one to three classes. The italic sans-serif running head ("Inspiration") still forms its own classes, separate from the roman text, which is promising for style separation.
- **Low thresholds risk merging different letters.** Phase 3 should not rely on a single global threshold. The plan's approach still holds: cluster *within* each OCR character, using correlation as the shape distance, then merge or split by style.
- **Touching pairs come out as single components.** "fi", "ty" and "ft" appear as their own classes, as obstacle 1 predicted. Ligature handling has to start at segmentation.
- **Multi-part letters split apart.** The dot of "i" and "j", and the pieces of punctuation, are separate components (dotless "ı" stems and "." dots show up as classes). Component-to-character assignment (Phase 1) has to regroup them. Leptonica's `JB_CHARACTERS` mode (components merged by a small dilation) is an option, but it also merges touching letters, so assigning components by OCR box is still the plan.
- **The corpus is stored sideways.** Each PDF page is a 300-dpi landscape scan of a two-page spread, stored upside down with `/Rotate 270`. That flag is wrong by 90°, so viewers show the spread sideways. Phase 1 preprocessing has to detect orientation from the image itself rather than trust `/Rotate`. (The spike renders with `/Rotate` applied, which is why it needs `--rotate 90`.) See `docs/acrobat-baseline.md`.

## Next

Phase 3 can build on `lept.classify`. It still needs per-instance coordinates: `ptaul` gives the upper-left corner of each component, so instances can be tied back to OCR characters.
