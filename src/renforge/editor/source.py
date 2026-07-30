from __future__ import annotations

from dataclasses import dataclass
import re


_TEXTBUTTON_PREFIX = re.compile(r"^\s*textbutton\b")
_ID_TOKEN = re.compile(r"""\bid\s+(?P<quote>["'])(?P<value>[^"']+)(?P=quote)""")
_XPOS_TOKEN = re.compile(r"\bxpos\s+(-?\d+)\b")
_YPOS_TOKEN = re.compile(r"\bypos\s+(-?\d+)\b")
_XPOS_KEYWORD = re.compile(r"\bxpos\b")
_YPOS_KEYWORD = re.compile(r"\bypos\b")


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


def analyze_textbutton_statement(line: str, *, expected_widget_id: str) -> TextbuttonStatement:
    if "\n" in line[:-1]:
        raise EditorSourceError("MULTILINE_STATEMENT_REJECTED", "statement must be single-line")
    if not _TEXTBUTTON_PREFIX.search(line):
        raise EditorSourceError("STATEMENT_KIND_MISMATCH", "source statement is not a textbutton")

    ids = list(_ID_TOKEN.finditer(line))
    if len(ids) != 1:
        raise EditorSourceError("ID_LITERAL_REQUIRED", "textbutton statement must contain exactly one literal id")
    widget_id = ids[0].group("value")
    if widget_id != expected_widget_id:
        raise EditorSourceError("ID_MISMATCH", "literal id does not match runtime widget id")

    xpos_keywords = list(_XPOS_KEYWORD.finditer(line))
    ypos_keywords = list(_YPOS_KEYWORD.finditer(line))
    xpos_matches = list(_XPOS_TOKEN.finditer(line))
    ypos_matches = list(_YPOS_TOKEN.finditer(line))

    if len(xpos_keywords) != 1 or len(xpos_matches) != 1:
        if len(xpos_keywords) == 1 and len(xpos_matches) == 0:
            raise EditorSourceError("XPOS_LITERAL_REQUIRED", "xpos must be a literal integer")
        raise EditorSourceError("XPOS_DUPLICATE", "textbutton statement must contain exactly one xpos")
    if len(ypos_keywords) != 1 or len(ypos_matches) != 1:
        if len(ypos_keywords) == 1 and len(ypos_matches) == 0:
            raise EditorSourceError("YPOS_LITERAL_REQUIRED", "ypos must be a literal integer")
        raise EditorSourceError("YPOS_DUPLICATE", "textbutton statement must contain exactly one ypos")

    xpos_match = xpos_matches[0]
    ypos_match = ypos_matches[0]
    return TextbuttonStatement(
        widget_id=widget_id,
        xpos=int(xpos_match.group(1)),
        ypos=int(ypos_match.group(1)),
        xpos_span=xpos_match.span(1),
        ypos_span=ypos_match.span(1),
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

