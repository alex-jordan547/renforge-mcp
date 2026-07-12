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
