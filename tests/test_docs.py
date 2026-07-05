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
