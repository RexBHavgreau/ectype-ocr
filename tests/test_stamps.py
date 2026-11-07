"""Kept results are judged by content and code, never by file times, and read without pickle."""

from __future__ import annotations

import dataclasses
import json
import os
import pickle
import re
import shutil
from pathlib import Path

import numpy as np
import pytest

from ectype import cluster, stamps
from ectype.cluster import Instance, cluster_workspace
from ectype.ocr import _fresh, ocr_pages, ocr_stamp_path
from ectype.project import Project
from ectype.segment import segment_workspace, seg_fresh
from ectype.synth import Degrade, make_page


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("stamps")
    (root / "pages").mkdir()
    for i, seed in enumerate((1, 2)):
        name = f"s{i + 1:02d}"
        make_page(root / "truth" / name, seed=seed,
                  degrade=Degrade(skew_degrees=0.3, blur_sigma=0.6, noise_sigma=6, speckles=40, jpeg_quality=85))
        shutil.copy(root / "truth" / name / "scan.png", root / "pages" / f"{name}.png")
    shutil.rmtree(root / "truth")
    ocr_pages(root / "pages", root / "ocr")
    segment_workspace(root)
    cluster_workspace(root)
    return root


@pytest.fixture
def ws(built, tmp_path):
    """A copy of the built workspace (copied with its times, as a shared folder would be)."""
    dst = tmp_path / "book"
    shutil.copytree(built, dst)
    return dst


class Counter:
    def __init__(self, monkeypatch):
        self.n = {"page": 0, "group": 0, "average": 0}
        for attr, key in (("_page_instances", "page"), ("_group_labels", "group"), ("average", "average")):
            monkeypatch.setattr(cluster, attr, self._wrap(getattr(cluster, attr), key))

    def _wrap(self, fn, key):
        def counted(*a, **k):
            self.n[key] += 1
            return fn(*a, **k)
        return counted


def _files(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_page_cache_round_trip(ws):
    name = "s01"
    png, seg, labels = (ws / "pages" / f"{name}.png", ws / "segment" / f"{name}.seg.json",
                        ws / "segment" / f"{name}.labels.png")
    from ectype.ocr import OcrPage

    ocr = OcrPage.from_json(json.loads((ws / "ocr" / f"{name}.ocr.json").read_text(encoding="utf-8")))
    instances, slants = cluster._page_instances(png, seg, labels, ocr)
    assert instances and slants
    path = ws / "kept.npz"
    cluster._save_page(path, "stamp-1", instances, slants)
    assert cluster._load_page(path, "stamp-2") is None  # another stamp: not used
    back, back_slants = cluster._load_page(path, "stamp-1")
    assert back_slants == slants and list(back_slants) == list(slants)
    assert len(back) == len(instances)
    for a, b in zip(instances, back):
        for f in dataclasses.fields(Instance):
            x, y = getattr(a, f.name), getattr(b, f.name)
            if f.name == "crop":
                assert x.dtype == y.dtype and x.shape == y.shape and x.tobytes() == y.tobytes()
            else:
                assert type(x) is type(y) and x == y, f.name
        assert a.fingerprint() == b.fingerprint()
        assert isinstance(b.box, list) and isinstance(b.crop_origin, tuple)


def test_page_cache_reads_nothing_odd(ws):
    path = ws / "kept.npz"
    path.write_bytes(b"not an archive")
    assert cluster._load_page(path, "x") is None
    assert cluster._load_page(ws / "missing.npz", "x") is None
    cache = cluster.Cache(ws / "c")
    (ws / "c" / "group").mkdir(parents=True)
    (ws / "c" / "group" / "a.npy").write_bytes(b"junk")
    assert cache.get(ws / "c" / "group" / "a.npy") is None


def test_average_and_group_round_trip(tmp_path):
    cache = cluster.Cache(tmp_path)
    composite = np.random.default_rng(0).random((7, 5)).astype(np.float64)
    canvas = {"baseline_row": 3.5, "centroid_col": 2.25, "width": 5, "height": 7, "x_height": 12.0,
              "instances_used": 4, "mean_shift_px": 0.125}
    path = tmp_path / "average" / "a.npz"
    cache.put(path, (composite, canvas))
    got, got_canvas = cache.get(path)
    assert got.dtype == composite.dtype and got.tobytes() == composite.tobytes()
    assert got_canvas == canvas and [type(v) for v in got_canvas.values()] == [type(v) for v in canvas.values()]
    labels = np.array([1, 2, 2, 3], np.int32)
    gpath = tmp_path / "group" / "g.npy"
    cache.put(gpath, labels)
    back = cache.get(gpath)
    assert back.dtype == labels.dtype and (back == labels).all()


def test_second_run_computes_nothing(ws, monkeypatch):
    out = ws / "cluster"
    before = _files(out)
    counter = Counter(monkeypatch)
    cluster_workspace(ws)
    assert counter.n == {"page": 0, "group": 0, "average": 0}
    after = _files(out)
    assert after["clusters.json"] == before["clusters.json"]
    for name, data in before.items():
        if name.startswith("composites/"):
            assert after[name] == data


def test_changed_reading_with_old_size_and_time_is_made_again(ws, monkeypatch):
    path = ws / "ocr" / "s01.ocr.json"
    st = path.stat()
    text = path.read_text(encoding="utf-8")
    m = re.search(r'"text": "([A-Za-z]{2,})"', text)
    word = m.group(1)
    other = word[1] + word[0] if word[1] != word[0] else "x" + word[1:]
    changed = text[:m.start(1)] + other + text[m.end(1):]
    assert changed != text and len(changed.encode()) == len(text.encode())
    path.write_text(changed, encoding="utf-8", newline="\n")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert path.stat().st_size == st.st_size and path.stat().st_mtime_ns == st.st_mtime_ns
    counter = Counter(monkeypatch)
    cluster_workspace(ws)
    assert counter.n["page"] == 1


def test_planted_pickles_are_never_loaded(ws):
    marker = ws / "marker.txt"

    class Boom:
        def __reduce__(self):
            return (open, (str(marker), "w"))

    planted = [ws / "cluster" / "cache" / "pages" / "s01.pkl", ws / "cluster" / "cache" / "pages" / "p001.pkl",
               ws / "cluster" / "cache" / "group" / "x.pkl", ws / "cluster" / "cache" / "average" / "y.pkl"]
    for p in planted:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(pickle.dumps(Boom()))
    # also under the names the cache looks for now: wrong content is made again, not executed
    (ws / "cluster" / "cache" / "pages" / "s01.npz").write_bytes(pickle.dumps(Boom()))
    cluster_workspace(ws)
    assert not marker.exists()
    assert not any(p.exists() for p in planted)
    assert not list((ws / "cluster" / "cache").rglob("*.pkl"))


def test_stale_cluster_follows_the_readings(ws):
    project = Project(ws)
    assert not project._stale("cluster")
    assert not project._stale("segment")
    path = ws / "ocr" / "s02.ocr.json"
    st = path.stat()
    text = path.read_text(encoding="utf-8")
    m = re.search(r'"text": "([A-Za-z]{2,})"', text)
    word = m.group(1)
    other = word[1] + word[0] if word[1] != word[0] else "x" + word[1:]
    path.write_text(text[:m.start(1)] + other + text[m.end(1):], encoding="utf-8", newline="\n")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert project._stale("cluster")
    assert project._stale("segment")  # the segmentation was made from the old reading
    segment_workspace(ws)
    assert not project._stale("segment")
    assert project._stale("cluster")
    cluster_workspace(ws)
    assert not project._stale("cluster")


def test_stale_cluster_without_inputs(ws):
    project = Project(ws)
    path = ws / "cluster" / "clusters.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["inputs"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert project._stale("cluster")
    path.write_text("{ not json", encoding="utf-8")
    assert project._stale("cluster")
    path.unlink()
    assert project._stale("cluster")


def test_segment_stamp(ws):
    assert seg_fresh(ws, "s01")
    (ws / "segment" / "s01.labels.png").unlink()
    assert not seg_fresh(ws, "s01")
    (ws / "segment" / "s02.seg.stamp").unlink()
    assert not seg_fresh(ws, "s02")


def test_review_edit_redoes_that_page(ws, monkeypatch):
    path = ws / "ocr" / "s01.ocr.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["lines"][0]["words"][0]["text"] = "Zz"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    assert not seg_fresh(ws, "s01") and seg_fresh(ws, "s02")
    counter = Counter(monkeypatch)
    segment_workspace(ws)
    cluster_workspace(ws)
    assert counter.n["page"] == 1


def test_ocr_migration(tmp_path):
    png, target = tmp_path / "p.png", tmp_path / "p.ocr.json"
    png.write_bytes(b"image one")
    target.write_text("{}", encoding="utf-8")
    os.utime(png, (1_000_000, 1_000_000))
    os.utime(target, (2_000_000, 2_000_000))
    assert not ocr_stamp_path(target).exists()
    assert _fresh(target, png)  # made by an older version, newer than its image: trusted once
    assert ocr_stamp_path(target).read_text(encoding="utf-8").strip() == stamps.file_digest(png)
    assert _fresh(target, png)
    png.write_bytes(b"image two")  # another image, however its time looks
    os.utime(png, (1_000_000, 1_000_000))
    assert not _fresh(target, png)
    # an old reading older than its image is not trusted, and gets no sidecar
    old = tmp_path / "q.ocr.json"
    q = tmp_path / "q.png"
    old.write_text("{}", encoding="utf-8")
    q.write_bytes(b"x")
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(q, (2_000_000, 2_000_000))
    assert not _fresh(old, q)
    assert not ocr_stamp_path(old).exists()
    assert not _fresh(tmp_path / "none.ocr.json", q)


def test_ocr_writes_its_stamp(ws):
    png, target = ws / "pages" / "s01.png", ws / "ocr" / "s01.ocr.json"
    assert ocr_stamp_path(target).read_text(encoding="utf-8").strip() == stamps.file_digest(png)
    assert _fresh(target, png)


def test_stamps_basics(tmp_path):
    f = tmp_path / "a"
    assert stamps.file_digest(f) == "-"
    f.write_bytes(b"abc")
    first = stamps.file_digest(f)
    f.write_bytes(b"abd")
    assert stamps.file_digest(f) != first
    assert stamps.stamp("a", "bc") != stamps.stamp("ab", "c")
    assert stamps.code_version() == stamps.code_version()


def test_no_pickle_in_the_source():
    src = Path(stamps.__file__).parent
    bad = re.compile(r"^\s*(import|from)\s+(pickle|cPickle|dill|shelve|marshal)\b|allow_pickle\s*=\s*True|joblib\.load|cloudpickle", re.M)
    found = [p.name for p in src.rglob("*.py") if bad.search(p.read_text(encoding="utf-8"))]
    assert not found, found
