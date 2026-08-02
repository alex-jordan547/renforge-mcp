from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TypeVar


class EditorSourceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# Logical parent size for evidence-gated full-screen Fixed fixtures (demo is 1280×720).
# Align write-back is only unlocked when independent focus geometry matches this parent
# under Ren'Py's align-sets-anchor placement: TL = fraction × (parent − widget).
DEFAULT_ALIGN_PARENT_SIZE: tuple[int, int] = (1280, 720)

# Modes whose editor original_position is the measured focus_list top-left and whose
# write-back is authored + (runtime − baseline). Preview uses absolute xpos/ypos.
RUNTIME_DELTA_POSITION_MODES = frozenset({"align", "offset"})


def uses_runtime_delta_position(mode: str | None) -> bool:
    """True for align/offset — baseline/delta position modes (not absolute xy/pos)."""
    return mode in RUNTIME_DELTA_POSITION_MODES


def _pair2(value: object) -> tuple[int | float, int | float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]  # type: ignore[return-value]
    return None


def textbutton_patch_kwargs(
    statement: TextbuttonStatement,
    source_key: dict | None,
) -> dict[str, object]:
    """Keyword args for ``apply_textbutton_patch`` from an analyzed source_key.

    Shared by coordinator commit paths so align/offset stay one branch each only
    where their geometry contracts differ.
    """
    mode = getattr(statement, "position_mode", "xy")
    key = source_key if isinstance(source_key, dict) else {}
    if mode == "align":
        baseline = _pair2(key.get("align_runtime_baseline"))
        size = _pair2(key.get("align_widget_size"))
        return {
            "align_runtime_baseline": tuple(baseline) if baseline is not None else None,
            "align_widget_size": tuple(int(v) for v in size) if size is not None else None,
        }
    if mode == "offset":
        baseline = _pair2(key.get("offset_runtime_baseline"))
        return {
            "offset_runtime_baseline": tuple(int(v) for v in baseline) if baseline is not None else None,
        }
    return {}

# Concurrent axis/placement properties that must not ride along with pure align (fx, fy).
_ALIGN_CONCURRENT_PROPERTY_WORDS = frozenset(
    {
        "xpos",
        "ypos",
        "pos",
        "offset",
        "anchor",
        "xalign",
        "yalign",
        "xanchor",
        "yanchor",
        "xoffset",
        "yoffset",
        "xcenter",
        "ycenter",
    }
)

# Concurrent properties that must not ride along with pure offset (x, y).
# Axis-split xoffset/yoffset and absolute/relative placement stay locked.
_OFFSET_CONCURRENT_PROPERTY_WORDS = frozenset(
    {
        "xpos",
        "ypos",
        "pos",
        "align",
        "anchor",
        "xalign",
        "yalign",
        "xanchor",
        "yanchor",
        "xoffset",
        "yoffset",
        "xcenter",
        "ycenter",
    }
)

# Properties whose value may be a bare top-level WORD (e.g. ``action pos``).
# Such WORDs are values, not position-form keywords.
_NAME_VALUE_PROPERTY_WORDS = frozenset(
    {
        "action",
        "hovered",
        "unhovered",
        "selected",
        "alternate",
        "style",
        "at",
        "default",
        "tooltip",
        "sensitive",
        "focus",
        "keyboard_focus",
        "keysym",
        "alternate_keysym",
    }
)


@dataclass(frozen=True)
class TextbuttonStatement:
    widget_id: str
    # Pixel integers for xy/pos/offset; fractional floats for align components.
    xpos: int | float
    ypos: int | float
    xpos_span: tuple[int, int]
    ypos_span: tuple[int, int]
    # "single_line" keeps spans relative to the statement line.
    # "block" keeps absolute character spans in the full source file.
    form: str = "single_line"
    source_line: int | None = None
    # xy | pos | align | offset — write-back preserves the authored form.
    position_mode: str = "xy"
    # When True, a pure literal anchor (fx, fy) is present and must be preserved.
    has_anchor: bool = False
    align_parent_size: tuple[int, int] = DEFAULT_ALIGN_PARENT_SIZE


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


def _require_single_literal_id(
    tokens: list[_Token],
    *,
    expected_widget_id: str,
    human_kind: str,
) -> str:
    """Return the single top-level literal id string, or raise a shared id error."""
    keyword_counts_id = 0
    widget_id: str | None = None
    id_invalid = False
    for index, token in enumerate(tokens):
        if token.depth != 0 or token.kind != "WORD" or token.text != "id":
            continue
        keyword_counts_id += 1
        value_index = _next_top_level_index(tokens, index)
        if value_index is None or tokens[value_index].kind != "STRING":
            id_invalid = True
            continue
        widget_id = _parse_string_token(tokens[value_index])
    if keyword_counts_id != 1 or id_invalid or widget_id is None:
        raise EditorSourceError(
            "ID_LITERAL_REQUIRED",
            f"{human_kind} statement must contain exactly one literal id",
        )
    if widget_id != expected_widget_id:
        raise EditorSourceError("ID_MISMATCH", "literal id does not match runtime widget id")
    return widget_id


# Property keywords that may follow pure pair forms on a textbutton line.
# Expression operators (if/or/else/and) are intentionally absent.
_TEXTBUTTON_PAIR_FOLLOWER_WORDS = frozenset(
    {
        "id",
        "xpos",
        "ypos",
        "pos",
        "align",
        "offset",
        "anchor",
        "action",
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
        "selected",
        "alternate",
        "keysym",
        "alternate_keysym",
    }
)


def _previous_top_level_token(tokens: list[_Token], index: int) -> _Token | None:
    for cursor in range(index - 1, -1, -1):
        if tokens[cursor].depth == 0:
            return tokens[cursor]
    return None


def _top_level_property_keyword_indexes(tokens: list[_Token], keyword: str) -> list[int]:
    """Indexes of top-level ``keyword`` used as a property name (any value form).

    Bare values of other properties are excluded so ``action pos`` does not count
    as a ``pos`` position form (Codex P2 on issue #38).
    """
    indexes: list[int] = []
    for index, token in enumerate(tokens):
        if token.depth != 0 or token.kind != "WORD" or token.text != keyword:
            continue
        previous = _previous_top_level_token(tokens, index)
        if (
            previous is not None
            and previous.kind == "WORD"
            and previous.text in _NAME_VALUE_PROPERTY_WORDS
        ):
            continue
        indexes.append(index)
    return indexes


def _tuple_property_indexes(tokens: list[_Token], keyword: str) -> list[int]:
    """Indexes of top-level ``keyword`` used as a property (value starts with ``(``)."""
    indexes: list[int] = []
    for index in _top_level_property_keyword_indexes(tokens, keyword):
        next_index = index + 1
        if next_index >= len(tokens):
            continue
        next_token = tokens[next_index]
        if next_token.kind == "SYMBOL" and next_token.text == "(":
            indexes.append(index)
    return indexes


def _pos_property_indexes(tokens: list[_Token]) -> list[int]:
    return _tuple_property_indexes(tokens, "pos")


def _parse_float_at(
    tokens: list[_Token], start_index: int
) -> tuple[float, int, tuple[int, int]] | None:
    """Parse a pure float or integer token run starting at start_index.

    Accepts ``N``, ``N.M``, and ``-N.M`` (lexer may emit NUMBER / . / NUMBER).
    Returns (value, index_after_last_token, full_span).
    """
    if start_index >= len(tokens) or tokens[start_index].kind != "NUMBER":
        return None
    first = tokens[start_index]
    if (
        start_index + 2 < len(tokens)
        and tokens[start_index + 1].kind == "SYMBOL"
        and tokens[start_index + 1].text == "."
        and tokens[start_index + 2].kind == "NUMBER"
    ):
        text = f"{first.text}.{tokens[start_index + 2].text}"
        end = tokens[start_index + 2].end
        return float(text), start_index + 3, (first.start, end)
    return float(first.text), start_index + 1, (first.start, first.end)


def _parse_literal_float_pair(
    tokens: list[_Token],
    keyword_index: int,
) -> tuple[float, float, tuple[int, int], tuple[int, int]] | None:
    """Parse ``keyword (X, Y)`` with pure float/int literals."""
    open_index = keyword_index + 1
    if open_index >= len(tokens):
        return None
    if tokens[open_index].kind != "SYMBOL" or tokens[open_index].text != "(":
        return None
    x_parsed = _parse_float_at(tokens, open_index + 1)
    if x_parsed is None:
        return None
    x_value, after_x, x_span = x_parsed
    if after_x >= len(tokens) or tokens[after_x].kind != "SYMBOL" or tokens[after_x].text != ",":
        return None
    y_parsed = _parse_float_at(tokens, after_x + 1)
    if y_parsed is None:
        return None
    y_value, after_y, y_span = y_parsed
    if after_y >= len(tokens) or tokens[after_y].kind != "SYMBOL" or tokens[after_y].text != ")":
        return None
    following = after_y + 1
    if following < len(tokens):
        next_token = tokens[following]
        if (
            next_token.depth != 0
            or next_token.kind != "WORD"
            or next_token.text not in _TEXTBUTTON_PAIR_FOLLOWER_WORDS
        ):
            return None
    return x_value, y_value, x_span, y_span


def _parse_literal_pos_pair(
    tokens: list[_Token],
    pos_index: int,
) -> tuple[int, int, tuple[int, int], tuple[int, int]] | None:
    """Parse ``pos (X, Y)`` with pure integer literals; return values and number spans."""
    open_index = pos_index + 1
    if open_index + 4 >= len(tokens):
        return None
    if tokens[open_index].kind != "SYMBOL" or tokens[open_index].text != "(":
        return None
    x_token = tokens[open_index + 1]
    comma_token = tokens[open_index + 2]
    y_token = tokens[open_index + 3]
    close_token = tokens[open_index + 4]
    if x_token.kind != "NUMBER" or y_token.kind != "NUMBER":
        return None
    if comma_token.kind != "SYMBOL" or comma_token.text != ",":
        return None
    if close_token.kind != "SYMBOL" or close_token.text != ")":
        return None
    following = open_index + 5
    if following < len(tokens):
        next_token = tokens[following]
        if (
            next_token.depth != 0
            or next_token.kind != "WORD"
            or next_token.text not in _TEXTBUTTON_PAIR_FOLLOWER_WORDS
        ):
            return None
    return (
        int(x_token.text),
        int(y_token.text),
        (x_token.start, x_token.end),
        (y_token.start, y_token.end),
    )


def align_to_pixels(
    xalign: float,
    yalign: float,
    *,
    parent_size: tuple[int, int] = DEFAULT_ALIGN_PARENT_SIZE,
    widget_size: tuple[int, int],
) -> tuple[int, int]:
    """Convert authored align fractions to logical pixel top-left.

    Ren'Py ``align (a, b)`` also sets the anchor to ``(a, b)``, so the focus
    top-left is ``fraction × (parent − widget)``, not ``fraction × parent``.
    """
    parent_w, parent_h = parent_size
    widget_w, widget_h = int(widget_size[0]), int(widget_size[1])
    return (
        int(round(float(xalign) * (parent_w - widget_w))),
        int(round(float(yalign) * (parent_h - widget_h))),
    )


def pixels_to_align(
    x: int,
    y: int,
    *,
    parent_size: tuple[int, int] = DEFAULT_ALIGN_PARENT_SIZE,
    widget_size: tuple[int, int],
) -> tuple[float, float]:
    """Convert logical pixel top-left to align fractions (requires non-zero extent)."""
    parent_w, parent_h = parent_size
    widget_w, widget_h = int(widget_size[0]), int(widget_size[1])
    extent_w = int(parent_w) - widget_w
    extent_h = int(parent_h) - widget_h
    if extent_w == 0 or extent_h == 0:
        raise EditorSourceError(
            "ALIGN_EXTENT_ZERO",
            "align conversion requires non-zero placement extent on both axes",
        )
    return float(x) / float(extent_w), float(y) / float(extent_h)


def align_geometry_matches_parent(
    *,
    authored: tuple[float, float],
    runtime_xy: tuple[int, int],
    widget_size: tuple[int, int],
    parent_size: tuple[int, int] = DEFAULT_ALIGN_PARENT_SIZE,
    tolerance: int = 1,
) -> bool:
    """True when independent focus TL matches ``align × (parent − widget)`` within tolerance."""
    ax, ay = float(authored[0]), float(authored[1])
    rx, ry = int(runtime_xy[0]), int(runtime_xy[1])
    widget_w, widget_h = int(widget_size[0]), int(widget_size[1])
    parent_w, parent_h = int(parent_size[0]), int(parent_size[1])
    expected_x = ax * float(parent_w - widget_w)
    expected_y = ay * float(parent_h - widget_h)
    return abs(rx - expected_x) <= tolerance and abs(ry - expected_y) <= tolerance


def _format_align_component(value: float) -> str:
    # High precision so reload pixel agreement stays within 1 logical pixel after
    # align fraction round-trips (demo parent is 1280×720).
    text = f"{float(value):.12f}".rstrip("0").rstrip(".")
    if text in {"-0", ""}:
        text = "0"
    if "." not in text:
        text = f"{text}.0"
    return text


def _require_pure_anchor_if_present(tokens: list[_Token]) -> bool:
    """Return True if a pure literal anchor is present; raise if impure/duplicate.

    Counts every top-level ``anchor`` *property* (including bare expressions such
    as ``anchor anchor_value``), not only tuple forms. Bare values of other
    properties (e.g. ``action anchor``) are ignored.
    """
    anchor_word_indexes = _top_level_property_keyword_indexes(tokens, "anchor")
    if not anchor_word_indexes:
        return False
    if len(anchor_word_indexes) > 1:
        raise EditorSourceError("ANCHOR_DUPLICATE", "textbutton statement must contain exactly one anchor")
    # Must be the supported property form: anchor (
    tuple_indexes = _tuple_property_indexes(tokens, "anchor")
    if not tuple_indexes or tuple_indexes[0] != anchor_word_indexes[0]:
        raise EditorSourceError(
            "ANCHOR_LITERAL_REQUIRED",
            "anchor must be a pure literal pair of numbers: anchor (x, y)",
        )
    if _parse_literal_float_pair(tokens, tuple_indexes[0]) is None:
        raise EditorSourceError(
            "ANCHOR_LITERAL_REQUIRED",
            "anchor must be a pure literal pair of numbers: anchor (x, y)",
        )
    return True


def analyze_textbutton_statement(line: str, *, expected_widget_id: str) -> TextbuttonStatement:
    if is_textbutton_block_header(line):
        raise EditorSourceError(
            "MULTILINE_STATEMENT_REJECTED",
            "textbutton block headers require analyze_textbutton_block_statement",
        )
    statement_text = _statement_text(line)
    tokens = _lex_single_line(statement_text)
    top_level = [token for token in tokens if token.depth == 0]
    if not top_level or top_level[0].kind != "WORD" or top_level[0].text != "textbutton":
        raise EditorSourceError("STATEMENT_KIND_MISMATCH", "source statement is not a textbutton")

    has_xy = any(
        token.depth == 0 and token.kind == "WORD" and token.text in {"xpos", "ypos"}
        for token in tokens
    )
    # Count every top-level *property* keyword (including bare expressions) so
    # concurrent or dynamic forms cannot be ignored by the tuple-only scan.
    pos_word_indexes = _top_level_property_keyword_indexes(tokens, "pos")
    align_word_indexes = _top_level_property_keyword_indexes(tokens, "align")
    offset_word_indexes = _top_level_property_keyword_indexes(tokens, "offset")

    form_count = sum(
        [
            bool(has_xy),
            bool(pos_word_indexes),
            bool(align_word_indexes),
            bool(offset_word_indexes),
        ]
    )
    if form_count > 1:
        raise EditorSourceError(
            "POSITION_FORM_MIXED",
            "textbutton cannot mix align/pos/offset/xpos/ypos position forms",
        )
    if len(pos_word_indexes) > 1:
        raise EditorSourceError("POS_DUPLICATE", "textbutton statement must contain exactly one pos")
    if len(align_word_indexes) > 1:
        raise EditorSourceError("ALIGN_DUPLICATE", "textbutton statement must contain exactly one align")
    if len(offset_word_indexes) > 1:
        raise EditorSourceError(
            "OFFSET_DUPLICATE",
            "textbutton statement must contain exactly one offset",
        )

    if len(align_word_indexes) == 1:
        widget_id = _require_single_literal_id(
            tokens,
            expected_widget_id=expected_widget_id,
            human_kind="textbutton",
        )
        # Reject concurrent axis/placement properties (offset, xalign, anchor, …).
        concurrent = [
            keyword
            for keyword in _ALIGN_CONCURRENT_PROPERTY_WORDS
            if _top_level_property_keyword_indexes(tokens, keyword)
        ]
        if concurrent:
            raise EditorSourceError(
                "POSITION_FORM_MIXED",
                "textbutton align form does not combine with concurrent placement properties",
            )
        tuple_indexes = _tuple_property_indexes(tokens, "align")
        if not tuple_indexes or tuple_indexes[0] != align_word_indexes[0]:
            raise EditorSourceError(
                "ALIGN_LITERAL_REQUIRED",
                "align must be a pure literal pair: align (x, y)",
            )
        parsed_align = _parse_literal_float_pair(tokens, tuple_indexes[0])
        if parsed_align is None:
            raise EditorSourceError(
                "ALIGN_LITERAL_REQUIRED",
                "align must be a pure literal pair: align (x, y)",
            )
        xalign, yalign, x_span, y_span = parsed_align
        return TextbuttonStatement(
            widget_id=widget_id,
            xpos=xalign,
            ypos=yalign,
            xpos_span=x_span,
            ypos_span=y_span,
            form="single_line",
            source_line=None,
            position_mode="align",
            has_anchor=False,
            align_parent_size=DEFAULT_ALIGN_PARENT_SIZE,
        )

    if len(pos_word_indexes) == 1:
        widget_id = _require_single_literal_id(
            tokens,
            expected_widget_id=expected_widget_id,
            human_kind="textbutton",
        )
        has_anchor = _require_pure_anchor_if_present(tokens)
        tuple_indexes = _pos_property_indexes(tokens)
        if not tuple_indexes or tuple_indexes[0] != pos_word_indexes[0]:
            raise EditorSourceError(
                "POS_LITERAL_REQUIRED",
                "pos must be a pure literal pair of integers: pos (x, y)",
            )
        parsed_pos = _parse_literal_pos_pair(tokens, tuple_indexes[0])
        if parsed_pos is None:
            raise EditorSourceError(
                "POS_LITERAL_REQUIRED",
                "pos must be a pure literal pair of integers: pos (x, y)",
            )
        xpos_value, ypos_value, xpos_span, ypos_span = parsed_pos
        return TextbuttonStatement(
            widget_id=widget_id,
            xpos=xpos_value,
            ypos=ypos_value,
            xpos_span=xpos_span,
            ypos_span=ypos_span,
            form="single_line",
            source_line=None,
            position_mode="pos",
            has_anchor=has_anchor,
        )

    if len(offset_word_indexes) == 1:
        widget_id = _require_single_literal_id(
            tokens,
            expected_widget_id=expected_widget_id,
            human_kind="textbutton",
        )
        concurrent = [
            keyword
            for keyword in _OFFSET_CONCURRENT_PROPERTY_WORDS
            if _top_level_property_keyword_indexes(tokens, keyword)
        ]
        if concurrent:
            raise EditorSourceError(
                "POSITION_FORM_MIXED",
                "textbutton offset form does not combine with concurrent placement properties",
            )
        tuple_indexes = _tuple_property_indexes(tokens, "offset")
        if not tuple_indexes or tuple_indexes[0] != offset_word_indexes[0]:
            raise EditorSourceError(
                "OFFSET_LITERAL_REQUIRED",
                "offset must be a pure literal pair of integers: offset (x, y)",
            )
        # Same integer-pair shape as pos (supports signed NUMBER tokens).
        parsed_offset = _parse_literal_pos_pair(tokens, tuple_indexes[0])
        if parsed_offset is None:
            raise EditorSourceError(
                "OFFSET_LITERAL_REQUIRED",
                "offset must be a pure literal pair of integers: offset (x, y)",
            )
        ox_value, oy_value, ox_span, oy_span = parsed_offset
        return TextbuttonStatement(
            widget_id=widget_id,
            xpos=ox_value,
            ypos=oy_value,
            xpos_span=ox_span,
            ypos_span=oy_span,
            form="single_line",
            source_line=None,
            position_mode="offset",
            has_anchor=False,
        )

    # xy form (optional pure anchor for issue #40).
    statement = _analyze_positioned_kind_statement(
        line,
        expected_widget_id=expected_widget_id,
        expected_kind="textbutton",
        statement_cls=TextbuttonStatement,
    )
    has_anchor = _require_pure_anchor_if_present(tokens)
    if has_anchor:
        return TextbuttonStatement(
            widget_id=statement.widget_id,
            xpos=statement.xpos,
            ypos=statement.ypos,
            xpos_span=statement.xpos_span,
            ypos_span=statement.ypos_span,
            form=statement.form,
            source_line=statement.source_line,
            position_mode="xy",
            has_anchor=True,
        )
    return statement



def is_textbutton_block_header(line: str) -> bool:
    """True when the line is a textbutton header ending with an explicit block colon."""
    try:
        statement_text = _statement_text(line)
    except EditorSourceError:
        return False
    tokens = _lex_single_line(statement_text)
    top_level = [token for token in tokens if token.depth == 0]
    if not top_level or top_level[0].kind != "WORD" or top_level[0].text != "textbutton":
        return False
    colon_tokens = [token for token in top_level if token.kind == "SYMBOL" and token.text == ":"]
    return len(colon_tokens) == 1 and top_level[-1] is colon_tokens[0]


def analyze_textbutton_block_statement(
    source_text: str,
    *,
    source_line: int,
    expected_widget_id: str,
) -> TextbuttonStatement:
    """Analyze multi-line textbutton with id/xpos/ypos authored in the child block.

    Supported shape (one physical form, already-proven adapter)::

        textbutton "Label":
            id "widget_id"
            xpos 100
            ypos 200
            action NullAction()

    Header must not carry id/xpos/ypos. Only direct child properties at the block
    indent are considered. Patch spans are absolute within ``source_text``.
    """
    lines = source_text.splitlines(keepends=True)
    if source_line < 1 or source_line > len(lines):
        raise EditorSourceError("SOURCE_LINE_INVALID", "textbutton source line is outside the source file")

    header_line = lines[source_line - 1]
    if not is_textbutton_block_header(header_line):
        raise EditorSourceError(
            "MULTILINE_STATEMENT_REJECTED",
            "textbutton statement is not a supported multi-line block header",
        )

    header_text = header_line.rstrip("\r\n")
    header_tokens = _lex_single_line(header_text)
    for token in header_tokens:
        if token.depth == 0 and token.kind == "WORD" and token.text in {"id", "xpos", "ypos"}:
            raise EditorSourceError(
                "POSITION_ON_HEADER_UNSUPPORTED",
                "textbutton block form requires id/xpos/ypos in the child block only",
            )

    header_indent = len(header_line) - len(header_line.lstrip(" \t"))
    try:
        child_indexes = _button_child_line_indexes(lines, source_line, header_indent)
    except EditorSourceError as exc:
        if exc.code == "BUTTON_BLOCK_REQUIRED":
            raise EditorSourceError(
                "MULTILINE_STATEMENT_REJECTED",
                "textbutton block must contain a child block",
            ) from exc
        raise
    child_indent = min(
        len(lines[index]) - len(lines[index].lstrip(" \t")) for index in child_indexes
    )

    keyword_counts = {"id": 0, "xpos": 0, "ypos": 0}
    widget_id: str | None = None
    xpos_value: int | None = None
    ypos_value: int | None = None
    xpos_span: tuple[int, int] | None = None
    ypos_span: tuple[int, int] | None = None
    invalid_literals: set[str] = set()

    for child_index in child_indexes:
        child_line = lines[child_index]
        child_indent_value = len(child_line) - len(child_line.lstrip(" \t"))
        if child_indent_value != child_indent:
            # Nested deeper (e.g. inside a child container) is ignored for identity.
            continue
        child_text = child_line.rstrip("\r\n")
        tokens = _lex_single_line(child_text)
        top_level = [token for token in tokens if token.depth == 0]
        if not top_level or top_level[0].kind != "WORD":
            continue
        keyword = top_level[0].text
        if keyword not in keyword_counts:
            continue
        keyword_counts[keyword] += 1
        value_index = _next_top_level_index(tokens, 0)
        line_offset = sum(len(line) for line in lines[:child_index])
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
        if following_index is not None and tokens[following_index].kind != "WORD":
            invalid_literals.add(keyword)
            continue
        value = int(value_token.text)
        absolute_span = (line_offset + value_token.start, line_offset + value_token.end)
        if keyword == "xpos":
            xpos_value = value
            xpos_span = absolute_span
        else:
            ypos_value = value
            ypos_span = absolute_span

    if keyword_counts["id"] != 1:
        raise EditorSourceError(
            "ID_LITERAL_REQUIRED",
            "textbutton block must contain exactly one literal id",
        )
    if "id" in invalid_literals or widget_id is None:
        raise EditorSourceError("ID_LITERAL_REQUIRED", "id must be a literal string")
    if widget_id != expected_widget_id:
        raise EditorSourceError("ID_MISMATCH", "literal id does not match runtime widget id")
    if keyword_counts["xpos"] == 0:
        raise EditorSourceError(
            "XPOS_LITERAL_REQUIRED",
            "textbutton block must author a literal xpos",
        )
    if keyword_counts["xpos"] != 1:
        raise EditorSourceError(
            "XPOS_DUPLICATE",
            "textbutton block must contain exactly one xpos",
        )
    if keyword_counts["ypos"] == 0:
        raise EditorSourceError(
            "YPOS_LITERAL_REQUIRED",
            "textbutton block must author a literal ypos",
        )
    if keyword_counts["ypos"] != 1:
        raise EditorSourceError(
            "YPOS_DUPLICATE",
            "textbutton block must contain exactly one ypos",
        )
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
        form="block",
        source_line=source_line,
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


def apply_textbutton_patch(
    source_bytes: bytes,
    statement: TextbuttonStatement,
    *,
    x: int,
    y: int,
    align_runtime_baseline: tuple[int, int] | list[int] | None = None,
    align_widget_size: tuple[int, int] | list[int] | None = None,
    offset_runtime_baseline: tuple[int, int] | list[int] | None = None,
) -> bytes:
    # Block form spans are absolute in the full source; single-line spans are
    # relative to the single line bytes passed by the coordinator.
    if statement.position_mode == "offset":
        # Ren'Py offset is additive on top of the base placement. Commit x/y are
        # measured runtime top-left; write authored + (runtime − baseline).
        if offset_runtime_baseline is None or len(offset_runtime_baseline) != 2:
            raise EditorSourceError(
                "OFFSET_BASELINE_REQUIRED",
                "offset write-back requires a measured runtime baseline",
            )
        ox = int(statement.xpos) + (int(x) - int(offset_runtime_baseline[0]))
        oy = int(statement.ypos) + (int(y) - int(offset_runtime_baseline[1]))
        return _apply_integer_span_patch(
            source_bytes,
            xpos_span=statement.xpos_span,
            ypos_span=statement.ypos_span,
            x=ox,
            y=oy,
        )
    if statement.position_mode == "align":
        # Proven geometry only: measured baseline + widget size are mandatory.
        # Ren'Py ``align (a, b)`` sets anchor to (a, b), so ΔTL = Δalign × (parent − widget).
        if align_runtime_baseline is None or len(align_runtime_baseline) != 2:
            raise EditorSourceError(
                "ALIGN_BASELINE_REQUIRED",
                "align write-back requires a measured runtime baseline",
            )
        if align_widget_size is None or len(align_widget_size) != 2:
            raise EditorSourceError(
                "ALIGN_WIDGET_SIZE_REQUIRED",
                "align write-back requires a measured widget size",
            )
        parent_w, parent_h = statement.align_parent_size
        widget_w = int(align_widget_size[0])
        widget_h = int(align_widget_size[1])
        # Signed extents: preserve direction when widget > parent; lock zero axes.
        extent_w = int(parent_w) - widget_w
        extent_h = int(parent_h) - widget_h
        dx = int(x) - int(align_runtime_baseline[0])
        dy = int(y) - int(align_runtime_baseline[1])
        if extent_w == 0:
            if dx != 0:
                raise EditorSourceError(
                    "ALIGN_EXTENT_ZERO",
                    "cannot move on X when placement extent is zero",
                )
            ax = float(statement.xpos)
        else:
            ax = float(statement.xpos) + dx / float(extent_w)
        if extent_h == 0:
            if dy != 0:
                raise EditorSourceError(
                    "ALIGN_EXTENT_ZERO",
                    "cannot move on Y when placement extent is zero",
                )
            ay = float(statement.ypos)
        else:
            ay = float(statement.ypos) + dy / float(extent_h)
        source_text = source_bytes.decode("utf-8")
        replacements = [
            (statement.xpos_span[0], statement.xpos_span[1], _format_align_component(ax)),
            (statement.ypos_span[0], statement.ypos_span[1], _format_align_component(ay)),
        ]
        replacements.sort(key=lambda item: item[0], reverse=True)
        patched = source_text
        for start, end, replacement in replacements:
            patched = f"{patched[:start]}{replacement}{patched[end:]}"
        return patched.encode("utf-8")
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
    """Dispatch to a dedicated analyzer. Not a merged grammar.

    Multi-line textbutton blocks are handled by
    :func:`analyze_textbutton_block_statement` via the coordinator (needs full
    source text). A block header alone is rejected here with
    ``MULTILINE_STATEMENT_REJECTED``.
    """
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
