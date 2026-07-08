from pathlib import Path

from renforge.translation import _COUNT_RE, list_languages, list_translation_strings


def test_list_languages_reads_tl_dir(tmp_path: Path) -> None:
    tl = tmp_path / "proj" / "game" / "tl"
    (tl / "french").mkdir(parents=True)
    (tl / "spanish").mkdir()
    (tl / "None").mkdir()  # reserved, must be excluded

    assert list_languages(tmp_path / "proj") == ["french", "spanish"]


def test_list_languages_without_tl_dir(tmp_path: Path) -> None:
    (tmp_path / "proj" / "game").mkdir(parents=True)
    assert list_languages(tmp_path / "proj") == []


def test_count_regex_parses_translate_count_output() -> None:
    line = "french: 12 missing dialogue translations, 3 missing string translations."
    m = _COUNT_RE.search(line)
    assert m and int(m.group("dialogue")) == 12 and int(m.group("strings")) == 3


def test_list_translation_strings_keeps_blocks_separate(tmp_path: Path) -> None:
    tl = tmp_path / "proj" / "game" / "tl" / "french"
    tl.mkdir(parents=True)
    (tl / "script.rpy").write_text(
        '''
translate french start_abcd:
    # "Hello."
    "Bonjour."

translate french next_efgh:
    # "Bye."
    "Au revoir."
''',
        encoding="utf-8",
    )

    rows = list_translation_strings(tmp_path / "proj", "french")

    assert [(row["id"], row["src"], row["tr"]) for row in rows] == [
        ("start_abcd", "Hello.", "Bonjour."),
        ("next_efgh", "Bye.", "Au revoir."),
    ]


def test_list_translation_strings_handles_escaped_quotes_and_skips_non_dialogue(tmp_path: Path) -> None:
    tl = tmp_path / "proj" / "game" / "tl" / "french"
    tl.mkdir(parents=True)
    (tl / "script.rpy").write_text(
        r'''
translate french start_abcd:
    # "Say \"hello\"."
    voice "voice/line.ogg"
    "Dis \"bonjour\"."
''',
        encoding="utf-8",
    )

    rows = list_translation_strings(tmp_path / "proj", "french")

    assert rows[0]["src"] == 'Say "hello".'
    assert rows[0]["tr"] == 'Dis "bonjour".'


def test_list_translation_strings_generates_unique_string_ids_across_blocks(tmp_path: Path) -> None:
    tl = tmp_path / "proj" / "game" / "tl" / "french"
    tl.mkdir(parents=True)
    (tl / "common.rpy").write_text(
        '''
translate french strings:
    old "Start"
    new "Démarrer"

translate french strings:
    old "Quit"
    new "Quitter"
''',
        encoding="utf-8",
    )

    rows = list_translation_strings(tmp_path / "proj", "french")

    assert [(row["id"], row["src"], row["tr"]) for row in rows] == [
        ("string_1", "Start", "Démarrer"),
        ("string_2", "Quit", "Quitter"),
    ]
