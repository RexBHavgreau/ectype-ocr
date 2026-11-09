"""Downloads are checked against known fingerprints (verify.py); no real network here."""

from __future__ import annotations

import hashlib
import io
import json

import pytest

from ectype import kraken_worker, languages, texts, verify

BODY = b"the real file " * 500


class Reply(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def fingerprint(data: bytes = BODY) -> dict:
    return {"size": len(data), "git": verify.git_blob_id(data), "sha256": hashlib.sha256(data).hexdigest()}


def served(files: dict[str, bytes], seen: list | None = None):
    """A stand-in for urlopen: url ending -> bytes."""
    def opener(url, timeout=None):
        if seen is not None:
            seen.append(url)
        for tail, data in files.items():
            if str(url).endswith(tail):
                return Reply(data)
        raise OSError(f"404 {url}")
    return opener


def test_exact_match_is_put_in_place(tmp_path):
    dest = tmp_path / "a" / "x.bin"
    steps = []
    got = verify.fetch("http://h/x.bin", dest, fingerprint(), opener=served({"x.bin": BODY}),
                       progress=lambda done, total: steps.append((done, total)))
    assert got == dest and dest.read_bytes() == BODY
    assert steps[-1] == (len(BODY), len(BODY))
    assert not list(tmp_path.rglob("*.part"))


def test_git_blob_id_is_gits():
    assert verify.git_blob_id(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"  # git's empty blob
    assert verify.git_blob_id(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


@pytest.mark.parametrize("served_bytes", [BODY[:-1], BODY + b"!"])
def test_size_mismatch_leaves_nothing(tmp_path, served_bytes):
    dest = tmp_path / "x.bin"
    with pytest.raises(verify.DownloadMismatch, match="known fingerprint"):
        verify.fetch("http://h/x.bin", dest, fingerprint(), opener=served({"x.bin": served_bytes}))
    assert list(tmp_path.iterdir()) == []


def test_changed_byte_of_the_same_size_leaves_nothing(tmp_path):
    changed = BODY[:100] + b"X" + BODY[101:]
    assert len(changed) == len(BODY) and changed != BODY
    dest = tmp_path / "x.bin"
    with pytest.raises(verify.DownloadMismatch, match="didn't match its known fingerprint, so it wasn't used"):
        verify.fetch("http://h/x.bin", dest, fingerprint(), opener=served({"x.bin": changed}))
    assert list(tmp_path.iterdir()) == []
    # the git id alone, and the SHA-256 alone, each catch it
    for key in ("git", "sha256"):
        with pytest.raises(verify.DownloadMismatch):
            verify.fetch("http://h/x.bin", dest, {"size": len(BODY), key: fingerprint()[key]},
                         opener=served({"x.bin": changed}))
        assert list(tmp_path.iterdir()) == []


def test_a_failed_connection_leaves_no_part(tmp_path):
    dest = tmp_path / "x.bin"
    with pytest.raises(OSError):
        verify.fetch("http://h/other", dest, fingerprint(), opener=served({}))
    assert list(tmp_path.iterdir()) == []


def test_a_fingerprint_without_a_hash_is_refused(tmp_path):
    with pytest.raises(verify.DownloadMismatch):
        verify.fetch("http://h/x.bin", tmp_path / "x.bin", {"size": len(BODY)}, opener=served({"x.bin": BODY}))
    with pytest.raises(verify.DownloadMismatch):
        verify.fetch("http://h/x.bin", tmp_path / "x.bin", {}, opener=served({"x.bin": BODY}))
    assert list(tmp_path.iterdir()) == []


def test_the_shipped_fingerprints_cover_every_download():
    for code in languages.LANGUAGES:
        for source in ("tessdata", "tessdata_best"):
            want = verify.expected(source, f"{code}.traineddata")
            assert want and want["size"] > 0 and len(want["git"]) == 40, (source, code)
    for books, source, ext in ((texts.HEBREW_BOOKS, "hebrew-bible", ".xml"), (texts.GREEK_BOOKS, "greek-nt", ".txt"),
                               (texts.PESHITTA_BOOKS, "peshitta", ".txt")):
        for book in books:
            assert len(verify.known(source, book + ext)["sha256"]) == 64
    assert verify.known("vulgate", "vul.tsv")["sha256"] and verify.known("quran", texts.QURAN_FILE)["sha256"]
    for source, (_repo, commit, _folder) in verify.PINS.items():
        assert len(commit) == 40 and verify.fingerprints()[source]["commit"] == commit
    assert "/master/" not in verify.url_for("hebrew-bible", "Gen.xml") and verify.QURAN_COMMIT in verify.url_for("quran", "q")


def test_unknown_file_is_refused_and_never_downloaded(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    seen = []
    monkeypatch.setattr("urllib.request.urlopen", served({}, seen))
    with pytest.raises(verify.DownloadMismatch, match="wasn't downloaded"):
        verify.known("tessdata", "xyz.traineddata")
    monkeypatch.setattr(languages, "LANGUAGES", {**languages.LANGUAGES, "xyz": "Made up"})
    with pytest.raises(verify.DownloadMismatch):
        languages.download(["xyz"])
    assert seen == [] and not (tmp_path / "data" / "tessdata" / "xyz.traineddata").exists()


def test_language_download_is_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    source = tmp_path / "server"
    source.mkdir()
    (source / "lat.traineddata").write_bytes(BODY)
    url = source.as_uri() + "/{code}.traineddata"
    with pytest.raises(verify.DownloadMismatch):
        languages.download(["lat"], url=url, fingerprints={"lat": {**fingerprint(), "git": "0" * 40}})
    folder = tmp_path / "data" / "tessdata"
    assert not (folder / "lat.traineddata").exists() and not list(folder.glob("*.part"))
    assert languages.download(["lat"], url=url, fingerprints={"lat": fingerprint()}) == ["lat"]
    assert (folder / "lat.traineddata").read_bytes() == BODY


def _text_fingerprints(monkeypatch, files: dict[str, bytes]):
    """Fingerprints for a few files of the Vulgate and the Qurʾān sources, at the real pins."""
    table = {src: {"repo": "x/y", "commit": verify.PINS[src][1], "files": {n: fingerprint(d) for n, d in fs.items()}}
             for src, fs in files.items()}
    monkeypatch.setattr(verify, "_loaded", table)


def test_a_kept_text_that_doesnt_match_is_fetched_again(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    tsv = "\n".join(f"Genesis\tGen\t1\t1\t{n}\tIn principio verse {n}" for n in range(1, 40)).encode() + b"\n"
    _text_fingerprints(monkeypatch, {"vulgate": {"vul.tsv": tsv}})
    seen = []
    monkeypatch.setattr("urllib.request.urlopen", served({"vul.tsv": tsv}, seen))
    verses = texts.load("vulgate")
    assert len(verses) == 39 and len(seen) == 1 and "/vul.tsv" in seen[0] and verify.VUL_COMMIT in seen[0]
    texts.load("vulgate")
    assert len(seen) == 1, "a kept copy that matches is used"
    # an older copy (no stamp), a damaged copy and a copy of another pin are each fetched again
    cache = texts.folder() / "vulgate.txt"
    texts._stamp(cache).unlink()
    texts.load("vulgate")
    assert len(seen) == 2
    cache.write_text("tampered\n", encoding="utf-8")
    assert texts.load("vulgate") == verses and len(seen) == 3
    texts._stamp(cache).write_text(json.dumps({"pin": "old", "sha256": hashlib.sha256(cache.read_bytes()).hexdigest()}))
    texts.load("vulgate")
    assert len(seen) == 4 and cache.read_text(encoding="utf-8").startswith("In principio")


def test_a_swapped_text_download_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    tsv = b"Genesis\tGen\t1\t1\t1\tIn principio\n"
    _text_fingerprints(monkeypatch, {"vulgate": {"vul.tsv": tsv}})
    monkeypatch.setattr("urllib.request.urlopen", served({"vul.tsv": tsv.replace(b"principio", b"principiu")}))
    with pytest.raises(verify.DownloadMismatch):
        texts.load("vulgate")
    assert not (texts.folder() / "vulgate.txt").exists()


def test_arabic_uses_the_quran_alone_while_van_dyck_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    lines = [f"1|{n}|بِسْمِ اللَّهِ {n}" for n in range(1, 6300)] + ["# Tanzil notice"]
    quran = ("\n".join(lines) + "\n").encode("utf-8")
    _text_fingerprints(monkeypatch, {"quran": {texts.QURAN_FILE: quran}})
    seen = []
    monkeypatch.setattr("urllib.request.urlopen", served({texts.QURAN_FILE: quran}, seen))
    assert texts.VAN_DYCK_FINGERPRINT is None
    verses = texts.load("arabic")
    assert len(verses) == 6299 and all("ebible" not in u and "tanzil" not in u for u in seen)
    assert "# Tanzil notice" in (texts.folder() / "quran.txt").read_text(encoding="utf-8"), "the notice is kept"
    assert not hasattr(texts, "_tanzil_form")


def test_best_model_is_checked(tmp_path, monkeypatch):
    from ectype import train

    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    _loaded = {"tessdata_best": {"repo": "x/y", "commit": verify.PINS["tessdata_best"][1],
                                 "files": {"eng.traineddata": fingerprint()}}}
    monkeypatch.setattr(verify, "_loaded", _loaded)
    monkeypatch.setattr("urllib.request.urlopen", served({"eng.traineddata": BODY[:-5] + b"xxxxx"}))
    with pytest.raises(train.TrainingError, match="known fingerprint"):
        train.best_model("eng")
    assert not list((tmp_path / "data").rglob("eng.traineddata*"))
    monkeypatch.setattr("urllib.request.urlopen", served({"eng.traineddata": BODY}))
    assert train.best_model("eng").read_bytes() == BODY


# -- Kraken models: Zenodo's MD5 ------------------------------------------------------------


def md5(data: bytes) -> str:
    return "md5:" + hashlib.md5(data).hexdigest()


def test_zenodo_checksums_are_read_from_either_api():
    old = {"files": [{"key": "model.safetensors", "checksum": "md5:aa"}, {"key": "x", "size": 1}]}
    new = {"files": {"entries": {"model.mlmodel": {"checksum": "md5:bb"}}}}
    assert verify.zenodo_checksums(old) == {"model.safetensors": "md5:aa"}
    assert verify.zenodo_checksums(new) == {"model.mlmodel": "md5:bb"}


def test_a_model_with_the_listed_md5_is_kept(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"weights")
    (tmp_path / "metadata.json").write_text("{}", encoding="utf-8")
    verify.check_files(tmp_path, {"model.safetensors": md5(b"weights"), "metadata.json": md5(b"{}")})
    assert (tmp_path / "model.safetensors").is_file()


def test_a_model_with_another_md5_is_deleted(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"weights")
    with pytest.raises(verify.DownloadMismatch, match="known fingerprint"):
        verify.check_files(tmp_path, {"model.safetensors": md5(b"other weights")})
    assert not (tmp_path / "model.safetensors").exists()


def test_a_model_file_with_no_md5_on_record_is_refused(tmp_path):
    (tmp_path / "model.mlmodel").write_bytes(b"weights")
    with pytest.raises(verify.DownloadMismatch):
        verify.check_files(tmp_path, {})
    assert not (tmp_path / "model.mlmodel").exists()
    with pytest.raises(verify.DownloadMismatch):
        verify.check_md5(tmp_path / "gone.bin", None)


def test_the_worker_checks_a_download_it_makes(tmp_path, monkeypatch):
    """`get` with a fake htrmopo that writes the files, and a fake Zenodo record."""
    import sys
    import types

    def fake_get_model(doi, path, callback=None):
        out = tmp_path / path
        out.mkdir(parents=True, exist_ok=True)
        (out / "model.safetensors").write_bytes(b"tampered")
        return str(out)

    monkeypatch.setitem(sys.modules, "htrmopo", types.SimpleNamespace(get_model=fake_get_model))
    with pytest.raises(verify.DownloadMismatch):
        kraken_worker.get("10.5281/zenodo.123", str(tmp_path / "m"), {"model.safetensors": md5(b"weights")})
    assert not (tmp_path / "m").exists(), "the whole download is removed"
    monkeypatch.setattr(kraken_worker, "_zenodo_checksums", lambda doi: (_ for _ in ()).throw(OSError("blocked")))
    with pytest.raises(verify.DownloadMismatch):
        kraken_worker.get("10.5281/zenodo.123", str(tmp_path / "m2"))
    assert not (tmp_path / "m2").exists()


def test_the_app_reports_a_mismatch_plainly(tmp_path, monkeypatch):
    from ectype import kraken

    monkeypatch.setenv("ECTYPE_DATA_DIR", str(tmp_path / "data"))
    message = str(verify.mismatch("The model file model.safetensors"))

    def fake_run(args, timeout=600, progress=None):
        (kraken.models_dir() / kraken.code_for("10.5281/zenodo.9")).mkdir(parents=True)
        raise kraken.KrakenError(f"Kraken failed (get): {message}")

    monkeypatch.setattr(kraken, "_run", fake_run)
    with pytest.raises(kraken.KrakenError) as info:
        kraken.install_model({"doi": "10.5281/zenodo.9"})
    assert str(info.value) == message
    assert not (kraken.models_dir() / kraken.code_for("10.5281/zenodo.9")).exists()


def test_a_renamed_model_file_is_accepted_by_its_contents(tmp_path):
    """The model downloader may store a file under another name than Zenodo's: it passes if its
    contents match one of the record's files, and is refused if they match none."""
    import hashlib

    from ectype import verify

    data = b"model weights"
    (tmp_path / "renamed.safetensors").write_bytes(data)
    listed = {"original.safetensors": "md5:" + hashlib.md5(data).hexdigest()}
    verify.check_files(tmp_path, listed)
    assert (tmp_path / "renamed.safetensors").is_file()
    (tmp_path / "other.mlmodel").write_bytes(b"something else")
    with pytest.raises(verify.DownloadMismatch):
        verify.check_files(tmp_path, listed)
    assert not (tmp_path / "other.mlmodel").exists()
