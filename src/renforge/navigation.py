"""Shared Ren'Py navigation target resolution."""

from __future__ import annotations

from typing import Any

from .scanner import scan_project


def resolve_warp_target(project_root: str, target: str) -> dict[str, Any]:
    """Resolve a label or validate an explicit ``file:line`` warp target."""

    value = (target or "").strip()
    if not value:
        return {"ok": False, "error": "target is required"}

    if ":" in value:
        file_part, _, line_part = value.rpartition(":")
        if not file_part or not line_part.strip().isdigit():
            return {"ok": False, "error": "invalid warp target; expected file:line"}
        return {"ok": True, "target": f"{file_part}:{int(line_part.strip())}"}

    index = scan_project(project_root)
    labels = index.get("labels", [])
    matches = [label for label in labels if label.get("name") == value]
    if not matches and value.startswith("."):
        matches = [label for label in labels if str(label.get("name", "")).endswith(value)]
        if len(matches) > 1:
            candidates = ", ".join(str(label.get("name")) for label in matches[:20])
            return {"ok": False, "error": f"ambiguous local label {value}: {candidates}"}

    for label in matches:
        file = label.get("file")
        line = label.get("line")
        if not file or not line:
            return {"ok": False, "error": f"target '{value}' does not expose file and line"}
        return {"ok": True, "target": f"{file}:{int(line)}"}

    return {"ok": False, "error": f"unknown label: {value}"}


__all__ = ["resolve_warp_target"]
