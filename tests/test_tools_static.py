from pathlib import Path

from renforge.tools.static import inspect_project, parse_lint_text, scan_project_index


def _demo_project_root() -> Path:
    return Path(__file__).resolve().parents[1] / "examples" / "demo_game"


def test_inspect_project_detects_expected_demo_markers() -> None:
    result = inspect_project(str(_demo_project_root()))

    assert result["exists"] is True
    assert result["is_directory"] is True
    assert set(result["detected_markers"]) == {
        "game",
        "game/options.rpy",
        "game/script.rpy",
    }


def test_scan_project_index_returns_coherent_summary() -> None:
    result = scan_project_index(str(_demo_project_root()))
    summary = result["summary"]

    assert summary["label_count"] == len(result["labels"])
    assert summary["menu_count"] == len(result["menus"])
    assert summary["jump_count"] == len(result["jumps"])
    assert summary["call_count"] == len(result["calls"])
    assert summary["character_count"] == len(result["characters"])
    assert summary["image_count"] == len(result["images"])


def test_scan_project_can_select_filter_and_page_sections() -> None:
    result = scan_project_index(
        str(_demo_project_root()),
        sections=["labels"],
        file_glob="game/script.rpy",
        symbol="start",
        offset=0,
        limit=1,
    )

    assert result["labels"] == [{"file": "game/script.rpy", "line": 16, "name": "start"}]
    assert "variables" not in result
    assert result["pagination"]["labels"] == {
        "total": 1,
        "offset": 0,
        "limit": 1,
        "returned": 1,
    }


def test_scan_project_can_return_summary_only() -> None:
    result = scan_project_index(str(_demo_project_root()), sections=[])

    assert set(result) == {"summary", "pagination"}


def test_parse_lint_text_returns_count_and_diagnostics() -> None:
    result = parse_lint_text(
        "\n".join(
            [
                "game/script.rpy:12: warning: label is unreachable",
                "game/script.rpy:13: [error] missing return",
                "some noise line",
            ]
        )
    )

    assert result["count"] == 2
    assert len(result["diagnostics"]) == 2
    assert result["diagnostics"][0]["file"] == "game/script.rpy"
    assert result["diagnostics"][0]["line"] == 12
    assert result["diagnostics"][0]["severity"] == "warning"
    assert result["diagnostics"][0]["message"] == "label is unreachable"
    assert result["diagnostics"][1]["severity"] == "error"
    assert result["diagnostics"][1]["message"] == "missing return"
