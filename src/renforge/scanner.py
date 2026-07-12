from __future__ import annotations

import os
import re
from typing import Any, Dict, List


LABEL_RE = re.compile(
    r"^\s*label\s+((?:[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)|(?:\.[A-Za-z_][\w]*))"
    r"\s*(?:\([^)]*\))?\s*:\s*(?:#.*)?$"
)
JUMP_RE = re.compile(r"^\s*jump\s+(.+?)\s*(?:#.*)?$")
CALL_RE = re.compile(r"^\s*call\s+(.+?)\s*(?:#.*)?$")
MENU_RE = re.compile(r"^\s*menu\b(.*):\s*(?:#.*)?$")
SCREEN_RE = re.compile(r"^\s*screen\s+[A-Za-z_][\w]*")
CHOICE_RE = re.compile(r"^\s*['\"](.+?)['\"]\s*:\s*(?:#.*)?$")
CHARACTER_RE = re.compile(
    r"^\s*define\s+([A-Za-z_][\w]*)\s*=\s*Character\(\s*([\"'])(.*?)\2"
)
DEFAULT_RE = re.compile(r"^\s*default\s+([A-Za-z_][\w]*)\s*=\s*.+")
ASSIGN_RE = re.compile(r"^\s*\$\s*([A-Za-z_][\w]*)\s*=\s*.+")
IMAGE_ASSIGN_RE = re.compile(
    r"^\s*define\s+([A-Za-z_][\w]*)\s*=\s*Image\(\s*(.+?)\s*\)\s*(?:#.*)?$"
)
IMAGE_LINE_RE = re.compile(r"^\s*image\s+(.+?)\s*=\s*(.+?)\s*(?:#.*)?$")


def _is_static_target(value: str) -> bool:
    return bool(re.fullmatch(r"(?:\.)?[A-Za-z_][\w]*(?:[./-][A-Za-z_][\w]*)*", value))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _relative_path(base: str, target: str) -> str:
    try:
        rel = os.path.relpath(target, base)
    except ValueError:
        return target
    # Keep reported paths POSIX-style on every platform.
    return rel.replace(os.sep, "/")


def _append_item(bucket: List[Dict[str, Any]], file: str, line_no: int, **fields: Any) -> None:
    item = {"file": file, "line": line_no}
    item.update(fields)
    bucket.append(item)


def scan_project(project_path: str) -> Dict[str, Any]:
    """
    Scanner léger pour le bootstrap: lit les .rpy et extrait une vue minimale:
    labels, jumps, calls, menus/choices, defines Character et images.

    Retour JSON-friendly: dict, listes et scalaires simples.
    """

    project_root = os.path.abspath(project_path)

    result: Dict[str, Any] = {
        "files": [],
        "variables": [],
        "graph": {"edges": []},
        "labels": [],
        "jumps": [],
        "calls": [],
        "menus": [],
        "characters": [],
        "images": [],
        "unresolved_targets": [],
    }

    if not os.path.isdir(project_root):
        return result

    game_root = os.path.join(project_root, "game")
    if not os.path.isdir(game_root):
        return result

    for root, _dirs, filenames in os.walk(game_root):
        for fname in sorted(filenames):
            if not fname.endswith(".rpy"):
                continue

            file_path = os.path.join(root, fname)
            rel_file = _relative_path(project_root, file_path)

            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as fp:
                    lines = fp.readlines()
            except Exception:
                result["files"].append({"file": rel_file, "line_count": 0})
                continue

            result["files"].append({"file": rel_file, "line_count": len(lines)})

            current_label = None
            current_global_label = None
            screen_indent: int | None = None

            current_menu: Dict[str, Any] | None = None
            menu_indent = 0

            for idx, raw_line in enumerate(lines, start=1):
                line = raw_line.rstrip("\n")
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                if current_menu is not None and _indent(line) <= menu_indent:
                    result["menus"].append(current_menu)
                    current_menu = None

                line_indent = _indent(line)
                if screen_indent is not None and line_indent <= screen_indent:
                    screen_indent = None
                if screen_indent is not None:
                    # Screen-language ``label`` is a displayable, not a
                    # story label.  It can contain dynamic expressions such
                    # as ``label h.who`` and must not inflate story coverage.
                    continue
                if SCREEN_RE.match(line):
                    screen_indent = line_indent
                    continue

                match = LABEL_RE.match(line)
                if match:
                    declared_name = match.group(1)
                    if declared_name.startswith(".") and current_global_label:
                        label_name = current_global_label + declared_name
                    else:
                        label_name = declared_name
                        if not declared_name.startswith("."):
                            current_global_label = declared_name
                    _append_item(result["labels"], rel_file, idx, name=label_name)
                    current_label = label_name
                    continue

                default_match = DEFAULT_RE.match(line)
                if default_match:
                    _append_item(
                        result["variables"],
                        rel_file,
                        idx,
                        name=default_match.group(1),
                        kind="default",
                    )
                    continue

                assign_match = ASSIGN_RE.match(line)
                if assign_match:
                    _append_item(
                        result["variables"],
                        rel_file,
                        idx,
                        name=assign_match.group(1),
                        kind="assignment",
                    )
                    continue

                match = JUMP_RE.match(line)
                if match:
                    target = match.group(1).strip()
                    if target.startswith(".") and current_global_label:
                        target = current_global_label + target
                    item_fields = {"target": target}
                    if current_label is not None:
                        item_fields["source"] = current_label

                    if _is_static_target(target):
                        item_fields["kind"] = "static"
                    else:
                        item_fields["kind"] = "dynamic"

                    _append_item(result["jumps"], rel_file, idx, **item_fields)
                    result["graph"]["edges"].append(
                        {
                            "kind": "jump",
                            "target": target,
                            "file": rel_file,
                            "line": idx,
                            **({"source": current_label} if current_label is not None else {}),
                        }
                    )
                    continue

                match = CALL_RE.match(line)
                if match:
                    target = match.group(1).strip()
                    if target.startswith(".") and current_global_label:
                        target = current_global_label + target
                    item_fields = {"target": target}
                    if current_label is not None:
                        item_fields["source"] = current_label

                    if _is_static_target(target):
                        item_fields["kind"] = "static"
                    else:
                        item_fields["kind"] = "dynamic"

                    _append_item(result["calls"], rel_file, idx, **item_fields)
                    result["graph"]["edges"].append(
                        {
                            "kind": "call",
                            "target": target,
                            "file": rel_file,
                            "line": idx,
                            **({"source": current_label} if current_label is not None else {}),
                        }
                    )
                    continue

                if current_menu is not None:
                    choice_match = CHOICE_RE.match(line)
                    if choice_match:
                        current_menu["choices"].append(choice_match.group(1))
                    continue

                menu_match = MENU_RE.match(line)
                if menu_match:
                    current_menu = {
                        "file": rel_file,
                        "line": idx,
                        "label": menu_match.group(1).strip() or "menu",
                        "choices": [],
                    }
                    menu_indent = _indent(line)
                    continue

                char_match = CHARACTER_RE.match(line)
                if char_match:
                    _append_item(
                        result["characters"],
                        rel_file,
                        idx,
                        name=char_match.group(1),
                        label=char_match.group(3),
                    )
                    continue

                img_match = IMAGE_ASSIGN_RE.match(line)
                if img_match:
                    _append_item(
                        result["images"],
                        rel_file,
                        idx,
                        name=img_match.group(1),
                        source=img_match.group(2),
                    )
                    continue

                img_line_match = IMAGE_LINE_RE.match(line)
                if img_line_match:
                    _append_item(
                        result["images"],
                        rel_file,
                        idx,
                        name=img_line_match.group(1).strip(),
                        source=img_line_match.group(2).strip(),
                    )
                    continue

            if current_menu is not None:
                result["menus"].append(current_menu)

    label_names = {item["name"] for item in result["labels"]}
    for edge in result["graph"]["edges"]:
        target = edge.get("target", "")
        if edge["kind"] in {"jump", "call"} and _is_static_target(target):
            if target not in label_names:
                result["unresolved_targets"].append(
                    {
                        "file": edge["file"],
                        "line": edge["line"],
                        "kind": edge["kind"],
                        "source": edge.get("source"),
                        "target": target,
                    }
                )

    return result
