"""Downloads checked against a known fingerprint.

Ectype will be shared with others, so a file that was swapped, cut short or tampered with
must never be used. Every GitHub source is pinned to one commit (the constants below), and
`fingerprints.json` (made by `tools/make_fingerprints.py`) holds each file's size and git
blob id at that commit, and for the small known texts also a SHA-256. `fetch` downloads a
file, checks it while it arrives, and only then puts it in place.

Kraken models are checked against the MD5 that Zenodo lists for each file (`check_md5`).
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Callable

# The pinned commits. To move one: change it here, run tools/make_fingerprints.py, and commit
# both. (Each is the default branch's head on 2026-11-07, except the Qurʾān's, pinned since 0.6.1.)
TESSDATA_COMMIT = "ced78752cc61322fb554c280d13360b35b8684e4"
TESSDATA_BEST_COMMIT = "e12c65a915945e4c28e237a9b52bc4a8f39a0cec"
MORPHHB_COMMIT = "3d15126fb1ef74867fc1434be1942e837932691f"
SBLGNT_COMMIT = "d7f35adca9ec410e1d1499c63b4dd989d76c8d4d"
VUL_COMMIT = "10d9d6191dd81f110a9788e66b4818483f32d31d"
PESHITTA_COMMIT = "9850f5addade26f681334aa475570bef9b0b440a"
QURAN_COMMIT = "d96dab765d712634ba17d1550cd5d1dc15c25b36"

# source -> (repository, commit, folder inside it where the paths below start)
PINS = {
    "tessdata": ("tesseract-ocr/tessdata", TESSDATA_COMMIT, ""),
    "tessdata_best": ("tesseract-ocr/tessdata_best", TESSDATA_BEST_COMMIT, ""),
    "hebrew-bible": ("openscriptures/morphhb", MORPHHB_COMMIT, "wlc/"),
    "greek-nt": ("LogosBible/SBLGNT", SBLGNT_COMMIT, "data/sblgnt/text/"),
    "vulgate": ("lukesmithxyz/vul", VUL_COMMIT, ""),
    "peshitta": ("ETCBC/peshitta", PESHITTA_COMMIT, "plain/0.2/"),
    "quran": ("rizaumami/quran-epub", QURAN_COMMIT, "src/quran/"),
}
RAW = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"
# Sources small enough to download whole when fingerprinting: they also get a SHA-256.
WHOLE = ("hebrew-bible", "greek-nt", "vulgate", "peshitta", "quran")


class DownloadMismatch(Exception):
    """A download that doesn't match what is known of it, or isn't known at all."""


def mismatch(name: str) -> DownloadMismatch:
    return DownloadMismatch(f"{name} didn't match its known fingerprint, so it wasn't used. Try again later.")


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


_loaded: dict | None = None


def fingerprints() -> dict:
    """The shipped fingerprints.json (source -> {repo, commit, files})."""
    global _loaded
    if _loaded is None:
        try:
            _loaded = json.loads((Path(__file__).with_name("fingerprints.json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _loaded = {}
    return _loaded


def expected(source: str, path: str) -> dict | None:
    """What is known of one file of a pinned source, or None when it isn't known or the
    fingerprints are for another commit than the pin."""
    entry = fingerprints().get(source)
    if not entry or entry.get("commit") != PINS[source][1]:
        return None
    return (entry.get("files") or {}).get(path)


def url_for(source: str, path: str) -> str:
    """The address of one file at its source's pinned commit (path from the source's folder)."""
    repo, commit, folder = PINS[source]
    return RAW.format(repo=repo, commit=commit, path=folder + path)


def known(source: str, path: str) -> dict:
    """The fingerprint of a file, or a refusal: a file not on record is never downloaded."""
    found = expected(source, path)
    if found is None:
        raise DownloadMismatch(f"{path} isn't one of the files Ectype OCR knows how to check, so it wasn't "
                               "downloaded.")
    return found


def _check(size: int, sha1, sha256, want: dict) -> bool:
    if "size" in want and size != want["size"]:
        return False
    if "git" in want and sha1.hexdigest() != want["git"]:
        return False
    if "sha256" in want and sha256.hexdigest() != want["sha256"]:
        return False
    return bool({"git", "sha256"} & set(want))  # a fingerprint with no hash proves nothing


def matches(path: Path, want: dict) -> bool:
    """Whether a file already on disk has the fingerprint (for small cached copies)."""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return _check(len(data), hashlib.sha1(b"blob %d\0" % len(data) + data), hashlib.sha256(data), want)


def fetch(url: str, dest: Path, want: dict, progress: Callable[[int, int], None] | None = None,
          timeout: float = 60, opener=None, name: str | None = None) -> Path:
    """Download `url` to `dest`, but only if it has the fingerprint `want` ({"size", "git",
    "sha256"}, any of the last two). It goes to `dest` + ".part" first, is hashed as it arrives, and
    is renamed into place only when everything matches; otherwise the .part is deleted and
    DownloadMismatch raised. A download that fails any other way leaves no .part either."""
    name = name or dest.name
    if not want or not ({"git", "sha256"} & set(want)):
        raise mismatch(name)
    size = want.get("size")
    sha1 = hashlib.sha1(b"blob %d\0" % size) if size is not None else None
    sha256 = hashlib.sha256()
    part = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    got = 0
    try:
        with (opener or urllib.request.urlopen)(url, timeout=timeout) as response, open(part, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0) if hasattr(response, "headers") else 0
            while chunk := response.read(1 << 16):
                got += len(chunk)
                if size is not None and got > size:
                    raise mismatch(name)  # more than it should be: stop reading
                out.write(chunk)
                sha256.update(chunk)
                if sha1 is not None:
                    sha1.update(chunk)
                if progress:
                    progress(got, total or size or 0)
        if sha1 is None:  # no size on record: the git id can't be made as it arrives
            data = part.read_bytes()
            sha1 = hashlib.sha1(b"blob %d\0" % len(data) + data)
        if not _check(got, sha1, sha256, want):
            raise mismatch(name)
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return dest


def check_md5(path: Path, checksum: str | None, name: str | None = None) -> None:
    """Check a downloaded file against a Zenodo checksum ("md5:abc..."). On a mismatch, or
    when no checksum is known, the file is deleted and DownloadMismatch raised."""
    name = name or path.name
    kind, _, digest = (checksum or "").partition(":")
    if kind.lower() != "md5" or not digest:
        path.unlink(missing_ok=True)
        raise mismatch(name)
    md5 = hashlib.md5(usedforsecurity=False)
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                md5.update(chunk)
    except OSError:
        raise mismatch(name) from None
    if md5.hexdigest() != digest.lower():
        path.unlink(missing_ok=True)
        raise mismatch(name)


ZENODO_RECORD = "https://zenodo.org/api/records/{record}"


def zenodo_checksums(record: dict) -> dict[str, str]:
    """File name -> "md5:..." from a Zenodo record (its `files`, as the old and the new API
    write them: `key` or `filename`, and `checksum`)."""
    out = {}
    files = record.get("files") or []
    if isinstance(files, dict):  # {"entries": {name: {...}}} in some answers
        files = [{"key": k, **v} for k, v in (files.get("entries") or {}).items()]
    for f in files:
        name = f.get("key") or f.get("filename") or f.get("name")
        if name and f.get("checksum"):
            out[str(name)] = str(f["checksum"])
    return out


def check_files(folder: Path, checksums: dict[str, str], must: tuple[str, ...] = (".mlmodel", ".safetensors")) -> None:
    """Check the files a model download left in `folder` (any depth) against Zenodo's checksums,
    by file name. A file that doesn't match is deleted and DownloadMismatch raised. A model file
    (`must` endings) under a name not on record (the downloader may rename it) must have the
    contents of one of the record's files; otherwise it can't be vouched for and is refused."""
    listed = {c.partition(":")[2].lower() for c in checksums.values() if c.lower().startswith("md5:")}
    for path in sorted(p for p in Path(folder).rglob("*") if p.is_file()):
        checksum = checksums.get(path.name)
        if checksum is not None:
            check_md5(path, checksum, name=f"The model file {path.name}")
        elif path.name.endswith(must):
            if _md5_of(path) not in listed:
                path.unlink(missing_ok=True)
                raise mismatch(f"The model file {path.name}")


def _md5_of(path: Path) -> str | None:
    md5 = hashlib.md5(usedforsecurity=False)
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                md5.update(chunk)
    except OSError:
        return None
    return md5.hexdigest()
