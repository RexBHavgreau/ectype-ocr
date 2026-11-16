"""Choosing a Kraken model for a language: the list functions, the command line and the menu."""

import pytest

from ectype import cli, kraken, languages

MODELS = {
    "kraken_111": {"title": "Hebrew square print", "scripts": ["Hebrew"], "file": "x"},
    "kraken_222": {"title": "Hebrew Masoretic", "scripts": ["Hebrew"], "file": "x"},
    "kraken_333": {"title": "Arabic and Hebrew", "scripts": ["Arabic", "Hebrew"], "file": "x"},
    "kraken_444": {"title": "Greek", "scripts": ["Greek", "Latin"], "file": "x", "book": "Thesaurus"},
}


@pytest.fixture(autouse=True)
def models(monkeypatch):
    monkeypatch.setattr(kraken, "installed_models", lambda: dict(MODELS))


def test_split_and_models_for_a_language():
    assert languages.tesseract_codes(["eng", "kraken_111", "heb"]) == ["eng", "heb"]
    assert languages.kraken_codes(["eng", "kraken_111", "heb"]) == ["kraken_111"]
    assert set(languages.kraken_for("heb")) == {"kraken_111", "kraken_222", "kraken_333"}
    assert set(languages.kraken_for("ara")) == {"kraken_333"}  # a many-script model is offered for each
    assert languages.kraken_for("grc") == ["kraken_444"]
    assert languages.kraken_for("lat") == []  # the Greek model's Latin is only its numbers and punctuation


def test_reader_of_and_choose_reader():
    lst = ["eng", "heb", "kraken_111"]
    assert languages.reader_of(lst, "heb") == "kraken_111"
    assert languages.reader_of(lst, "eng") is None
    # one model replaced by another; the rest stays in order
    assert languages.choose_reader(lst, "heb", "kraken_222") == ["eng", "heb", "kraken_222"]
    # Tesseract alone
    assert languages.choose_reader(lst, "heb", None) == ["eng", "heb"]
    # a first choice
    assert languages.choose_reader(["eng", "heb"], "heb", "kraken_333") == ["eng", "heb", "kraken_333"]
    # the language is ticked too
    assert languages.choose_reader(["eng"], "heb", "kraken_111") == ["eng", "heb", "kraken_111"]
    # a model of several scripts replaces the model of its script; other scripts' models stay
    assert languages.choose_reader(["heb", "kraken_111", "grc", "kraken_444"], "heb", "kraken_333") == \
        ["heb", "kraken_333", "grc", "kraken_444"]


def test_an_older_project_with_only_a_kraken_code():
    lst = ["eng", "kraken_222"]  # no Hebrew language ticked
    assert languages.orphan_readers(lst) == ["kraken_222"]
    hebrew = [r for r in languages.reader_rows(lst) if r[0] == "Hebrew"][0]
    assert "kraken_222" in hebrew[2] and hebrew[1] == "Hebrew"
    assert languages.choose_reader_for_script(lst, "Hebrew", None) == ["eng"]  # it can be removed
    assert languages.orphan_readers(["eng", "heb", "kraken_222"]) == []


def test_reader_rows_and_titles():
    rows = languages.reader_rows(["eng", "heb"])
    assert [r[0] for r in rows] == ["Hebrew"]  # English has no model installed
    assert set(rows[0][2]) == {"kraken_111", "kraken_222", "kraken_333"}
    assert languages.reader_title("kraken_111") == "Kraken: Hebrew square print"
    assert languages.reader_title("kraken_444") == "Kraken: trained on Thesaurus"


def test_kraken_choices_for_the_command_line():
    assert languages.apply_kraken_choices(["eng", "heb"], ["heb=222"]) == ["eng", "heb", "kraken_222"]
    assert languages.apply_kraken_choices(["eng", "heb"], ["heb=kraken_111", "heb=222"]) == ["eng", "heb", "kraken_222"]
    with pytest.raises(ValueError, match="ectype kraken list"):
        languages.apply_kraken_choices(["heb"], ["heb=999"])
    with pytest.raises(ValueError, match="reads Greek"):
        languages.apply_kraken_choices(["heb"], ["heb=444"])
    with pytest.raises(ValueError, match="LANG=MODEL"):
        languages.apply_kraken_choices(["heb"], ["222"])


def test_kraken_flag_is_read_by_the_reading_commands():
    parser = cli.build_parser()
    for argv in (["run", "b.pdf"], ["text", "b.pdf"], ["text-files", "d"], ["ocr", "ws"]):
        args = parser.parse_args([*argv, "--lang", "eng+heb", "--kraken", "heb=111", "--kraken", "ara=333"])
        assert args.kraken == ["heb=111", "ara=333"]
    args = parser.parse_args(["run", "b.pdf", "--lang", "eng+heb", "--kraken", "heb=111"])
    assert cli.apply_kraken_options(args) is None and args.lang == "eng+heb+kraken_111"
    args = parser.parse_args(["run", "b.pdf", "--lang", "eng+heb", "--kraken", "heb=999"])
    assert "isn't installed" in cli.apply_kraken_options(args)
    args = parser.parse_args(["run", "b.pdf", "--lang", "eng+heb+kraken_222"])  # the old way keeps working
    assert cli.apply_kraken_options(args) is None and args.lang == "eng+heb+kraken_222"


def test_kraken_commands_and_languages_listing(capsys, monkeypatch, tmp_path):
    f = tmp_path / "m.mlmodel"
    f.write_bytes(b"x" * 2000)
    models = {k: dict(v, file=str(f)) for k, v in MODELS.items()}
    monkeypatch.setattr(kraken, "installed_models", lambda: dict(models))
    assert cli.main(["kraken", "list"]) == 0
    out = capsys.readouterr().out
    assert "Hebrew square print" in out and "trained on Thesaurus" in out and "kraken_333" in out
    monkeypatch.setattr(kraken, "worker_command", lambda: None)
    assert cli.main(["kraken", "available"]) == 2
    assert "engine isn't installed" in capsys.readouterr().out
    assert cli.main(["kraken", "add", "555"]) == 2
    monkeypatch.setattr(languages, "installed", lambda: ["eng", "heb", *models])
    assert cli.main(["languages"]) == 0
    out = capsys.readouterr().out
    assert "choose with --kraken LANG=MODEL" in out and "Hebrew:" in out
    assert out.index("eng") < out.index("Kraken models")


def test_the_menu_lists_tesseract_languages_and_a_reader_choice(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from ectype.gui import LanguageButton

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(languages, "installed", lambda: ["eng", "heb", "grc", *MODELS])
    button = LanguageButton()
    sent = []
    button.changed.connect(sent.append)
    button.set_languages(["eng", "heb"])
    assert set(button.boxes) == {"eng", "heb", "grc"}  # no Kraken codes among the ticks

    def submenus():
        return {a.text(): a.menu() for a in button.menu_.actions() if a.menu()}

    assert list(submenus()) == ["Read Hebrew with"]
    acts = submenus()["Read Hebrew with"].actions()
    items = [a.text() for a in acts]
    assert items[0] == "Tesseract alone" and "Kraken: Hebrew Masoretic" in items and "Kraken: Hebrew square print" in items
    assert [a.text() for a in acts if a.isChecked()] == ["Tesseract alone"]
    assert [a.text() for a in button.menu_.actions()][-3:] == ["Kraken models…", "Add languages…", "Trained models…"]
    next(a for a in acts if a.text() == "Kraken: Hebrew Masoretic").trigger()
    assert sent[-1] == ["eng", "heb", "kraken_222"]
    assert "Hebrew (Kraken)" in button.text() and "Hebrew Masoretic" in button.toolTip()
    # ticking Greek adds its choice (after the event loop turns); Tesseract alone removes the model
    button.boxes["grc"].setChecked(True)
    app.processEvents()
    assert sent[-1] == ["eng", "heb", "grc", "kraken_222"]
    assert set(submenus()) == {"Read Hebrew with", "Read Greek (Ancient) with"}
    submenus()["Read Hebrew with"].actions()[0].trigger()
    assert sent[-1] == ["eng", "heb", "grc"]


def test_a_model_of_two_scripts_keeps_the_other_language():
    # Hebrew and Arabic both read by the Arabic-and-Hebrew model; a Hebrew-only model chosen for
    # Hebrew takes Hebrew from it (fewest scripts wins, as reading does) and Arabic keeps it.
    lst = ["heb", "ara", "kraken_333"]
    new = languages.choose_reader(lst, "heb", "kraken_111")
    assert new == ["heb", "ara", "kraken_333", "kraken_111"]
    assert languages.reader_of(new, "heb") == "kraken_111"
    assert languages.reader_of(new, "ara") == "kraken_333"
    # without Arabic ticked, the two-script model goes
    assert languages.choose_reader(["heb", "kraken_333"], "heb", "kraken_111") == ["heb", "kraken_111"]
