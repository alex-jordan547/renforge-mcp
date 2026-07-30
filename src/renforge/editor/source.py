from __future__ import annotations

import ast
from dataclasses import dataclass


class EditorSourceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TextbuttonStatement:
    widget_id: str
    xpos: int
    ypos: int
    xpos_span: tuple[int, int]
    ypos_span: tuple[int, int]


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    start: int
    end: int
    depth: int


def _is_identifier_start(ch: str) -> bool:
    return ch == "_" or ch.isalpha()


def _is_identifier_part(ch: str) -> bool:
    return ch == "_" or ch.isalpha() or ch.isdigit()


def _lex_single_line(statement: str) -> list[_Token]:
    tokens: list[_Token] = []
    depth = 0
    index = 0
    length = len(statement)
    while index < length:
        ch = statement[index]
        if ch in " \t\r":
            index += 1
            continue
        if ch == "#":
            break
        if ch in "'\"":
            quote = ch
            start = index
            index += 1
            escaped = False
            while index < length:
                current = statement[index]
                if escaped:
                    escaped = False
                    index += 1
                    continue
                if current == "\\":
                    escaped = True
                    index += 1
                    continue
                if current == quote:
                    index += 1
                    break
                index += 1
            else:
                raise EditorSourceError("UNTERMINATED_STRING", "unterminated string literal")
            tokens.append(_Token("STRING", statement[start:index], start, index, depth))
            continue
        if ch in "([{":
            tokens.append(_Token("SYMBOL", ch, index, index + 1, depth))
            depth += 1
            index += 1
            continue
        if ch in ")]}":
            tokens.append(_Token("SYMBOL", ch, index, index + 1, depth))
            depth = max(0, depth - 1)
            index += 1
            continue
        if ch in "+-" and index + 1 < length and statement[index + 1].isdigit():
            start = index
            index += 1
            while index < length and statement[index].isdigit():
                index += 1
            tokens.append(_Token("NUMBER", statement[start:index], start, index, depth))
            continue
        if ch.isdigit():
            start = index
            index += 1
            while index < length and statement[index].isdigit():
                index += 1
            tokens.append(_Token("NUMBER", statement[start:index], start, index, depth))
            continue
        if _is_identifier_start(ch):
            start = index
            index += 1
            while index < length and _is_identifier_part(statement[index]):
                index += 1
            tokens.append(_Token("WORD", statement[start:index], start, index, depth))
            continue
        tokens.append(_Token("SYMBOL", ch, index, index + 1, depth))
        index += 1
    return tokens


def _parse_string_token(token: _Token) -> str:
    try:
        parsed = ast.literal_eval(token.text)
    except (ValueError, SyntaxError) as exc:
        raise EditorSourceError("ID_LITERAL_INVALID", f"invalid string literal: {exc}") from exc
    if not isinstance(parsed, str):
        raise EditorSourceError("ID_LITERAL_INVALID", "id must be a string literal")
    return parsed


def _next_top_level_token(tokens: list[_Token], index: int) -> _Token | None:
    cursor = index + 1
    while cursor < len(tokens):
        candidate = tokens[cursor]
        if candidate.depth == 0:
            return candidate
        cursor += 1
    return None


def analyze_textbutton_statement(line: str, *, expected_widget_id: str) -> TextbuttonStatement:
    if "\n" in line[:-1]:
        raise EditorSourceError("MULTILINE_STATEMENT_REJECTED", "statement must be single-line")
    statement_text = line[:-1] if line.endswith("\n") else line
    tokens = _lex_single_line(statement_text)
    top_level = [token for token in tokens if token.depth == 0]
    if not top_level or top_level[0].kind != "WORD" or top_level[0].text != "textbutton":
        raise EditorSourceError("STATEMENT_KIND_MISMATCH", "source statement is not a textbutton")

    keyword_counts = {"id": 0, "xpos": 0, "ypos": 0}
    widget_id: str | None = None
    xpos_value: int | None = None
    ypos_value: int | None = None
    xpos_span: tuple[int, int] | None = None
    ypos_span: tuple[int, int] | None = None
    invalid_literals: set[str] = set()

    for index, token in enumerate(tokens):
        if token.depth != 0 or token.kind != "WORD" or token.text not in keyword_counts:
            continue
        keyword = token.text
        keyword_counts[keyword] += 1
        value_token = _next_top_level_token(tokens, index)
        if value_token is None:
            invalid_literals.add(keyword)
            continue
        if keyword == "id":
            if value_token.kind != "STRING":
                invalid_literals.add(keyword)
                continue
            widget_id = _parse_string_token(value_token)
            continue
        if value_token.kind != "NUMBER":
            invalid_literals.add(keyword)
            continue
        value = int(value_token.text)
        if keyword == "xpos":
            xpos_value = value
            xpos_span = (value_token.start, value_token.end)
        else:
            ypos_value = value
            ypos_span = (value_token.start, value_token.end)

    if keyword_counts["id"] != 1:
        raise EditorSourceError("ID_LITERAL_REQUIRED", "textbutton statement must contain exactly one literal id")
    if "id" in invalid_literals or widget_id is None:
        raise EditorSourceError("ID_LITERAL_REQUIRED", "id must be a literal string")
    if widget_id != expected_widget_id:
        raise EditorSourceError("ID_MISMATCH", "literal id does not match runtime widget id")
    if keyword_counts["xpos"] != 1:
        raise EditorSourceError("XPOS_DUPLICATE", "textbutton statement must contain exactly one xpos")
    if keyword_counts["ypos"] != 1:
        raise EditorSourceError("YPOS_DUPLICATE", "textbutton statement must contain exactly one ypos")
    if "xpos" in invalid_literals or xpos_value is None or xpos_span is None:
        raise EditorSourceError("XPOS_LITERAL_REQUIRED", "xpos must be a literal integer")
    if "ypos" in invalid_literals or ypos_value is None or ypos_span is None:
        raise EditorSourceError("YPOS_LITERAL_REQUIRED", "ypos must be a literal integer")

    return TextbuttonStatement(
        widget_id=widget_id,
        xpos=xpos_value,
        ypos=ypos_value,
        xpos_span=xpos_span,
        ypos_span=ypos_span,
    )


def apply_textbutton_patch(source_bytes: bytes, statement: TextbuttonStatement, *, x: int, y: int) -> bytes:
    source_text = source_bytes.decode("utf-8")
    replacements = [
        (statement.xpos_span[0], statement.xpos_span[1], str(int(x))),
        (statement.ypos_span[0], statement.ypos_span[1], str(int(y))),
    ]
    replacements.sort(key=lambda item: item[0], reverse=True)
    patched = source_text
    for start, end, replacement in replacements:
        patched = f"{patched[:start]}{replacement}{patched[end:]}"
    return patched.encode("utf-8")
