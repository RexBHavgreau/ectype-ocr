"""Export a workspace as an InDesign document (IDML): the OCR text, laid out.

Two layouts:

- Reflow: the text flows into a fresh layout, one story threaded through text
  frames on as many pages as it needs, in the fonts chosen. Running heads and
  folios are dropped; paragraphs split by a page or column break are joined again,
  and line-end hyphens removed where they only split a word. Headings, notes and
  set-off passages (indented blocks: quotations, verse) get their own paragraph
  styles; notes are gathered at the end under "Notes", with their printed numbers.
- Facsimile: every text block of every page in its own frame where it stands on
  the scan, line for line (forced line breaks), in the fonts most like the scan's,
  chosen by `fontmatch`. The scan itself is placed on a locked, non-printing layer
  under the text, for checking.

Measurements arrive in scan pixels and are converted to points here. Pages are
single-sided, one to a spread, centred on the spread's origin: spread coordinates
are page coordinates shifted left by half the page's width and up by half its height.
"""

from __future__ import annotations

import itertools
import math
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

import numpy as np

from .layout import Block, Layout, LWord, Paragraph, join_lines, vocabulary

DOM = "8.0"
PKG = 'xmlns:idPkg="http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"'
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
LINE_BREAK = " "  # a forced line break inside a paragraph
TEXT_LAYER, SCAN_LAYER = "ua1", "ua2"  # fixed ids; generated ones start at u100
JUSTIFY = {"justify": "LeftJustified", "left": "LeftAlign", "center": "CenterAlign", "right": "RightAlign"}


@dataclass
class Face:
    family: str
    style: str = "Regular"


@dataclass
class IdmlOptions:
    mode: str = "reflow"  # or "facsimile"
    body: Face = field(default_factory=lambda: Face("EB Garamond", "Regular"))  # reflow
    italic_style: str = "Italic"  # reflow: the body family's italic
    body_size: float | None = None  # reflow, points; None: the scan's
    leading: float | None = None  # reflow, points; None: the scan's, scaled with the size
    faces: dict[int, Face] = field(default_factory=dict)  # facsimile: glyph style -> face, overriding the matches
    include_scan: bool = True  # facsimile: the scan on a locked, non-printing layer


class _Ids:
    def __init__(self):
        self._n = itertools.count(0x100)

    def __call__(self) -> str:
        return f"u{next(self._n):x}"


def _a(**attrs) -> str:
    return " ".join(f"{k}={quoteattr(str(v))}" for k, v in attrs.items() if v is not None)


def _num(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Character and paragraph runs


@dataclass
class Run:
    text: str
    face: Face | None = None
    size: float | None = None
    hscale: float | None = None  # percent
    superscript: bool = False
    italic: bool = False  # reflow: the body family's italic


@dataclass
class Para:
    style: str  # paragraph style name
    runs: list[Run]
    attrs: dict = field(default_factory=dict)  # local overrides: Justification, indents, ...
    leading: float | None = None


def _story_xml(story_id: str, paras: list[Para], footnote_free: bool = True) -> str:
    out = [HEAD, f"<idPkg:Story {PKG} DOMVersion=\"{DOM}\">",
           f"<Story {_a(Self=story_id, AppliedTOCStyle='n', TrackChanges='false', StoryTitle='$ID/', AppliedNamedGrid='n')}>",
           '<StoryPreference OpticalMarginAlignment="false" OpticalMarginSize="12" FrameType="TextFrameType" '
           'StoryOrientation="Horizontal" StoryDirection="LeftToRightDirection" />',
           '<InCopyExportOption IncludeGraphicProxies="true" IncludeAllResources="false" />']
    for k, p in enumerate(paras):
        attrs = {"AppliedParagraphStyle": f"ParagraphStyle/{p.style}", **p.attrs}
        out.append(f"<ParagraphStyleRange {_a(**attrs)}>")
        if p.leading is not None:
            out.append(f'<Properties><Leading type="unit">{_num(p.leading)}</Leading></Properties>')
        runs = p.runs or [Run("")]
        for j, r in enumerate(runs):
            ra = {"AppliedCharacterStyle": "CharacterStyle/$ID/[No character style]"}
            if r.face is not None:
                ra["FontStyle"] = r.face.style
            if r.size is not None:
                ra["PointSize"] = _num(r.size)
            if r.hscale is not None and abs(r.hscale - 100) >= 0.5:
                ra["HorizontalScale"] = _num(r.hscale)
            if r.superscript:
                ra["Position"] = "Superscript"
            out.append(f"<CharacterStyleRange {_a(**ra)}>")
            if r.face is not None:
                out.append(f'<Properties><AppliedFont type="string">{escape(r.face.family)}</AppliedFont></Properties>')
            if r.text:
                out.append(f"<Content>{escape(r.text)}</Content>")
            if j == len(runs) - 1 and k < len(paras) - 1:
                out.append("<Br />")
            out.append("</CharacterStyleRange>")
        out.append("</ParagraphStyleRange>")
    out += ["</Story>", "</idPkg:Story>"]
    return "\n".join(out)


def _merge(runs: list[Run]) -> list[Run]:
    out: list[Run] = []
    for r in runs:
        if out:
            p = out[-1]
            if (p.face, p.size, p.hscale, p.superscript, p.italic) == (r.face, r.size, r.hscale, r.superscript, r.italic):
                p.text += r.text
                continue
        out.append(Run(r.text, r.face, r.size, r.hscale, r.superscript, r.italic))
    return out


def _word_runs(w: LWord, base: Run) -> list[Run]:
    """A word as runs: its superscript tail (a note reference) apart."""
    sup = w.superscript if len(w.superscript) == len(w.text) else [False] * len(w.text)
    k = next((i for i, s in enumerate(sup) if s), len(w.text))
    runs = [Run(w.text[:k], base.face, base.size, base.hscale, False, base.italic)] if k else []
    if k < len(w.text):
        runs.append(Run(w.text[k:], base.face, base.size, base.hscale, True, base.italic))
    return runs


# ---------------------------------------------------------------------------
# Resources


def _styles_xml(para_styles: list[dict], body: Face) -> str:
    base_attrs = (
        'Imported="false" EmptyNestedStyles="true" EmptyLineStyles="true" EmptyGrepStyles="true" FillColor="Color/Black" '
        'FontStyle="Regular" PointSize="12" HorizontalScale="100" KerningMethod="$ID/Metrics" Ligatures="true" '
        'PageNumberType="AutoPageNumber" StrokeWeight="1" Tracking="0" Composer="HL Composer" DropCapCharacters="0" '
        'DropCapLines="0" BaselineShift="0" Capitalization="Normal" StrokeColor="Swatch/None" HyphenateLadderLimit="3" '
        'VerticalScale="100" LeftIndent="0" RightIndent="0" FirstLineIndent="0" AutoLeading="120" Hyphenation="true" '
        'HyphenateAfterFirst="2" HyphenateBeforeLast="2" HyphenateCapitalizedWords="true" HyphenateWordsLongerThan="5" '
        'NoBreak="false" HyphenationZone="36" SpaceBefore="0" SpaceAfter="0" Underline="false" OTFFigureStyle="Default" '
        'DesiredWordSpacing="100" MaximumWordSpacing="133" MinimumWordSpacing="80" DesiredLetterSpacing="0" '
        'MaximumLetterSpacing="0" MinimumLetterSpacing="0" DesiredGlyphScaling="100" MaximumGlyphScaling="100" '
        'MinimumGlyphScaling="100" StartParagraph="Anywhere" KeepAllLinesTogether="false" KeepWithNext="0" '
        'KeepFirstLines="2" KeepLastLines="2" Position="Normal" StrikeThru="false" CharacterAlignment="AlignEmCenter" '
        'KeepLinesTogether="false" Justification="LeftAlign" SingleWordJustification="FullyJustified" '
        'LastLineIndent="0" HyphenateLastWord="true" ParagraphBreakType="Anywhere" '
        'LeadingModel="LeadingModelAkiBelow" ParagraphDirection="LeftToRightDirection" KeyboardDirection="DefaultDirection"'
    )
    out = [HEAD, f"<idPkg:Styles {PKG} DOMVersion=\"{DOM}\">",
           '<RootCharacterStyleGroup Self="ua3">',
           '<CharacterStyle Self="CharacterStyle/$ID/[No character style]" Imported="false" Name="$ID/[No character style]" />',
           "</RootCharacterStyleGroup>",
           '<RootParagraphStyleGroup Self="ua4">',
           f'<ParagraphStyle Self="ParagraphStyle/$ID/[No paragraph style]" Name="$ID/[No paragraph style]" {base_attrs}>',
           '<Properties><Leading type="enumeration">Auto</Leading><TabList type="list"></TabList>'
           f'<AppliedFont type="string">{escape(body.family)}</AppliedFont></Properties>',
           "</ParagraphStyle>",
           '<ParagraphStyle Self="ParagraphStyle/$ID/NormalParagraphStyle" Name="$ID/NormalParagraphStyle" Imported="false" '
           'NextStyle="ParagraphStyle/$ID/NormalParagraphStyle" EmptyNestedStyles="true" EmptyLineStyles="true" EmptyGrepStyles="true">',
           '<Properties><BasedOn type="string">$ID/[No paragraph style]</BasedOn></Properties>',
           "</ParagraphStyle>"]
    for st in para_styles:
        name = st["name"]
        attrs = {"Self": f"ParagraphStyle/{name}", "Name": name, "Imported": "false",
                 "NextStyle": f"ParagraphStyle/{st.get('next', name)}"}
        attrs.update(st.get("attrs", {}))
        out.append(f"<ParagraphStyle {_a(**attrs)}>")
        props = ['<BasedOn type="string">$ID/NormalParagraphStyle</BasedOn>']
        if st.get("leading") is not None:
            props.append(f'<Leading type="unit">{_num(st["leading"])}</Leading>')
        if st.get("font"):
            props.append(f'<AppliedFont type="string">{escape(st["font"])}</AppliedFont>')
        out.append("<Properties>" + "".join(props) + "</Properties>")
        out.append("</ParagraphStyle>")
    out += ["</RootParagraphStyleGroup>",
            '<RootObjectStyleGroup Self="ua5">',
            '<ObjectStyle Self="ObjectStyle/$ID/[None]" Name="$ID/[None]" AppliedParagraphStyle="ParagraphStyle/$ID/[No paragraph style]" '
            'FillColor="Swatch/None" StrokeWeight="0" StrokeColor="Swatch/None" />',
            '<ObjectStyle Self="ObjectStyle/$ID/[Normal Graphics Frame]" Name="$ID/[Normal Graphics Frame]" '
            'AppliedParagraphStyle="ParagraphStyle/$ID/NormalParagraphStyle" FillColor="Swatch/None" StrokeWeight="0" StrokeColor="Swatch/None">'
            '<Properties><BasedOn type="object">ObjectStyle/$ID/[None]</BasedOn></Properties></ObjectStyle>',
            '<ObjectStyle Self="ObjectStyle/$ID/[Normal Text Frame]" Name="$ID/[Normal Text Frame]" '
            'AppliedParagraphStyle="ParagraphStyle/$ID/NormalParagraphStyle" FillColor="Swatch/None" StrokeWeight="0" StrokeColor="Swatch/None">'
            '<Properties><BasedOn type="object">ObjectStyle/$ID/[None]</BasedOn></Properties></ObjectStyle>',
            "</RootObjectStyleGroup>",
            "</idPkg:Styles>"]
    return "\n".join(out)


def _fonts_xml(faces: set[tuple[str, str]]) -> str:
    """The fonts the document uses, as InDesign lists them (other applications that read
    IDML, such as Scribus, find fonts through this list)."""
    from fontTools.ttLib import TTFont

    from .resources import font_index

    index = {(f.lower(), s.lower()): p for f, s, p in font_index()}
    out = [HEAD, f"<idPkg:Fonts {PKG} DOMVersion=\"{DOM}\">"]
    families: dict[str, list[str]] = {}
    for family, style in sorted(faces):
        families.setdefault(family, []).append(style)
    for k, (family, styles) in enumerate(sorted(families.items())):
        fam_id = f"di{k + 1:x}"
        out.append(f"<FontFamily {_a(Self=fam_id, Name=family)}>")
        for style in styles:
            path = index.get((family.lower(), style.lower()))
            ps, kind, status, version = f"{family}-{style}".replace(" ", ""), "OpenTypeTT", "NotAvailable", "$ID/"
            if path is not None:
                try:
                    font = TTFont(str(path), lazy=True, fontNumber=0)
                    ps = font["name"].getDebugName(6) or ps
                    version = font["name"].getDebugName(5) or version
                    kind = "OpenTypeCFF" if "CFF " in font else "OpenTypeTT" if path.suffix.lower() == ".otf" else "TrueType"
                    status = "Installed"
                except Exception:
                    pass
            name = f"{family} {style}"
            out.append(f"<Font {_a(Self=f'{fam_id}Fontn{name}', FontFamily=family, Name=name, PostScriptName=ps, Status=status, FontStyleName=style, FontType=kind, WritingScript=0, FullName=name, FullNameNative=name, FontStyleNameNative=style, PlatformName='$ID/', Version=version, TypekitID='$ID/')} />")
        out.append("</FontFamily>")
    out.append("</idPkg:Fonts>")
    return "\n".join(out)


def _faces_in(paras: list[Para], styles: list[dict]) -> set[tuple[str, str]]:
    faces = {(r.face.family, r.face.style) for p in paras for r in p.runs if r.face is not None}
    faces |= {(st["font"], st.get("attrs", {}).get("FontStyle", "Regular")) for st in styles if st.get("font")}
    return faces


def _graphic_xml() -> str:
    return "\n".join([
        HEAD, f"<idPkg:Graphic {PKG} DOMVersion=\"{DOM}\">",
        '<Color Self="Color/Black" Model="Process" Space="CMYK" ColorValue="0 0 0 100" ColorOverride="Specialblack" '
        'AlternateSpace="NoAlternateColor" AlternateColorValue="" Name="Black" ColorEditable="false" ColorRemovable="false" '
        'Visible="true" SwatchCreatorID="7937" />',
        '<Color Self="Color/Paper" Model="Process" Space="CMYK" ColorValue="0 0 0 0" ColorOverride="Specialpaper" '
        'AlternateSpace="NoAlternateColor" AlternateColorValue="" Name="Paper" ColorEditable="true" ColorRemovable="false" '
        'Visible="true" SwatchCreatorID="7937" />',
        '<Color Self="Color/Registration" Model="Registration" Space="CMYK" ColorValue="100 100 100 100" '
        'ColorOverride="Specialregistration" AlternateSpace="NoAlternateColor" AlternateColorValue="" Name="Registration" '
        'ColorEditable="false" ColorRemovable="false" Visible="true" SwatchCreatorID="7937" />',
        '<Swatch Self="Swatch/None" Name="None" ColorEditable="false" ColorRemovable="false" Visible="true" SwatchCreatorID="7937" />',
        '<StrokeStyle Self="StrokeStyle/$ID/Solid" Name="$ID/Solid" />',
        "</idPkg:Graphic>",
    ])


def _preferences_xml(width: float, height: float, pages: int, margins: tuple[float, float, float, float]) -> str:
    top, left, bottom, right = margins
    return "\n".join([
        HEAD, f"<idPkg:Preferences {PKG} DOMVersion=\"{DOM}\">",
        f"<DocumentPreference {_a(PageHeight=_num(height), PageWidth=_num(width), PagesPerDocument=pages, FacingPages='false', CreatePrimaryTextFrame='false', DocumentBleedTopOffset=0, DocumentBleedBottomOffset=0, DocumentBleedInsideOrLeftOffset=0, DocumentBleedOutsideOrRightOffset=0, DocumentBleedUniformSize='true', PageBinding='LeftToRight', Intent='PrintIntent', ColumnDirection='Horizontal', AllowPageShuffle='true', PreserveLayoutWhenShuffling='true')} />",
        f"<MarginPreference {_a(ColumnCount=1, ColumnGutter=12, Top=_num(top), Bottom=_num(bottom), Left=_num(left), Right=_num(right), ColumnDirection='Horizontal', ColumnsPositions=f'0 {_num(width - left - right)}')} />",
        '<TextPreference SmartTextReflow="true" AddPages="EndOfStory" LimitToMasterTextFrames="false" DeleteEmptyPages="true" '
        'PreserveFacingPageSpreads="false" />',
        '<ViewPreference HorizontalMeasurementUnits="Points" VerticalMeasurementUnits="Points" />',
        "</idPkg:Preferences>",
    ])


def _frame_xml(frame_id: str, story: str, layer: str, x0: float, y0: float, x1: float, y1: float,
               prev: str = "n", nxt: str = "n", first_baseline: str = "LeadingOffset") -> str:
    pts = "".join(f'<PathPointType Anchor="{_num(x)} {_num(y)}" LeftDirection="{_num(x)} {_num(y)}" RightDirection="{_num(x)} {_num(y)}" />'
                  for x, y in ((x0, y0), (x0, y1), (x1, y1), (x1, y0)))
    return (f"<TextFrame {_a(Self=frame_id, ParentStory=story, PreviousTextFrame=prev, NextTextFrame=nxt, ContentType='TextType', ItemLayer=layer, Locked='false', Visible='true', Name='$ID/', AppliedObjectStyle='ObjectStyle/$ID/[Normal Text Frame]', ItemTransform='1 0 0 1 0 0')}>"
            f'<Properties><PathGeometry><GeometryPathType PathOpen="false"><PathPointArray>{pts}</PathPointArray>'
            f"</GeometryPathType></PathGeometry></Properties>"
            f"<TextFramePreference {_a(TextColumnCount=1, FirstBaselineOffset=first_baseline, VerticalJustification='TopAlign', AutoSizingType='Off', InsetSpacing='0 0 0 0', IgnoreWrap='false')} />"
            "</TextFrame>")


def _image_xml(ids: _Ids, layer: str, uri: str, width: float, height: float, ppi: float) -> str:
    left, top = -width / 2, -height / 2
    pts = "".join(f'<PathPointType Anchor="{_num(x)} {_num(y)}" LeftDirection="{_num(x)} {_num(y)}" RightDirection="{_num(x)} {_num(y)}" />'
                  for x, y in ((left, top), (left, top + height), (left + width, top + height), (left + width, top)))
    return (f"<Rectangle {_a(Self=ids(), ContentType='GraphicType', ItemLayer=layer, Locked='true', Visible='true', Name='Scan', AppliedObjectStyle='ObjectStyle/$ID/[Normal Graphics Frame]', ItemTransform='1 0 0 1 0 0')}>"
            f'<Properties><PathGeometry><GeometryPathType PathOpen="false"><PathPointArray>{pts}</PathPointArray>'
            f"</GeometryPathType></PathGeometry></Properties>"
            f"<Image {_a(Self=ids(), ItemTransform=f'1 0 0 1 {_num(left)} {_num(top)}', ActualPpi=f'{_num(ppi)} {_num(ppi)}', EffectivePpi=f'{_num(ppi)} {_num(ppi)}', ImageTypeName='$ID/JPEG', Visible='true', Name='$ID/')}>"
            f'<Properties><Profile type="string">$ID/None</Profile><GraphicBounds Left="0" Top="0" Right="{_num(width)}" Bottom="{_num(height)}" /></Properties>'
            f"<Link {_a(Self=ids(), AssetURL='$ID/', AssetID='$ID/', LinkResourceURI=uri, LinkResourceFormat='$ID/JPEG', StoredState='Normal', LinkClassID=35906, LinkClientID=257, LinkResourceModified='false', LinkObjectModified='false', ShowInUI='true', CanEmbed='true', CanUnembed='true', CanPackage='true', ImportPolicy='NoAutoImport', ExportPolicy='NoAutoExport')} />"
            "</Image></Rectangle>")


def _spread_xml(spread_id: str, page_id: str, page_name: str, width: float, height: float,
                margins: tuple[float, float, float, float], items: list[str]) -> str:
    top, left, bottom, right = margins
    return "\n".join([
        HEAD, f"<idPkg:Spread {PKG} DOMVersion=\"{DOM}\">",
        f"<Spread {_a(Self=spread_id, PageCount=1, BindingLocation=0, AllowPageShuffle='true', ItemTransform='1 0 0 1 0 0', ShowMasterItems='true', FlattenerOverride='Default')}>",
        f"<Page {_a(Self=page_id, Name=page_name, AppliedMaster='n', GeometricBounds=f'0 0 {_num(height)} {_num(width)}', ItemTransform=f'1 0 0 1 {_num(-width / 2)} {_num(-height / 2)}', OverrideList='', MasterPageTransform='1 0 0 1 0 0')}>",
        f"<MarginPreference {_a(ColumnCount=1, ColumnGutter=12, Top=_num(top), Bottom=_num(bottom), Left=_num(left), Right=_num(right), ColumnDirection='Horizontal', ColumnsPositions=f'0 {_num(width - left - right)}')} />",
        "</Page>",
        *items,
        "</Spread>", "</idPkg:Spread>",
    ])


def _designmap(stories: list[str], spreads: list[str], layers: list[tuple[str, str, bool, bool]], first_page: str,
               page_count: int) -> str:
    out = [HEAD, '<?aid style="50" type="document" readerVersion="6.0" featureSet="257" product="8.0(370)" ?>',
           f"<Document {PKG} {_a(DOMVersion=DOM, Self='d', StoryList=' '.join(stories), ZeroPoint='0 0', ActiveLayer=layers[0][0])}>",
           '<idPkg:Graphic src="Resources/Graphic.xml" />',
           '<idPkg:Fonts src="Resources/Fonts.xml" />',
           '<idPkg:Styles src="Resources/Styles.xml" />',
           '<idPkg:Preferences src="Resources/Preferences.xml" />']
    for layer_id, name, locked, printable in layers:
        out.append(f"<Layer {_a(Self=layer_id, Name=name, Visible='true', Locked=str(locked).lower(), IgnoreWrap='false', ShowGuides='true', LockGuides='false', UI='true', Expendable='true', Printable=str(printable).lower())} />")
    out += [f'<idPkg:Spread src="Spreads/Spread_{s}.xml" />' for s in spreads]
    out.append(f"<Section {_a(Self='ua6', Length=page_count, Name='', ContinueNumbering='true', IncludeSectionPrefix='false', Marker='', PageStart=first_page, SectionPrefix='')}>"
               '<Properties><PageNumberStyle type="enumeration">Arabic</PageNumberStyle></Properties></Section>')
    out += [f'<idPkg:Story src="Stories/Story_{s}.xml" />' for s in stories]
    out.append("</Document>")
    return "\n".join(out)


def _package(dest: Path, files: dict[str, str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.adobe.indesign-idml-package", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", HEAD + (
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="designmap.xml" media-type="text/xml" /></rootfiles></container>'),
            compress_type=zipfile.ZIP_DEFLATED)
        for name, text in files.items():
            z.writestr(name, text.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
    tmp.replace(dest)


# ---------------------------------------------------------------------------
# Measurements


def _pt(px: float, dpi: float) -> float:
    return px * 72.0 / dpi


def _pitch(lines) -> float | None:
    pitches = [b.baseline - a.baseline for a, b in zip(lines, lines[1:]) if b.baseline > a.baseline]
    return float(np.median(pitches)) if pitches else None


def _body_metrics(layout: Layout) -> dict:
    """The text block's typical margins, baseline pitch and first-line indent, in px."""
    lefts, rights, tops, bottoms, pitches, indents = [], [], [], [], [], []
    for page in layout.pages:
        text = [b for b in page.blocks if b.kind == "text"]
        if not text:
            continue
        lefts.append(min(b.box[0] for b in text))
        rights.append(page.width_px - max(b.box[2] for b in text))
        tops.append(min(b.lines[0].baseline - 2.2 * page.xh for b in text))
        bottoms.append(page.height_px - max(b.lines[-1].baseline for b in text))
        for b in text:
            p = _pitch(b.lines)
            if p:
                pitches.append(p)
            indents += [para.indent for para in b.paragraphs if para.indent > 0.8 * page.xh]
    med = lambda v, d: float(np.median(v)) if v else d
    return {"left": med(lefts, 300), "right": med(rights, 300), "top": med(tops, 300), "bottom": med(bottoms, 300),
            "pitch": med(pitches, 2.6 * layout.xh), "indent": med(indents, 3 * layout.xh)}


# ---------------------------------------------------------------------------
# Facsimile


def _facsimile(layout: Layout, dest: Path, matches: dict, options: IdmlOptions, workspace: Path) -> dict:
    ids = _Ids()
    text_layer, scan_layer = TEXT_LAYER, SCAN_LAYER
    styles_used: dict[str, dict] = {}
    body_style = next((sid for sid, m in sorted(matches.items(), key=lambda kv: -kv[1].instances)
                       if m.face and m.zone == "" and not m.italic), None)

    def face_for(style: int, italic: bool, zone: str, size_class: float) -> tuple[Face, float, float]:
        """(face, point size, horizontal scale %) for a glyph style."""
        m = matches.get(style)
        if style in options.faces:
            f = options.faces[style]
        elif m is not None and m.face is not None:
            f = Face(m.face.family, m.face.style)
        else:  # a style too rare to match: the nearest matched style of its slant and zone
            near = [x for x in matches.values() if x.face and x.italic == italic and x.zone == zone] or \
                   [x for x in matches.values() if x.face and x.italic == italic] or [x for x in matches.values() if x.face]
            if not near:
                return options.body, 10.0, 100.0
            n = max(near, key=lambda x: x.instances)
            size = (n.point_size or 10.0) * (size_class / (n.size_class or 1.0))
            return Face(n.face.family, n.face.style), round(size, 2), 100.0
        size = m.point_size if m is not None and m.point_size else 10.0
        return f, size, round(100 * (m.horizontal_scale if m is not None else 1.0), 1)

    kinds = {"text": "Facsimile Text", "heading": "Facsimile Heading", "note": "Facsimile Note",
             "head": "Facsimile Running Head", "foot": "Facsimile Running Foot"}
    for name in kinds.values():
        styles_used[name] = {"name": name, "attrs": {"Hyphenation": "false", "MinimumWordSpacing": 60, "MaximumWordSpacing": 200,
                                                     "MinimumGlyphScaling": 97, "MaximumGlyphScaling": 103}}
    stories, spreads, files = [], [], {}
    all_paras: list[Para] = []
    links = dest.with_name(dest.stem + " Links")
    first_page = None
    for number, page in enumerate(layout.pages, start=1):
        dpi = page.dpi
        w, h = _pt(page.width_px, dpi), _pt(page.height_px, dpi)
        left, top = -w / 2, -h / 2
        items = []
        if options.include_scan:
            links.mkdir(parents=True, exist_ok=True)
            jpg = links / f"{page.name}.jpg"
            from PIL import Image

            with Image.open(workspace / "pages" / f"{page.name}.png") as im:
                im.convert("L").save(jpg, "JPEG", quality=85, dpi=(dpi, dpi))
            uri = "file:" + quote(jpg.resolve().as_posix(), safe="/:")
            items.append(_image_xml(ids, scan_layer, uri, w, h, dpi))
        for block in page.blocks:
            story = ids()
            paras, (x0, y0, x1, y1) = _facsimile_block(block, dpi, face_for, kinds[block.kind])
            all_paras += paras
            stories.append(story)
            files[f"Stories/Story_{story}.xml"] = _story_xml(story, paras)
            items.append(_frame_xml(ids(), story, text_layer, left + x0, top + y0, left + x1, top + y1))
        spread, page_id = ids(), ids()
        first_page = first_page or page_id
        spreads.append(spread)
        files[f"Spreads/Spread_{spread}.xml"] = _spread_xml(spread, page_id, str(number), w, h, (0, 0, 0, 0), items)
    if not layout.pages:
        raise ValueError("no pages with OCR to export")
    p0 = layout.pages[0]
    default_face = face_for(body_style, False, "", 1.0)[0] if body_style is not None else options.body
    files["Resources/Styles.xml"] = _styles_xml(list(styles_used.values()), default_face)
    files["Resources/Fonts.xml"] = _fonts_xml(_faces_in(all_paras, []) | {(default_face.family, default_face.style)})
    files["Resources/Graphic.xml"] = _graphic_xml()
    files["Resources/Preferences.xml"] = _preferences_xml(_pt(p0.width_px, p0.dpi), _pt(p0.height_px, p0.dpi), len(layout.pages), (0, 0, 0, 0))
    layers = [(text_layer, "Text", False, True)] + ([(scan_layer, "Scan", True, False)] if options.include_scan else [])
    files["designmap.xml"] = _designmap(stories, spreads, layers, first_page, len(layout.pages))
    _package(dest, files)
    return {"pages": len(layout.pages), "stories": len(stories), "links": str(links) if options.include_scan else None}


def _facsimile_block(block: Block, dpi: float, face_for, style_name: str) -> tuple[list[Para], tuple[float, float, float, float]]:
    """A block's paragraphs, line for line, and its frame (page points)."""
    lines = block.lines
    pitch = _pitch(lines)
    styles = [w.style for line in lines for w in line.words if w.style >= 0]
    main = max(set(styles), key=styles.count) if styles else -1
    italic_main = sum(w.italic for line in lines for w in line.words) > sum(1 for line in lines for _ in line.words) / 2
    zone = block.kind if block.kind in ("head", "foot") else ""
    face, size, hscale = face_for(main, italic_main, zone, block.size_class)
    leading = _pt(pitch, dpi) if pitch else round(1.2 * size, 2)
    x0, y0, x1, y1 = _block_frame(block, dpi, leading)
    left = min(p.left for p in block.paragraphs)

    # Each line set in its replacement fonts must fit its measure, or it would wrap and
    # push the block's last lines out of the frame: narrow the block's type just enough.
    worst = 0.0
    for p in block.paragraphs:
        for k, line in enumerate(p.lines):
            start = _pt((p.lines[0].box[0] if k == 0 else p.left), dpi)
            natural = 0.0
            for j, w in enumerate(line.words):
                f, s, hs = face_for(w.style if w.style >= 0 else main, w.italic, zone, block.size_class)
                natural += ((_text_width(f, w.text + (" " if j < len(line.words) - 1 else ""), s) or 0.0) * hs / 100)
            worst = max(worst, natural / max(1.0, x1 - start))
    fit = min(1.0, 0.97 / worst) if worst > 0 else 1.0

    paras = []
    for p in block.paragraphs:
        runs: list[Run] = []
        for k, line in enumerate(p.lines):
            for j, w in enumerate(line.words):
                f, s, hs = face_for(w.style if w.style >= 0 else main, w.italic, zone, block.size_class)
                runs += _word_runs(w, Run("", f, s, round(hs * fit, 1)))
                if j < len(line.words) - 1:
                    runs.append(Run(" ", f, s, round(hs * fit, 1)))
            if k < len(p.lines) - 1:
                runs.append(Run(LINE_BREAK, face, size, round(hscale * fit, 1)))
        first = p.lines[0].box[0]
        attrs = {"Justification": JUSTIFY[p.align]}
        if p.align in ("left", "justify"):
            attrs["LeftIndent"] = _num(_pt(p.left - left, dpi))
            attrs["FirstLineIndent"] = _num(_pt(first - p.left, dpi))
        paras.append(Para(style_name, _merge(runs), attrs, leading))
    return paras, (x0, y0, x1, y1)


def _block_frame(block: Block, dpi: float, leading_pt: float) -> tuple[float, float, float, float]:
    """The block's frame in page points: the first baseline one leading below the top,
    and a leading of room under the last (a line needs its descent inside the frame).
    Justified lines end at the widest one's end; where lines aren't justified, the frame
    is wider than the ink, so a replacement font a little wider doesn't wrap them."""
    lines = block.lines
    left = min(p.left for p in block.paragraphs)
    right = max(max(l.box[2] for l in p.lines) for p in block.paragraphs)
    width = right - left
    if any(p.align == "center" for p in block.paragraphs):
        centre = float(np.mean([(l.box[0] + l.box[2]) / 2 for l in lines]))
        half = max(l.box[2] - l.box[0] for l in lines) / 2 * 1.15
        left, right = centre - half, centre + half
    elif not all(p.align == "justify" for p in block.paragraphs):
        right += 0.12 * width
    y0 = _pt(lines[0].baseline, dpi) - leading_pt
    y1 = _pt(lines[-1].baseline, dpi) + leading_pt
    return _pt(left, dpi), y0, _pt(right, dpi), y1


# ---------------------------------------------------------------------------
# Reflow


def _reflow(layout: Layout, dest: Path, matches: dict, options: IdmlOptions) -> dict:
    ids = _Ids()
    layer = TEXT_LAYER
    m = _body_metrics(layout)
    dpi = layout.text_dpi()
    width = float(np.median([_pt(p.width_px, p.dpi) for p in layout.pages]))
    height = float(np.median([_pt(p.height_px, p.dpi) for p in layout.pages]))
    margins = (_pt(m["top"], dpi), _pt(m["left"], dpi), _pt(m["bottom"], dpi), _pt(m["right"], dpi))
    body_match = next((x for x in sorted(matches.values(), key=lambda x: -x.instances) if x.zone == "" and not x.italic and x.point_size), None)
    scan_size = body_match.point_size if body_match else 11.0
    size = options.body_size or scan_size
    leading = options.leading or round(_pt(m["pitch"], dpi) * size / scan_size, 2)
    indent = round(_pt(m["indent"], dpi) * size / scan_size, 2)
    body, italic = options.body, Face(options.body.family, options.italic_style)

    vocab = vocabulary(layout)
    paras: list[Para] = []
    notes: list[Para] = []
    prev_open = False  # the last paragraph may continue in the next block
    for page in layout.pages:
        text_left = min((b.box[0] for b in page.blocks if b.kind == "text"), default=0)
        for block in page.blocks:
            if block.kind in ("head", "foot"):
                continue
            ratio = block.size_class or 1.0
            if block.kind == "note":
                for p in block.paragraphs:
                    notes.append(Para("Note", _reflow_runs(p, vocab, body, italic, None)))
                continue
            set_off = block.kind == "text" and block.box[0] - text_left > 2 * page.xh
            for k, p in enumerate(block.paragraphs):
                heading_size = round(size * ratio, 2) if block.kind == "heading" else None
                runs = _reflow_runs(p, vocab, body, italic, heading_size)
                continues = (k == 0 and prev_open and paras and block.kind == "text" and not set_off
                             and paras[-1].style in ("Body", "Body First") and p.indent < 0.5 * page.xh
                             and p.lines[0].box[0] - block.box[0] < 0.8 * page.xh)
                if continues:
                    _join_para(paras[-1], runs, vocab)
                else:
                    if block.kind == "heading":
                        style = "Heading"
                    elif set_off:
                        style = "Extract"
                    elif paras and paras[-1].style == "Heading" or p.indent < 0.5 * page.xh:
                        style = "Body First"
                    else:
                        style = "Body"
                    attrs = {} if style in ("Body", "Body First") else {"Justification": JUSTIFY[p.align]}
                    paras.append(Para(style, runs, attrs, round(heading_size * 1.2, 2) if heading_size else None))
                last = p.lines[-1]
                prev_open = (block.kind == "text" and not set_off and p.align == "justify"
                             and block.box[2] - last.box[2] <= page.xh and len(p.lines) >= 1)
    if notes:
        paras.append(Para("Notes Heading", [Run("Notes")]))
        paras += notes

    para_styles = [
        {"name": "Body", "attrs": {"Justification": "LeftJustified", "FirstLineIndent": _num(indent), "PointSize": _num(size), "FontStyle": body.style},
         "leading": leading, "font": body.family},
        {"name": "Body First", "next": "Body", "attrs": {"Justification": "LeftJustified", "FirstLineIndent": 0, "PointSize": _num(size), "FontStyle": body.style},
         "leading": leading, "font": body.family},
        {"name": "Extract", "attrs": {"Justification": "LeftJustified", "LeftIndent": _num(2 * indent), "RightIndent": _num(2 * indent),
                                      "SpaceBefore": _num(leading / 2), "SpaceAfter": _num(leading / 2), "PointSize": _num(size), "FontStyle": body.style},
         "leading": leading, "font": body.family},
        {"name": "Heading", "next": "Body First", "attrs": {"Justification": "CenterAlign", "PointSize": _num(round(size * 1.3, 2)), "SpaceBefore": _num(leading),
                                                            "SpaceAfter": _num(leading / 2), "KeepWithNext": 2, "Hyphenation": "false", "FontStyle": body.style},
         "leading": round(leading * 1.3, 2), "font": body.family},
        {"name": "Notes Heading", "next": "Note", "attrs": {"Justification": "LeftAlign", "PointSize": _num(size), "SpaceBefore": _num(2 * leading),
                                                             "SpaceAfter": _num(leading / 2), "KeepWithNext": 2, "FontStyle": body.style},
         "leading": leading, "font": body.family},
        {"name": "Note", "attrs": {"Justification": "LeftJustified", "PointSize": _num(round(size * 0.8, 2)), "SpaceAfter": _num(leading / 4), "FontStyle": body.style},
         "leading": round(leading * 0.8, 2), "font": body.family},
    ]

    # Enough pages for the text: its width set in the body font, over the line length,
    # plus a line per paragraph end, over lines per page; a little spare.
    measure = width - margins[1] - margins[3]
    total = _text_width(options.body, "".join(r.text for p in paras for r in p.runs), size)
    lines = (total / measure if total else sum(len(r.text) for p in paras for r in p.runs) * size * 0.45 / measure) * 1.03 + len(paras) * 0.5
    per_page = max(1, int((height - margins[0] - margins[2]) / leading))
    count = max(1, math.ceil(lines / per_page * 1.05) + 2)  # spare pages; InDesign's Smart Text Reflow can drop them

    story = ids()
    frames = [ids() for _ in range(count)]
    spreads, files, first_page = [], {}, None
    for k in range(count):
        left, top = -width / 2, -height / 2
        item = _frame_xml(frames[k], story, layer, left + margins[1], top + margins[0], left + width - margins[3], top + height - margins[2],
                          prev=frames[k - 1] if k else "n", nxt=frames[k + 1] if k + 1 < count else "n", first_baseline="AscentOffset")
        spread, page_id = ids(), ids()
        first_page = first_page or page_id
        spreads.append(spread)
        files[f"Spreads/Spread_{spread}.xml"] = _spread_xml(spread, page_id, str(k + 1), width, height, margins, [item])
    files[f"Stories/Story_{story}.xml"] = _story_xml(story, paras)
    files["Resources/Styles.xml"] = _styles_xml(para_styles, body)
    files["Resources/Fonts.xml"] = _fonts_xml(_faces_in(paras, para_styles) | {(italic.family, italic.style)})
    files["Resources/Graphic.xml"] = _graphic_xml()
    files["Resources/Preferences.xml"] = _preferences_xml(width, height, count, margins)
    files["designmap.xml"] = _designmap([story], spreads, [(layer, "Text", False, True)], first_page, count)
    _package(dest, files)
    return {"pages": count, "paragraphs": len(paras), "notes": len(notes), "body": f"{body.family} {body.style}",
            "size": size, "leading": leading}


def _reflow_runs(p: Paragraph, vocab: set[str], body: Face, italic: Face, size: float | None) -> list[Run]:
    runs: list[Run] = []
    words = join_lines(p.lines, vocab)
    for j, w in enumerate(words):
        face = italic if w.italic else body
        runs += _word_runs(w, Run("", face if w.italic else None, size, None, False, w.italic))
        if j < len(words) - 1:
            runs.append(Run(" ", face if w.italic and words[j + 1].italic else None, size, None, False, w.italic and words[j + 1].italic))
    return _merge(runs)


def _join_para(para: Para, runs: list[Run], vocab: set[str]) -> None:
    """Continue a paragraph across a page or block break, mending a split word."""
    if para.runs and runs:
        last, first = para.runs[-1], runs[0]
        tail = last.text.rstrip()
        if tail.endswith(("-", "‐", "¬")) and len(tail) > 1 and tail[-2].isalpha() and first.text[:1].islower():
            head = tail.split(" ")[-1][:-1]
            nxt = first.text.split(" ")[0]
            if f"{head}-{nxt}".lower() not in vocab:
                last.text = tail[:-1]
                para.runs = _merge(para.runs + runs)
                return
        last.text = last.text.rstrip() + " "
    para.runs = _merge(para.runs + runs)


@lru_cache(maxsize=64)
def _advances(family: str, style: str) -> tuple[dict[int, int], int, int] | None:
    """(code point -> advance, units per em, a default advance) of an installed face."""
    from fontTools.ttLib import TTFont

    from .resources import font_index

    path = next((p for f, s, p in font_index() if f.lower() == family.lower() and s.lower() == style.lower()), None)
    path = path or next((p for f, s, p in font_index() if f.lower() == family.lower()), None)
    if path is None:
        return None
    font = TTFont(str(path), lazy=True, fontNumber=0)
    cmap, hmtx = font.getBestCmap() or {}, font["hmtx"]
    advances = {cp: hmtx[name][0] for cp, name in cmap.items()}
    return advances, font["head"].unitsPerEm, advances.get(ord("n"), font["head"].unitsPerEm // 2)


def _text_width(face: Face, text: str, size: float) -> float | None:
    """Width of the text set in the face, in points, from the font's advance widths."""
    metrics = _advances(face.family, face.style)
    if metrics is None:
        return None
    advances, upm, default = metrics
    return sum(advances.get(ord(c), default) for c in text) * size / upm


# ---------------------------------------------------------------------------


def export_idml(workspace: Path, dest: Path, options: IdmlOptions | None = None, layout: Layout | None = None,
                matches: dict | None = None) -> dict:
    """Write the workspace's text as an IDML document: reflowed or in facsimile."""
    from .layout import build_layout

    options = options or IdmlOptions()
    workspace, dest = Path(workspace), Path(dest)
    layout = layout or build_layout(workspace)
    if not layout.pages or not any(p.blocks for p in layout.pages):
        raise ValueError("no OCR text to export; run OCR first")
    if matches is None:
        from .fontmatch import match_styles

        matches = match_styles(workspace, layout) if (workspace / "cluster" / "clusters.json").is_file() else {}
    if options.mode == "facsimile":
        out = _facsimile(layout, dest, matches, options, workspace)
    else:
        out = _reflow(layout, dest, matches, options)
    return {"idml": str(dest), "mode": options.mode, **out}
