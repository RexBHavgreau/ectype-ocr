from __future__ import annotations

import os
import subprocess
import sys
import textwrap

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

CRASH = """
import faulthandler
from ectype import crashlog


def reading_page():
    crashlog.note("read: page 3 of 26")
    faulthandler._sigabrt()


crashlog.install()
reading_page()
"""

NEXT = """
from ectype import crashlog

crashlog.install()
print(crashlog.previous_crash())
crashlog.close_cleanly()
"""

CLEAN = """
from ectype import crashlog

crashlog.install()
crashlog.note("hello")
crashlog.close_cleanly()
"""


def _run(tmp_path, code, name):
    script = tmp_path / name
    script.write_text(textwrap.dedent(code), encoding="utf-8")
    env = dict(os.environ, ECTYPE_DATA_DIR=str(tmp_path / "data"), PYTHONPATH=SRC)
    return subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)


def test_an_abort_leaves_a_report_found_at_the_next_start(tmp_path):
    first = _run(tmp_path, CRASH, "crash.py")
    assert first.returncode != 0
    assert len(list((tmp_path / "data" / "crash").glob("running-*.txt"))) == 1
    second = _run(tmp_path, NEXT, "next.py")
    assert second.returncode == 0, second.stderr
    report = second.stdout.strip()
    assert os.path.basename(report).startswith("crash-") and report.endswith(".txt")
    text = open(report, encoding="utf-8").read()
    assert text.startswith("Ectype OCR ")
    assert "read: page 3 of 26" in text
    assert "Fatal Python error: Aborted" in text
    assert "in reading_page" in text
    assert "Frozen: False" in text
    # that start ended normally: only the crash report is left
    assert [p.name for p in (tmp_path / "data" / "crash").iterdir()] == [os.path.basename(report)]


def test_a_normal_exit_leaves_nothing(tmp_path):
    result = _run(tmp_path, CLEAN, "clean.py")
    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "data" / "crash").iterdir()) == []


def test_only_ten_reports_are_kept(tmp_path):
    folder = tmp_path / "data" / "crash"
    folder.mkdir(parents=True)
    for i in range(12):
        (folder / f"crash-20260101-0000{i:02d}.txt").write_text("x", encoding="utf-8")
    assert _run(tmp_path, CLEAN, "clean.py").returncode == 0
    names = sorted(p.name for p in folder.iterdir())
    assert len(names) == 10 and names[0] == "crash-20260101-000002.txt"
