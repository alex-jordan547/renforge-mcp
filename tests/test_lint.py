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
