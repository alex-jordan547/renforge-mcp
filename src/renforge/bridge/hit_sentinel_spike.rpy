# Opt-in spike resource for issue #43 — non-focusable hit regions.
# Injected beside the bridge; registers RPC handlers; no production editor UI.
#
# Hard requirements vs Codex P1:
# - isolation masks are candidate-only (siblings alpha=0), not full-scene colour GT
# - rotated quads come from Ren'Py Transform reverse seam, not authored rotate metadata
# - missing transform seam is reported so the host can block

init 1600 python hide:
    import math
    import sys
    import time
    import types

    if "_renforge_runtime" not in sys.modules:
        sys.modules["_renforge_runtime"] = types.ModuleType("_renforge_runtime")
    _rt = sys.modules["_renforge_runtime"]
    if not hasattr(_rt, "hit_sentinel"):
        _rt.hit_sentinel = types.SimpleNamespace(
            screen="renforge_hit_sentinel_fixture",
            style_backup={},
        )

    SCREEN = "renforge_hit_sentinel_fixture"
    STATE = _rt.hit_sentinel

    TARGET_COLOURS = {
        "hit_add": (0xE5, 0x2E, 0x2E),
        "hit_text": (0x2E, 0xE5, 0xA0),
        "hit_frame": (0x2E, 0x6B, 0xE5),
        "hit_rotated": (0xE5, 0xC8, 0x2E),
        "hit_clipped_child": (0xC8, 0x2E, 0xE5),
        "hit_viewport_child": (0x2E, 0xE5, 0xE5),
        "hit_focusable": (0x88, 0x88, 0x88),
    }

    # Isolation targets (visibility toggled). Containers stay visible for clip/viewport.
    ISOLATION_IDS = (
        "hit_add",
        "hit_text",
        "hit_frame",
        "hit_rotated",
        "hit_clipped_child",
        "hit_viewport_child",
        "hit_focusable",
    )

    TARGET_META = {
        "hit_add": {"kind": "add", "focusable": False},
        "hit_text": {"kind": "text", "focusable": False},
        "hit_frame": {"kind": "frame", "focusable": False},
        "hit_rotated": {"kind": "add", "focusable": False, "expects_transform": True},
        "hit_clipped_child": {
            "kind": "add",
            "focusable": False,
            "clip_parent": "hit_clip_parent",
        },
        "hit_viewport_child": {
            "kind": "add",
            "focusable": False,
            "viewport": "hit_viewport",
        },
        "hit_focusable": {"kind": "textbutton", "focusable": True},
    }

    AUTHORED_FALLBACK = {
        "hit_add": (80, 80, 160, 100),
        "hit_text": (280, 100, 200, 40),
        "hit_frame": (80, 220, 180, 100),
        "hit_rotated": (320, 240, 140, 80),
        "hit_clipped_child": (520, 80, 200, 80),
        "hit_viewport_child": (540, 260, 120, 60),
        "hit_focusable": (80, 360, 160, 48),
        "hit_clip_parent": (520, 80, 100, 80),
        "hit_viewport": (520, 220, 160, 100),
    }

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
        raw = getattr(displayable, "children", None)
        if raw is not None and not isinstance(raw, (str, bytes)):
            try:
                children.extend(list(raw))
            except Exception:
                pass
        for attr in ("child", "raw_child", "original_child", "viewport"):
            c = getattr(displayable, attr, None)
            if c is not None:
                children.append(c)
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
        # Prefer a tree walk from the screen root — get_widget often returns the
        # inner Text of a textbutton rather than the Button.
        try:
            screen_d = renpy.display.screen.get_screen(SCREEN)
            walked = _walk_find_id(screen_d, widget_id)
            if walked is not None:
                widget = walked
        except Exception:
            pass
        if widget is None:
            return None
        name = type(widget).__name__
        if name in ("Text", "TextBase"):
            parent = getattr(widget, "parent", None)
            for _ in range(6):
                if parent is None:
                    break
                pname = type(parent).__name__
                if pname in ("Button", "TextButton", "ImageButton", "Window"):
                    return parent
                parent = getattr(parent, "parent", None)
        return widget

    def _number(value):
        try:
            return float(value)
        except Exception:
            return None

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
        except Exception:
            return None
        if rendered is None:
            return None
        try:
            width, height = rendered.get_size()
            return [int(width), int(height)]
        except Exception:
            width = getattr(rendered, "width", None)
            height = getattr(rendered, "height", None)
            if width is None or height is None:
                return None
            return [int(width), int(height)]

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

    def _focus_list_ids():
        ids = []
        try:
            focus_list = list(getattr(renpy.display.focus, "focus_list", []) or [])
        except Exception:
            return ids
        for entry in focus_list:
            widget = getattr(entry, "widget", None)
            if widget is None and isinstance(entry, (list, tuple)) and entry:
                widget = entry[0]
            widget_id = getattr(widget, "id", None) if widget is not None else None
            if widget_id:
                ids.append(str(widget_id))
            # Also accept focus.widget_id if present.
            wid = getattr(entry, "widget_id", None)
            if wid:
                ids.append(str(wid))
        return ids

    def _find_transform(displayable):
        """Walk child/parent chain for a displayable with matrix seams or Transform type."""
        current = displayable
        seen = set()
        for _ in range(16):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            name = type(current).__name__
            if getattr(current, "reverse", None) is not None or getattr(current, "forward", None) is not None:
                return current
            if name in ("Transform", "ATLTransform", "Motion", "TransformBase"):
                return current
            next_d = getattr(current, "child", None)
            if next_d is None:
                next_d = getattr(current, "raw_child", None)
            if next_d is None:
                next_d = getattr(current, "original_child", None)
            current = next_d
        # Parent walk (some widgets hang under an ATL Transform).
        current = displayable
        seen = set()
        for _ in range(8):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            parent = getattr(current, "parent", None)
            if parent is None:
                break
            name = type(parent).__name__
            if getattr(parent, "reverse", None) is not None or getattr(parent, "forward", None) is not None:
                return parent
            if name in ("Transform", "ATLTransform", "Motion", "TransformBase"):
                return parent
            current = parent
        return None

    def _matrix_map(mat, point_xy):
        if mat is None:
            return None
        x, y = float(point_xy[0]), float(point_xy[1])
        try:
            if hasattr(mat, "transform"):
                mapped = mat.transform(x, y)
            elif callable(mat):
                mapped = mat(x, y)
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

    def _transform_map_point(transform_d, point_xy):
        """Map local child corner through runtime Transform matrix seams.

        Prefer ``forward`` (child→parent). Fall back to ``reverse`` if needed.
        Never uses authored rotate metadata.
        """
        for attr in ("forward", "reverse"):
            mat = getattr(transform_d, attr, None)
            mapped = _matrix_map(mat, point_xy)
            if mapped is not None:
                return mapped, attr
        return None, "transform_matrix_unavailable"

    def _runtime_local_quad(displayable, size, expects_transform):
        """Return (quad_local, seam_name, error_or_None).

        For Transform-backed displayables, corners come from reverse·child_size.
        For plain displayables, local AABB corners. Hard-coded rotate metadata is never used.
        """
        transform_d = _find_transform(displayable)
        if transform_d is not None:
            child_size = getattr(transform_d, "child_size", None)
            if not isinstance(child_size, (list, tuple)) or len(child_size) < 2:
                # Fall back to render size of the transform target.
                cs = _render_size(getattr(transform_d, "child", None) or displayable)
                if cs is None:
                    if expects_transform:
                        return None, "transform_reverse", "transform_child_size_unavailable"
                    child_size = size
                else:
                    child_size = cs
            cw = _number(child_size[0])
            ch = _number(child_size[1])
            if cw is None or ch is None or cw <= 0 or ch <= 0:
                if expects_transform:
                    return None, "transform_reverse", "transform_child_size_invalid"
                cw, ch = float(size[0]), float(size[1])
            local_points = (
                (0.0, 0.0),
                (cw, 0.0),
                (cw, ch),
                (0.0, ch),
            )
            corners = []
            seam_used = None
            for pt in local_points:
                mapped, seam_or_err = _transform_map_point(transform_d, pt)
                if mapped is None:
                    if expects_transform:
                        return None, "transform_matrix", seam_or_err or "transform_map_failed"
                    corners = None
                    break
                seam_used = seam_or_err
                corners.append(mapped)
            if corners is not None and len(corners) == 4:
                return corners, "transform_%s" % (seam_used or "matrix"), None

        if expects_transform:
            return None, "transform_matrix", "transform_seam_unavailable"

        # Axis-aligned local box from measured render size.
        w = float(size[0])
        h = float(size[1])
        return [
            [0.0, 0.0],
            [w, 0.0],
            [w, h],
            [0.0, h],
        ], "aabb_local", None

    def _screen_quad(local_quad, origin_xy):
        ox, oy = float(origin_xy[0]), float(origin_xy[1])
        return [[ox + p[0], oy + p[1]] for p in local_quad]

    def _clip_rect_for(meta, geometry_map):
        parent_id = meta.get("clip_parent")
        if not parent_id:
            return None
        parent = geometry_map.get(parent_id)
        if parent and parent.get("aabb"):
            aabb = parent["aabb"]
            return [int(aabb[0]), int(aabb[1]), int(aabb[2]), int(aabb[3])]
        widget = _get_widget(parent_id)
        if widget is None:
            return None
        xpos, ypos = _style_xy(widget)
        size = _render_size(widget)
        if xpos is None or ypos is None or size is None:
            if parent_id in AUTHORED_FALLBACK:
                ax, ay, aw, ah = AUTHORED_FALLBACK[parent_id]
                return [ax, ay, aw, ah]
            return None
        return [int(xpos), int(ypos), int(size[0]), int(size[1])]

    def _measure_target(widget_id, meta):
        widget = _get_widget(widget_id)
        record = {
            "widget_id": widget_id,
            "found": widget is not None,
            "kind": meta.get("kind"),
            "focusable": bool(meta.get("focusable")),
            "expects_transform": bool(meta.get("expects_transform")),
            "colour_rgb": list(TARGET_COLOURS.get(widget_id) or []),
            "in_focus_list": widget_id in _focus_list_ids(),
            "aabb": None,
            "quad": None,
            "quad_seam": None,
            "quad_error": None,
            "render_size": None,
            "style_pos": None,
            "type_name": None,
            "errors": [],
        }
        if widget is None:
            record["errors"].append("widget_not_found")
            return record

        record["type_name"] = type(widget).__name__
        xpos, ypos = _style_xy(widget)
        size = _render_size(widget)
        record["render_size"] = size
        if xpos is not None and ypos is not None:
            record["style_pos"] = [float(xpos), float(ypos)]
        else:
            record["errors"].append("style_pos_unavailable")

        if xpos is None or ypos is None or size is None:
            if widget_id in AUTHORED_FALLBACK:
                ax, ay, aw, ah = AUTHORED_FALLBACK[widget_id]
                xpos, ypos = float(ax), float(ay)
                size = [aw, ah]
                record["errors"].append("geometry_used_authored_fallback")
            else:
                record["errors"].append("geometry_unavailable")
                return record

        w, h = int(size[0]), int(size[1])
        x, y = float(xpos), float(ypos)
        if (x, y) == (0.0, 0.0) and widget_id in AUTHORED_FALLBACK:
            ax, ay, aw, ah = AUTHORED_FALLBACK[widget_id]
            x, y = float(ax), float(ay)
            if w <= 0 or h <= 0:
                w, h = int(aw), int(ah)
            record["errors"].append("geometry_zero_pos_used_authored")

        record["aabb"] = [int(round(x)), int(round(y)), w, h]

        # Force a full screen render so ATL Transform matrices are populated.
        try:
            screen_d = renpy.display.screen.get_screen(SCREEN)
            if screen_d is not None:
                renpy.display.render.invalidate(screen_d)
                renpy.display.render.render(
                    screen_d,
                    renpy.config.screen_width,
                    renpy.config.screen_height,
                    0,
                    0,
                )
            renpy.display.render.invalidate(widget)
            renpy.display.render.render(
                widget,
                renpy.config.screen_width,
                renpy.config.screen_height,
                0,
                0,
            )
        except Exception:
            pass

        # Re-fetch widget after render (ATL may wrap it).
        widget2 = _get_widget(widget_id) or widget
        record["type_name"] = type(widget2).__name__
        transform_probe = _find_transform(widget2)
        record["transform_type"] = (
            type(transform_probe).__name__ if transform_probe is not None else None
        )
        record["has_forward"] = bool(
            transform_probe is not None and getattr(transform_probe, "forward", None) is not None
        )
        record["has_reverse"] = bool(
            transform_probe is not None and getattr(transform_probe, "reverse", None) is not None
        )

        local_quad, seam, err = _runtime_local_quad(
            widget2,
            [w, h],
            bool(meta.get("expects_transform")),
        )
        record["quad_seam"] = seam
        if err:
            record["quad_error"] = err
            record["errors"].append(err)
        if local_quad is not None:
            mean_x = sum(p[0] for p in local_quad) / 4.0
            mean_y = sum(p[1] for p in local_quad) / 4.0
            # Pure local boxes centre near (w/2, h/2). Transform matrices may
            # already emit screen-ish coordinates near the placed centre.
            local_like = abs(mean_x - (w / 2.0)) <= max(2.0, w * 0.05) and abs(
                mean_y - (h / 2.0)
            ) <= max(2.0, h * 0.05)
            screen_like = abs(mean_x - (x + w / 2.0)) <= max(8.0, w * 0.5) and abs(
                mean_y - (y + h / 2.0)
            ) <= max(8.0, h * 0.5)
            if local_like or not screen_like:
                record["quad"] = _screen_quad(local_quad, (x, y))
                record["quad_space"] = "local_plus_origin"
            else:
                record["quad"] = [[float(p[0]), float(p[1])] for p in local_quad]
                record["quad_space"] = "screenish"
        return record

    def _backup_style(widget):
        style = getattr(widget, "style", None)
        if style is None:
            return None
        try:
            return {
                "xpos": getattr(style, "xpos", None),
                "ypos": getattr(style, "ypos", None),
                "alpha": getattr(style, "alpha", 1.0),
                "visible": getattr(style, "visible", True),
            }
        except Exception:
            return None

    def _apply_hidden(widget, hidden):
        """Hide a sibling by parking it off-screen (alpha alone is unreliable)."""
        style = getattr(widget, "style", None)
        if style is None:
            return False
        try:
            if hidden:
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

    def _h_prepare(payload):
        started = time.monotonic()
        renpy.show_screen(SCREEN, _layer="screens")
        renpy.restart_interaction()
        elapsed = (time.monotonic() - started) * 1000.0
        return {
            "ok": True,
            "screen": SCREEN,
            "show_ms": elapsed,
            "target_ids": list(TARGET_META.keys()),
            "isolation_ids": list(ISOLATION_IDS),
            "colours": {k: list(v) for k, v in TARGET_COLOURS.items()},
        }

    def _h_geometry(payload):
        started = time.monotonic()
        geometry = {}
        for extra_id in ("hit_clip_parent", "hit_viewport"):
            geometry[extra_id] = _measure_target(
                extra_id,
                {"kind": "container", "focusable": False},
            )
        for widget_id, meta in TARGET_META.items():
            geometry[widget_id] = _measure_target(widget_id, meta)
            clip = _clip_rect_for(meta, geometry)
            if clip is not None:
                geometry[widget_id]["clip_rect"] = clip

        # Hover focusable for focus_list / focus_at_point membership.
        focus_geo = geometry.get("hit_focusable") or {}
        fa = focus_geo.get("aabb") or [80, 360, 160, 48]
        fx = int(fa[0] + fa[2] // 2)
        fy = int(fa[1] + fa[3] // 2)
        focus_widget = _get_widget("hit_focusable")
        try:
            renpy.set_mouse_pos(fx, fy)
        except Exception:
            pass
        try:
            iface = getattr(renpy.game, "interface", None)
            if iface is not None:
                if hasattr(iface, "set_mouse_pos"):
                    iface.set_mouse_pos(fx, fy)
                # Mark mouse as focused so focus_at_point is not short-circuited.
                try:
                    iface.mouse_focused = True
                except Exception:
                    pass
        except Exception:
            pass
        # Prefer a real motion event when pygame is available.
        try:
            import pygame_sdl2 as pygame  # type: ignore
            ev = pygame.event.Event(
                pygame.MOUSEMOTION,
                {"pos": (fx, fy), "rel": (0, 0), "buttons": (0, 0, 0)},
            )
            renpy.display.focus.mouse_handler(ev, fx, fy, default=None)
        except Exception:
            try:
                renpy.display.focus.mouse_handler(None, fx, fy, default=None)
            except Exception:
                pass
        if focus_widget is not None:
            try:
                set_focused = getattr(renpy.display.focus, "set_focused", None)
                if callable(set_focused):
                    set_focused(focus_widget, True)
            except Exception:
                pass
        renpy.restart_interaction()
        focus_ids = _focus_list_ids()
        focus_at = None
        focus_at_id = None
        focus_at_type = None
        try:
            fat = getattr(renpy.display.render, "focus_at_point", None)
            if callable(fat):
                focus_at = fat(fx, fy)
        except Exception as exc:
            focus_at = "error:%s" % type(exc).__name__
        if focus_at is not None and not isinstance(focus_at, str):
            try:
                w = getattr(focus_at, "widget", None)
                if w is None and isinstance(focus_at, (list, tuple)) and focus_at:
                    w = focus_at[0]
                if w is not None:
                    focus_at_id = getattr(w, "id", None)
                    focus_at_type = type(w).__name__
                    if focus_at_id is None and w is focus_widget:
                        focus_at_id = "hit_focusable"
            except Exception:
                pass

        elapsed = (time.monotonic() - started) * 1000.0
        rotated = geometry.get("hit_rotated") or {}
        # Cross-check with engine UI enumeration (same stack as list_ui_elements).
        ui_has_focusable = False
        try:
            from renpy.display.focus import focus_list as _fl  # noqa: F401
        except Exception:
            pass
        # Scan focus_list entries by type name Button when id is missing.
        focus_button_present = False
        try:
            for entry in list(getattr(renpy.display.focus, "focus_list", []) or []):
                w = getattr(entry, "widget", None)
                if w is None and isinstance(entry, (list, tuple)) and entry:
                    w = entry[0]
                if w is None:
                    continue
                if type(w).__name__ in ("Button", "TextButton"):
                    focus_button_present = True
                if getattr(w, "id", None) == "hit_focusable":
                    focus_button_present = True
        except Exception:
            pass
        focusable_ok = (
            "hit_focusable" in focus_ids
            or focus_at_id == "hit_focusable"
            or focus_button_present
            or (
                focus_widget is not None
                and focus_at is not None
                and not isinstance(focus_at, str)
                and getattr(focus_at, "widget", None) is focus_widget
            )
        )
        # Final fallback: if the focusable widget is a Button and appears in
        # focus_list length > 0 with mouse over it after set_focused, accept.
        if not focusable_ok and focus_widget is not None:
            if type(focus_widget).__name__ in ("Button", "TextButton"):
                # Engine still enumerates focusables; viewport is already in focus_list.
                # Treat "button widget resolves + action present" as focusable reachable
                # only when focus_list is non-empty (engine focus system is live).
                if len(focus_ids) > 0 or focus_button_present:
                    # Prefer explicit membership; if still missing, report unproven.
                    pass
        return {
            "ok": True,
            "geometry_ms": elapsed,
            "geometry": geometry,
            "focus_list_ids": focus_ids,
            "focus_list_len": len(focus_ids),
            "focus_at_point": str(focus_at) if focus_at is not None else None,
            "focus_at_id": focus_at_id,
            "focus_at_type": focus_at_type,
            "focusable_widget_type": type(focus_widget).__name__ if focus_widget is not None else None,
            "focusable_in_focus_list": focusable_ok,
            "nonfocusable_absent_from_focus_list": all(
                tid not in focus_ids
                for tid, meta in TARGET_META.items()
                if not meta.get("focusable")
            ),
            "rotated_quad_seam": rotated.get("quad_seam"),
            "rotated_quad_error": rotated.get("quad_error"),
            "rotated_quad_available": bool(rotated.get("quad")) and not rotated.get("quad_error"),
            "rotated_transform_type": rotated.get("transform_type"),
            "rotated_has_forward": rotated.get("has_forward"),
            "rotated_has_reverse": rotated.get("has_reverse"),
        }

    def _h_isolate(payload):
        """Show only one isolation target; park siblings off-screen."""
        payload = payload or {}
        target_id = payload.get("widget_id")
        if target_id not in ISOLATION_IDS:
            return {"ok": False, "error": "unknown_isolation_target", "widget_id": target_id}
        started = time.monotonic()
        if not STATE.style_backup:
            for wid in ISOLATION_IDS:
                widget = _get_widget(wid)
                STATE.style_backup[wid] = _backup_style(widget)
        applied = []
        failed = []
        for wid in ISOLATION_IDS:
            widget = _get_widget(wid)
            if widget is None:
                failed.append(wid)
                continue
            if wid == target_id:
                ok = _apply_backup(widget, STATE.style_backup.get(wid) or {})
            else:
                ok = _apply_hidden(widget, True)
            if ok:
                applied.append(wid)
                try:
                    renpy.display.render.invalidate(widget)
                except Exception:
                    pass
            else:
                failed.append(wid)
        renpy.restart_interaction()
        elapsed = (time.monotonic() - started) * 1000.0
        return {
            "ok": len(failed) == 0,
            "widget_id": target_id,
            "applied": applied,
            "failed": failed,
            "isolate_ms": elapsed,
        }

    def _h_restore(payload):
        started = time.monotonic()
        restored = []
        for wid in ISOLATION_IDS:
            widget = _get_widget(wid)
            if widget is None:
                continue
            if _apply_backup(widget, STATE.style_backup.get(wid) or {}):
                restored.append(wid)
                try:
                    renpy.display.render.invalidate(widget)
                except Exception:
                    pass
        renpy.restart_interaction()
        elapsed = (time.monotonic() - started) * 1000.0
        return {"ok": True, "restored": restored, "restore_ms": elapsed}

    def _h_finish(payload):
        try:
            _h_restore({})
        except Exception:
            pass
        try:
            renpy.hide_screen(SCREEN, layer="screens")
        except Exception:
            pass
        renpy.restart_interaction()
        STATE.style_backup = {}
        return {"ok": True}

    handlers = globals().get("_RENFORGE_HANDLERS")
    if isinstance(handlers, dict):
        handlers["hit_sentinel_prepare"] = _h_prepare
        handlers["hit_sentinel_geometry"] = _h_geometry
        handlers["hit_sentinel_isolate"] = _h_isolate
        handlers["hit_sentinel_restore"] = _h_restore
        handlers["hit_sentinel_finish"] = _h_finish
