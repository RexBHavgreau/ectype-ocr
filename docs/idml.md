# Text layer, languages and IDML export

Three additions for using Ectype when the end product is an InDesign document rather than a PDF:
- **Text-layer mode** gives a searchable PDF quickly, with no glyph tracing.
- **OCR languages** can be chosen and downloaded.
- **IDML export** lays the OCR text out, reflowed or in facsimile.

## Text-layer mode

`ectype run scan.pdf --mode text`, or **Text layer only** in the app. This runs preprocessing and OCR, then writes a PDF of the upright, deskewed pages with every OCR word in invisible text (render mode 3) over its scanned position.

- **Font.** The text uses a "glyphless" font made on the fly: one empty glyph per character, mapped to Unicode by the ToUnicode table. Any script the OCR reads is covered without needing a font that has it.
- **Placement.** Each word is scaled horizontally to its scanned width and sized so a selection covers its line.
- **Page image.** Grayscale JPEG at the page's resolution (quality 60), or black and white (CCITT Group 4).

On the Young chapter (26 pages), text extraction matches the OCR to within line endings (character error rate 0.3%):

| Page image | Size |
| --- | --- |
| Grayscale | 10.4 MB |
| Black and white | 1.3 MB |

## Languages

The **Languages** menu lists the installed Tesseract languages, each with a checkbox; several can be ticked. **Add languages…** at the bottom opens a searchable list of Tesseract's 122 languages and downloads the chosen ones from the `tessdata` repository (`raw.githubusercontent.com/tesseract-ocr/tessdata`).

- **Where they go.** Tesseract reads from one folder, so the first download creates the user's own folder and copies the shipped languages and Tesseract's output configs beside the new ones. The folder is `%LOCALAPPDATA%\Ectype OCR\tessdata` on Windows, `~/.local/share/ectype/tessdata` on Linux, or `ECTYPE_DATA_DIR` if set.
- **Command line:** `ectype languages --add grc lat`.
- **Changing languages** reads the pages again on the next run. Previously, a page's cached OCR was reused regardless of language. Corrections made in the Review tab belong to the old reading and are lost; the app says so.

## IDML export

**Export IDML…** in the app, or `ectype idml <workspace> --mode reflow|facsimile`.

The export first makes sure segmentation and glyph grouping are current, and runs them if not. That analysis is what finds italics, running heads, superscripts and the scan's typefaces. No glyph tracing is involved. The export then builds a page layout (`layout.py`):
- **Blocks:** lines of one zone and size that overlap, with a normal baseline pitch.
- **Paragraphs:** a new one starts at an indented line or after a short line.
- **Alignment:** justified, centred, flush left or right, read from the line ends.

### Reflow

One story, threaded through text frames on as many pages as the text needs. The page count is estimated from the text's width in the body font, plus two spare pages; InDesign's Smart Text Reflow is switched on.

- **Page:** the scan's page size, with its text block's margins.
- **Kept out:** running heads and folios.
- **Joined:** paragraphs split by a page break, and line-end hyphens that only split a word ("in-" + "spiration"). A hyphen stays when the hyphenated form occurs elsewhere in the book, or when the joined word doesn't occur but both halves do.
- **Paragraph styles:**
  - Body, and Body First (no indent, after headings)
  - Heading
  - Extract (set-off passages: quotations, verse)
  - Note, and Notes Heading
- **Italics:** the body family's italic.
- **Notes:** gathered at the end under "Notes", with their printed numbers. They aren't anchored as InDesign footnotes: Tesseract often misses the small superscript reference numbers in the text. On the Young chapter, the "2" after "Inspiration." and the whole "2 Ibid., 230–31." line are missing from the OCR. Anchoring notes to references that may not be there would put them in the wrong places.

### Facsimile

Every text block of every page goes in its own frame where it stands on the scan:
- **Lines** are kept with forced line breaks, and paragraphs keep their indents and alignment.
- **First baseline:** one leading below the frame's top, on the scanned baseline.
- **The scan** is placed under the text on a locked layer that doesn't print, linked from `<name> Links/`.

Fonts are chosen per glyph style (`fontmatch.py`):
- **Comparison.** Each style's averaged glyphs are compared with every installed font of the same slant, drawn at the same x-height on a 72-row canvas.
- **Weight.** The body text is taken to be regular weight. Other styles match bold fonts only if their strokes are clearly heavier, because scanned ink spreads and would otherwise always pick bold.
- **Family.** Text styles prefer the body's family slightly, as books set notes and italics in the body face.
- **Size.** Point sizes follow from the scanned x-height and the font's.
- **Width.** A horizontal scale is measured per style. Each block is narrowed further if any of its lines would overflow its measure in the replacement font, which would push lines out of the frame.

### Checking

InDesign isn't available in this environment, so the files were checked in two other ways:

1. **Structure,** against IDML documents written by InDesign (SimpleIDML's test files): package layout, designmap, spreads, stories, styles, fonts and links.
2. **Rendering in Scribus,** which imports IDML. Results on the Young chapter:
   - **Reflow:** 26 pages, with every word of the text present (the only "missing" words are halves of joined hyphenations).
   - **Facsimile:** line for line over the scan. Scribus ignores `HorizontalScale`. With the scale folded into the point size, 97% of the words land in place. Most of the remaining 3% are hyphenated line ends that the text extraction joins.
   - **Geometry:** Scribus showed that single pages must be centred on the spread's origin.

On a synthetic page set in Liberation Serif at 11 pt, the font match picks Liberation Serif Regular at 10.98 pt.

**Still to check in InDesign:**
- that the files open without warnings
- line fit in facsimile with InDesign's own composer
- the reflow page count
- links to the scan images
