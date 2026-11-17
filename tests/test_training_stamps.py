"""Training's kept results are judged by content; stamps kept in the old form (size and file
time) are accepted once and written again in the new form."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ectype import manuscript, stamps
from test_manuscript import FakeKraken, _book, _hand, data  # noqa: F401


def test_remembered_digest_follows_content_and_rereads_only_on_change(tmp_path, monkeypatch):
    f = tmp_path / "m.bin"
    f.write_bytes(b"one")
    a = stamps.remembered_digest(f)
    assert a == stamps.file_digest(f)
    calls = []
    real = stamps.file_digest
    monkeypatch.setattr(stamps, "file_digest", lambda p: calls.append(p) or real(p))
    assert stamps.remembered_digest(f) == a and calls == [], "same size and time: not read again"
    f.write_bytes(b"two")   # same size
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10**9))
    assert stamps.remembered_digest(f) != a and len(calls) == 1
    assert stamps.remembered_digest(tmp_path / "none") == "-"


def test_manuscript_stamp_is_content_and_old_form_is_accepted_once(data, tmp_path):  # noqa: F811
    p = _book(tmp_path)
    a = _hand(tmp_path, "Square")
    p.set_hands("heb", [a])
    model = Path(manuscript.hands()[a]["file"])
    fake = FakeKraken({str(model): [("אבג", 95.0), ("דהו", 90.0), ("זח", 97.0)]})
    manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    image = p.workspace / "pages" / "p001.png"
    found = manuscript.lines_of(p.workspace, "p001")
    assert found["image"] == "sha1:" + stamps.file_digest(image)
    # Same bytes, different time: still fresh.
    os.utime(image, ns=(1, 1))
    manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    assert fake.segmented == 1 and len(fake.read) == 1

    # Put the old forms in the kept files, as an earlier version wrote them.
    lines_file = p.workspace / "ms" / "p001.lines.json"
    read_file = p.workspace / "ms" / "p001.read.json"
    d = json.loads(lines_file.read_text(encoding="utf-8"))
    d["image"] = manuscript._old_stamp(image)
    lines_file.write_text(json.dumps(d), encoding="utf-8")
    r = json.loads(read_file.read_text(encoding="utf-8"))
    for line in r["lines"]:
        for rd in line["readings"].values():
            rd["model"] = manuscript._old_stamp(model)
    read_file.write_text(json.dumps(r), encoding="utf-8")
    manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    assert fake.segmented == 1 and len(fake.read) == 1, "old stamps accepted: nothing found or read again"
    assert manuscript.lines_of(p.workspace, "p001")["image"].startswith("sha1:")
    new = json.loads(read_file.read_text(encoding="utf-8"))
    assert all(rd["model"] == manuscript._stamp(model) for l in new["lines"] for rd in l["readings"].values())

    # A model with other content is stale.
    model.write_bytes(b"changed model")
    manuscript.read_workspace(p.workspace, p.settings, sessions=lambda n: fake)
    assert len(fake.read) == 2


def test_edition_stamp_is_content_and_old_form_is_accepted_once(tmp_path, monkeypatch):
    from ectype import align, pool, trainall

    scan = tmp_path / "book.pdf"
    scan.write_bytes(b"%PDF scan one")
    cfg = tmp_path / "training.ini"
    cfg.write_text(f"[training]\nscripts = hebrew\n[edition: Book]\nscan = {scan}\ntext = wlc\nlanguage = heb\n",
                   encoding="utf-8")
    ran = []
    monkeypatch.setattr(align, "read_and_align", lambda *a, **k: ran.append(a) or {"kept": 5, "skipped": 1})
    monkeypatch.setattr(trainall, "train_scripts", lambda *a, **k: [])
    monkeypatch.setattr(pool, "languages", lambda: [])
    done_file = tmp_path / "training-editions.json"

    trainall.run(cfg)
    assert len(ran) == 1
    first = json.loads(done_file.read_text(encoding="utf-8"))["Book"]["stamp"]
    assert first == trainall._stamp(scan, "wlc")
    os.utime(scan, ns=(1, 1))                      # a copy with another time: same content
    trainall.run(cfg)
    assert len(ran) == 1

    done_file.write_text(json.dumps({"Book": {"stamp": trainall._old_stamp(scan, "wlc"), "kept": 5, "skipped": 1}}),
                         encoding="utf-8")
    report = trainall.run(cfg)
    assert len(ran) == 1 and any("already matched" in l for l in report), "old stamp accepted"
    assert json.loads(done_file.read_text(encoding="utf-8"))["Book"]["stamp"] == trainall._stamp(scan, "wlc")

    scan.write_bytes(b"%PDF scan two")
    trainall.run(cfg)
    assert len(ran) == 2
