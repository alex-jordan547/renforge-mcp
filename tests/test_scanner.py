from pathlib import Path

from renforge.scanner import scan_project


def _ensure_demo_project(tmp_path: Path) -> Path:
    demo_project = Path(__file__).resolve().parents[1] / "examples" / "demo_game"
    if demo_project.is_dir():
        return demo_project

    project = tmp_path / "demo_project"
    game_dir = project / "game"
    game_dir.mkdir(parents=True)
    (game_dir / "script.rpy").write_text(
        "\n".join(
            [
                "label start:",
                "default score = 0",
                "$ player_name = \"Rin\"",
                "    jump choice",
                "",
                "label choice:",
                "    menu:",
                "        \"Forward\":",
                "            jump good",
                "        \"Back\":",
                "            jump bad",
                "",
                "label good:",
                "    jump end",
                "",
                "label bad:",
                "    jump end",
                "",
                "label end:",
            ]
        )
    )
    return project


def test_scan_project_detects_demo_labels_jumps_and_menu(tmp_path: Path) -> None:
    project_root = _ensure_demo_project(tmp_path)
    result = scan_project(str(project_root))

    labels = {item["name"] for item in result["labels"]}
    jumps = [item["target"] for item in result["jumps"]]
    menu_labels = [item["label"] for item in result["menus"]]
    variable_names = {item["name"] for item in result["variables"]}
    var_kinds = {item["kind"] for item in result["variables"]}
    files = {item["file"] for item in result["files"]}
    graph_edges = result["graph"]["edges"]

    assert {"start", "village_gate", "crossroads", "summit"}.issubset(labels)
    assert {"default", "assignment"} <= var_kinds
    assert "renforge_choice" in variable_names
    assert "crossroads" in jumps
    assert "summit" in jumps
    assert len(jumps) >= 3
    assert any(item["target"] == "crossroads" for item in graph_edges)
    assert result["files"], "expected scanner to report parsed files"
    assert {"game/script.rpy"} <= files
    assert any("menu" in lbl or lbl == "menu" for lbl in menu_labels)
    assert any(
        item["name"] == "renforge_choice" and item["kind"] == "default"
        for item in result["variables"]
    )
    assert any(
        item["name"] == "renforge_choice" and item["kind"] == "assignment"
        for item in result["variables"]
    )
    assert all(
        {"file", "line", "kind", "target"} <= set(edge.keys())
        for edge in graph_edges
    )
    assert any("target" in edge for edge in graph_edges)
    assert result["unresolved_targets"] == []


def test_scan_project_resolves_local_and_dotted_labels(tmp_path: Path) -> None:
    project = tmp_path / "project"
    game = project / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text(
        "\n".join(
            [
                "label route.chapter:",
                "    jump .detail",
                "label .detail:",
                "    return",
                "label other:",
                "    return",
                "label .detail:",
                "    return",
            ]
        ),
        encoding="utf-8",
    )

    result = scan_project(str(project))

    assert [item["name"] for item in result["labels"]] == [
        "route.chapter",
        "route.chapter.detail",
        "other",
        "other.detail",
    ]
    assert result["jumps"][0]["target"] == "route.chapter.detail"


def test_scan_project_excludes_screen_language_labels(tmp_path: Path) -> None:
    game = tmp_path / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text(
        "label start:\n    return\n\nscreen history():\n    label h.who:\n        text h.who\n",
        encoding="utf-8",
    )

    result = scan_project(str(tmp_path))

    assert [item["name"] for item in result["labels"]] == ["start"]


def test_scan_project_excludes_embedded_sdks_and_tools(tmp_path: Path) -> None:
    game = tmp_path / "game"
    embedded = tmp_path / "tools" / "renpy-sdk" / "launcher" / "game"
    game.mkdir()
    embedded.mkdir(parents=True)
    (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    (embedded / "script.rpy").write_text(
        "label foreign_launcher_start:\n    return\n",
        encoding="utf-8",
    )

    result = scan_project(str(tmp_path))

    assert [item["name"] for item in result["labels"]] == ["start"]
    assert [item["file"] for item in result["files"]] == ["game/script.rpy"]
