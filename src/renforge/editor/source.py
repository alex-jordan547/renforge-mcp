from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TypeVar


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
class ButtonStatement:
    widget_id: str
    xpos: int
    ypos: int
    xpos_span: tuple[int, int]
    ypos_span: tuple[int, int]
    source_line: int


@dataclass(frozen=True)
class ImagebuttonStatement:
    widget_id: str
    xpos: int
    ypos: int
    xpos_span: tuple[int, int]
    ypos_span: tuple[int, int]


@dataclass(frozen=True)
class BarStatement:
    widget_id: str
    xpos: int
    ypos: int
    xpos_span: tuple[int, int]
    ypos_span: tuple[int, int]


@dataclass(frozen=True)
class VbarStatement:
    widget_id: str
    xpos: int
    ypos: int
    xpos_span: tuple[int, int]
    ypos_span: tuple[int, int]


@dataclass(frozen=True)
class SliderStatement:
    widget_id: str
    xpos: int
    ypos: int
    xpos_span: tuple[int, int]
    ypos_span: tuple[int, int]


_StatementT = TypeVar(
    "_StatementT",
    bound=TextbuttonStatement
    | ImagebuttonStatement
    | BarStatement
    | VbarStatement
    | SliderStatement,
)


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


def _next_top_level_index(tokens: list[_Token], index: int) -> int | None:
    cursor = index + 1
    while cursor < len(tokens):
        if tokens[cursor].depth == 0:
            return cursor
        cursor += 1
    return None




def _statement_text(line: str) -> str:
    if "\n" in line[:-1]:
        raise EditorSourceError("MULTILINE_STATEMENT_REJECTED", "statement must be single-line")
    return line[:-1] if line.endswith("\n") else line


def peek_statement_kind(line: str) -> str | None:
    """Return the first top-level WORD on a single source line, or None."""
    try:
        statement_text = _statement_text(line)
    except EditorSourceError:
        return None
    tokens = _lex_single_line(statement_text)
    for token in tokens:
        if token.depth == 0 and token.kind == "WORD":
            return token.text
    return None


def _analyze_positioned_kind_statement(
    line: str,
    *,
    expected_widget_id: str,
    expected_kind: str,
    statement_cls: type[_StatementT],
) -> _StatementT:
    statement_text = _statement_text(line)
    tokens = _lex_single_line(statement_text)
    top_level = [token for token in tokens if token.depth == 0]
    if not top_level or top_level[0].kind != "WORD" or top_level[0].text != expected_kind:
        raise EditorSourceError(
            "STATEMENT_KIND_MISMATCH",
            f"source statement is not a {expected_kind}",
        )

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
        value_index = _next_top_level_index(tokens, index)
        if value_index is None:
            invalid_literals.add(keyword)
            continue
        value_token = tokens[value_index]
        if keyword == "id":
            if value_token.kind != "STRING":
                invalid_literals.add(keyword)
                continue
            widget_id = _parse_string_token(value_token)
            continue
        if value_token.kind != "NUMBER":
            invalid_literals.add(keyword)
            continue
        # Reject compound expressions like `xpos 100-20` (NUMBER followed by
        # non-WORD). A pure literal is followed by a keyword/action WORD or EOS.
        following_index = _next_top_level_index(tokens, value_index)
        if following_index is not None and tokens[following_index].kind != "WORD":
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
        raise EditorSourceError(
            "ID_LITERAL_REQUIRED",
            f"{expected_kind} statement must contain exactly one literal id",
        )
    if "id" in invalid_literals or widget_id is None:
        raise EditorSourceError("ID_LITERAL_REQUIRED", "id must be a literal string")
    if widget_id != expected_widget_id:
        raise EditorSourceError("ID_MISMATCH", "literal id does not match runtime widget id")
    if keyword_counts["xpos"] != 1:
        raise EditorSourceError(
            "XPOS_DUPLICATE",
            f"{expected_kind} statement must contain exactly one xpos",
        )
    if keyword_counts["ypos"] != 1:
        raise EditorSourceError(
            "YPOS_DUPLICATE",
            f"{expected_kind} statement must contain exactly one ypos",
        )
    if "xpos" in invalid_literals or xpos_value is None or xpos_span is None:
        raise EditorSourceError("XPOS_LITERAL_REQUIRED", "xpos must be a literal integer")
    if "ypos" in invalid_literals or ypos_value is None or ypos_span is None:
        raise EditorSourceError("YPOS_LITERAL_REQUIRED", "ypos must be a literal integer")

    return statement_cls(
        widget_id=widget_id,
        xpos=xpos_value,
        ypos=ypos_value,
        xpos_span=xpos_span,
        ypos_span=ypos_span,
    )


def analyze_textbutton_statement(line: str, *, expected_widget_id: str) -> TextbuttonStatement:
    return _analyze_positioned_kind_statement(
        line,
        expected_widget_id=expected_widget_id,
        expected_kind="textbutton",
        statement_cls=TextbuttonStatement,
    )


def analyze_imagebutton_statement(line: str, *, expected_widget_id: str) -> ImagebuttonStatement:
    return _analyze_positioned_kind_statement(
        line,
        expected_widget_id=expected_widget_id,
        expected_kind="imagebutton",
        statement_cls=ImagebuttonStatement,
    )


# Words that may legally follow a literal xpos/ypos on a bar line (property
# keywords). Expression operators such as `if` / `or` are intentionally absent
# so `xpos 100 if flag else 20` is rejected rather than half-patched.
_BAR_POSITION_FOLLOWER_WORDS = frozenset(
    {
        "id",
        "xpos",
        "ypos",
        "value",
        "range",
        "style",
        "xsize",
        "ysize",
        "xmaximum",
        "ymaximum",
        "xminimum",
        "yminimum",
        "xfill",
        "yfill",
        "xalign",
        "yalign",
        "xanchor",
        "yanchor",
        "xoffset",
        "yoffset",
        "xcenter",
        "ycenter",
        "tooltip",
        "sensitive",
        "focus",
        "keyboard_focus",
        "hovered",
        "unhovered",
        "released",
        "changed",
        "thumb",
        "thumb_offset",
        "thumb_shadow",
        "left_bar",
        "right_bar",
        "top_bar",
        "bottom_bar",
        "bar_invert",
        "bar_resizing",
        "bar_vertical",
    }
)


def _bar_like_error(human_kind: str, code: str) -> EditorSourceError:
    messages = {
        "STATEMENT_KIND_MISMATCH": f"source statement is not a {human_kind}",
        "MULTILINE_STATEMENT_REJECTED": f"{human_kind} block statements are not writable",
        "ID_LITERAL_REQUIRED_COUNT": f"{human_kind} statement must contain exactly one literal id",
        "ID_LITERAL_REQUIRED": "id must be a literal string",
        "ID_MISMATCH": "literal id does not match runtime widget id",
        "BAR_STYLE_POSITION_UNSUPPORTED": (
            f"{human_kind} position is not directly authored as literal xpos/ypos"
        ),
        "BAR_POSITION_NOT_DIRECTLY_AUTHORED": (
            f"{human_kind} position is not directly authored as literal xpos/ypos"
        ),
        "XPOS_DUPLICATE": f"{human_kind} statement must contain exactly one xpos",
        "YPOS_DUPLICATE": f"{human_kind} statement must contain exactly one ypos",
        "XPOS_LITERAL_REQUIRED": "xpos must be a literal integer",
        "YPOS_LITERAL_REQUIRED": "ypos must be a literal integer",
    }
    message_code = "ID_LITERAL_REQUIRED" if code == "ID_LITERAL_REQUIRED_COUNT" else code
    return EditorSourceError(message_code, messages[code])


def _analyze_bar_like_statement(
    line: str,
    *,
    expected_widget_id: str,
    expected_source_kind: str,
    statement_cls: type[_StatementT],
    human_kind: str,
) -> _StatementT:
    statement_text = _statement_text(line)
    tokens = _lex_single_line(statement_text)
    top_level = [token for token in tokens if token.depth == 0]
    if (
        not top_level
        or top_level[0].kind != "WORD"
        or top_level[0].text != expected_source_kind
    ):
        raise _bar_like_error(human_kind, "STATEMENT_KIND_MISMATCH")

    if any(token.kind == "SYMBOL" and token.text == ":" for token in top_level):
        raise _bar_like_error(human_kind, "MULTILINE_STATEMENT_REJECTED")

    has_style_keyword = any(
        token.depth == 0 and token.kind == "WORD" and token.text == "style" for token in tokens
    )
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
        value_index = _next_top_level_index(tokens, index)
        if value_index is None:
            invalid_literals.add(keyword)
            continue
        value_token = tokens[value_index]
        if keyword == "id":
            if value_token.kind != "STRING":
                invalid_literals.add(keyword)
                continue
            widget_id = _parse_string_token(value_token)
            continue
        if value_token.kind != "NUMBER":
            invalid_literals.add(keyword)
            continue
        following_index = _next_top_level_index(tokens, value_index)
        if following_index is not None:
            following = tokens[following_index]
            # Reject symbols (`100-20`) and expression words (`100 if flag`).
            # Only another bar property keyword or EOS proves a pure integer.
            if following.kind != "WORD" or following.text not in _BAR_POSITION_FOLLOWER_WORDS:
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
        raise _bar_like_error(human_kind, "ID_LITERAL_REQUIRED_COUNT")
    if "id" in invalid_literals or widget_id is None:
        raise _bar_like_error(human_kind, "ID_LITERAL_REQUIRED")
    if widget_id != expected_widget_id:
        raise _bar_like_error(human_kind, "ID_MISMATCH")

    def _missing_position_code() -> str:
        if has_style_keyword:
            return "BAR_STYLE_POSITION_UNSUPPORTED"
        return "BAR_POSITION_NOT_DIRECTLY_AUTHORED"

    if keyword_counts["xpos"] == 0:
        raise _bar_like_error(human_kind, _missing_position_code())
    if keyword_counts["xpos"] != 1:
        raise _bar_like_error(human_kind, "XPOS_DUPLICATE")
    if keyword_counts["ypos"] == 0:
        raise _bar_like_error(human_kind, _missing_position_code())
    if keyword_counts["ypos"] != 1:
        raise _bar_like_error(human_kind, "YPOS_DUPLICATE")
    if "xpos" in invalid_literals or xpos_value is None or xpos_span is None:
        raise _bar_like_error(human_kind, "XPOS_LITERAL_REQUIRED")
    if "ypos" in invalid_literals or ypos_value is None or ypos_span is None:
        raise _bar_like_error(human_kind, "YPOS_LITERAL_REQUIRED")

    return statement_cls(
        widget_id=widget_id,
        xpos=xpos_value,
        ypos=ypos_value,
        xpos_span=xpos_span,
        ypos_span=ypos_span,
    )


def analyze_bar_statement(line: str, *, expected_widget_id: str) -> BarStatement:
    return _analyze_bar_like_statement(
        line,
        expected_widget_id=expected_widget_id,
        expected_source_kind="bar",
        statement_cls=BarStatement,
        human_kind="bar",
    )


def analyze_vbar_statement(line: str, *, expected_widget_id: str) -> VbarStatement:
    return _analyze_bar_like_statement(
        line,
        expected_widget_id=expected_widget_id,
        expected_source_kind="vbar",
        statement_cls=VbarStatement,
        human_kind="vbar",
    )


def is_slider_style_bar_line(line: str) -> bool:
    """True when the line is a single-line ``bar`` with pure literal ``style "slider"``.

    Ren'Py 8.5.3 has no screen-language ``slider`` keyword (measured). Games author
    sliders as ``bar`` statements that select the ``slider`` style. Adapter identity
    is therefore ``bar`` + literal style ``"slider"``, not a first-word keyword.

    Computed style expressions such as ``style "slider" if flag else "bar"`` are
    rejected (same purity rule as literal xpos/ypos followers).
    """
    try:
        statement_text = _statement_text(line)
    except EditorSourceError:
        return False
    tokens = _lex_single_line(statement_text)
    top_level = [token for token in tokens if token.depth == 0]
    if not top_level or top_level[0].kind != "WORD" or top_level[0].text != "bar":
        return False
    if any(token.kind == "SYMBOL" and token.text == ":" for token in top_level):
        return False
    pure_style_names: list[str] = []
    for index, token in enumerate(tokens):
        if token.depth != 0 or token.kind != "WORD" or token.text != "style":
            continue
        value_index = _next_top_level_index(tokens, index)
        if value_index is None:
            return False
        value_token = tokens[value_index]
        if value_token.kind != "STRING":
            return False
        following_index = _next_top_level_index(tokens, value_index)
        if following_index is not None:
            following = tokens[following_index]
            # Only another bar property keyword or EOS proves a pure style literal.
            if following.kind != "WORD" or following.text not in _BAR_POSITION_FOLLOWER_WORDS:
                return False
        try:
            pure_style_names.append(_parse_string_token(value_token))
        except EditorSourceError:
            return False
    return len(pure_style_names) == 1 and pure_style_names[0] == "slider"


def analyze_slider_statement(line: str, *, expected_widget_id: str) -> SliderStatement:
    """Analyze a single-line slider-styled bar (source keyword ``bar``, style ``"slider"``)."""
    if not is_slider_style_bar_line(line):
        raise _bar_like_error("slider", "STATEMENT_KIND_MISMATCH")
    return _analyze_bar_like_statement(
        line,
        expected_widget_id=expected_widget_id,
        expected_source_kind="bar",
        statement_cls=SliderStatement,
        human_kind="slider",
    )


def _apply_integer_span_patch(
    source_bytes: bytes,
    *,
    xpos_span: tuple[int, int],
    ypos_span: tuple[int, int],
    x: int,
    y: int,
) -> bytes:
    source_text = source_bytes.decode("utf-8")
    replacements = [
        (xpos_span[0], xpos_span[1], str(int(x))),
        (ypos_span[0], ypos_span[1], str(int(y))),
    ]
    replacements.sort(key=lambda item: item[0], reverse=True)
    patched = source_text
    for start, end, replacement in replacements:
        patched = f"{patched[:start]}{replacement}{patched[end:]}"
    return patched.encode("utf-8")


def apply_textbutton_patch(source_bytes: bytes, statement: TextbuttonStatement, *, x: int, y: int) -> bytes:
    return _apply_integer_span_patch(
        source_bytes,
        xpos_span=statement.xpos_span,
        ypos_span=statement.ypos_span,
        x=x,
        y=y,
    )


def apply_imagebutton_patch(
    source_bytes: bytes, statement: ImagebuttonStatement, *, x: int, y: int
) -> bytes:
    return _apply_integer_span_patch(
        source_bytes,
        xpos_span=statement.xpos_span,
        ypos_span=statement.ypos_span,
        x=x,
        y=y,
    )


def apply_bar_patch(source_bytes: bytes, statement: BarStatement, *, x: int, y: int) -> bytes:
    return _apply_integer_span_patch(
        source_bytes,
        xpos_span=statement.xpos_span,
        ypos_span=statement.ypos_span,
        x=x,
        y=y,
    )


def apply_vbar_patch(source_bytes: bytes, statement: VbarStatement, *, x: int, y: int) -> bytes:
    return _apply_integer_span_patch(
        source_bytes,
        xpos_span=statement.xpos_span,
        ypos_span=statement.ypos_span,
        x=x,
        y=y,
    )


def apply_slider_patch(source_bytes: bytes, statement: SliderStatement, *, x: int, y: int) -> bytes:
    return _apply_integer_span_patch(
        source_bytes,
        xpos_span=statement.xpos_span,
        ypos_span=statement.ypos_span,
        x=x,
        y=y,
    )


def analyze_editable_statement(
    line: str, *, expected_widget_id: str
) -> tuple[
    str,
    TextbuttonStatement | ImagebuttonStatement | BarStatement | VbarStatement | SliderStatement,
]:
    """Dispatch to a dedicated analyzer. Not a merged grammar."""
    kind = peek_statement_kind(line)
    if kind == "textbutton":
        return kind, analyze_textbutton_statement(line, expected_widget_id=expected_widget_id)
    if kind == "imagebutton":
        return kind, analyze_imagebutton_statement(line, expected_widget_id=expected_widget_id)
    if kind == "bar":
        # Slider is not a Ren'Py screen-language keyword; route bar+style "slider".
        if is_slider_style_bar_line(line):
            return "slider", analyze_slider_statement(line, expected_widget_id=expected_widget_id)
        return kind, analyze_bar_statement(line, expected_widget_id=expected_widget_id)
    if kind == "vbar":
        return kind, analyze_vbar_statement(line, expected_widget_id=expected_widget_id)
    if kind is None:
        raise EditorSourceError(
            "STATEMENT_KIND_MISMATCH",
            "source line does not contain a supported statement kind",
        )
    raise EditorSourceError("STATEMENT_KIND_MISMATCH", f"unsupported statement kind: {kind!r}")


def apply_editable_statement_patch(
    source_bytes: bytes,
    kind: str,
    statement: TextbuttonStatement
    | ImagebuttonStatement
    | BarStatement
    | VbarStatement
    | SliderStatement,
    *,
    x: int,
    y: int,
) -> bytes:
    if kind == "textbutton":
        if not isinstance(statement, TextbuttonStatement):
            raise EditorSourceError("STATEMENT_KIND_MISMATCH", "statement does not match textbutton kind")
        return apply_textbutton_patch(source_bytes, statement, x=x, y=y)
    if kind == "imagebutton":
        if not isinstance(statement, ImagebuttonStatement):
            raise EditorSourceError("STATEMENT_KIND_MISMATCH", "statement does not match imagebutton kind")
        return apply_imagebutton_patch(source_bytes, statement, x=x, y=y)
    if kind == "bar":
        if not isinstance(statement, BarStatement):
            raise EditorSourceError("STATEMENT_KIND_MISMATCH", "statement does not match bar kind")
        return apply_bar_patch(source_bytes, statement, x=x, y=y)
    if kind == "vbar":
        if not isinstance(statement, VbarStatement):
            raise EditorSourceError("STATEMENT_KIND_MISMATCH", "statement does not match vbar kind")
        return apply_vbar_patch(source_bytes, statement, x=x, y=y)
    if kind == "slider":
        if not isinstance(statement, SliderStatement):
            raise EditorSourceError("STATEMENT_KIND_MISMATCH", "statement does not match slider kind")
        return apply_slider_patch(source_bytes, statement, x=x, y=y)
    raise EditorSourceError("STATEMENT_KIND_MISMATCH", f"unsupported statement kind: {kind!r}")


def _button_child_line_indexes(lines: list[str], source_line: int, header_indent: int) -> list[int]:
    child_indexes: list[int] = []
    for index in range(source_line, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if indent <= header_indent:
            break
        child_indexes.append(index)

    if not child_indexes:
        raise EditorSourceError(
            "BUTTON_BLOCK_REQUIRED",
            "button statement must contain an explicit child block",
        )
    return child_indexes


def analyze_button_statement(
    source_text: str,
    *,
    source_line: int,
    expected_widget_id: str,
) -> ButtonStatement:
    lines = source_text.splitlines(keepends=True)
    if source_line < 1 or source_line > len(lines):
        raise EditorSourceError("SOURCE_LINE_INVALID", "button source line is outside the source file")

    header_line = lines[source_line - 1]
    header_text = header_line.rstrip("\r\n")
    tokens = _lex_single_line(header_text)
    top_level = [token for token in tokens if token.depth == 0]
    if not top_level or top_level[0].kind != "WORD" or top_level[0].text != "button":
        raise EditorSourceError("STATEMENT_KIND_MISMATCH", "source statement is not a button")

    colon_tokens = [token for token in top_level if token.kind == "SYMBOL" and token.text == ":"]
    if len(colon_tokens) != 1 or top_level[-1] is not colon_tokens[0]:
        raise EditorSourceError(
            "BUTTON_BLOCK_REQUIRED",
            "button statement must end with an explicit block header",
        )

    header_indent = len(header_line) - len(header_line.lstrip(" \t"))
    child_indexes = _button_child_line_indexes(lines, source_line, header_indent)
    child_indent = min(
        len(lines[index]) - len(lines[index].lstrip(" \t"))
        for index in child_indexes
    )
    for child_index in child_indexes:
        child_line = lines[child_index]
        child_indent_value = len(child_line) - len(child_line.lstrip(" \t"))
        if child_indent_value != child_indent:
            continue
        child_tokens = _lex_single_line(child_line.rstrip("\r\n"))
        child_top_level = [token for token in child_tokens if token.depth == 0]
        if (
            child_top_level
            and child_top_level[0].kind == "WORD"
            and child_top_level[0].text in {"xpos", "ypos"}
        ):
            raise EditorSourceError(
                "POSITION_IN_BLOCK",
                "button xpos/ypos inside the child block are not editable",
            )

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
        value_index = _next_top_level_index(tokens, index)
        if value_index is None:
            invalid_literals.add(keyword)
            continue
        value_token = tokens[value_index]
        if keyword == "id":
            if value_token.kind != "STRING":
                invalid_literals.add(keyword)
                continue
            widget_id = _parse_string_token(value_token)
            continue
        if value_token.kind != "NUMBER":
            invalid_literals.add(keyword)
            continue
        following_index = _next_top_level_index(tokens, value_index)
        if following_index is not None:
            following_token = tokens[following_index]
            if following_token.kind != "WORD" and not (
                following_token.kind == "SYMBOL"
                and following_token.text == ":"
                and following_index == len(tokens) - 1
            ):
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
        raise EditorSourceError("ID_LITERAL_REQUIRED", "button statement must contain exactly one literal id")
    if "id" in invalid_literals or widget_id is None:
        raise EditorSourceError("ID_LITERAL_REQUIRED", "button id must be a literal string")
    if widget_id != expected_widget_id:
        raise EditorSourceError("ID_MISMATCH", "literal button id does not match runtime widget id")
    if keyword_counts["xpos"] != 1:
        raise EditorSourceError("XPOS_DUPLICATE", "button statement must contain exactly one xpos")
    if keyword_counts["ypos"] != 1:
        raise EditorSourceError("YPOS_DUPLICATE", "button statement must contain exactly one ypos")
    if "xpos" in invalid_literals or xpos_value is None or xpos_span is None:
        raise EditorSourceError("XPOS_LITERAL_REQUIRED", "button xpos must be a literal integer")
    if "ypos" in invalid_literals or ypos_value is None or ypos_span is None:
        raise EditorSourceError("YPOS_LITERAL_REQUIRED", "button ypos must be a literal integer")

    return ButtonStatement(
        widget_id=widget_id,
        xpos=xpos_value,
        ypos=ypos_value,
        xpos_span=xpos_span,
        ypos_span=ypos_span,
        source_line=source_line,
    )


def apply_button_patch(source_bytes: bytes, statement: ButtonStatement, *, x: int, y: int) -> bytes:
    source_text = source_bytes.decode("utf-8")
    lines = source_text.splitlines(keepends=True)
    if statement.source_line < 1 or statement.source_line > len(lines):
        raise EditorSourceError("SOURCE_LINE_INVALID", "button source line is outside the source file")
    header_offset = sum(len(line) for line in lines[: statement.source_line - 1])
    replacements = [
        (header_offset + statement.xpos_span[0], header_offset + statement.xpos_span[1], str(int(x))),
        (header_offset + statement.ypos_span[0], header_offset + statement.ypos_span[1], str(int(y))),
    ]
    replacements.sort(key=lambda item: item[0], reverse=True)
    patched = source_text
    for start, end, replacement in replacements:
        patched = f"{patched[:start]}{replacement}{patched[end:]}"
    return patched.encode("utf-8")
