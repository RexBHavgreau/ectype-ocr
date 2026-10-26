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

Each export is a folder of its own, as docx-to-idml writes them: `<name>/<name>.idml`, with the images in `<name>/Links/`, linked relative to the IDML (`file:Links/p001.jpg`). The folder can be moved or sent on without relinking.

The export first makes sure segmentation and glyph grouping are current, and runs them if not. That analysis is what finds italics, running heads, superscripts and the scan's typefaces. No glyph tracing is involved. The export then builds a page layout (`layout.py`):
- **Blocks:** lines of one zone and size that overlap, with a normal baseline pitch.
- **Paragraphs:** a new one starts at an indented line or after a short line.
- **Alignment:** justified, centred, flush left or right, read from the line ends.
- **Folios:** each page's printed number, read from its running head or foot, and inferred for pages without one (the Young chapter: 168–193).

### Reflow: through docx-to-idml

Reflow is laid out by [docx-to-idml](https://github.com/RexBHavgreau/docx-to-idml), as decided for it on October 24 (its `docs/ENGINE.md`). Ectype writes the reading text as a Word manuscript (`docx.py`) and runs the installed converter's `convert` command (`converter.py`). It reads the converter's location from `program.json` in docx-to-idml's settings folder, or from `ECTYPE_DOCX2IDML` (a command line, e.g. `python -m docx2idml` to run it from source). The converter gives the book its masters, running heads, front matter and footnotes, in the fonts of its layout profile (chosen in the export dialog). Its report is shown in the Log. If docx-to-idml isn't installed, the dialog says so and links to its releases.

The manuscript (kept beside the IDML):
- **Headings:** Heading 1 (the largest on its page, a chapter title), Heading 2 and 3, and an all-capitals line under a chapter title as Author.
- **Body paragraphs** as Normal; set-off passages (quotations, verse) as Quote; centred and right-aligned lines kept so.
- **Italics** as italic runs; raised figures as superscripts.
- **Notes:** Word footnotes where their reference was found, i.e. a superscript figure on the same page matching the note's printed number. The others are gathered at the end under "Notes" and counted in the report, because the OCR often misses small reference figures. On the Young chapter it missed the "2" after "Inspiration." and the whole "2 Ibid., 230–31." line.
- **"Check OCR":** a character style on words read with confidence under 70 and not corrected in Review.
- **"Page Marker":** optionally, `{p. 172}` where each page of the scan begins, inside a paragraph when the page breaks in one.
- **Left out:** running heads and folios. Paragraphs split by a page break are joined, and line-end hyphens removed where they only split a word (the hyphenated form is kept when it occurs elsewhere in the book).

On the Young chapter, docx-to-idml lays the manuscript out as 33 pages: about 30 of text plus front matter, with placeholders such as [Book title] and [ISBN] that its report names.

### Facsimile

Every text block of every page goes in its own frame where it stands on the scan:
- **Lines** are kept with forced line breaks, and paragraphs keep their indents and alignment.
- **First baseline:** one leading below the frame's top, on the scanned baseline.
- **Pages:** facing pages as in the book (left-hand pages are versos), numbered from the folios.
- **The scan** is placed under the text on a layer of its own, "Scan", locked and non-printing.

It is written with docx-to-idml's IDML writer, copied into `src/ectype/idmlkit/` unchanged (MIT). That writer brings InDesign's own base settings and styles, and pages, spreads, sections and links as InDesign writes them. Its output has been opened in InDesign and Affinity Publisher. Ectype subclasses it for frames placed freely on the page, a leading per paragraph, and the scan layer.

Fonts are chosen per glyph style (`fontmatch.py`), and each becomes a character style named for what it is on the scan ("Text", "Text Italic", "Notes", "Running Head"…), so a font is changed in one place in InDesign:
- **Comparison.** Each style's averaged glyphs are compared with every installed font of the same slant, drawn at the same x-height on a 72-row canvas.
- **Weight.** The body text is taken to be regular weight. Other styles match bold fonts only if their strokes are clearly heavier, because scanned ink spreads and would otherwise always pick bold.
- **Family.** Text styles prefer the body's family slightly, as books set notes and italics in the body face.
- **Size.** Point sizes follow from the scanned x-height and the font's.
- **Width.** A horizontal scale is measured per style. Each block is narrowed further, locally, if any of its lines would overflow its measure in the replacement font, which would push lines out of the frame.

On a synthetic page set in Liberation Serif at 11 pt, the font match picks Liberation Serif Regular at 10.98 pt.

### Checking

Every IDML export, Reflow and Facsimile, is checked in the tests with docx-to-idml's `tools/idml_check.py`. It checks:
- the package structure
- the links between parts: frames, stories, pages, masters and sections
- Adobe's IDML schema (DOM 21.5), with jing

CI clones docx-to-idml for this and runs Reflow through it. Both exports of the Young chapter pass.

The first version of this export (October 26) wrote its own IDML. It failed the schema (wrong DOM version, frame insets written as attributes), set `PagesPerDocument` to the page count (InDesign then adds that many pages at the start) and had no page descriptors. All three were things docx-to-idml had already found and fixed against InDesign, so that writer was replaced.

**Still to check in InDesign:** a Facsimile export (line fit with InDesign's composer, the scan layer and links) and a Reflow export of a real chapter.
