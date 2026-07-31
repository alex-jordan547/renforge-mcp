"""Structural diffs for Ren'Py scene snapshots."""

from __future__ import annotations

from typing import Any


def _compact_node(node: dict[str, Any]) -> dict[str, Any]:
    """Return the stable, compact representation used for additions/removals."""
    return {
        "id": node.get("id"),
        "type": node.get("type"),
        "bounds": node.get("bounds"),
    }


def _usable_bounds(node: dict[str, Any]) -> dict[str, Any] | None:
    """Return bounds only when the snapshot says they are measurable."""
    if not node.get("bounds_available", True):
        return None
    bounds = node.get("bounds")
    if not isinstance(bounds, dict):
        return None
    return bounds


def diff_scenes(
    before: dict[str, Any], after: dict[str, Any], *, move_threshold: int = 1
) -> dict[str, Any]:
    """Compare two scene snapshots by node id.

    Position and size changes are reported only when their respective deltas are
    strictly greater than ``move_threshold``. Other structural properties are
    compared exactly when present (or ``None`` when absent).
    """
    before_by_id = {node["id"]: node for node in before.get("nodes", [])}
    after_by_id = {node["id"]: node for node in after.get("nodes", [])}

    added = [
        _compact_node(after_by_id[node_id])
        for node_id in sorted(set(after_by_id) - set(before_by_id))
    ]
    removed = [
        _compact_node(before_by_id[node_id])
        for node_id in sorted(set(before_by_id) - set(after_by_id))
    ]

    changed: list[dict[str, Any]] = []
    unchanged = 0
    for node_id in sorted(set(before_by_id) & set(after_by_id)):
        old = before_by_id[node_id]
        new = after_by_id[node_id]
        node_changes: dict[str, Any] = {}

        old_bounds = _usable_bounds(old)
        new_bounds = _usable_bounds(new)
        if old_bounds is not None and new_bounds is not None:
            dx = new_bounds.get("x", 0) - old_bounds.get("x", 0)
            dy = new_bounds.get("y", 0) - old_bounds.get("y", 0)
            if abs(dx) > move_threshold or abs(dy) > move_threshold:
                node_changes["moved"] = {"dx": dx, "dy": dy}

            dw = new_bounds.get("width", 0) - old_bounds.get("width", 0)
            dh = new_bounds.get("height", 0) - old_bounds.get("height", 0)
            if abs(dw) > move_threshold or abs(dh) > move_threshold:
                node_changes["resized"] = {"dw": dw, "dh": dh}

        old_text = old.get("text")
        new_text = new.get("text")
        if old_text != new_text:
            node_changes["text_changed"] = {"from": old_text, "to": new_text}

        old_color = (old.get("color") or {}).get("dominant")
        new_color = (new.get("color") or {}).get("dominant")
        if old_color != new_color:
            node_changes["color_changed"] = {"from": old_color, "to": new_color}

        old_zorder = old.get("zorder")
        new_zorder = new.get("zorder")
        if old_zorder != new_zorder:
            node_changes["z_changed"] = {"from": old_zorder, "to": new_zorder}

        if not node_changes:
            unchanged += 1
            continue

        changed.append(
            {
                "id": node_id,
                "changes": sorted(node_changes),
                **{kind: node_changes[kind] for kind in sorted(node_changes)},
            }
        )

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
    }
