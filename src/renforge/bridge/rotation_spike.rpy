# Opt-in spike resource for issue #48 — rotate-aware transform seam measurement.
# Injected only into temporary demo copies.

init 1600 python hide:
    import math
    import sys
    import types

    if "_renforge_runtime" not in sys.modules:
        sys.modules["_renforge_runtime"] = types.ModuleType("_renforge_runtime")
    _rt = sys.modules["_renforge_runtime"]
    if not hasattr(_rt, "rotation_spike"):
        _rt.rotation_spike = types.SimpleNamespace()
    _state = _rt.rotation_spike

    SCREEN = "renforge_editor_rotation_fixture"
    TARGET_IDS = ("rotation_target", "rotation_reference", "rotation_other")
    BACKUP_STYLE = {}


    def _walk_find_id(displayable, widget_id, seen=None):
        if displayable is None:
            return None
        if seen is None:
            seen = set()
        did = id(displayable)
        if did in seen:
            return None
        seen.add(did)

        if getattr(displayable, "id", None) == widget_id:
            return displayable

        children = []
        raw_children = getattr(displayable, "children", None)
        if raw_children is not None and not isinstance(raw_children, (str, bytes)):
            try:
                children.extend(list(raw_children))
            except Exception:
                pass
        for attr in ("child", "raw_child", "original_child"):
            child = getattr(displayable, attr, None)
            if child is not None:
                children.append(child)

        visit = getattr(displayable, "visit", None)
        if callable(visit):
            try:
                visited = visit()
                if isinstance(visited, (list, tuple)):
                    children.extend(list(visited))
            except Exception:
                pass

        for child in children:
            found = _walk_find_id(child, widget_id, seen)
            if found is not None:
                return found
        return None

    def _get_widget(widget_id):
        try:
            widget = renpy.get_widget(SCREEN, widget_id)
        except Exception:
            widget = None

        # Prefer authoritative tree walk; get_widget can return an inner text node.
        try:
            screen_root = renpy.display.screen.get_screen(SCREEN)
            walked = _walk_find_id(screen_root, widget_id)
            if walked is not None:
                widget = walked
        except Exception:
            pass

        # If we landed on nested text, move up to the button container.
        if widget is None:
            return None
        name = type(widget).__name__
        if name in ("Text", "TextBase"):
            parent = getattr(widget, "parent", None)
            for _ in range(8):
                if parent is None:
                    break
                parent_name = type(parent).__name__
                if parent_name in ("Button", "TextButton", "ImageButton", "Window"):
                    return parent
                parent = getattr(parent, "parent", None)
        return widget

    def _style_xy(displayable):
        style = getattr(displayable, "style", None)
        if style is None:
            return None, None
        try:
            xpos = getattr(style, "xpos", None)
            ypos = getattr(style, "ypos", None)
        except Exception:
            return None, None
        if isinstance(xpos, (int, float)) and isinstance(ypos, (int, float)):
            return float(xpos), float(ypos)
        return None, None

    def _render_size(displayable):
        if displayable is None:
            return None
        try:
            rendered = renpy.display.render.render(
                displayable,
                renpy.config.screen_width,
                renpy.config.screen_height,
                0,
                0,
            )
            if rendered is None:
                return None
            width, height = rendered.get_size()
            return [int(width), int(height)]
        except Exception:
            pass
        try:
            child_size = getattr(displayable, "child_size", None)
            if isinstance(child_size, (list, tuple)) and len(child_size) >= 2:
                return [int(child_size[0]), int(child_size[1])]
        except Exception:
            pass
        return None

    def _matrix_map(transform_fn, point):
        if transform_fn is None:
            return None
        x, y = float(point[0]), float(point[1])
        try:
            if hasattr(transform_fn, "transform"):
                mapped = transform_fn.transform(x, y)
            elif callable(transform_fn):
                mapped = transform_fn(x, y)
            else:
                return None
        except Exception:
            return None
        if mapped is None:
            return None
        try:
            return [float(mapped[0]), float(mapped[1])]
        except Exception:
            return None

    def _find_transform(displayable):
        def _as_candidates(node):
            values = []
            for attr in ("at", "transform", "_transform", "child_transform", "transformation", "transforms"):
                value = getattr(node, attr, None)
                if value is None:
                    continue
                if isinstance(value, (list, tuple)):
                    values.extend(value)
                else:
                    values.append(value)
            return values

        # Try child chain first (Transform may sit between wrapper nodes).
        current = displayable
        seen = set()
        for _ in range(16):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            for candidate in _as_candidates(current):
                if candidate is None:
                    continue
                if getattr(candidate, "forward", None) is not None or getattr(candidate, "reverse", None) is not None:
                    return candidate
            if getattr(current, "forward", None) is not None or getattr(current, "reverse", None) is not None:
                return current
            name = type(current).__name__
            if name in ("Transform", "ATLTransform", "Motion", "TransformBase"):
                return current
            next_child = getattr(current, "child", None)
            if next_child is None:
                next_child = getattr(current, "raw_child", None)
            if next_child is None:
                next_child = getattr(current, "original_child", None)
            current = next_child

        # Fallback to parent walk.
        current = displayable
        seen = set()
        for _ in range(8):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            parent = getattr(current, "parent", None)
            if parent is None:
                break
            for candidate in _as_candidates(parent):
                if candidate is None:
                    continue
                if getattr(candidate, "forward", None) is not None or getattr(candidate, "reverse", None) is not None:
                    return candidate
            if getattr(parent, "forward", None) is not None or getattr(parent, "reverse", None) is not None:
                return parent
            if type(parent).__name__ in ("Transform", "ATLTransform", "Motion", "TransformBase"):
                return parent
            current = parent
        return None

    def _screen_quad_from_local(local_quad, transform_displayable):
        # Most transforms map local child coordinates to screen coordinates through
        # one of seams (forward/reverse). Use only seam output; never authored
        # rotation math.
        forward = getattr(transform_displayable, "forward", None)
        for seam in ("forward", "reverse"):
            seam_fn = getattr(transform_displayable, seam, None)
            if seam_fn is None:
                continue
            projected = []
            for point in local_quad:
                mapped = _matrix_map(seam_fn, point)
                if mapped is None:
                    return None, seam, None, "transform_map_failed"
                projected.append(mapped)
            return projected, seam, None, None
        # Transform exists but no seam callable.
        return None, "transform", None, "transform_seam_unavailable"

    def _roundtrip_error(transform_displayable, local_quad, projected_quad):
        reverse_fn = getattr(transform_displayable, "reverse", None)
        if reverse_fn is None:
            return None
        recovered = []
        for point in projected_quad:
            mapped = _matrix_map(reverse_fn, point)
            if mapped is None:
                return None
            recovered.append(mapped)
        if len(recovered) != len(local_quad):
            return None

        errors = []
        for recovered_point, source_point in zip(recovered, local_quad):
            dx = float(recovered_point[0]) - float(source_point[0])
            dy = float(recovered_point[1]) - float(source_point[1])
            errors.append(math.hypot(dx, dy))
        return float(max(errors)) if errors else None

    def _measure_widget(widget_id):
        widget = _get_widget(widget_id)
        record = {
            "widget_id": widget_id,
            "found": widget is not None,
            "found_by": None,
            "quad": None,
            "transform_present": False,
            "roundtrip_error": None,
            "notes": "widget_missing",
            "quad_source": None,
            "aabb": None,
            "render_size": None,
            "style_pos": None,
            "type_name": None,
            "has_forward": False,
            "has_reverse": False,
        }
        if widget is None:
            return record

        record["type_name"] = type(widget).__name__
        xpos, ypos = _style_xy(widget)
        size = _render_size(widget)
        if size is None:
            size = [0, 0]

        if xpos is not None and ypos is not None and size[0] > 0 and size[1] > 0:
            record["style_pos"] = [int(round(xpos)), int(round(ypos))]
            record["aabb"] = [
                int(round(xpos)),
                int(round(ypos)),
                int(size[0]),
                int(size[1]),
            ]
        else:
            record["notes"] = "geometry_unavailable"
            return record

        # Force a render first, so ATL/Transform seams are available.
        try:
            screen_root = renpy.display.screen.get_screen(SCREEN)
            if screen_root is not None:
                renpy.display.render.invalidate(screen_root)
                renpy.display.render.render(
                    screen_root,
                    renpy.config.screen_width,
                    renpy.config.screen_height,
                    0,
                    0,
                )
        except Exception:
            pass

        render_size = _render_size(widget)
        if render_size and render_size[0] > 0 and render_size[1] > 0:
            size = [int(render_size[0]), int(render_size[1])]

        transform_d = _find_transform(widget)
        if transform_d is not None:
            record["transform_present"] = True
            record["type_name"] = type(transform_d).__name__
            record["has_forward"] = getattr(transform_d, "forward", None) is not None
            record["has_reverse"] = getattr(transform_d, "reverse", None) is not None

        if not record["transform_present"]:
            record["notes"] = "transform_missing"
            return record

        # Use runtime matrix seams only.
        child_size = getattr(transform_d, "child_size", None)
        if isinstance(child_size, (list, tuple)) and len(child_size) >= 2:
            w = int(child_size[0])
            h = int(child_size[1])
        else:
            w, h = int(size[0]), int(size[1])
        if w <= 0 or h <= 0:
            record["notes"] = "transform_child_size_unavailable"
            return record

        local_quad = (
            [0.0, 0.0],
            [float(w), 0.0],
            [float(w), float(h)],
            [0.0, float(h)],
        )
        projected, seam_name, _, seam_error = _screen_quad_from_local(local_quad, transform_d)
        if projected is None:
            record["notes"] = seam_error or "transform_matrix_unavailable"
            return record

        record["quad"] = projected
        record["quad_source"] = seam_name
        record["notes"] = "transform_rotation"
        record["roundtrip_error"] = _roundtrip_error(transform_d, local_quad, projected)
        if record["roundtrip_error"] is not None:
            try:
                if float(record["roundtrip_error"]) > 0.5:
                    record["notes"] = "transform_rotation_roundtrip_imperfect"
            except Exception:
                pass
        return record

    def _backup_style(widget):
        style = getattr(widget, "style", None)
        if style is None:
            return None
        try:
            return {
                "xpos": getattr(style, "xpos", 0),
                "ypos": getattr(style, "ypos", 0),
                "alpha": getattr(style, "alpha", 1.0),
                "visible": getattr(style, "visible", True),
            }
        except Exception:
            return None

    def _apply_hidden(widget):
        style = getattr(widget, "style", None)
        if style is None:
            return False
        try:
            style.xpos = -4000
            style.ypos = -4000
            try:
                style.alpha = 0.0
            except Exception:
                pass
            try:
                style.visible = False
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _apply_backup(widget, backup):
        style = getattr(widget, "style", None)
        if style is None or not isinstance(backup, dict):
            return False
        try:
            if "xpos" in backup:
                style.xpos = backup["xpos"]
            if "ypos" in backup:
                style.ypos = backup["ypos"]
            if "alpha" in backup:
                try:
                    style.alpha = backup["alpha"]
                except Exception:
                    pass
            if "visible" in backup:
                try:
                    style.visible = backup["visible"]
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def _handle_measure(payload):
        request_ids = payload.get("target_ids") if isinstance(payload, dict) else None
        if not isinstance(request_ids, list) or not request_ids:
            request_ids = list(TARGET_IDS)
        geometry = {}
        for item in request_ids:
            geometry[str(item)] = _measure_widget(str(item))
        return {"ok": True, "geometry": geometry}

    def _handle_set_isolation(payload):
        target_id = None
        if isinstance(payload, dict):
            target_id = payload.get("target_id", payload.get("widget_id"))
        elif isinstance(payload, str):
            target_id = payload
        if target_id is None:
            target_id = "all"
        if target_id == "all":
            _state.selected = "all"
            for wid in TARGET_IDS:
                widget = _get_widget(wid)
                if widget is None:
                    continue
                _apply_backup(widget, BACKUP_STYLE.get(wid) or {})
                try:
                    renpy.display.render.invalidate(widget)
                except Exception:
                    pass
            renpy.restart_interaction()
            return {"ok": True, "target_id": "all"}

        if target_id not in TARGET_IDS:
            return {"ok": False, "error": "unknown_target_id", "target_id": target_id}
        _state.selected = str(target_id)
        if not BACKUP_STYLE:
            for wid in TARGET_IDS:
                candidate = _get_widget(wid)
                BACKUP_STYLE[wid] = _backup_style(candidate)

        failed = []
        for wid in TARGET_IDS:
            candidate = _get_widget(wid)
            if candidate is None:
                failed.append(wid)
                continue
            if wid == target_id:
                if not _apply_backup(candidate, BACKUP_STYLE.get(wid) or {}):
                    failed.append(wid)
            else:
                if not _apply_hidden(candidate):
                    failed.append(wid)
            try:
                renpy.display.render.invalidate(candidate)
            except Exception:
                pass

        renpy.restart_interaction()
        return {"ok": len(failed) == 0, "target_id": target_id, "failed": failed}

    def _handle_restore(payload):
        if not BACKUP_STYLE:
            for wid in TARGET_IDS:
                widget = _get_widget(wid)
                BACKUP_STYLE[wid] = _backup_style(widget)
        restored = []
        for wid in TARGET_IDS:
            widget = _get_widget(wid)
            if widget is None:
                continue
            if _apply_backup(widget, BACKUP_STYLE.get(wid) or {}):
                restored.append(wid)
                try:
                    renpy.display.render.invalidate(widget)
                except Exception:
                    pass
        renpy.restart_interaction()
        return {"ok": True, "restored": restored}

    handlers = globals().get("_RENFORGE_HANDLERS")
    if isinstance(handlers, dict):
        handlers["rotation_spike_measure"] = _handle_measure
        handlers["rotation_spike_set_isolation"] = _handle_set_isolation
        handlers["rotation_spike_restore"] = _handle_restore
