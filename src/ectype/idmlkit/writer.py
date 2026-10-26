"""Write a Document (model.py) as an IDML package.

Starts from base/ (InDesign's own preferences, colours, fonts and root styles, made by
tools/make_base.py) and adds the book: styles, masters, spreads with pages and text frames,
stories, sections and footnote options. Elements InDesign writes with many attributes (spreads,
pages, frames, stories, sections) are cloned from base/snippets.xml.

Footnotes are written as decided after the Affinity tests (PLAN.md): the Footnote paragraph
style; the number and separator in a run of their own; the Footnote style's settings also on
every run of note text.
"""

import copy
import datetime
import math
import os
import re
import shutil
import uuid
import zipfile

from lxml import etree

from .images import image_info
from .model import Paragraph

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "base")
PKG = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"
MIMETYPE = b"application/vnd.adobe.indesign-idml-package"
DOM = "21.5"
NONE_CHAR = "CharacterStyle/$ID/[No character style]"
ROOT_PARA = "$ID/[No paragraph style]"
PAGE_GAP = 180                    # vertical space between spreads, as InDesign lays them out

# Order of a style's <Properties> children after BasedOn, AppliedFont and Leading (the schema's).
PROPS_ORDER = ["UnderlineColor", "UnderlineGapColor", "UnderlineType", "StrikeThroughColor",
               "StrikeThroughGapColor", "StrikeThroughType", "ParagraphBorderColor",
               "ParagraphBorderGapColor", "ParagraphBorderType", "SameParaStyleSpacing",
               "BalanceRaggedLines", "RuleAboveColor", "RuleAboveGapColor", "RuleAboveType",
               "RuleBelowColor", "RuleBelowGapColor", "RuleBelowType", "SpanSplitColumnCount",
               "PreviewColor"]

# Special characters: <?ACE n?> processing instructions.
SPECIAL = {"page_number": "18", "section_marker": "19"}

# Paragraph settings carried onto every run of footnote text (see the module docstring).
NOTE_ATTRS = ("PointSize", "FirstLineIndent", "LeftIndent", "RightIndent", "Justification",
              "FontStyle")


def _parse(path):
    return etree.parse(path, etree.XMLParser(remove_blank_text=True)).getroot()


HEADER = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def serialize(root):
    """A part as InDesign writes it: the XML declaration, then the whole document, so the
    <?aid ... type="document" ?> instruction before designmap.xml's root is kept (without it
    InDesign doesn't recognise the file at all)."""
    return HEADER + etree.tostring(root.getroottree(), encoding="UTF-8", xml_declaration=False)


def _num(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return ("%.4f" % v).rstrip("0").rstrip(".")
    return str(v)


def page_numbers(doc):
    """[(page number, its section)] for every page."""
    out = []
    sections = sorted(doc.sections, key=lambda s: s.page)
    number = 0
    for i in range(doc.page_count):
        sec = [s for s in sections if s.page <= i][-1]
        if sec.page == i:
            number = sec.start if sec.start is not None else number + 1
        else:
            number += 1
        out.append((number, sec))
    return out


def page_names(doc):
    """Each page's folio as printed: "iv", "12"."""
    names = []
    for number, sec in page_numbers(doc):
        if sec.numbering == "LowerRoman":
            names.append(roman(number))
        elif sec.numbering == "UpperRoman":
            names.append(roman(number, True))
        else:
            names.append(str(number))
    return names


def roman(n, upper=False):
    vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"),
            (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out.upper() if upper else out


class Ids:
    """Self ids that don't clash with those already in the base."""

    def __init__(self):
        top = 0
        for dirpath, _dirs, files in os.walk(BASE):
            for f in files:
                with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                    for m in re.finditer(r'Self="u([0-9a-f]+)"', fh.read()):
                        top = max(top, int(m.group(1), 16))
        self.n = max(top + 0x100, 0x1000)

    def __call__(self):
        self.n += 1
        return "u%x" % self.n


class Writer:
    def __init__(self, doc, relative_links=True):
        self.doc = doc
        self.relative_links = relative_links
        self.ids = Ids()
        self.snip = {el.tag: el for el in _parse(os.path.join(BASE, "snippets.xml"))}
        self.parts = {}           # package path -> root element
        self.layer = None

    # ---- package ----

    def write(self, path):
        doc = self.doc
        self.links_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "Links")
        self.linked = {}
        dm = _parse(os.path.join(BASE, "designmap.xml"))
        self.layer = dm.find("Layer").get("Self")
        self.styles = _parse(os.path.join(BASE, "Resources", "Styles.xml"))
        self.prefs = _parse(os.path.join(BASE, "Resources", "Preferences.xml"))
        self.graphic = _parse(os.path.join(BASE, "Resources", "Graphic.xml"))
        self._write_colors(dm)
        self._write_styles()
        self._write_preferences()
        self.section_ids = [self.ids() for _ in doc.sections]
        masters = self._write_masters()
        spreads, page_ids, frames = self._write_spreads(masters)
        stories = self._write_stories(frames)
        self._write_designmap(dm, masters, spreads, stories, page_ids)

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, zipfile.ZIP_STORED)
            files = {"designmap.xml": dm, "Resources/Styles.xml": self.styles,
                     "Resources/Preferences.xml": self.prefs, "Resources/Graphic.xml": self.graphic}
            files.update(self.parts)
            for name in ("META-INF/container.xml", "Resources/Graphic.xml", "Resources/Fonts.xml",
                         "XML/Tags.xml", "XML/BackingStory.xml"):
                files.setdefault(name, _parse(os.path.join(BASE, *name.split("/"))))
            for name, root in files.items():
                z.writestr(name, serialize(root))
        return path

    def _write_colors(self, dm):
        """CMYK process swatches, cloned from one InDesign wrote, each also listed in the
        designmap's colour group."""
        if not self.doc.colors:
            return
        template = [c for c in self.graphic.iter("Color") if c.get("Space") == "CMYK"
                    and c.get("Model") == "Process" and c.get("SwatchColorGroupReference")][0]
        group = dm.find("ColorGroup")
        n = len(group.findall("ColorGroupSwatch"))
        for name, cmyk in self.doc.colors:
            c = copy.deepcopy(template)
            ref = "%sColorGroupSwatch%x" % (group.get("Self"), n)
            n += 1
            c.set("Self", "Color/%s" % name)
            c.set("Name", name)
            c.set("ColorValue", " ".join(_num(v) for v in cmyk))
            c.set("SwatchColorGroupReference", ref)
            template.getparent().insert(list(template.getparent()).index(template) + 1, c)
            etree.SubElement(group, "ColorGroupSwatch", Self=ref, SwatchItemRef="Color/%s" % name)

    def _wrap(self, kind, el):
        """<idPkg:Kind> wrapper around a part's main element."""
        w = etree.Element("{%s}%s" % (PKG, kind), nsmap={"idPkg": PKG})
        w.set("DOMVersion", DOM)
        w.append(el)
        return w

    # ---- styles and preferences ----

    def _style_el(self, s, kind):
        tag = "ParagraphStyle" if kind == "paragraph" else "CharacterStyle"
        el = etree.Element(tag, Self="%s/%s" % (tag, s.name), Name=s.name, Imported="false")
        if kind == "paragraph":
            el.set("NextStyle", "ParagraphStyle/%s" % (s.next_style or s.name))
        # The housekeeping attributes InDesign writes on every style.
        for k, v in (("SplitDocument", "false"), ("EmitCss", "true"),
                     ("StyleUniqueId", str(uuid.uuid5(uuid.NAMESPACE_URL, tag + "/" + s.name))),
                     ("IncludeClass", "true"), ("ExtendedKeyboardShortcut", "0 0 0"),
                     ("EpubAriaRole", "")):
            el.set(k, v)
        if kind == "paragraph":
            for k in ("EmptyNestedStyles", "EmptyLineStyles", "EmptyGrepStyles"):
                el.set(k, "true")
        el.set("KeyboardShortcut", "0 0")
        for k, v in s.attrs.items():
            el.set(k, _num(v))
        props = etree.SubElement(el, "Properties")
        based = etree.SubElement(props, "BasedOn")
        if s.based_on:
            based.set("type", "object")
            based.text = "%s/%s" % (tag, s.based_on)
        else:
            based.set("type", "string")
            based.text = ROOT_PARA if kind == "paragraph" else "$ID/[No character style]"
        self._font_leading(props, s.font, s.leading)
        for name in sorted(s.props, key=lambda n: PROPS_ORDER.index(n) if n in PROPS_ORDER else 99):
            typ, value = s.props[name]
            etree.SubElement(props, name, type=typ).text = _num(value)
        if s.tabs:
            tl = etree.SubElement(props, "TabList", type="list")
            for alignment, position, leader in s.tabs:
                item = etree.SubElement(tl, "ListItem", type="record")
                etree.SubElement(item, "Alignment", type="enumeration").text = alignment
                etree.SubElement(item, "AlignmentCharacter", type="string").text = "."
                etree.SubElement(item, "Leader", type="string").text = leader or ""
                etree.SubElement(item, "Position", type="unit").text = _num(position)
        return el

    @staticmethod
    def _font_leading(props, font, leading):
        if font:
            etree.SubElement(props, "AppliedFont", type="string").text = font
        if leading is not None:
            if leading == "Auto":
                etree.SubElement(props, "Leading", type="enumeration").text = "Auto"
            else:
                etree.SubElement(props, "Leading", type="unit").text = _num(leading)

    def _write_styles(self):
        pg = self.styles.find("RootParagraphStyleGroup")
        for s in self.doc.paragraph_styles:
            pg.append(self._style_el(s, "paragraph"))
        cg = self.styles.find("RootCharacterStyleGroup")
        for s in self.doc.character_styles:
            cg.append(self._style_el(s, "character"))
        group = self.styles.find("RootCellStyleGroup")
        for cs in self.doc.cell_styles:
            el = etree.SubElement(group, "CellStyle", Self="CellStyle/%s" % cs.name,
                                  ExtendedKeyboardShortcut="0 0 0")
            for k, v in cs.attrs.items():
                el.set(k, _num(v))
            if cs.paragraph_style:
                el.set("AppliedParagraphStyle", "ParagraphStyle/%s" % cs.paragraph_style)
            if cs.fill:
                el.set("FillColor", "Color/%s" % cs.fill)
            el.set("KeyboardShortcut", "0 0")
            el.set("Name", cs.name)
            etree.SubElement(etree.SubElement(el, "Properties"), "BasedOn",
                             type="string").text = "$ID/[None]"
        group = self.styles.find("RootTableStyleGroup")
        for ts in self.doc.table_styles:
            el = etree.SubElement(group, "TableStyle", Self="TableStyle/%s" % ts.name,
                                  ExtendedKeyboardShortcut="0 0 0", Name=ts.name)
            for k, v in ts.attrs.items():
                el.set(k, _num(v))
            if ts.header_cell_style:
                el.set("HeaderRegionSameAsBodyRegion", "false")
                el.set("HeaderRegionCellStyle", "CellStyle/%s" % ts.header_cell_style)
            if ts.body_cell_style:
                el.set("BodyRegionCellStyle", "CellStyle/%s" % ts.body_cell_style)
            el.set("KeyboardShortcut", "0 0")
            etree.SubElement(etree.SubElement(el, "Properties"), "BasedOn",
                             type="string").text = "$ID/[No table style]"
        tocs = list(self.styles.iter("TOCStyle"))
        after = tocs[-1] if tocs else None
        for toc in self.doc.toc_styles:
            el = etree.Element("TOCStyle", Self="TOCStyle/%s" % toc.name, Title=toc.title,
                               Name=toc.name, RunIn="false", IncludeHidden="false",
                               IncludeBookDocuments="false", CreateBookmarks="true",
                               SetStoryDirection="Horizontal", NumberedParagraphs="ExcludeNumbers",
                               RemoveForcedLineBreak="false", MakeAnchor="false")
            if toc.title_style:
                el.set("TitleStyle", "ParagraphStyle/%s" % toc.title_style)
            for n, (style, level, fmt) in enumerate(toc.entries):
                e = etree.SubElement(el, "TOCStyleEntry", Self="%sTOCStyleEntry%d" % (self.ids(), n),
                                     Name=style, Level=str(level), PageNumberPosition="AfterEntry",
                                     Separator="$ID/^t", SortAlphabet="false")
                pr = etree.SubElement(e, "Properties")
                etree.SubElement(pr, "FormatStyle", type="object").text = "ParagraphStyle/%s" % fmt
                etree.SubElement(pr, "PageNumberStyle", type="string").text = "$ID/[Same Style]"
                etree.SubElement(pr, "SeparatorStyle", type="string").text = "$ID/[Same Style]"
            if after is not None:
                after.addnext(el)
            else:
                self.styles.append(el)
            after = el

    def _write_preferences(self):
        doc, p = self.doc, self.prefs
        dp = p.find("DocumentPreference")
        dp.set("PageWidth", _num(doc.width))
        dp.set("PageHeight", _num(doc.height))
        dp.set("FacingPages", _num(doc.facing))
        if doc.bleed:
            dp.set("DocumentBleedUniformSize", "false")
            for k in ("Top", "Bottom", "OutsideOrRight"):
                dp.set("DocumentBleed%sOffset" % k, _num(doc.bleed))
            dp.set("DocumentBleedInsideOrLeftOffset", "0")
        # Smart Text Reflow adds pages when a story runs past its estimate. In a facing
        # document it adds them two at a time, or one extra page would turn every later recto
        # (and its chapter opener master) into a verso.
        p.find("TextPreference").set("PreserveFacingPageSpreads",
                                     "true" if doc.facing else "false")
        # PagesPerDocument stays as InDesign writes it (1): it is the page count for a new
        # document, not this one's. Set to the real count, InDesign adds that many pages before
        # the file's own when opening it.
        mp = p.find("MarginPreference")
        m = doc.margins
        for k, v in (("Top", m["top"]), ("Bottom", m["bottom"]), ("Left", m["inside"]),
                     ("Right", m["outside"])):
            mp.set(k, _num(v))
        mp.set("ColumnsPositions", "0 %s" % _num(self.measure))
        fo = p.find("FootnoteOption")
        fn = doc.footnotes
        _attrs, _font, leading = doc.resolved(doc.footnote_style)
        lead = leading if isinstance(leading, (int, float)) else 11
        any_rule = fn.get("rule", False) or fn.get("continuation_rule", True)
        fo.set("FootnoteTextStyle", "ParagraphStyle/%s" % doc.footnote_style)
        fo.set("FootnoteMarkerStyle", "CharacterStyle/%s" % doc.footnote_marker_style
               if doc.style(doc.footnote_marker_style, "character") else NONE_CHAR)
        fo.set("SeparatorText", doc.footnote_separator)
        fo.set("Spacer", _num(fn.get("space_above", 12)))
        fo.set("SpaceBetween", _num(fn.get("space_between", 3)))
        fo.set("FootnoteFirstBaselineOffset", "LeadingOffset")
        fo.set("FootnoteMinimumFirstBaselineOffset",
               _num(lead + (fn.get("space_below_rule", 6) if any_rule else 0)))
        fo.set("NoSplitting", "false")
        fo.set("RuleOn", _num(fn.get("rule", False)))
        fo.set("ContinuingRuleOn", _num(fn.get("continuation_rule", True)))
        for prefix in ("Rule", "ContinuingRule"):
            fo.set(prefix + "Width", _num(fn.get("rule_length", 72)))
            fo.set(prefix + "LineWeight", "0.5")
            fo.set(prefix + "Offset", "0")
            fo.set(prefix + "LeftIndent", "0")
        grid = doc.baseline_grid
        if grid:
            gp = p.find("GridPreference")
            gp.set("BaselineStart", _num(grid.get("start", doc.margins["top"])))
            gp.set("BaselineDivision", _num(grid["division"]))
            gp.set("BaselineGridShown", _num(grid.get("shown", True)))
            gp.set("BaselineGridRelativeOption",
                   grid.get("relative", "TopOfMarginOfBaselineGridRelativeOption"))
        restart = fo.find("Properties/RestartNumbering")
        if restart is not None:
            restart.text = ("DontRestart" if fn.get("numbering") == "continuous"
                            else "SectionRestart")

    # ---- geometry ----

    @property
    def measure(self):
        m = self.doc.margins
        return self.doc.width - m["inside"] - m["outside"]

    def _is_recto(self, i):
        """In facing documents a page's side follows its number, as in InDesign: odd numbers
        are rectos, so a section restarting at 1 after an odd page puts two rectos together."""
        if not self.doc.facing:
            return True
        if not hasattr(self, "_numbers"):
            self._numbers = [n for n, _sec in self._page_numbers()]
        return self._numbers[i] % 2 == 1

    def _page_left(self, i):
        """x of the page's left edge in spread coordinates."""
        if not self.doc.facing:
            return -self.doc.width / 2
        return 0 if self._is_recto(i) else -self.doc.width

    def _spreads(self):
        """Pages grouped into spreads: a verso starts a spread, a recto ends one."""
        n = self.doc.page_count
        if not self.doc.facing:
            return [[i] for i in range(n)]
        groups = []
        for i in range(n):
            if not groups or not self._is_recto(i) or self._is_recto(groups[-1][-1]):
                groups.append([i])
            else:
                groups[-1].append(i)
        return groups

    def _page_numbers(self):
        return page_numbers(self.doc)

    def _page_names(self):
        return page_names(self.doc)

    def _descriptor(self, page, number, sec):
        """Page number information, as InDesign records it on each page: prefix, numbering
        style, continue numbering, include prefix, the number (twice), section marker."""
        props = page.find("Properties")
        desc = etree.Element("Descriptor", type="list")
        for typ, text in (("string", ""), ("enumeration", sec.numbering),
                          ("boolean", _num(sec.start is None)), ("boolean", "false"),
                          ("long", str(number)), ("long", str(number)), ("string", sec.marker)):
            etree.SubElement(desc, "ListItem", type=typ).text = text
        props.insert(0, desc)

    def _page_el(self, self_id, x, name, master_id, alt_layout, master_page=False):
        page = copy.deepcopy(self.snip["Page"] if not master_page else
                             self.snip["MasterSpread"].find("Page"))
        page.set("Self", self_id)
        page.set("ItemTransform", "1 0 0 1 %s %s" % (_num(x), _num(-self.doc.height / 2)))
        page.set("GeometricBounds", "0 0 %s %s" % (_num(self.doc.height), _num(self.doc.width)))
        page.set("Name", name)
        page.set("AppliedMaster", master_id or "n")
        page.set("OverrideList", "")
        page.set("AppliedAlternateLayout", alt_layout)
        desc = page.find("Properties/Descriptor")
        if desc is not None:
            desc.getparent().remove(desc)
        m = self.doc.margins
        mp = page.find("MarginPreference")
        for k, v in (("Top", m["top"]), ("Bottom", m["bottom"]), ("Left", m["inside"]),
                     ("Right", m["outside"])):
            mp.set(k, _num(v))
        mp.set("ColumnsPositions", "0 %s" % _num(self.measure))
        return page

    def _frame_el(self, story_id, left, top, width, height, master=False, valign=None):
        f = copy.deepcopy(self.snip["TextFrame"])
        if master:
            f.set("AllowOverrides", "true")
        if valign:
            f.find("TextFramePreference").set("VerticalJustification", valign)
        fid = self.ids()
        f.set("Self", fid)
        f.set("ParentStory", story_id)
        f.set("PreviousTextFrame", "n")
        f.set("NextTextFrame", "n")
        f.set("ItemLayer", self.layer)
        f.set("ItemTransform", "1 0 0 1 %s %s" % (_num(left + width / 2), _num(top + height / 2)))
        hw, hh = _num(width / 2), _num(height / 2)
        pts = f.findall("Properties/PathGeometry/GeometryPathType/PathPointArray/PathPointType")
        for pt, (x, y) in zip(pts, (("-" + hw, "-" + hh), ("-" + hw, hh), (hw, hh), (hw, "-" + hh))):
            xy = "%s %s" % (x, y)
            pt.set("Anchor", xy)
            pt.set("LeftDirection", xy)
            pt.set("RightDirection", xy)
        f.find("TextFramePreference").set("TextColumnFixedWidth", _num(width))
        return f

    # ---- masters, spreads ----

    def _write_masters(self):
        out = {}
        for m in self.doc.masters:
            out[m.name] = self.ids()
        frame_ids = {}            # (master name, frame key, side) -> frame id
        self.master_stories = []  # (story id, paragraphs)
        self.primary_frames = {}  # master name -> {side: primary frame element}
        for m in self._parents_first():
            ms = etree.Element("MasterSpread", self.snip["MasterSpread"].attrib)
            mid = out[m.name]
            ms.set("Self", mid)
            ms.set("Name", m.name)
            ms.set("NamePrefix", m.prefix)
            ms.set("BaseName", m.base)
            ms.set("PageCount", "2" if self.doc.facing else "1")
            ms.set("PrimaryTextFrame", "n")
            parent = out.get(m.based_on) if m.based_on else None
            if self.doc.facing:
                sides = [("verso", -self.doc.width), ("recto", 0)]
            else:
                sides = [("recto", -self.doc.width / 2)]
            pages = {}
            for side, x in sides:
                page = self._page_el(self.ids(), x, m.prefix, parent, "n", master_page=True)
                pairs = []
                for key in m.removes:
                    pid = frame_ids.get((m.based_on, key, side))
                    if pid:
                        pairs += [pid, "n"]
                page.set("OverrideList", " ".join(pairs))
                ms.append(page)
                pages[side] = x
            primary_story = None
            for fr in m.frames:
                x = pages.get(fr.side, pages.get("recto"))
                top, left, bottom, right = fr.bounds
                if fr.primary:
                    # One story threaded through the master's verso and recto frames.
                    new = primary_story is None
                    primary_story = primary_story or self.ids()
                    sid = primary_story
                else:
                    new, sid = True, self.ids()
                f = self._frame_el(sid, x + left, -self.doc.height / 2 + top, right - left,
                                   bottom - top, master=True, valign=fr.valign)
                ms.append(f)
                if new:
                    self.master_stories.append(
                        (sid, fr.paragraphs or [Paragraph("$ID/NormalParagraphStyle")]))
                if fr.primary:
                    prev = self.primary_frames.get(m.name, {})
                    if prev:
                        last = list(prev.values())[-1]
                        last.set("NextTextFrame", f.get("Self"))
                        f.set("PreviousTextFrame", last.get("Self"))
                    else:
                        ms.set("PrimaryTextFrame", f.get("Self"))
                    prev[fr.side] = f
                    self.primary_frames[m.name] = prev
                if fr.key:
                    frame_ids[(m.name, fr.key, fr.side)] = f.get("Self")
            self.parts["MasterSpreads/MasterSpread_%s.xml" % mid] = self._wrap("MasterSpread", ms)
        return out

    def _parents_first(self):
        done, out = set(), []
        masters = {m.name: m for m in self.doc.masters}

        def visit(m):
            if m.name in done:
                return
            done.add(m.name)
            if m.based_on in masters:
                visit(masters[m.based_on])
            out.append(m)
        for m in self.doc.masters:
            visit(m)
        return out

    def _write_spreads(self, masters):
        doc = self.doc
        default_master = masters[doc.masters[0].name] if doc.masters else None
        names = self._page_names()
        numbers = self._page_numbers()
        page_ids = [self.ids() for _ in range(doc.page_count)]
        story_ids = [self.ids() for _ in doc.stories]
        frames = {k: [] for k in range(len(doc.stories))}
        spreads = []
        page_els = {}
        names_by_id = {v: k for k, v in masters.items()}
        for k, group in enumerate(self._spreads()):
            sp = etree.Element("Spread", self.snip["Spread"].attrib)
            for child in self.snip["Spread"]:
                sp.append(copy.deepcopy(child))
            sid = self.ids()
            sp.set("Self", sid)
            sp.set("ItemTransform", "1 0 0 1 0 %s" % _num(k * (doc.height + PAGE_GAP)))
            sp.set("PageCount", str(len(group)))
            # The spine's position among the spread's pages: after the verso, if there is one.
            sp.set("BindingLocation", "0" if self._is_recto(group[0]) else "1")
            for i in group:
                mname = doc.page_masters.get(i)
                mid = masters.get(mname, default_master) if mname != "" else None
                page = self._page_el(page_ids[i], self._page_left(i), names[i], mid,
                                     self.section_ids[0])
                self._descriptor(page, *numbers[i])
                sp.append(page)
                page_els[i] = (page, names_by_id.get(mid))
            for i in group:
                for k2, story in enumerate(doc.stories):
                    if i in story.pages:
                        m = doc.margins
                        recto = self._is_recto(i)
                        left = self._page_left(i) + (m["inside"] if recto else m["outside"])
                        sink = story.sink if i == story.pages[0] else 0
                        top = -doc.height / 2 + m["top"] + sink
                        height = doc.height - m["top"] - m["bottom"] - sink
                        f = self._frame_el(story_ids[k2], left, top, self.measure, height,
                                           valign=story.valign)
                        sp.append(f)
                        frames[k2].append(f)
                        if story.linked:
                            page, mname = page_els[i]
                            side = "recto" if recto else "verso"
                            mf = self.primary_frames.get(mname, {}).get(side)
                            if mf is not None:
                                pairs = (page.get("OverrideList") or "").split()
                                page.set("OverrideList",
                                         " ".join(pairs + [mf.get("Self"), f.get("Self")]))
            for i in group:
                for placed in doc.images:
                    if placed.page == i:
                        x = self._page_left(i) + placed.left
                        y = -doc.height / 2 + placed.top
                        sp.append(self._image_rect(placed.image, "PlacedImage", x, y, placed.wrap))
            self.parts["Spreads/Spread_%s.xml" % sid] = self._wrap("Spread", sp)
            spreads.append(sid)
        for k2, fl in frames.items():
            for a, b in zip(fl, fl[1:]):
                a.set("NextTextFrame", b.get("Self"))
                b.set("PreviousTextFrame", a.get("Self"))
        self.story_ids = story_ids
        return spreads, page_ids, frames

    # ---- stories ----

    def _write_stories(self, frames):
        out = []
        todo = [(self.story_ids[k], story.paragraphs, story)
                for k, story in enumerate(self.doc.stories)]
        for sid, paragraphs, story in [(a, b, None) for a, b in self.master_stories] + todo:
            st = etree.Element("Story", self.snip["Story"].attrib)
            for child in self.snip["Story"]:
                st.append(copy.deepcopy(child))
            st.set("Self", sid)
            if story is not None and story.optical_margins:
                pref = st.find("StoryPreference")
                pref.set("OpticalMarginAlignment", "true")
                pref.set("OpticalMarginSize", _num(story.optical_margins))
            for n, para in enumerate(paragraphs):
                st.append(self._paragraph(para, last=(n == len(paragraphs) - 1)))
            self.parts["Stories/Story_%s.xml" % sid] = self._wrap("Story", st)
            out.append(sid)
        return out

    def _csr(self, style, attrs=None, font=None, leading=None):
        el = etree.Element("CharacterStyleRange",
                           AppliedCharacterStyle="CharacterStyle/%s" % style if style else NONE_CHAR)
        for k, v in (attrs or {}).items():
            el.set(k, _num(v))
        if font or leading is not None:
            self._font_leading(etree.SubElement(el, "Properties"), font, leading)
        return el

    @staticmethod
    def _content(parent, text):
        """Text as Content elements; a line break inside a paragraph is U+2028."""
        if text:
            etree.SubElement(parent, "Content").text = text.replace("\n", " ")

    def _paragraph(self, para, last, note=None, number=False):
        """note: (attrs, font, leading) of the Footnote style, for text inside a footnote;
        number: write the footnote number first (a note's first paragraph)."""
        psr = etree.Element("ParagraphStyleRange",
                            AppliedParagraphStyle="ParagraphStyle/%s" % para.style)
        for k, v in para.attrs.items():
            psr.set(k, _num(v))
        if number:
            # The footnote number and separator in a run of their own.
            attrs, font, leading = note
            num = self._csr(None, attrs, font, leading)
            c = etree.SubElement(num, "Content")
            pi = etree.ProcessingInstruction("ACE", "4")
            pi.tail = self.doc.footnote_separator
            c.append(pi)
            psr.append(num)
        for run in para.runs:
            if run.image is not None:
                csr = self._csr(None)
                csr.append(self._image_rect(run.image, "AnchoredImage", 0, 0, None))
                psr.append(csr)
                continue
            if run.table is not None:
                csr = self._csr(None)
                csr.append(self._table(run.table))
                etree.SubElement(csr, "Content")
                psr.append(csr)
                continue
            if run.footnote is not None:
                csr = self._csr(self.doc.footnote_marker_style
                                if self.doc.style(self.doc.footnote_marker_style, "character")
                                else None)
                csr.append(self._footnote(run.footnote))
                psr.append(csr)
                continue
            if note is not None:
                # The Footnote style's settings on the run (for Affinity), except what the
                # run's character style sets: a local setting would override it (italic in
                # a note, the Greek and Hebrew fonts).
                attrs, font, leading = note
                cs = self.doc.style(run.style, "character") if run.style else None
                merged = {k: v for k, v in attrs.items() if not (cs and k in cs.attrs)}
                merged.update(run.attrs)
                csr = self._csr(run.style, merged, None if cs and cs.font else font, leading)
            else:
                csr = self._csr(run.style, run.attrs)
            if run.special:
                c = etree.SubElement(csr, "Content")
                pi = etree.ProcessingInstruction("ACE", SPECIAL[run.special])
                pi.tail = run.text.replace("\n", "\u2028") if run.text else None
                c.append(pi)
            else:
                self._content(csr, run.text)
            psr.append(csr)
        if not len(psr):
            psr.append(self._csr(None))
        if not last:
            if psr[-1].find("Footnote") is not None:
                psr.append(self._csr(None))
            etree.SubElement(psr[-1], "Br")
        return psr

    # ---- images ----

    def _link(self, src):
        """Copy the image into the Links folder beside the IDML (once) and return its path."""
        if src not in self.linked:
            os.makedirs(self.links_dir, exist_ok=True)
            name = os.path.basename(src)
            dest = os.path.join(self.links_dir, name)
            n = 1
            while os.path.exists(dest) and os.path.abspath(dest) != os.path.abspath(src) and \
                    dest in self.linked.values():
                n += 1
                stem, ext = os.path.splitext(name)
                dest = os.path.join(self.links_dir, "%s-%d%s" % (stem, n, ext))
            if os.path.abspath(dest) != os.path.abspath(src):
                shutil.copy2(src, dest)
            self.linked[src] = dest
        return self.linked[src]

    def _uri(self, path):
        """The link to an image: relative to the IDML (file:Links/file.jpg), so the export folder
        can be moved or sent on; both InDesign and Affinity find images linked that way (g11,
        October 21). With relative_links=False, the full path as InDesign itself writes it."""
        if self.relative_links:
            rel = os.path.relpath(path, os.path.dirname(self.links_dir))
            return "file:" + rel.replace("\\", "/")
        return "file:" + os.path.abspath(path).replace("\\", "/")

    def _image_rect(self, img, kind, x, y, wrap):
        """A frame holding a linked image, cloned from InDesign's (reference 11)."""
        path = self._link(img.path)
        wpx, hpx, xppi, yppi, fmt = image_info(path)
        gw, gh = wpx / xppi * 72, hpx / yppi * 72          # the image's own size, points
        w = img.width or gw
        h = img.height or (w * gh / gw)
        scale = w / gw
        rect = copy.deepcopy(self.snip[kind][0])
        rect.set("Self", self.ids())
        if kind == "PlacedImage":
            rect.set("ItemLayer", self.layer)
        rect.set("ItemTransform", "1 0 0 1 %s %s" % (_num(x), _num(y)))
        pts = rect.findall("Properties/PathGeometry/GeometryPathType/PathPointArray/PathPointType")
        for pt, (px, py) in zip(pts, ((0, 0), (0, h), (w, h), (w, 0))):
            xy = "%s %s" % (_num(px), _num(py))
            pt.set("Anchor", xy)
            pt.set("LeftDirection", xy)
            pt.set("RightDirection", xy)
        twp = rect.find("TextWrapPreference")
        twp.set("TextWrapMode", "BoundingBoxTextWrap" if wrap else "None")
        off = twp.find("Properties/TextWrapOffset")
        for k, v in zip(("Top", "Left", "Bottom", "Right"), wrap or (0, 0, 0, 0)):
            off.set(k, _num(v))
        image = rect.find("Image")
        image.set("Self", self.ids())
        image.set("ItemTransform", "%s 0 0 %s 0 0" % (_num(scale), _num(scale)))
        image.set("ActualPpi", "%s %s" % (_num(xppi), _num(yppi)))
        image.set("EffectivePpi", "%s %s" % (_num(round(xppi / scale)), _num(round(yppi / scale))))
        type_name = "$ID/JPEG" if fmt == "JPEG" else "$ID/Portable Network Graphics (PNG)"
        image.set("ImageTypeName", type_name)
        gb = image.find("Properties/GraphicBounds")
        gb.set("Right", _num(gw))
        gb.set("Bottom", _num(gh))
        link = image.find("Link")
        link.set("Self", self.ids())
        link.set("LinkResourceURI", self._uri(path))
        link.set("LinkResourceFormat", type_name)
        st = os.stat(path)
        when = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
        filetime = int((st.st_mtime + 11644473600) * 10000000)     # 100 ns since 1601
        link.set("LinkImportStamp", "file %d %d" % (filetime, st.st_size))
        link.set("LinkImportModificationTime", when)
        link.set("LinkImportTime", when)
        link.set("LinkResourceSize", "0~%x" % st.st_size)
        return rect

    def _cell_insets(self, t, header):
        """(top, left, bottom, right) text insets of the table style's header or body cells."""
        ts = next((x for x in self.doc.table_styles if x.name == t.style), None)
        name = ts and (ts.header_cell_style if header else ts.body_cell_style)
        cs = next((x for x in self.doc.cell_styles if x.name == name), None)
        a = cs.attrs if cs else {}
        return tuple(a.get("Text%sInset" % k, 4) for k in ("Top", "Left", "Bottom", "Right"))

    def _row_height(self, t, r, insets):
        """An estimate of a row's height, which InDesign writes as SingleRowHeight and Affinity
        needs to break a table across frames. InDesign grows a row that is too short. From
        reference 07: one line = insets + about 0.68 of the type size; each more line adds the
        leading; characters average about half the type size in width."""
        top, left, bottom, right = insets
        best = 0
        for c, paras in enumerate(t.rows[r]):
            if not paras:
                continue
            span = t.spans.get((r, c), 1)
            width = sum(t.widths[c:c + span]) - left - right
            lines, size, leading = 0, 10, 12
            for para in paras:
                attrs, _font, lead = self.doc.resolved(para.style)
                size = float(attrs.get("PointSize", 12))
                leading = lead if isinstance(lead, (int, float)) else size * 1.2
                chars = sum(len(run.text) for run in para.runs)
                per_line = max(1, int(width / (size * 0.5)))
                lines += max(1, math.ceil(chars / per_line))
            best = max(best, top + bottom + size * 0.68 + leading * (lines - 1))
        return best or 12

    def _table(self, t):
        """A table as InDesign writes it: rows (with heights), columns, then cells row by row,
        named "column:row"; a cell covered by a span to its left is left out. Insets come from
        the table style's cell styles, as InDesign repeats them on rows, columns and cells."""
        tid = self.ids()
        ncols = len(t.widths)
        el = etree.Element("Table", Self=tid, HeaderRowCount=str(t.header_rows),
                           FooterRowCount="0", NumHeaderColumns="0",
                           HeaderColumnsPosition="HeaderColumnsLeft", TextTopInset="4",
                           TextLeftInset="4", TextBottomInset="4", TextRightInset="4",
                           ClipContentToTextCell="false",
                           BodyRowCount=str(len(t.rows) - t.header_rows), ColumnCount=str(ncols),
                           TableDirection="LeftToRightDirection")
        if t.style:
            el.set("AppliedTableStyle", "TableStyle/%s" % t.style)

        def insets(target, ins):
            for k, v in zip(("TextTopInset", "TextLeftInset", "TextBottomInset",
                             "TextRightInset"), ins):
                target.set(k, _num(v))
            target.set("ClipContentToTextCell", "false")

        for r in range(len(t.rows)):
            ins = self._cell_insets(t, r < t.header_rows)
            row = etree.SubElement(el, "Row", Self="%sRow%x" % (tid, r), Name=str(r))
            insets(row, ins)
            row.set("SingleRowHeight", _num(round(self._row_height(t, r, ins), 2)))
        for c, w in enumerate(t.widths):
            col = etree.SubElement(el, "Column", Self="%sColumn%x" % (tid, c), Name=str(c),
                                   ColumnType="BodyColumn")
            insets(col, self._cell_insets(t, False))
            col.set("SingleColumnWidth", _num(w))
        for r, row in enumerate(t.rows):
            for c in range(ncols):
                paras = row[c] if c < len(row) else []
                if paras is None:
                    continue
                cell = etree.SubElement(el, "Cell", Self="%si%x" % (tid, r * ncols + c),
                                        Name="%d:%d" % (c, r), RowSpan="1",
                                        ColumnSpan=str(t.spans.get((r, c), 1)),
                                        ColumnType="BodyColumn", CellType="TextTypeCell")
                insets(cell, self._cell_insets(t, r < t.header_rows))
                cell.set("AppliedCellStyle", "CellStyle/$ID/[None]")
                cell.set("AppliedCellStylePriority", "0")
                for n, para in enumerate(paras):
                    cell.append(self._paragraph(para, last=(n == len(paras) - 1)))
        return el

    def _footnote(self, paragraphs):
        fn = etree.Element("Footnote")
        attrs, font, leading = self.doc.resolved(self.doc.footnote_style)
        note = ({k: v for k, v in attrs.items() if k in NOTE_ATTRS}, font, leading)
        for n, para in enumerate(paragraphs):
            fn.append(self._paragraph(para, last=(n == len(paragraphs) - 1), note=note,
                                      number=(n == 0)))
        return fn

    # ---- designmap ----

    def _write_designmap(self, dm, masters, spreads, stories, page_ids):
        doc = self.doc
        dm.set("Name", doc.name + ".indd")
        backing = dm.find("{%s}BackingStory" % PKG)
        story_list = (dm.get("StoryList") or "").split() + stories
        dm.set("StoryList", " ".join(story_list))
        layers = dm.findall("Layer")
        anchor = layers[-1] if layers else dm.find("{%s}Tags" % PKG)
        pos = list(dm).index(anchor) + 1
        new = []
        for mid in masters.values():
            new.append(etree.Element("{%s}MasterSpread" % PKG,
                                     src="MasterSpreads/MasterSpread_%s.xml" % mid))
        for sid in spreads:
            new.append(etree.Element("{%s}Spread" % PKG, src="Spreads/Spread_%s.xml" % sid))
        sections = sorted(doc.sections, key=lambda s: s.page)
        for n, sec in enumerate(sections):
            el = copy.deepcopy(self.snip["Section"])
            el.set("Self", self.section_ids[n])
            end = sections[n + 1].page if n + 1 < len(sections) else doc.page_count
            el.set("Length", str(end - sec.page))
            # As InDesign writes them: the layout's name on the first section only, and each
            # section's AlternateLayoutLength the pages from its start to the end.
            el.set("AlternateLayoutLength", str(doc.page_count - sec.page))
            if n:
                el.set("AlternateLayout", "")
            el.set("PageStart", page_ids[sec.page])
            el.set("Marker", sec.marker)
            el.set("ContinueNumbering", _num(sec.start is None))
            if sec.start is not None:
                el.set("PageNumberStart", str(sec.start))
            elif "PageNumberStart" in el.attrib:
                del el.attrib["PageNumberStart"]
            el.set("IncludeSectionPrefix", "false")
            style = el.find("Properties/PageNumberStyle")
            if style is not None:
                style.text = sec.numbering
            new.append(el)
        for el in new:
            dm.insert(pos, el)
            pos += 1
        pos = list(dm).index(backing) + 1
        for sid in stories:
            dm.insert(pos, etree.Element("{%s}Story" % PKG, src="Stories/Story_%s.xml" % sid))
            pos += 1


def write_idml(doc, path, relative_links=True):
    """Write the IDML at path; linked images go to a Links folder beside it."""
    return Writer(doc, relative_links).write(path)


def export_book(doc, out_dir, name=None, relative_links=True):
    """An export as decided (October 21): a folder of its own holding the IDML and a Links
    folder with the images, linked relative to the IDML. Returns the IDML's path."""
    name = name or doc.name
    folder = os.path.join(out_dir, name)
    os.makedirs(folder, exist_ok=True)
    return write_idml(doc, os.path.join(folder, name + ".idml"), relative_links)
