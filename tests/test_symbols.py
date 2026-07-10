from pathlib import Path


def test_find_references_separates_live_and_dead_constants(tmp_path: Path) -> None:
    from renforge.symbols import find_references

    game = tmp_path / "game"
    game.mkdir()
    (game / "ui.rpy").write_text(
        "\n".join(
            [
                'define LIVE_ICON = "images/live.png"',
                'define DEAD_ICON = "images/dead.png"',
                "screen toolbar():",
                "    add LIVE_ICON",
                '    text "DEAD_ICON is only text"',
                "    # DEAD_ICON is only a comment",
            ]
        ),
        encoding="utf-8",
    )

    live = find_references(tmp_path, "LIVE_ICON")
    dead = find_references(tmp_path, "DEAD_ICON")

    assert [(item["line"], item["kind"]) for item in live["occurrences"]] == [
        (1, "definition"),
        (4, "reference"),
    ]
    assert dead["occurrences"] == [
        {
            "file": "game/ui.rpy",
            "line": 2,
            "column": 8,
            "kind": "definition",
            "context": 'define DEAD_ICON = "images/dead.png"',
        }
    ]
    assert dead["unused"] is True


def test_find_references_counts_renpy_text_interpolation(tmp_path: Path) -> None:
    from renforge.symbols import find_references

    game = tmp_path / "game"
    game.mkdir()
    (game / "ui.rpy").write_text(
        "\n".join(
            [
                'define LIVE_ICON = "images/live.png"',
                'text "Selected: [LIVE_ICON]"',
                'text "Escaped bracket: [[LIVE_ICON]"',
            ]
        ),
        encoding="utf-8",
    )

    result = find_references(tmp_path, "LIVE_ICON")

    assert result["definition_count"] == 1
    assert result["reference_count"] == 1
    assert result["unused"] is False
    assert result["analysis"] == "renpy-aware-token-index"


def test_find_references_counts_named_action_targets(tmp_path: Path) -> None:
    from renforge.symbols import find_references

    game = tmp_path / "game"
    game.mkdir()
    (game / "ui.rpy").write_text(
        "\n".join(
            [
                "default score = 0",
                'textbutton "Add" action IncrementVariable("score", 1)',
                'text "score is plain prose"',
                '# SetVariable("score", 99)',
            ]
        ),
        encoding="utf-8",
    )

    result = find_references(tmp_path, "score")

    assert [(item["line"], item["kind"]) for item in result["occurrences"]] == [
        (1, "definition"),
        (2, "reference"),
    ]


def test_find_references_counts_named_screen_action(tmp_path: Path) -> None:
    from renforge.symbols import find_references

    game = tmp_path / "game"
    game.mkdir()
    (game / "ui.rpy").write_text(
        "screen panel():\n    text \"Panel\"\nlabel start:\n    $ Show(\"panel\")()\n",
        encoding="utf-8",
    )

    result = find_references(tmp_path, "panel")

    assert result["definition_count"] == 1
    assert result["reference_count"] == 1
    assert result["unused"] is False
