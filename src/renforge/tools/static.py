"""Static inspection helpers for RenForge."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict

from ..lint import parse_lint_output
from ..scanner import scan_project


def inspect_project(project_path: str) -> Dict[str, Any]:
    project = Path(project_path).expanduser().resolve()
    project_key = str(project)

    markers = ("game", "game/options.rpy", "game/script.rpy")
    payload: Dict[str, Any] = {
        "project": project_key,
        "exists": project.exists(),
        "is_directory": project.is_dir(),
        "detected_markers": [],
        "top_level_files": [],
    }

    if not payload["exists"]:
        payload["errors"] = [f"Project path does not exist: {project_key}"]
        return payload

    if not payload["is_directory"]:
        payload["errors"] = [f"Project path is not a directory: {project_key}"]
        return payload

    payload["detected_markers"] = [
        marker
        for marker in markers
        if (project / marker).exists()
    ]
    payload["top_level_files"] = sorted(
        str(child.relative_to(project))
        for child in project.iterdir()
        if child.is_file()
    )
    return payload


def _matches_scan_filters(item: Any, *, file_glob: str, symbol: str) -> bool:
    if file_glob:
        if not isinstance(item, dict) or not fnmatch(str(item.get("file", "")), file_glob):
            return False
    if not symbol:
        return True

    expected = symbol.casefold()

    def _contains(value: Any) -> bool:
        if isinstance(value, str):
            return value.casefold() == expected
        if isinstance(value, list):
            return any(_contains(child) for child in value)
        if isinstance(value, dict):
            return any(_contains(child) for key, child in value.items() if key != "file")
        return False

    return _contains(item)


def scan_project_index(
    project_path: str,
    *,
    sections: list[str] | None = None,
    file_glob: str = "",
    symbol: str = "",
    offset: int = 0,
    limit: int = 200,
) -> Dict[str, Any]:
    result = scan_project(project_path)
    summary = {
        "label_count": len(result.get("labels", [])),
        "menu_count": len(result.get("menus", [])),
        "jump_count": len(result.get("jumps", [])),
        "call_count": len(result.get("calls", [])),
        "character_count": len(result.get("characters", [])),
        "image_count": len(result.get("images", [])),
    }
    available = tuple(result)
    selected = list(sections) if sections is not None else list(available)
    unknown = sorted(set(selected) - set(available))
    if unknown:
        raise ValueError(f"unknown scan sections: {', '.join(unknown)}")

    page_offset = max(0, int(offset))
    page_limit = max(1, min(int(limit), 1_000))
    response: Dict[str, Any] = {"summary": summary, "pagination": {}}
    for section in selected:
        value = result[section]
        if section == "graph":
            items = value.get("edges", []) if isinstance(value, dict) else []
            filtered = [
                item
                for item in items
                if _matches_scan_filters(item, file_glob=file_glob, symbol=symbol)
            ]
            page = filtered[page_offset : page_offset + page_limit]
            response[section] = {"edges": page}
        elif isinstance(value, list):
            filtered = [
                item
                for item in value
                if _matches_scan_filters(item, file_glob=file_glob, symbol=symbol)
            ]
            page = filtered[page_offset : page_offset + page_limit]
            response[section] = page
        else:
            response[section] = value
            continue
        response["pagination"][section] = {
            "total": len(filtered),
            "offset": page_offset,
            "limit": page_limit,
            "returned": len(page),
        }
    return response


def parse_lint_text(text: str) -> Dict[str, Any]:
    diagnostics = parse_lint_output(text)
    return {
        "diagnostics": diagnostics,
        "count": len(diagnostics),
    }
