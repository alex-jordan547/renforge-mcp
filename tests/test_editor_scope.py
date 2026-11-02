from __future__ import annotations

from pathlib import Path

import pytest

from renforge.editor.exceptions import EditorError
from renforge.editor.scope import (
    ENV_ALLOW_PATHS,
    is_path_in_scope,
    load_path_scope,
    require_in_scope,
)


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    game = root / "game"
    (game / "screens" / "tl").mkdir(parents=True)
    (game / "script.rpy").write_text("screen test_screen:\n", encoding="utf-8")
    (game / "gui.rpy").write_text("define gui.dialogue_xpos = 0\n", encoding="utf-8")
    (game / "screens" / "say.rpy").write_text("screen say:\n", encoding="utf-8")
    (game / "screens" / "tl" / "say.rpy").write_text("translate tl say:\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("raw", [None, "", "   ", ", ,"])
def test_load_path_scope_blank_or_unset_is_none(raw: str | None) -> None:
    env = {} if raw is None else {ENV_ALLOW_PATHS: raw}
    assert load_path_scope(env) is None


def test_load_path_scope_parses_comma_separated_patterns() -> None:
    scope = load_path_scope({ENV_ALLOW_PATHS: "screens/*.rpy, gui.rpy"})
    assert scope == ("screens/*.rpy", "gui.rpy")


@pytest.mark.parametrize(
    "pattern",
    ["/etc/passwd", "../script.rpy", "screens\\say.rpy", "."],
)
def test_load_path_scope_rejects_invalid_patterns(pattern: str) -> None:
    with pytest.raises(EditorError) as excinfo:
        load_path_scope({ENV_ALLOW_PATHS: pattern})
    assert excinfo.value.code == "PATH_SCOPE_INVALID"
    assert "RENFORGE_ALLOW_PATHS" in excinfo.value.message


@pytest.mark.parametrize(
    ("pattern", "reason"),
    [
        ("C:/game/screens.rpy", "PATH_DRIVE_LETTER_REJECTED"),
        ("c:gui.rpy", "PATH_DRIVE_LETTER_REJECTED"),
        ("C:", "PATH_DRIVE_LETTER_REJECTED"),
        ("screens/", "PATH_TRAILING_SLASH_REJECTED"),
        ("gui.rpy/", "PATH_TRAILING_SLASH_REJECTED"),
        ("screens/**/", "PATH_TRAILING_SLASH_REJECTED"),
    ],
)
def test_load_path_scope_rejects_windows_and_trailing_slash_patterns(
    pattern: str, reason: str
) -> None:
    with pytest.raises(EditorError) as excinfo:
        load_path_scope({ENV_ALLOW_PATHS: pattern})
    error = excinfo.value
    assert error.code == "PATH_SCOPE_INVALID"
    assert error.details == {"pattern": pattern, "reason": reason}
    assert pattern in error.message
    assert "RENFORGE_ALLOW_PATHS" in error.message


def test_scope_patterns_are_matched_case_sensitively() -> None:
    assert is_path_in_scope("screens/say.rpy", ("screens/*.rpy",)) is True
    assert is_path_in_scope("Screens/say.rpy", ("screens/*.rpy",)) is False
    assert is_path_in_scope("screens/SAY.RPY", ("screens/*.rpy",)) is False


def test_exact_pattern_matches_only_that_path() -> None:
    assert is_path_in_scope("gui.rpy", ("gui.rpy",)) is True
    assert is_path_in_scope("screens/gui.rpy", ("gui.rpy",)) is False


def test_star_does_not_cross_directory_separators() -> None:
    assert is_path_in_scope("screens/say.rpy", ("screens/*.rpy",)) is True
    assert is_path_in_scope("screens/tl/say.rpy", ("screens/*.rpy",)) is False
    assert is_path_in_scope("script.rpy", ("*.rpy",)) is True
    assert is_path_in_scope("screens/say.rpy", ("*.rpy",)) is False


def test_many_double_star_segments_do_not_recurse() -> None:
    pattern = "/".join(["**"] * 60 + ["target.rpy"])
    path = "/".join([f"d{index}" for index in range(200)] + ["target.rpy"])

    assert is_path_in_scope(path, (pattern,)) is True


def test_double_star_crosses_directories_including_zero() -> None:
    scope = ("screens/**/*.rpy",)
    assert is_path_in_scope("screens/say.rpy", scope) is True
    assert is_path_in_scope("screens/tl/say.rpy", scope) is True
    assert is_path_in_scope("screens/a/b/say.rpy", scope) is True
    assert is_path_in_scope("tl/say.rpy", scope) is False
    assert is_path_in_scope("script.rpy", ("**/*.rpy",)) is True
    assert is_path_in_scope("screens/tl/say.rpy", ("**/*.rpy",)) is True


def test_many_double_star_segments_still_match_correctly() -> None:
    scope = ("**/**/**/**/**/screens/**/**/**/**/*.rpy",)
    assert is_path_in_scope("screens/say.rpy", scope) is True
    assert is_path_in_scope("a/b/screens/c/d/say.rpy", scope) is True
    assert is_path_in_scope("a/b/c/d/e/f/screens/g/h/i/say.rpy", scope) is True
    assert is_path_in_scope("a/b/c/d/e/f/tl/say.rpy", scope) is False
    assert is_path_in_scope("screens/tl/say/deep.txt", scope) is False


def test_require_in_scope_unset_accepts_any_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_ALLOW_PATHS, raising=False)
    # No filesystem lookup happens when no scope is active.
    require_in_scope(_make_project(tmp_path), "missing.rpy")


def test_require_in_scope_accepts_a_matching_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_project(tmp_path)
    monkeypatch.setenv(ENV_ALLOW_PATHS, "screens/**/*.rpy")
    require_in_scope(root, "screens/tl/say.rpy")


def test_require_in_scope_refusal_names_path_and_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_project(tmp_path)
    monkeypatch.setenv(ENV_ALLOW_PATHS, "screens/*.rpy")
    with pytest.raises(EditorError) as excinfo:
        require_in_scope(root, "script.rpy")
    error = excinfo.value
    assert error.code == "PATH_OUT_OF_SCOPE"
    assert error.details == {"path": "script.rpy", "scope": ["screens/*.rpy"]}
    assert "script.rpy" in error.message
    assert "screens/*.rpy" in error.message


def test_require_in_scope_invalid_pattern_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_project(tmp_path)
    monkeypatch.setenv(ENV_ALLOW_PATHS, "../outside.rpy")
    with pytest.raises(EditorError) as excinfo:
        require_in_scope(root, "script.rpy")
    assert excinfo.value.code == "PATH_SCOPE_INVALID"
