"""Token-aware symbol reference lookup for Ren'Py script files."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
import re
from typing import Any


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NAMED_TARGET_CALLS = (
    "Call",
    "IncrementVariable",
    "Jump",
    "Hide",
    "SetLocalVariable",
    "SetScreenVariable",
    "SetVariable",
    "Show",
    "ShowMenu",
    "ShowTransient",
    "ToggleLocalVariable",
    "ToggleScreenVariable",
    "ToggleVariable",
)


def _copy_text_interpolations(
    line: str,
    start: int,
    end: int,
    masked: list[str],
) -> None:
    """Expose expressions inside Ren'Py ``[interpolation]`` as code."""

    index = start
    while index < end:
        opening = line.find("[", index, end)
        if opening < 0:
            return
        if opening + 1 < end and line[opening + 1] == "[":
            index = opening + 2
            continue
        closing = line.find("]", opening + 1, end)
        if closing < 0:
            return
        for position in range(opening + 1, closing):
            masked[position] = line[position]
        index = closing + 1


def _mask_non_code(
    line: str,
    triple_quote: str | None,
) -> tuple[str, str, str | None]:
    """Mask prose while preserving code and Ren'Py text interpolations."""

    chars = list(line)
    masked = [" "] * len(chars)
    semantic = [" "] * len(chars)
    index = 0
    while index < len(chars):
        if triple_quote is not None:
            end = line.find(triple_quote, index)
            if end < 0:
                return "".join(masked), "".join(semantic), triple_quote
            index = end + 3
            triple_quote = None
            continue

        char = chars[index]
        if char == "#":
            break
        if line.startswith("'''", index) or line.startswith('\"\"\"', index):
            triple_quote = line[index : index + 3]
            index += 3
            continue
        if char in {"'", '"'}:
            quote = char
            quote_start = index
            content_start = index + 1
            index += 1
            while index < len(chars):
                if chars[index] == "\\":
                    index += 2
                    continue
                index += 1
                if chars[index - 1] == quote:
                    break
            content_end = index - 1 if index and chars[index - 1] == quote else len(chars)
            semantic[quote_start:index] = chars[quote_start:index]
            _copy_text_interpolations(line, content_start, content_end, masked)
            continue
        masked[index] = char
        semantic[index] = char
        index += 1
    return "".join(masked), "".join(semantic), triple_quote


def _definition_column(code: str, symbol: str) -> int | None:
    escaped = re.escape(symbol)
    patterns = (
        rf"^\s*(?:define|default)\s+(?P<symbol>{escaped})\b",
        rf"^\s*(?:label|screen|transform|image)\s+(?P<symbol>{escaped})\b",
        rf"^\s*\$\s*(?P<symbol>{escaped})\s*=",
        rf"^\s*(?P<symbol>{escaped})\s*(?::[^=]+)?=",
    )
    for pattern in patterns:
        match = re.match(pattern, code)
        if match:
            return match.start("symbol")
    return None


def _semantic_string_columns(line: str, symbol: str) -> list[int]:
    """Locate string arguments whose API semantics name a label or variable."""

    calls = "|".join(_NAMED_TARGET_CALLS)
    escaped = re.escape(symbol)
    pattern = re.compile(
        rf"\b(?:{calls})\s*\(\s*(?P<quote>['\"])(?P<symbol>{escaped})(?P=quote)"
    )
    return [match.start("symbol") for match in pattern.finditer(line)]


def find_references(
    project_path: str | Path,
    symbol: str,
    *,
    file_glob: str = "",
    offset: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    """Find exact identifiers in code and Ren'Py text interpolation expressions."""

    if not _IDENTIFIER_RE.fullmatch(symbol):
        raise ValueError("symbol must be a valid identifier")

    root = Path(project_path).expanduser().resolve()
    game = root / "game"
    occurrences: list[dict[str, Any]] = []
    if game.is_dir():
        for path in sorted(game.rglob("*.rpy")):
            relative = path.relative_to(root).as_posix()
            if file_glob and not fnmatch(relative, file_glob):
                continue
            triple_quote: str | None = None
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line_number, line in enumerate(lines, start=1):
                code, semantic_code, triple_quote = _mask_non_code(line, triple_quote)
                definition_column = _definition_column(code, symbol)
                for match in _IDENTIFIER_RE.finditer(code):
                    if match.group(0) != symbol:
                        continue
                    occurrences.append(
                        {
                            "file": relative,
                            "line": line_number,
                            "column": match.start() + 1,
                            "kind": (
                                "definition"
                                if definition_column == match.start()
                                else "reference"
                            ),
                            "context": line.strip()[:500],
                        }
                    )
                for column in _semantic_string_columns(semantic_code, symbol):
                    occurrences.append(
                        {
                            "file": relative,
                            "line": line_number,
                            "column": column + 1,
                            "kind": "reference",
                            "context": line.strip()[:500],
                        }
                    )

    occurrences.sort(
        key=lambda item: (
            item["kind"] != "definition",
            item["file"],
            item["line"],
            item["column"],
        )
    )

    definitions = sum(item["kind"] == "definition" for item in occurrences)
    references = len(occurrences) - definitions
    page_offset = max(0, int(offset))
    page_limit = max(1, min(int(limit), 1_000))
    return {
        "ok": True,
        "symbol": symbol,
        "occurrences": occurrences[page_offset : page_offset + page_limit],
        "definition_count": definitions,
        "reference_count": references,
        "unused": definitions > 0 and references == 0,
        "pagination": {
            "total": len(occurrences),
            "offset": page_offset,
            "limit": page_limit,
            "returned": len(occurrences[page_offset : page_offset + page_limit]),
        },
        "analysis": "renpy-aware-token-index",
    }


__all__ = ["find_references"]
