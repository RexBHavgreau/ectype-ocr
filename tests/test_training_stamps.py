"""Training's kept results are judged by content; stamps kept in the old form (size and file
time) are accepted once and written again in the new form."""

from __future__ import annotations

import json
import os

from ectype import stamps


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


def test_model_errors_kept_by_content_and_old_stamp_accepted_once(tmp_path, monkeypatch):
    from ectype import training
    from ectype.pool import Item

    monkeypatch.setattr(training, "_model_dir", lambda code: tmp_path / "m")
    model = tmp_path / "model.traineddata"
    model.write_bytes(b"model one")
    items = [Item(id=i, lang="heb", book="b", unit="line", source="synthetic", split="test",
                  png=tmp_path / f"{i}.png", text="x") for i in ("a", "b")]
    read_calls = []

    def read(missing, per):
        read_calls.append([i.id for i in missing])
        for i in missing:
            per[i.id] = (1, 10)

    assert training._checked("heb", "general", model, items, read) == {"b": (2, 20)}
    kept = json.loads((tmp_path / "m" / "general.errors.json").read_text(encoding="utf-8"))
    assert kept["stamp"] == training._stamp(model)
    os.utime(model, ns=(1, 1))                      # same content, another time
    training._checked("heb", "general", model, items, read)
    assert len(read_calls) == 1

    kept["stamp"] = training._old_stamp(model)      # as the earlier version wrote it
    (tmp_path / "m" / "general.errors.json").write_text(json.dumps(kept), encoding="utf-8")
    assert training._checked("heb", "general", model, items, read) == {"b": (2, 20)}
    assert len(read_calls) == 1, "old stamp accepted: no image read again"
    assert json.loads((tmp_path / "m" / "general.errors.json").read_text(encoding="utf-8"))["stamp"] == training._stamp(model)

    model.write_bytes(b"model two!")                # other content: read again
    training._checked("heb", "general", model, items, read)
    assert len(read_calls) == 2
