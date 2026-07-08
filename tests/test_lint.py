from renforge.lint import parse_lint_output


def test_parse_lint_output_accepts_multiple_known_formats() -> None:
    output = "\n".join(
        [
            "game/script.rpy:12: warning: label is unreachable",
            "game/script.rpy:42: [error] menu choice has no default",
            "game/script.rpy:55: [INFO] Informational message",
        ]
    )
    diagnostics = parse_lint_output(output)

    assert diagnostics[0]["file"] == "game/script.rpy"
    assert diagnostics[0]["line"] == 12
    assert diagnostics[0]["severity"] == "warning"
    assert diagnostics[0]["message"] == "label is unreachable"

    assert diagnostics[1]["line"] == 42
    assert diagnostics[1]["severity"] == "error"
    assert diagnostics[1]["message"] == "menu choice has no default"

    assert diagnostics[2]["severity"] == "info"
    assert diagnostics[2]["message"] == "Informational message"


def test_parse_lint_output_unknown_lines_are_ignored() -> None:
    output = "\n".join(
        [
            "unstructured diagnostic line without a recognized format",
            "this line is not parseable",
        ]
    )
    diagnostics = parse_lint_output(output)
    assert diagnostics == []


def test_parse_lint_output_parses_sections_with_file_line_details() -> None:
    output = "\n".join(
        [
            "Orphan Translations",
            "game/script.rpy:33:",
            "    The default translation entry is orphaned.",
            "  game/other.rpy:77: [warning] old translation key",
            "This note should be ignored",
        ]
    )
    diagnostics = parse_lint_output(output)

    assert diagnostics[0]["file"] == "game/script.rpy"
    assert diagnostics[0]["line"] == 33
    assert diagnostics[0]["severity"] == "warning"
    assert diagnostics[0]["message"] == "The default translation entry is orphaned."

    assert diagnostics[1]["file"] == "game/other.rpy"
    assert diagnostics[1]["line"] == 77
    assert diagnostics[1]["severity"] == "warning"
    assert diagnostics[1]["message"] == "old translation key"


def test_parse_lint_output_parses_orphan_translation_file_blocks() -> None:
    output = "\n".join(
        [
            "Orphan Translations:",
            "",
            "game/tl/french/script.rpy:",
            "    * line     9 (id start)",
        ]
    )
    diagnostics = parse_lint_output(output)

    assert diagnostics == [
        {
            "file": "game/tl/french/script.rpy",
            "line": 9,
            "severity": "warning",
            "message": "Orphan Translations (id start)",
        }
    ]
