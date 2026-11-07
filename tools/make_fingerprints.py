"""Make src/ectype/fingerprints.json: the known fingerprint of every file Ectype OCR downloads
from GitHub, so a download that was swapped, cut short or tampered with is never used.

Run it from the repository root, with the source on the path:

    PYTHONPATH=src python tools/make_fingerprints.py

It is re-runnable. To move a pin (take a newer version of a source), change that commit in
`ectype/verify.py` (`--latest` prints each repository's newest commit), run this, check the
changes to fingerprints.json, and commit both.

For each pinned source (`verify.PINS`) it reads the list of files at the pinned commit with
their git blob ids. That list is git's tree for the commit, fetched without the files
themselves (a "blobless" fetch), so the Tesseract language data (gigabytes) is never
downloaded just to fingerprint it. It is the same information GitHub's trees API gives
(`/git/trees/<sha>?recursive=1`: path, sha), without needing an API key; each size comes from
the Content-Length of a HEAD request to the file's pinned address. A wrong size would make the
git id fail to match, never pass. For the small
known texts (`verify.WHOLE`) it also downloads each file, checks that its git blob id is the
tree's, and records its SHA-256; `verify.fetch` checks both.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from ectype import languages, texts, verify

OUT = Path(__file__).resolve().parent.parent / "src" / "ectype" / "fingerprints.json"


def wanted(source: str) -> list[str]:
    """The paths (from the source's folder) Ectype downloads."""
    if source in ("tessdata", "tessdata_best"):
        return [f"{code}.traineddata" for code in languages.LANGUAGES]
    if source == "hebrew-bible":
        return [f"{b}.xml" for b in texts.HEBREW_BOOKS]
    if source == "greek-nt":
        return [f"{b}.txt" for b in texts.GREEK_BOOKS]
    if source == "peshitta":
        return [f"{b}.txt" for b in texts.PESHITTA_BOOKS]
    if source == "vulgate":
        return ["vul.tsv"]
    if source == "quran":
        return ["quran-simple.txt"]
    raise KeyError(source)


def git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=900)
    if done.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def tree(repo: str, commit: str) -> dict[str, str]:
    """Every file of the commit: path -> git blob id (git's sizes would need the files)."""
    with tempfile.TemporaryDirectory(prefix="ectype-tree-") as tmp:
        here = Path(tmp)
        git("init", "-q", cwd=here)
        git("fetch", "-q", "--depth", "1", "--filter=blob:none", f"https://github.com/{repo}.git", commit, cwd=here)
        files = {}
        for line in git("ls-tree", "-r", "-z", "FETCH_HEAD", cwd=here).split("\0"):
            if line:
                meta, _, path = line.partition("\t")
                _mode, kind, sha = meta.split()
                if kind == "blob":
                    files[path] = sha
        return files


def size_of(url: str) -> int:
    """A file's size, from the Content-Length of a HEAD request to its pinned address."""
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Length"])


def make() -> dict:
    out = {}
    for source, (repo, commit, folder) in verify.PINS.items():
        print(f"{source}: {repo} at {commit[:10]}", file=sys.stderr)
        listed = tree(repo, commit)
        files = {}
        for path in wanted(source):
            if folder + path not in listed:
                print(f"  not in the repository: {folder + path}", file=sys.stderr)
                continue
            sha = listed[folder + path]
            size = size_of(verify.url_for(source, path))
            files[path] = {"size": size, "git": sha}
            if source in verify.WHOLE:
                with urllib.request.urlopen(verify.url_for(source, path), timeout=120) as r:
                    data = r.read()
                if len(data) != size or verify.git_blob_id(data) != sha:
                    raise SystemExit(f"{source}/{path}: the download isn't the commit's file")
                files[path]["sha256"] = hashlib.sha256(data).hexdigest()
        out[source] = {"repo": repo, "commit": commit, "files": files}
    return out


def latest() -> None:
    for source, (repo, commit, _folder) in verify.PINS.items():
        head = subprocess.run(["git", "ls-remote", f"https://github.com/{repo}.git", "HEAD"], capture_output=True,
                              text=True, timeout=120).stdout.split()
        now = head[0] if head else "unknown"
        print(f"{source:14} pinned {commit[:10]}  newest {now[:10]}{'' if now == commit else '  (newer)'}")


def main() -> None:
    if "--latest" in sys.argv:
        return latest()
    data = make()
    OUT.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
