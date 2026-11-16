"""Glyph grouping and font building report their progress and stop at a safe point, with the
same results as without."""

from __future__ import annotations

import shutil

import pytest

from ectype import cluster, stopping
from ectype.cluster import cluster_workspace
from ectype.fontbuild import build_workspace_fonts
from ectype.ocr import ocr_pages
from ectype.segment import segment_workspace
from ectype.stopping import StopFlag, Stopped
from ectype.synth import Degrade, make_page


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    root = tmp_path_factory.mktemp("clusterprogress") / "book"
    (root / "pages").mkdir(parents=True)
    for i, seed in enumerate((1, 2, 3)):
        name = f"s{i + 1:02d}"
        make_page(root / "truth" / name, seed=seed,
                  degrade=Degrade(skew_degrees=0.3, blur_sigma=0.6, noise_sigma=6, speckles=60, jpeg_quality=85))
        shutil.copy(root / "truth" / name / "scan.png", root / "pages" / f"{name}.png")
    ocr_pages(root / "pages", root / "ocr")
    segment_workspace(root)
    return root


def _copy(book, tmp_path, name):
    ws = tmp_path / name / "book"
    shutil.copytree(book, ws, ignore=shutil.ignore_patterns("cluster", "fonts"))
    return ws


def _json(ws):
    return (ws / "cluster" / "clusters.json").read_bytes()


def test_cluster_reports_progress_and_is_unchanged(book, tmp_path):
    plain = _copy(book, tmp_path, "plain")
    cluster_workspace(plain)
    watched = _copy(book, tmp_path, "watched")
    reports = []
    cluster_workspace(watched, progress=lambda d, t, w: reports.append((d, t, w)))
    assert _json(watched) == _json(plain)
    assert reports
    assert [w for w in dict.fromkeys(r[2] for r in reports)] == [n for n, _ in cluster.PHASES]
    last = None
    for d, t, w in reports:
        f = cluster.overall_fraction(w, d, t)
        assert last is None or f >= last - 1e-9
        last = f
    assert last == pytest.approx(1.0)
    for name, _ in cluster.PHASES:  # each phase ends complete
        done, total, _ = [r for r in reports if r[2] == name][-1]
        assert done == total


def test_cluster_stops_then_completes(book, tmp_path):
    whole = _copy(book, tmp_path, "whole")
    cluster_workspace(whole)
    ws = _copy(book, tmp_path, "stopped")
    flag = StopFlag()
    seen = []

    def progress(d, t, w):
        seen.append(w)
        flag.request()  # after the first report

    with stopping.active(flag), pytest.raises(Stopped):
        cluster_workspace(ws, progress=progress)
    assert len(seen) == 1
    assert not (ws / "cluster" / "clusters.json").exists()
    assert not list((ws / "cluster" / "cache").rglob("*.part"))
    cluster_workspace(ws)
    assert _json(ws) == _json(whole)


def test_stop_leaves_an_earlier_result_alone(book, tmp_path):
    ws = _copy(book, tmp_path, "earlier")
    cluster_workspace(ws)
    before = _json(ws)
    flag = StopFlag()
    flag.request()
    with stopping.active(flag), pytest.raises(Stopped):
        cluster_workspace(ws)
    assert _json(ws) == before


# (the .otf files carry a build timestamp, so the per-style records are compared)
def test_fonts_report_stop_and_complete(book, tmp_path):
    ws = _copy(book, tmp_path, "fonts")
    cluster_workspace(ws)
    plain = build_workspace_fonts(ws)
    infos = {p.name: p.read_bytes() for p in (ws / "fonts").glob("style-*.json")}
    shutil.rmtree(ws / "fonts")
    reports = []
    watched = build_workspace_fonts(ws, progress=lambda d, t, w: reports.append((d, t, w)))
    assert watched == plain
    assert {p.name: p.read_bytes() for p in (ws / "fonts").glob("style-*.json")} == infos
    assert reports and reports[-1][0] == reports[-1][1] == len(plain["styles"])
    assert [r[0] for r in reports] == sorted(r[0] for r in reports)

    shutil.rmtree(ws / "fonts")
    flag = StopFlag()
    with stopping.active(flag), pytest.raises(Stopped):
        build_workspace_fonts(ws, progress=lambda d, t, w: flag.request())
    assert not (ws / "fonts" / "fonts.json").exists()
    assert build_workspace_fonts(ws) == plain
    assert {p.name: p.read_bytes() for p in (ws / "fonts").glob("style-*.json")} == infos
