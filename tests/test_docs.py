from pathlib import Path

from renforge.docs import _TextExtractor


def test_text_extractor_strips_tags_and_scripts() -> None:
    html = (
        "<html><head><title>Menus</title>"
        "<style>.x{color:red}</style><script>var a=1;</script></head>"
        "<body><h1>The menu statement</h1><p>Displays a menu of choices.</p></body></html>"
    )
    parser = _TextExtractor()
    parser.feed(html)
    text = parser.text()

    assert parser.title == "Menus"
    assert "The menu statement" in text
    assert "Displays a menu of choices." in text
    assert "var a=1" not in text  # script content dropped
    assert "color:red" not in text  # style content dropped


def test_mcp_safety_docs_include_runtime_mutations() -> None:
    text = (Path(__file__).parents[1] / "docs" / "MCP.md").read_text(encoding="utf-8")
    safety = text.split("## Writes and safety", 1)[1].split("\n## ", 1)[0]

    assert "`renforge_control`" in safety
    assert "`renforge_saves`" in safety


def test_public_god_mode_tools_and_workflow_are_documented() -> None:
    root = Path(__file__).parents[1]
    docs = (root / "docs" / "MCP.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    public_tools = (
        "renforge_inspect_screen",
        "renforge_control",
        "renforge_send_input",
        "renforge_saves",
        "renforge_get_errors",
        "renforge_wait_until",
    )

    # Full tool catalogue + workflow live in the MCP guide; the README stays
    # install-first and only needs a clear pointer to that guide.
    for tool in public_tools:
        assert f"`{tool}`" in docs

    for marker in (
        "renforge_launch(project_path=project)",
        "edit game/script.rpy",
        'action="reload_script"',
        "renforge_wait_until",
        "renforge_screenshot(project_path=project)",
        'slot="branch-a"',
        'text="Branch B"',
        "renforge_get_errors(project_path=project)",
    ):
        assert marker in docs

    assert 'include=["metrics", "audio"]' in docs
    assert "docs/MCP.md" in readme
    assert "full tool catalogue" in readme.lower()
