from pathlib import Path

from renforge.translation import _COUNT_RE, list_languages


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
