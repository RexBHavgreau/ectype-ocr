"""Manuscript Mode (manuscript.py): hands, reading lines with one or several, corrections, the
lines changed in the Kraken view, sample pages. Kraken stands in as a fake session."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

import pytest
from PIL import Image, ImageDraw

from ectype import manuscript
from ectype.project import Project


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


class FakeKraken:
    """Kraken's line-finding and reading, made up: three lines a page; each hand reads a line
    as its texts say, its confidence too."""

    def __init__(self, readings: dict[str, list[tuple[str, float]]]):
        self.readings = readings  # model file -> [(text, confidence)] for lines 0, 1, 2
        self.segmented, self.read = 0, []

    def segment(self, image, direction, model=None):
        self.segmented += 1
        lines = []
        for k in range(3):
            y = 40 + 60 * k
            lines.append({"baseline": [[20, y + 30], [380, y + 30]],
                          "boundary": [[20, y], [380, y], [380, y + 40], [20, y + 40], [20, y]], "region": 0})
        return {"lines": lines, "regions": [[[10, 10], [390, 10], [390, 230], [10, 230], [10, 10]]]}

    def read_baselines(self, model_file, image, direction, lines):
        self.read.append((Path(model_file).parent.name, len(lines)))
        out = []
        for line in lines:
            k = (line["boundary"][0][1] - 40) // 60
            text, conf = self.readings[model_file][k]
            x0, x1 = 380, 20  # right to left: the first letter on the right
            step = (x0 - x1) / max(1, len(text))
            chars = [[c, [int(x0 - (i + 1) * step), 0, int(x0 - i * step), 1], conf] for i, c in enumerate(text)]
            out.append({"text": text, "chars": chars, "conf": conf})
        return out


def _book(tmp_path, pages=1) -> Project:
    p = Project(tmp_path / "book.ectype")
    (p.workspace / "pages").mkdir(parents=True)
    for n in range(1, pages + 1):
        im = Image.new("L", (400, 250), 255)
        d = ImageDraw.Draw(im)
        for k in range(3):
            d.rectangle([40, 50 + 60 * k, 360, 70 + 60 * k], fill=0)
        im.save(p.workspace / "pages" / f"p{n:03d}.png", dpi=(300, 300))
    p.set_script("manuscript")
    return p


def _hand(tmp_path, name: str, lang: str = "heb") -> str:
    model = tmp_path / f"{name}.safetensors"
    model.write_bytes(name.encode())
    return manuscript.new_hand(name, lang, str(model))


def test_hands_are_named_and_kept_per_language(data, tmp_path):
    a = _hand(tmp_path, "Sephardi square")
    _hand(tmp_path, "Uncial", "grc")
    assert manuscript.hand_name(a) == "Sephardi square"
    assert list(manuscript.hands("heb")) == [a] and len(manuscript.hands()) == 2
    with pytest.raises(ValueError):
        _hand(tmp_path, "sephardi SQUARE")  # the same name again
    assert Path(manuscript.hands()[a]["file"]).read_bytes() == b"Sephardi square", "a copy of the model it starts from"
    manuscript.remove_hand(a)
    assert list(manuscript.hands("heb")) == []


def test_reading_with_one_hand_and_the_ocr_file(data, tmp_path):
    p = _book(tmp_path)
    a = _hand(tmp_path, "Square")
    p.set_hands("heb", [a])
    assert p.settings.ms_languages == ["heb"] and p.stages() == ["preprocess", "read", "textlayer"]
    fake = FakeKraken({manuscript.hands()[a]["file"]: [("אבג דה", 95.0), ("וזח", 60.0), ("טי", 97.0)]})
    out = manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    assert out["lines"] == 3 and out["unsure"] == 1 and out["disagree"] == 0
    lines = manuscript.page_lines(p.workspace, "p001")
    assert [l["text"] for l in lines] == ["אבג דה", "וזח", "טי"]
    assert [l["unsure"] for l in lines] == [False, True, False]
    ocr = p.ocr("p001")
    assert [w.text for w in ocr.lines[0].words] == ["אבג", "דה"], "words in reading order"
    first = ocr.lines[0].words[0]
    assert first.box[0] > ocr.lines[0].words[1].box[0], "the first word is on the right"
    assert first.box[1] == 40 and first.box[3] == 81, "letters as tall as the line"
    # Read again: nothing new to find or read.
    manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    assert fake.segmented == 1 and len(fake.read) == 1


def test_several_hands_every_line_or_only_unsure_ones(data, tmp_path):
    p = _book(tmp_path)
    a, b = _hand(tmp_path, "Main"), _hand(tmp_path, "Masorah")
    fa, fb = manuscript.hands()[a]["file"], manuscript.hands()[b]["file"]
    fake = FakeKraken({fa: [("אבג", 95.0), ("דהו", 55.0), ("זח", 97.0)],
                       fb: [("אבג", 90.0), ("דהז", 80.0), ("זט", 99.0)]})
    p.set_hands("heb", [a, b])
    assert p.settings.hand_reading == manuscript.UNSURE
    out = manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    assert fake.read == [(Path(fa).parent.name, 3), (Path(fb).parent.name, 1)], "the second hand only on the unsure line"
    lines = manuscript.page_lines(p.workspace, "p001")
    assert lines[1]["text"] == "דהז" and lines[1]["hand"] == b, "the surer reading kept"
    assert lines[1]["disagree"] and out["disagree"] == 1
    assert lines[1]["readings"] == {"Main": "דהו", "Masorah": "דהז"}
    p.set_hand_reading(manuscript.EVERY)
    manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    assert fake.read[-1] == (Path(fb).parent.name, 2), "every line now: the two it hadn't read"
    lines = manuscript.page_lines(p.workspace, "p001")
    assert lines[2]["text"] == "זט" and lines[2]["disagree"]
    assert [x[0] for x in manuscript.least_sure(p.workspace, ["p001"])][:1] == ["p001"]
    assert manuscript.least_sure(p.workspace, ["p001"])[0][2] == -1.0, "lines the hands differ on first"


def test_correcting_a_line_keeps_it_for_the_output_and_the_hand(data, tmp_path):
    p = _book(tmp_path)
    a = _hand(tmp_path, "Square")
    p.set_hands("heb", [a])
    fake = FakeKraken({manuscript.hands()[a]["file"]: [("אבג", 95.0), ("דהו", 55.0), ("זח", 97.0)]})
    manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    r = p.correct_line("p001", 1, "דהז חט")
    assert r["old"] == "דהו" and r["new"] == "דהז חט" and r["hand"] == a
    line = manuscript.page_lines(p.workspace, "p001")[1]
    assert line["corrected"] and not line["unsure"] and line["text"] == "דהז חט"
    assert [w.text for w in p.ocr("p001").lines[1].words] == ["דהז", "חט"]
    items = manuscript.hand_items(a)
    assert [i.text for i in items] == ["דהז חט"] and items[0].book == p.book()
    with Image.open(items[0].png) as im:
        assert im.size == (369, 49), "the line inside its outline, a little room around it"
    p.correct_line("p001", 1, "דהז חי")
    assert [i.text for i in manuscript.hand_items(a)] == ["דהז חי"], "the same line saved again replaces it"
    assert p.manuscript_corrections() == 1


def test_changing_the_lines_found(data, tmp_path):
    p = _book(tmp_path)
    a = _hand(tmp_path, "Square")
    p.set_hands("heb", [a])
    fake = FakeKraken({manuscript.hands()[a]["file"]: [("אבג", 95.0), ("דהו", 55.0), ("זח", 97.0)]})
    manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    ws = p.workspace
    p.correct_line("p001", 0, "אבד")
    manuscript.split_line(ws, "p001", 2, 200)
    lines = manuscript.lines_of(ws, "p001")["lines"]
    assert len(lines) == 4 and manuscript.bbox(lines[2]["boundary"])[0] == 200, "right part first (right to left)"
    assert manuscript.bbox(lines[3]["boundary"])[2] == 201
    manuscript.join_lines(ws, "p001", 2)
    assert len(manuscript.lines_of(ws, "p001")["lines"]) == 3
    manuscript.set_line_box(ws, "p001", 1, [10, 90, 390, 140])
    assert manuscript.bbox(manuscript.lines_of(ws, "p001")["lines"][1]["boundary"]) == [10, 90, 390, 140]
    at = manuscript.add_line(ws, "p001", [20, 225, 380, 245])
    assert at == 3
    manuscript.delete_line(ws, "p001", 3)
    data = manuscript.lines_of(ws, "p001")
    assert data["edited"] and len(data["found"]) == 3 and manuscript.edited_pages(ws) == ["p001"]
    view = manuscript.page_lines(ws, "p001")
    assert view[0]["corrected"] and view[0]["text"] == "אבד", "an unchanged line keeps its correction"
    assert view[1]["unread"] and view[2]["unread"], "changed lines wait to be read again"
    # Read again: the changed lines only, and the page isn't searched for lines again.
    manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake, pages=["p001"])
    assert fake.segmented == 1 and fake.read[-1] == (Path(manuscript.hands()[a]["file"]).parent.name, 2)
    # The line-finding trained on the book: untouched pages are searched again, this one isn't.
    (ws / "ms" / "segmenter.safetensors").write_bytes(b"trained")
    manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    assert fake.segmented == 1
    p2 = _book(tmp_path / "other")
    p2.set_hands("heb", [a])
    manuscript.read_workspace(p2.workspace, p2.settings, sessions=lambda n: fake)
    (p2.workspace / "ms" / "segmenter.safetensors").write_bytes(b"trained")
    manuscript.read_workspace(p2.workspace, p2.settings, sessions=lambda n: fake)
    assert fake.segmented == 3, "found once with Kraken's line-finding, once with the book's"
    xml = ElementTree.parse(manuscript.page_xml(ws, "p001")).getroot()
    ns = {"p": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"}
    assert len(xml.findall(".//p:TextLine", ns)) == 3 and len(xml.findall(".//p:Baseline", ns)) == 3
    assert Path(xml.find("p:Page", ns).get("imageFilename")).is_absolute()


def test_sample_pages_from_the_middle_of_the_book(tmp_path):
    pages = [f"p{n:03d}" for n in range(1, 101)]
    sample = manuscript.sample_pages(pages, 5, seed=1)
    assert len(sample) == 5 and all("p011" <= p <= "p090" for p in sample)
    assert manuscript.sample_pages(pages[:3], 5) == pages[:3]
    p = Project(tmp_path / "b.ectype")
    assert p.choose_sample(pages=["p002", "p001"]) == ["p001", "p002"]
    assert Project(tmp_path / "b.ectype").settings.sample == ["p001", "p002"]


def test_cores_shared_among_readers(monkeypatch):
    monkeypatch.setattr(manuscript.os, "cpu_count", lambda: 16)
    assert manuscript.cores_plan(True, 50) == (4, 4)
    assert manuscript.cores_plan(True, 2) == (2, 8)
    assert manuscript.cores_plan(False, 50) == (1, 8)


def test_no_hand_chosen(data, tmp_path):
    p = _book(tmp_path)
    with pytest.raises(RuntimeError, match="No hand"):
        manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: FakeKraken({}))


def test_manuscript_and_print_keep_their_own_languages(data, tmp_path):
    p = Project(tmp_path / "b.ectype")
    p.set_languages(["eng", "heb"])
    a = _hand(tmp_path, "Square")
    p.set_script("manuscript")
    p.set_hands("heb", [a])
    p.set_script("print")
    assert p.languages() == ["eng", "heb"] and p.stages()[1] == "ocr"
    p.set_script("manuscript")
    assert p.settings.hands == {"heb": [a]} and p.update_stage() == "textlayer"
    p.set_hands("heb", [])
    assert p.settings.ms_languages == [] and p.settings.hands == {}
    saved = json.loads((p.workspace / "project.json").read_text(encoding="utf-8"))
    assert saved["script"] == "manuscript"
