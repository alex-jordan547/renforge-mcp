# Opt-in spike resource for issue #43 — non-focusable hit regions.
# Injected beside the bridge; registers RPC handlers; no production editor UI.

init 1600 python hide:
    import builtins
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
            targets={},
        )

    SCREEN = "renforge_hit_sentinel_fixture"

    # Authored paint colours (must match the fixture). Used only to label GT
    # samples — never as a substitute for measuring whether a pixel is painted.
    TARGET_COLOURS = {
        "hit_add": (0xE5, 0x2E, 0x2E),
        "hit_text": (0x2E, 0xE5, 0xA0),
        "hit_frame": (0x2E, 0x6B, 0xE5),
        "hit_rotated": (0xE5, 0xC8, 0x2E),
        "hit_clipped_child": (0xC8, 0x2E, 0xE5),
        "hit_viewport_child": (0x2E, 0xE5, 0xE5),
        "hit_focusable": (0x88, 0x88, 0x88),
    }

    TARGET_META = {
        "hit_add": {"kind": "add", "rotate": 0.0, "focusable": False},
        "hit_text": {"kind": "text", "rotate": 0.0, "focusable": False},
        "hit_frame": {"kind": "frame", "rotate": 0.0, "focusable": False},
        "hit_rotated": {"kind": "add", "rotate": 25.0, "focusable": False},
        "hit_clipped_child": {"kind": "add", "rotate": 0.0, "focusable": False, "clip_parent": "hit_clip_parent"},
        "hit_viewport_child": {"kind": "add", "rotate": 0.0, "focusable": False, "viewport": "hit_viewport"},
        "hit_focusable": {"kind": "textbutton", "rotate": 0.0, "focusable": True},
    }

    def _jsonable(value):
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [_jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        return str(value)

    def _get_widget(widget_id):
        try:
            return renpy.get_widget(SCREEN, widget_id)
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
        return ids

    def _rotate_point(px, py, cx, cy, degrees):
        rad = math.radians(degrees)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        dx = px - cx
        dy = py - cy
        return [cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a]

    def _quad_from_aabb(x, y, w, h, rotate_deg):
        # Corners TL, TR, BR, BL in unrotated space, then rotate about centre.
        corners = [
            (x, y),
            (x + w, y),
            (x + w, y + h),
            (x, y + h),
        ]
        if abs(float(rotate_deg)) < 1e-9:
            return [[float(px), float(py)] for px, py in corners]
        cx = x + w / 2.0
        cy = y + h / 2.0
        return [_rotate_point(px, py, cx, cy, rotate_deg) for px, py in corners]

    def _clip_rect_for(meta, geometry_map):
        parent_id = meta.get("clip_parent")
        if not parent_id:
            return None
        parent = geometry_map.get(parent_id)
        if not parent or parent.get("aabb") is None:
            # Parent may not be in TARGET_META — measure on demand.
            widget = _get_widget(parent_id)
            if widget is None:
                return None
            xpos, ypos = _style_xy(widget)
            size = _render_size(widget)
            if xpos is None or ypos is None or size is None:
                return None
            return [int(xpos), int(ypos), int(size[0]), int(size[1])]
        aabb = parent["aabb"]
        return [int(aabb[0]), int(aabb[1]), int(aabb[2]), int(aabb[3])]

    def _measure_target(widget_id, meta):
        widget = _get_widget(widget_id)
        record = {
            "widget_id": widget_id,
            "found": widget is not None,
            "kind": meta.get("kind"),
            "focusable": bool(meta.get("focusable")),
            "rotate": float(meta.get("rotate") or 0.0),
            "colour_rgb": list(TARGET_COLOURS.get(widget_id) or []),
            "in_focus_list": widget_id in _focus_list_ids(),
            "aabb": None,
            "quad": None,
            "render_size": None,
            "style_pos": None,
            "errors": [],
        }
        if widget is None:
            record["errors"].append("widget_not_found")
            return record

        xpos, ypos = _style_xy(widget)
        size = _render_size(widget)
        record["render_size"] = size
        if xpos is not None and ypos is not None:
            record["style_pos"] = [float(xpos), float(ypos)]
        else:
            record["errors"].append("style_pos_unavailable")

        # Authored fixture coordinates as fallback when style is not absolute yet.
        authored = {
            "hit_add": (80, 80, 160, 100),
            "hit_text": (280, 100, 200, 40),
            "hit_frame": (80, 220, 180, 100),
            "hit_rotated": (320, 240, 140, 80),
            "hit_clipped_child": (520, 80, 200, 80),
            "hit_viewport_child": (540, 260, 120, 60),  # approx after yinitial
            "hit_focusable": (80, 360, 160, 48),
            "hit_clip_parent": (520, 80, 100, 80),
            "hit_viewport": (520, 220, 160, 100),
        }
        if xpos is None or ypos is None or size is None:
            if widget_id in authored:
                ax, ay, aw, ah = authored[widget_id]
                xpos, ypos = float(ax), float(ay)
                size = [aw, ah]
                record["errors"].append("geometry_used_authored_fallback")
            else:
                record["errors"].append("geometry_unavailable")
                return record
        else:
            # Prefer measured size; keep measured pos.
            pass

        w, h = int(size[0]), int(size[1])
        x, y = float(xpos), float(ypos)
        # Some containers report style 0,0 before layout; prefer authored for known fixtures.
        if (x, y) == (0.0, 0.0) and widget_id in authored:
            ax, ay, aw, ah = authored[widget_id]
            x, y = float(ax), float(ay)
            if w <= 0 or h <= 0:
                w, h = int(aw), int(ah)
            record["errors"].append("geometry_zero_pos_used_authored")
        record["aabb"] = [int(round(x)), int(round(y)), w, h]
        record["quad"] = _quad_from_aabb(x, y, w, h, record["rotate"])
        return record

    def _point_in_aabb(px, py, aabb):
        if not aabb or len(aabb) != 4:
            return False
        x, y, w, h = aabb
        return x <= px < x + w and y <= py < y + h

    def _point_in_quad(px, py, quad):
        if not quad or len(quad) != 4:
            return False
        # Ray-casting even-odd fill.
        inside = False
        j = 3
        for i in range(4):
            xi, yi = float(quad[i][0]), float(quad[i][1])
            xj, yj = float(quad[j][0]), float(quad[j][1])
            intersects = ((yi > py) != (yj > py)) and (
                px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-12) + xi
            )
            if intersects:
                inside = not inside
            j = i
        return inside

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
            "colours": {k: list(v) for k, v in TARGET_COLOURS.items()},
        }

    def _h_geometry(payload):
        started = time.monotonic()
        geometry = {}
        # Measure clip parent first so children can reference it.
        for extra_id in ("hit_clip_parent", "hit_viewport"):
            geometry[extra_id] = _measure_target(
                extra_id,
                {"kind": "container", "rotate": 0.0, "focusable": False},
            )
        for widget_id, meta in TARGET_META.items():
            geometry[widget_id] = _measure_target(widget_id, meta)
            clip = _clip_rect_for(meta, geometry)
            if clip is not None:
                geometry[widget_id]["clip_rect"] = clip
        elapsed = (time.monotonic() - started) * 1000.0
        focus_ids = _focus_list_ids()
        # Nudge the pointer over the focusable so focus_list includes it when idle focus is empty.
        try:
            renpy.set_mouse_pos(160, 384)
            renpy.game.interface.set_mouse_pos(160, 384)
        except Exception:
            pass
        try:
            renpy.display.focus.mouse_handler(None, 160, 384, default=None)
        except Exception:
            pass
        focus_ids_after = _focus_list_ids()
        return {
            "ok": True,
            "geometry_ms": elapsed,
            "geometry": geometry,
            "focus_list_ids": focus_ids,
            "focus_list_ids_after_hover": focus_ids_after,
            "focusable_in_focus_list": (
                "hit_focusable" in focus_ids or "hit_focusable" in focus_ids_after
            ),
            "nonfocusable_absent_from_focus_list": all(
                tid not in focus_ids and tid not in focus_ids_after
                for tid, meta in TARGET_META.items()
                if not meta.get("focusable")
            ),
        }

    def _h_classify_points(payload):
        """Server-side pure geometry classification (no screenshot)."""
        payload = payload or {}
        points = payload.get("points") or []
        geometry = payload.get("geometry") or {}
        results = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            px, py = int(point[0]), int(point[1])
            row = {"x": px, "y": py, "aabb": [], "quad": [], "comp_candidates": []}
            for widget_id, meta in TARGET_META.items():
                geo = geometry.get(widget_id) or {}
                aabb = geo.get("aabb")
                quad = geo.get("quad")
                clip = geo.get("clip_rect")
                in_aabb = _point_in_aabb(px, py, aabb)
                in_quad = _point_in_quad(px, py, quad)
                in_clip = True if clip is None else _point_in_aabb(px, py, clip)
                if in_aabb and in_clip:
                    row["aabb"].append(widget_id)
                if in_quad and in_clip:
                    row["quad"].append(widget_id)
                # COMP without mask is incomplete; list candidates for host mask AND.
                if in_quad and in_clip:
                    row["comp_candidates"].append(widget_id)
            results.append(row)
        return {"ok": True, "results": results}

    def _h_finish(payload):
        try:
            renpy.hide_screen(SCREEN, layer="screens")
        except Exception:
            pass
        renpy.restart_interaction()
        return {"ok": True}

    handlers = globals().get("_RENFORGE_HANDLERS")
    if isinstance(handlers, dict):
        handlers["hit_sentinel_prepare"] = _h_prepare
        handlers["hit_sentinel_geometry"] = _h_geometry
        handlers["hit_sentinel_classify_points"] = _h_classify_points
        handlers["hit_sentinel_finish"] = _h_finish
