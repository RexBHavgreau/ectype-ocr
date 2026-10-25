# Acrobat ClearScan baseline

This is what `ectype analyze` and `ectype compare` report for the two Young chapter files in `corpus/`. It checks the "What Acrobat ClearScan actually does" section of `PLAN.md`. To reproduce:

```
ectype analyze "corpus/E J. Young Inspiration-1 - Copy.pdf" -o reports/analyze-acrobat
ectype analyze "corpus/E J. Young Inspiration-1.pdf" -o reports/analyze-scan
ectype compare "corpus/E J. Young Inspiration-1 - Copy.pdf" "corpus/E J. Young Inspiration-1.pdf" --align --labels Acrobat,Scan -o reports/compare-acrobat
```

## The two files

| | Scan (`E J. Young Inspiration-1.pdf`) | Acrobat (`E J. Young Inspiration-1 - Copy.pdf`) |
| --- | --- | --- |
| Producer | (none) | Adobe Acrobat (64-bit) 26 Paper Capture Plug-in with ClearScan |
| Pages | 13 two-page spreads: chapter 7 opening, book pp. 170–191, and the book's title page on the last spread | 13 |
| Size | 8.35 MB (643 KB/page) | 914 KB (70 KB/page) |
| Page images | One 3300×2550 8-bit gray JPEG per page, 300 dpi | One ~1660×1290 gray background per page at **150 dpi** (Flate over DCT), plus 1–2 narrow gutter-shadow strips at 300 dpi |
| Orientation | Images stored upside down, with `/Rotate 270` (wrong by 90°), so viewers show the spread sideways | Upright, `/Rotate 0`; each page deskewed by 0–1.2° and given its own slightly larger MediaBox (793–805 × 613–630 pt) |

`PLAN.md` describes a 17-page file; these files have 13 pages. Every other figure below matches the plan, so it is presumably the same chapter counted differently.

## The plan's findings, checked

| Plan says | Measured | Verdict |
| --- | --- | --- |
| Type0 fonts with CIDFontType0 (CFF) descendants, Identity-H, FontFile3 | All 72 font dictionaries: Type0 / CIDFontType0, Identity-H, FontFile3 `CIDFontType0C`, CIDSystemInfo Adobe-Identity-0 | ✔ |
| Names like `*Times New Roman-19350`; asterisk marks synthesized | All 72 start with `*`. Labels: Times New Roman 23, Microsoft Sans Serif 14, Calibri 13, Minion Pro 7, Verdana 7, Cambria 4, Comic Sans MS 3, Tahoma 1 | ✔ |
| Up to 393 glyphs per font | Largest program: 393 glyphs (`*Times New Roman-19350`, used on 7 pages) | ✔ |
| 17 variants of "i", 24 of "." | Exactly that in the largest program; across all programs: "." 308, "i" 279, "r" 238, "," 183 | ✔ |
| Ligatures like fi and fr | Multi-character glyphs: fi, fl, fr, ra, ry, rt, Af. Also OCR junk such as "We", "p.", ":-7", "'.'" | ✔, with junk |
| Unknowns mapped to U+FFFD | 202 U+FFFD glyphs across all programs (19 in the largest) | ✔ |
| Glyph order roughly by frequency | First CIDs of the largest program: e o a s i n t r h n l r c d u w h t e m … | ✔ |
| ToUnicode CMap per font | Every font has one | ✔ |
| 38 fonts reused across pages, about 230 KB | **38 embedded programs, 232,443 bytes**, behind 72 Type0 dictionaries (Acrobat writes a new dictionary on every page that uses a program) | ✔ |
| Background with text erased, faint residue; gutter strips | Confirmed visually: text is erased to faint gray ghosts; gutter strips are punched out of the background and painted at 300 dpi | ✔ |
| Visible text, one `Td` per word with a per-word `Tc` | Render mode 0 for all text; 1 `Td` per word (e.g. page 2: 1,025 shows, 1,021 `Td`, 981 nonzero `Tc`) | ✔ |
| Private `/Bezier 300` key | `/Bezier` on every Type0 dictionary; `/FontInfo` too | ✔ (+ `/FontInfo`) |

## Not in the plan

- **Every word ends with a space glyph** (CID 0x188 → U+0020 in the largest font), drawn inside the word's `Tj`. That is how copied text gets its word breaks.
- **Text is filled dark gray, not black.** The fill color varies from line to line (`0.122 g` to `0.145 g`), presumably sampled from the scan.
- **Glyphs carry OCR errors.** In the largest font, glyphs that are clearly "r" and "e" map to ";", a "W" maps to `'.'`, and an "o" with a speck maps to "?". This is the plan's obstacle 8 seen in practice: the right shape behind the wrong Unicode.
- **File size:** fonts are 25% of the file, background and strip images 49%, content streams 16%.

## Visual comparison against the scan

`compare --align` turns the scan to the matching quarter turn and fits an affine transform per page. All 13 pages align (ECC 0.84–0.99).

- Mean SSIM is 0.82. Ink disagreement is 46% pixel for pixel, but only **9% when 1 px offsets are forgiven** (under 1% on pp. 1, 3, 9 and 13; up to 29% on p. 6).
- Most of what remains is local position shift: in the overlay, whole words or lines sit one or two pixels off while their neighbors match exactly. Acrobat appears to dewarp or reposition per line, which a single affine fit can't follow. So pixel metrics against Acrobat mostly measure geometry, not glyph quality. Word-level position accuracy (Phase 1, from OCR word boxes) will be the better measure.
- There's no ground-truth text for the Young chapter yet, so its text accuracy isn't measured. pdfium extracts about 5,000–5,800 characters per full spread from Acrobat's text layer.
