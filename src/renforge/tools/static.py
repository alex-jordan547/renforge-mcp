"""Static inspection helpers for RenForge."""

from __future__ import annotations

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


def scan_project_index(project_path: str) -> Dict[str, Any]:
    result = scan_project(project_path)
    summary = {
        "label_count": len(result.get("labels", [])),
        "menu_count": len(result.get("menus", [])),
        "jump_count": len(result.get("jumps", [])),
        "call_count": len(result.get("calls", [])),
        "character_count": len(result.get("characters", [])),
        "image_count": len(result.get("images", [])),
    }
    result["summary"] = summary
    return result


def parse_lint_text(text: str) -> Dict[str, Any]:
    diagnostics = parse_lint_output(text)
    return {
        "diagnostics": diagnostics,
        "count": len(diagnostics),
    }
