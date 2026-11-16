"""Unpointed Hebrew prose lines for every Hebrew font (texts.py "hebrew-prose", synthtrain.py)."""

from __future__ import annotations

import re
import shutil

import pytest

from ectype import pool, synthtrain, texts

POINTS = re.compile("[֑-ֽֿ-ׇ]")  # every Hebrew mark but maqaf
SAMPLE = """אחד העם

\tכותרת של מאמר

1

ואני אך לפני חודש ימים בקרתי את הגימנסיה. ארבעה ימים, ארבע שעות בכל יום, הקשבתי אל הלמודים במחלקות השונות; וחוץ לזה נזדמנתי כמה פעמים עם מורים ועם תלמידים.
אע"פ שהדבר כך, כמו־כן בְּרֵאשִׁית בָּרָא אֱלֹהִים אֵת הַשָּׁמַיִם (ר' אבן עזרא) – לא באתי לבוא ולהרצות בעניין זה כלל וכלל.
This sentence is English and has no Hebrew letters in it at all, so it must go and never be used.
1234 5678 9012 3456 7890 1234 5678 9012
"""
PROSE = ["ואני אך לפני חודש ימים בקרתי את הגימנסיה, ארבעה ימים ארבע שעות בכל יום, הקשבתי אל הלמודים במחלקות השונות, "
         "וחוץ לזה נזדמנתי כמה פעמים עם מורים ועם תלמידים והרביתי שיחה עם אלו ועם אלו ועל סמך כל אלה נוטל אני רשות",
         "כי אין מגמתי כאן אלא לברר אותו ענין שהיה לנושא הוכוח, אך לא באתי להרצות בפרטות על מצב הגימנסיה מכל הצדדים",
         "מוסד חנוכי גדול כזה, אפילו פדגוג מומחה ומנוסה אינו יכול להקיף כל פרטיו במשך ימים אחדים, וכל שכן איש מן החוץ"]


def test_prose_is_cut_into_unpointed_lines_without_latin():
    pieces = texts._prose_pieces(SAMPLE)
    assert len(pieces) >= 2
    least, most = texts.PIECE_WORDS
    for p in pieces:
        assert least <= len(p.split()) <= most
        assert not POINTS.search(p), "no accents or points"
        assert not re.search("[A-Za-z]", p) and re.search("[א-ת]", p)
        assert p == " ".join(p.split()), "whitespace normalised"
    joined = " ".join(pieces)
    assert "כותרת" not in joined and "1234" not in joined, "titles and bare numbers left out"
    assert "בראשית ברא אלהים" in joined, "marks that slipped in are removed from the word"
    assert "כמו־כן" in joined and 'אע"פ' in joined and "–" in joined, "maqaf and punctuation kept"
    # a long paragraph is cut into several pieces, none over the limit
    long = " ".join(["מילה"] * 100) + "."
    cut = texts._prose_pieces(long)
    assert len(cut) >= 4 and all(len(c.split()) <= most for c in cut)
    # pointed letters in presentation forms (a dagesh letter in one code point) lose their points too
    assert not POINTS.search(" ".join(texts._prose_pieces("בָּרא " + "מילה " * 8)))


@pytest.fixture
def fonts(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    from ectype.synth import find_font

    folder = tmp_path / "fonts"
    folder.mkdir()
    shutil.copy(find_font("FreeSerif"), folder)
    return folder


def _bible(source, progress=None, accents=False):
    return ["בְּרֵאשִׁית בָּרָא אֱלֹהִים אֵת הַשָּׁמַיִם וְאֵת הָאָרֶץ וְהָאָרֶץ הָיְתָה תֹהוּ וָבֹהוּ"] * 6


def _texts(monkeypatch, prose):
    def load(source, progress=None, accents=False):
        if source == "hebrew-prose":
            if prose is None:
                raise RuntimeError("no connection")
            return prose * 3
        return _bible(source)

    monkeypatch.setattr(texts, "load", load)


def _books():
    books = pool.books("heb")
    return books.get("font: FreeSerif Regular"), books.get("font: FreeSerif Regular (unpointed)")


def test_a_font_done_for_pointed_hebrew_gets_only_the_unpointed_set(fonts, monkeypatch):
    # The pointed lines were made before this existed: make them with the unpointed set switched off.
    with monkeypatch.context() as before:
        before.setattr(synthtrain, "unpointed_lines", lambda *a, **k: None)
        _texts(before, PROSE)
        r = synthtrain.generate(fonts, ["hebrew"], lines_per_font=20)
    assert r["added"]["heb"] == 20
    pointed, none = _books()
    assert none is None
    ids = {i for i, m in pool.index("heb").items() if m["book"] == "font: FreeSerif Regular"}
    assert len(ids) == 20
    _texts(monkeypatch, PROSE)
    messages = []
    r = synthtrain.generate(fonts, ["hebrew"], lines_per_font=20, progress=messages.append)
    assert r["added"]["heb"] == synthtrain.unpointed_count(20) == 8, "only the unpointed lines are made"
    assert any("already done" in m for m in messages) and any("unpointed lines" in m for m in messages)
    pointed_after, unpointed = _books()
    assert pointed_after == pointed, "the pointed record is untouched"
    assert {i for i, m in pool.index("heb").items() if m["book"] == "font: FreeSerif Regular"} == ids
    lines = [m["text"] for m in pool.index("heb").values() if m["book"] == "font: FreeSerif Regular (unpointed)"]
    assert len(lines) == 8 and all(not POINTS.search(t) and 3 <= len(t.split()) <= 9 for t in lines)
    assert unpointed["source"] == "synthetic" and unpointed["fingerprint"] and unpointed["stamp"].endswith("-prose")
    # a second run makes nothing
    messages.clear()
    r = synthtrain.generate(fonts, ["hebrew"], lines_per_font=20, progress=messages.append)
    assert r["added"].get("heb", 0) == 0 and any("unpointed lines already done" in m for m in messages)


def test_unpointed_count_is_two_fifths_and_at_least_one():
    assert [synthtrain.unpointed_count(n) for n in (400, 20, 1, 2)] == [160, 8, 1, 1]


def test_without_the_prose_the_bible_without_points_stands_in(fonts, monkeypatch):
    _texts(monkeypatch, None)
    messages = []
    r = synthtrain.generate(fonts, ["hebrew"], lines_per_font=20, progress=messages.append)
    assert any("Project Ben-Yehuda" in m and "couldn't be downloaded" in m for m in messages)
    assert any("Project Ben-Yehuda" in k for k in r["skipped"])
    assert r["added"]["heb"] == 20 + 8
    _, unpointed = _books()
    assert unpointed["stamp"].endswith("-bible")
    lines = [m["text"] for m in pool.index("heb").values() if m["book"].endswith("(unpointed)")]
    assert lines and all(not POINTS.search(t) for t in lines)
    # once the prose can be had, the stand-in lines are replaced by prose lines
    _texts(monkeypatch, PROSE)
    r = synthtrain.generate(fonts, ["hebrew"], lines_per_font=20)
    assert r["added"]["heb"] == 8
    assert _books()[1]["stamp"].endswith("-prose")
    assert sum(1 for m in pool.index("heb").values() if m["book"].endswith("(unpointed)")) == 8


def test_a_font_without_points_takes_most_unpointed_lines_from_the_prose(fonts, tmp_path, monkeypatch):
    from ectype.synth import find_font
    from tests.test_training import _unpointed

    _unpointed(find_font("FreeSerif"), tmp_path / "Plain.ttf", "Plain Hebrew")
    _texts(monkeypatch, PROSE)
    plain = [synthtrain.FontInfo(tmp_path / "Plain.ttf", "Plain Hebrew Regular")]
    r = synthtrain.generate(plain, ["hebrew"], lines_per_font=40)
    pool_lines = pool.index("heb").values()
    unpointed = [m["text"] for m in pool_lines if m["book"].endswith("(unpointed)")]
    assert len(unpointed) == 16 and r["added"]["heb"] == 40 + 16
    bible = sum(1 for t in unpointed if "בראשית" in t or "תהו" in t)
    assert 0 < len(unpointed) - bible and bible <= len(unpointed) * 0.6, "mostly prose"
