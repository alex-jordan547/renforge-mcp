from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from ..dump import run_native_dump, normalize_definitions
from ..navigation import resolve_warp_target
from ..project import RenpyProject
from ..sdk import get_or_install_sdk
from ..scanner import scan_project


_STORY_MAP_CACHE: dict[Path, tuple[tuple[Any, ...], dict[str, Any]]] = {}


def _normalize_autopilot(project_root: Path) -> dict:
    path = project_root / ".renforge" / "autopilot.json"
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    return payload if isinstance(payload, dict) else {}


def _label_node_id(name: str) -> str:
    return f"label:{name}"


def resolve_game_file_path(
    project_root: str | Path,
    requested_path: str,
    *,
    max_bytes: int = 200_000,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    game_root = root / "game"

    if not isinstance(requested_path, str) or not requested_path.strip():
        return {"ok": False, "error": "path is required"}

    original = Path(requested_path)
    if original.is_absolute():
        return {"ok": False, "error": "path must be relative to game/"}

    normalized = requested_path.replace("\\", "/").lstrip("/")
    if normalized.startswith("game/"):
        normalized = normalized[len("game/") :]
    elif normalized == "game":
        return {"ok": False, "error": "path is required to point to a file inside game/"}

    candidate = game_root / normalized
    try:
        target = candidate.resolve()
    except OSError as exc:
        return {"ok": False, "error": f"invalid path: {type(exc).__name__}: {exc}"}

    try:
        if not target.is_relative_to(game_root):
            return {"ok": False, "error": "path must be inside game/"}
    except Exception:
        if str(target).startswith(str(game_root)):
            pass
        else:
            return {"ok": False, "error": "path must be inside game/"}

    if not target.is_file():
        return {"ok": False, "error": f"path does not point to a file: {target}"}

    try:
        size = target.stat().st_size
    except Exception as exc:
        return {"ok": False, "error": f"cannot stat file: {type(exc).__name__}: {exc}"}

    if size > max_bytes:
        return {"ok": False, "error": f"file is too large ({size} bytes > {max_bytes})"}

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "error": f"cannot read file: {type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "path": f"game/{normalized}",
        "text": text,
    }


def _story_map_signature(project_root: Path) -> tuple[tuple[Any, ...], tuple[int, int]]:
    game_root = project_root / "game"
    entries: list[tuple[str, int, int]] = []
    if game_root.exists():
        for file_path in sorted(game_root.rglob("*.rpy")):
            try:
                stats = file_path.stat()
            except OSError:
                continue
            try:
                relative = str(file_path.relative_to(project_root))
            except ValueError:
                continue
            entries.append((relative, stats.st_mtime_ns, stats.st_size))

    autopilot = project_root / ".renforge" / "autopilot.json"
    try:
        autopilot_stats = autopilot.stat()
        autopilot_signature = (autopilot_stats.st_mtime_ns, autopilot_stats.st_size)
    except OSError:
        autopilot_signature = (0, 0)

    return (tuple(entries), autopilot_signature)


def build_story_map(project_root: str) -> dict:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        return {"ok": False, "error": f"Project root does not exist: {root}", "nodes": [], "edges": []}

    signature = _story_map_signature(root)
    cached = _STORY_MAP_CACHE.get(root)
    if cached is not None and cached[0] == signature:
        return copy.deepcopy(cached[1])

    index = scan_project(str(root))
    nodes: list[dict] = []
    edges: list[dict] = []

    labels = {label["name"]: label for label in index.get("labels", []) if label.get("name")}
    discovered = set(labels.keys())
    autopilot = _normalize_autopilot(root)
    covered = set(autopilot.get("labels_covered", []))

    native_locations: dict[str, dict] = {}
    try:
        project = RenpyProject(root)
        sdk = get_or_install_sdk(project_root=project.abs_root)
        raw_dump = run_native_dump(sdk, project)
        for definition in normalize_definitions(raw_dump):
            if definition.get("kind") == "label" and definition.get("name"):
                native_locations[definition["name"]] = definition
    except Exception:
        native_locations = {}

    for name, item in labels.items():
        node_data = {
            "label": name,
            "type": "label",
            "covered": name in covered,
            "name": name,
        }
        if item.get("file"):
            node_data["file"] = item.get("file")
        if item.get("line"):
            node_data["line"] = item.get("line")

        node = {
            "id": _label_node_id(name),
            "label": name,
            "data": node_data,
        }
        native = native_locations.get(name)
        if native:
            if native.get("file"):
                node["data"]["file"] = native.get("file")
            if native.get("line"):
                node["data"]["line"] = native.get("line")
        nodes.append(node)

    for edge in index.get("graph", {}).get("edges", []):
        source = edge.get("source") or "_entry"
        target = edge.get("target")

        if target and target not in discovered and edge.get("kind") in {"jump", "call"}:
            discovered.add(target)
            edge_type = edge.get("kind") or "jump"
            discovered_data = {
                "label": target,
                "type": edge_type,
                "covered": False,
                "name": target,
            }
            nodes.append(
                {
                    "id": _label_node_id(target),
                    "label": target,
                    "data": discovered_data,
                }
            )

        edge_type = edge.get("kind") or "jump"
        edges.append(
            {
                "id": f"{source}:{edge.get('kind')}:{target}:{edge.get('line', 0)}",
                "source": _label_node_id(source) if source else _label_node_id("_entry"),
                "target": _label_node_id(target) if target else _label_node_id("_entry"),
                "label": str(edge_type),
                "type": edge_type,
            }
        )

    payload = {
        "ok": True,
        "project": str(root),
        "nodes": nodes,
        "edges": edges,
        "autopilot": autopilot,
        "coverage": {"covered": sorted(covered), "total": len(nodes)},
    }
    return _cache_story_map(root, signature, payload)


def _cache_story_map(project_root: Path, signature: tuple[Any, ...], payload: dict[str, Any]) -> dict[str, Any]:
    _STORY_MAP_CACHE[project_root] = (signature, copy.deepcopy(payload))
    return copy.deepcopy(payload)
