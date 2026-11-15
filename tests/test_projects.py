"""Where a book's project folder is kept (Ectype's data folder), and results-only runs keep nothing."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from PIL import Image

from ectype import cli, freespace
from ectype.project import Project, projects_dir


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OMP_THREAD_LIMIT", "1")
    return tmp_path / "data"


def _pdf(path: Path, seed: int) -> Path:
    from ectype.synth import Degrade, make_page

    path.parent.mkdir(parents=True, exist_ok=True)
    truth = Path(tempfile.mkdtemp()) / path.stem
    make_page(truth, "Inspiration is the word of God given to us. " * 6, seed=seed, point_size=10,
              page_size_pt=(432, 300),
              degrade=Degrade(skew_degrees=0.0, blur_sigma=0.3, noise_sigma=2, speckles=0, jpeg_quality=None))
    Image.open(truth / "scan.png").convert("L").save(path, "PDF", resolution=300)
    return path


def test_for_scan_keeps_projects_in_the_data_folder(tmp_path, data):
    a, b = tmp_path / "one" / "Book.pdf", tmp_path / "two" / "Book.pdf"
    for p in (a, b):
        p.parent.mkdir()
        p.write_bytes(b"x")
    assert Project.find_for_scan(a) is None and not data.exists(), "looking creates nothing"
    first = Project.for_scan(a).workspace
    assert first == data / "projects" / "Book.ectype" and (first / "project.json").is_file()
    assert Project.for_scan(a).workspace == first and Project.find_for_scan(a) == first
    assert Project.for_scan(b).workspace == data / "projects" / "Book (2).ectype"
    assert Project.for_scan(a).workspace == first
    assert not list(tmp_path.rglob("*.ectype")) or all(data in p.parents for p in tmp_path.rglob("*.ectype"))
    assert (projects_dir() / "index.json").is_file()


def test_an_old_project_beside_the_scan_is_used(tmp_path, data):
    scan = tmp_path / "Old.pdf"
    scan.write_bytes(b"x")
    Project(tmp_path / "Old.ectype", scan)
    assert Project.find_for_scan(scan) == tmp_path / "Old.ectype"
    assert Project.for_scan(scan).workspace == tmp_path / "Old.ectype"
    assert not (data / "projects").exists()


def _left(data):
    return [p for p in (data / "projects", data / "work") if p.exists() and any(p.iterdir())]


def test_results_only_runs_keep_nothing(tmp_path, data):
    scan = _pdf(tmp_path / "scans" / "Libro.pdf", 3)
    assert cli.main(["run", str(scan), "--mode", "text"]) == 0
    assert sorted(p.name for p in scan.parent.iterdir()) == ["Libro - OCR.pdf", "Libro.pdf"]
    assert _left(data) == []
    assert cli.main(["run", str(scan), "--mode", "plaintext"]) == 0
    assert sorted(p.name for p in scan.parent.iterdir()) == ["Libro - OCR.pdf", "Libro.pdf", "Libro.txt"]
    assert _left(data) == []


def test_keep_leaves_the_project_in_the_projects_folder(tmp_path, data):
    scan = _pdf(tmp_path / "scans" / "Libro.pdf", 3)
    assert cli.main(["run", str(scan), "--mode", "text", "--keep"]) == 0
    assert sorted(p.name for p in scan.parent.iterdir()) == ["Libro - OCR.pdf", "Libro.pdf"]
    assert Project.find_for_scan(scan) == data / "projects" / "Libro.ectype"
    assert list((data / "projects" / "Libro.ectype" / "ocr").glob("*.ocr.json"))


def test_a_failing_run_deletes_its_temporary_folder(tmp_path, data, monkeypatch):
    scan = _pdf(tmp_path / "scans" / "Libro.pdf", 3)

    def boom(args):
        raise RuntimeError("no")

    monkeypatch.setattr(cli, "cmd_ocr", boom)
    with pytest.raises(RuntimeError):
        cli._run_in  # the failure comes out of main as a message, so call cmd_run directly
        cli.cmd_run(cli.build_parser().parse_args(["run", str(scan), "--mode", "text"]))
    assert _left(data) == []
    assert sorted(p.name for p in scan.parent.iterdir()) == ["Libro.pdf"]


def test_free_space_lists_projects_and_keeps_them(tmp_path, data):
    scan = tmp_path / "S.pdf"
    scan.write_bytes(b"x")
    ws = Project.for_scan(scan).workspace
    (ws / "pages").mkdir()
    (ws / "pages" / "p001.png").write_bytes(b"z" * 1000)
    r = freespace.report()
    assert r["projects"]["bytes"] >= 1000 and r["projects"]["folders"] == 1
    freespace.remove_cache()
    freespace.remove_leftovers()
    freespace.remove_font_lines()
    assert (ws / "pages" / "p001.png").is_file()
