screen _renforge_editor_overlay():
    layer "screens"
    zorder 12000

    if _renforge_editor_is_active():
        add _renforge_editor_event_catcher()

        fixed:
            id "rf_overlay_root"
            xfill True
            yfill True

            if _renforge_editor_guide_x() is not None:
                add Solid("#ff0000", xysize=(1, config.screen_height)):
                    xpos int(_renforge_editor_guide_x())
                    ypos 0
                    at Transform(alpha=_renforge_editor_opacity())

            if _renforge_editor_guide_y() is not None:
                add Solid("#ff0000", xysize=(config.screen_width, 1)):
                    xpos 0
                    ypos int(_renforge_editor_guide_y())
                    at Transform(alpha=_renforge_editor_opacity())

            add Solid("#0066ff", xysize=(80, 20)):
                xpos 1000
                ypos 16
                at Transform(alpha=_renforge_editor_opacity())

            $ _rf_label = _renforge_editor_label_snapshot()
            if _rf_label is not None:
                frame:
                    id "rf_label"
                    xpos int(_rf_label["x"])
                    ypos int(_rf_label["y"])
                    xsize int(_rf_label["w"])
                    ysize int(_rf_label["h"])
                    background Solid("#00ff00")
                    at Transform(alpha=float(_rf_label["alpha"]))
                    text _rf_label["text"]:
                        color "#000000"
                        size 16
                        xalign 0.0
                        yalign 0.5

        if _renforge_editor_opacity() < 0.25:
            fixed:
                xsize 72
                ysize 38
                xpos 4
                ypos 4
                add Solid("#ffffff", xysize=(72, 38))
                add Solid("#000000", xysize=(72, 2))
                add Solid("#000000", xysize=(72, 2)):
                    ypos 36
                add Solid("#000000", xysize=(2, 38))
                add Solid("#000000", xysize=(2, 38)):
                    xpos 70

        textbutton "RF":
            id "rf_exit"
            xpos 10
            ypos 10
            action Function(_renforge_editor_exit)
            text_color "#101010"
            at Transform(alpha=_renforge_editor_opacity())

        textbutton "Save":
            id "rf_save"
            xpos 184
            ypos 214
            action NullAction()
            sensitive False
            at Transform(alpha=_renforge_editor_opacity())

init 1100 python:
    import builtins
    import hashlib
    import queue
    import sys
    import threading
    import time
    import types

    try:
        import pygame_sdl2 as pygame
    except Exception:
        pygame = None

    if "_renforge_runtime" not in sys.modules:
        raise Exception("RenForge bridge must load before editor.rpy")
    _renforge_runtime_module = sys.modules["_renforge_runtime"]
    if not hasattr(_renforge_runtime_module, "editor_v1"):
        _renforge_runtime_module.editor_v1 = types.SimpleNamespace()

    _EDITOR_SCREEN = "_renforge_editor_overlay"
    _EDITOR_OWNER = "renforge.editor.v1"
    _SNAP_ACQUIRE = 6
    _SNAP_RELEASE = 10
    _ALLOWED_ANCESTRY_TYPES = set(
        [
            "ScreenDisplayable",
            "Fixed",
            "MultiBox",
            "Button",
            "Text",
            "Frame",
            "Window",
            "Transform",
            "Viewport",
            "Container",
            "Grid",
            "HBox",
            "VBox",
            "Drag",
            "Null",
        ]
    )

    def _renforge_editor_state():
        state = _renforge_runtime_module.editor_v1
        if not hasattr(state, "initialized"):
            state.initialized = True
            state.script_generation = 0
            state.active = False
            state.screen = None
            state.selected_runtime_key = None
            state.selected_widget_id = None
            state.selected_screen = None
            state.selected_lock_reason = None
            state.selected_original_position = None
            state.preview_position = None
            state.pointer = [0, 0]
            state.drag_active = False
            state.drag_offset = [0, 0]
            state.snap_anchor_x = None
            state.snap_anchor_y = None
            state.guide_x = None
            state.guide_y = None
            state.opacity = 0.9
            state.label_rect = [20, 20, 220, 32]
            state.label_alpha = 1.0
            state.label_text = "No selection"
            state.save_enabled = False
            state.accepted_observations = []
            state.last_event_trace = []
            state.coordinator = None
            state.coordinator_applied = []
            state.last_coordinator_apply = None
            state.last_restore_method = None
            state.last_preview_method = None
            state.main_thread_id = None
        state.script_generation = int(getattr(state, "script_generation", 0)) + 1
        return state


    class _RenforgeEditorCoordinatorIO(object):
        def __init__(self):
            self.requests = queue.Queue()
            self.results = queue.Queue()
            self.stop = threading.Event()
            self.counter = 0
            self.thread = threading.Thread(
                target=self._loop,
                name="renforge.editor.v1.coordinator",
                daemon=True,
            )
            self.thread.start()

        def submit(self, payload):
            self.counter += 1
            request_id = "task0-%d" % self.counter
            self.requests.put((request_id, payload, threading.get_ident(), time.time()))
            return request_id

        def _loop(self):
            while not self.stop.is_set():
                try:
                    request_id, payload, submitted_thread, submitted_at = self.requests.get(timeout=0.1)
                except queue.Empty:
                    continue
                self.results.put(
                    {
                        "request_id": request_id,
                        "payload": payload,
                        "submitted_thread_id": submitted_thread,
                        "worker_thread_id": threading.get_ident(),
                        "submitted_at": submitted_at,
                        "completed_at": time.time(),
                    }
                )

        def collect_nowait(self):
            items = []
            while True:
                try:
                    items.append(self.results.get_nowait())
                except queue.Empty:
                    break
            return items


    def _renforge_editor_ensure_coordinator():
        state = _renforge_editor_state()
        coordinator = getattr(state, "coordinator", None)
        if coordinator is None or not getattr(coordinator, "thread", None):
            coordinator = _RenforgeEditorCoordinatorIO()
            state.coordinator = coordinator
        return coordinator


    def _renforge_editor_children(displayable):
        children = getattr(displayable, "children", None)
        if children is not None and not builtins.isinstance(children, (str, bytes)):
            try:
                return list(children)
            except Exception:
                pass
        child = getattr(displayable, "child", None)
        if child is not None:
            return [child]
        visit = getattr(displayable, "visit", None)
        if callable(visit):
            try:
                visited = visit()
            except Exception:
                return []
            if builtins.isinstance(visited, (builtins.list, tuple)):
                return list(visited)
        return []


    def _renforge_editor_screen_name(focus):
        screen_obj = getattr(focus, "screen", None)
        name = getattr(screen_obj, "screen_name", None)
        if name is None:
            name = getattr(focus, "screen_name", None)
        if not name:
            return None
        try:
            return name[0] if builtins.isinstance(name, (builtins.list, tuple)) else str(name)
        except Exception:
            return None


    def _renforge_editor_location(displayable):
        location = getattr(displayable, "_location", None)
        if builtins.isinstance(location, (builtins.list, tuple)) and len(location) >= 2:
            try:
                return [str(location[0]), int(location[1])]
            except Exception:
                return None
        return None


    def _renforge_editor_widget_map(screen_name):
        get_screen = getattr(getattr(renpy.display, "screen", None), "get_screen", None)
        if not callable(get_screen):
            return None, {}
        try:
            screen = get_screen(screen_name)
        except Exception:
            return None, {}
        if screen is None:
            return None, {}
        widgets = getattr(screen, "widgets", None) or {}
        if not isinstance(widgets, builtins.dict):
            return screen, {}
        return screen, widgets


    def _renforge_editor_descendant_ids(widget):
        found = set()
        stack = [widget]
        while stack:
            current = stack.pop()
            if current is None:
                continue
            current_id = id(current)
            if current_id in found:
                continue
            found.add(current_id)
            stack.extend(_renforge_editor_children(current))
        return found


    def _renforge_editor_resolve_widget_id(screen_name, widget):
        if widget is None or not isinstance(screen_name, str):
            return None, None, "MISSING_WIDGET"
        screen, widgets = _renforge_editor_widget_map(screen_name)
        if not widgets:
            return screen, None, "MISSING_WIDGET_MAP"
        descendants = _renforge_editor_descendant_ids(widget)
        matches = []
        for key, value in widgets.items():
            if value is None:
                continue
            if id(value) in descendants:
                matches.append(str(key))
        if not matches:
            return screen, None, "SYNTHETIC_WIDGET_ID"
        unique = []
        for item in matches:
            if item not in unique:
                unique.append(item)
        if len(unique) != 1:
            return screen, None, "MULTI_INSTANCE_UNSUPPORTED"
        return screen, unique[0], None


    def _renforge_editor_find_ancestry(screen, target):
        found = []
        seen = set()

        def visit(node, ancestry):
            if node is None:
                return False
            node_id = id(node)
            if node_id in seen:
                return False
            seen.add(node_id)
            if node_id == id(target):
                found.extend(ancestry + [node])
                return True
            children = _renforge_editor_children(node)
            for child in children:
                if visit(child, ancestry + [node]):
                    return True
            return False

        visit(screen, [])
        return found


    def _renforge_editor_crop_state(node):
        class_name = getattr(getattr(node, "__class__", None), "__name__", "unknown")
        if "Viewport" in class_name:
            return "viewport"
        if "Crop" in class_name:
            return "crop_displayable"
        clipping = getattr(node, "clipping", None)
        if clipping is True:
            return "clipping_true"
        crop = getattr(node, "crop", None)
        if crop not in (None, False):
            return "transform_crop"
        return "none"


    def _renforge_editor_runtime_key_from_focus(focus, ordinal):
        screen_name = _renforge_editor_screen_name(focus)
        if not isinstance(screen_name, str):
            return None, "MISSING_INVOCATION_PATH", None, None
        widget = getattr(focus, "widget", None)
        screen, widget_id, resolve_error = _renforge_editor_resolve_widget_id(screen_name, widget)
        if resolve_error is not None:
            return None, resolve_error, None, None
        if not isinstance(widget_id, str) or not widget_id:
            return None, "SYNTHETIC_WIDGET_ID", None, None
        if screen is None:
            return None, "MISSING_SCREEN", None, None
        named_widget = None
        widgets = getattr(screen, "widgets", None) or {}
        if isinstance(widgets, builtins.dict):
            named_widget = widgets.get(widget_id)
        if named_widget is None:
            named_widget = widget
        source_location = _renforge_editor_location(named_widget)
        if source_location is None:
            return None, "MISSING_SOURCE_LOCATION", None, None
        ancestry_nodes = _renforge_editor_find_ancestry(screen, named_widget)
        if not ancestry_nodes:
            return None, "UNKNOWN_ANCESTRY_TYPE", None, None
        ancestry = []
        for index, node in enumerate(ancestry_nodes):
            class_name = getattr(getattr(node, "__class__", None), "__name__", "unknown")
            if class_name not in _ALLOWED_ANCESTRY_TYPES:
                return None, "UNKNOWN_ANCESTRY_TYPE", None, None
            crop_state = _renforge_editor_crop_state(node)
            if crop_state not in (
                "none",
                "viewport",
                "crop_displayable",
                "transform_crop",
                "clipping_true",
            ):
                return None, "UNKNOWN_CROP_STATE", None, None
            editor_owned = bool(
                screen_name == _EDITOR_SCREEN
                or getattr(node, "_renforge_editor_owner", None) == _EDITOR_OWNER
                or getattr(named_widget, "_renforge_editor_owner", None) == _EDITOR_OWNER
            )
            ancestry.append(
                {
                    "index": int(index),
                    "type": class_name,
                    "source_location": _renforge_editor_location(node),
                    "screen_owner": _EDITOR_OWNER if editor_owned else "game",
                    "crop_state": crop_state,
                    "editor_owned": editor_owned,
                }
            )
        key = {
            "screen": screen_name,
            "invocation_path": [screen_name],
            "widget_id": widget_id,
            "source_location": source_location,
            "instance_discriminator": "%s:%s" % (widget_id, screen_name),
            "ancestry": ancestry,
        }
        return key, None, named_widget, widget


    def _renforge_editor_validate_runtime_key(runtime_key):
        if not isinstance(runtime_key, builtins.dict):
            return "INVALID_RUNTIME_KEY"
        ancestry = runtime_key.get("ancestry")
        if not builtins.isinstance(ancestry, (builtins.list, tuple)) or not ancestry:
            return "UNKNOWN_ANCESTRY_TYPE"
        for node in ancestry:
            if not isinstance(node, builtins.dict):
                return "UNKNOWN_ANCESTRY_TYPE"
            node_type = str(node.get("type") or "")
            if node_type not in _ALLOWED_ANCESTRY_TYPES:
                return "UNKNOWN_ANCESTRY_TYPE"
            crop_state = str(node.get("crop_state") or "")
            if crop_state not in (
                "none",
                "viewport",
                "crop_displayable",
                "transform_crop",
                "clipping_true",
            ):
                return "UNKNOWN_CROP_STATE"
            if crop_state in ("viewport", "crop_displayable", "transform_crop", "clipping_true"):
                return "CLIPPED_ANCESTRY_UNSUPPORTED"
        return None


    def _renforge_editor_focus_candidates():
        candidates = []
        try:
            focus_list = list(renpy.display.focus.focus_list or [])
        except Exception:
            focus_list = []
        for ordinal, focus in enumerate(focus_list):
            x = getattr(focus, "x", None)
            y = getattr(focus, "y", None)
            w = getattr(focus, "w", None)
            h = getattr(focus, "h", None)
            if None in (x, y, w, h):
                continue
            try:
                rect = [int(x), int(y), int(w), int(h)]
            except Exception:
                continue
            if rect[2] <= 0 or rect[3] <= 0:
                continue
            runtime_key, resolve_error, named_widget, focused_widget = _renforge_editor_runtime_key_from_focus(
                focus,
                ordinal,
            )
            screen_name = _renforge_editor_screen_name(focus)
            owner_hit = bool(screen_name == _EDITOR_SCREEN)
            if runtime_key is not None:
                for node in runtime_key.get("ancestry", []):
                    if bool(node.get("editor_owned")):
                        owner_hit = True
                        break
            candidate = {
                "focus": focus,
                "rect": rect,
                "ordinal": int(ordinal),
                "runtime_key": runtime_key,
                "resolve_error": resolve_error,
                "named_widget": named_widget,
                "focused_widget": focused_widget,
                "editor_owned": owner_hit,
            }
            candidates.append(candidate)
        counts = {}
        for candidate in candidates:
            key = candidate.get("runtime_key")
            if not isinstance(key, builtins.dict):
                continue
            signature = (
                key.get("screen"),
                key.get("widget_id"),
                tuple(key.get("source_location") or []),
            )
            counts[signature] = counts.get(signature, 0) + 1
        for candidate in candidates:
            key = candidate.get("runtime_key")
            if not isinstance(key, builtins.dict):
                continue
            signature = (
                key.get("screen"),
                key.get("widget_id"),
                tuple(key.get("source_location") or []),
            )
            if counts.get(signature, 0) > 1:
                candidate["resolve_error"] = "MULTI_INSTANCE_UNSUPPORTED"
        return candidates


    def _renforge_editor_barrier():
        renpy.restart_interaction()
        data = renpy.screenshot_to_bytes(None)
        frame_id = hashlib.sha256(data).hexdigest()
        return frame_id


    def _renforge_editor_observation_for_candidate(candidate):
        runtime_key = candidate.get("runtime_key")
        if not isinstance(runtime_key, builtins.dict):
            return None, candidate.get("resolve_error") or "UNMEASURED"
        validation = _renforge_editor_validate_runtime_key(runtime_key)
        if validation is not None:
            return None, validation
        frame_id = _renforge_editor_barrier()
        rect = list(candidate.get("rect") or [])
        if len(rect) != 4:
            return None, "UNMEASURED"
        script_generation = int(_renforge_editor_state().script_generation)
        focused_widget = candidate.get("focused_widget")
        observation = {
            "runtime_key": runtime_key,
            "rect": [int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])],
            "measurement_method": "focus_list",
            "frame_id": frame_id,
            "script_generation": script_generation,
            "object_id": id(focused_widget) if focused_widget is not None else None,
        }
        return observation, None


    def _renforge_editor_set_label(pointer_x, pointer_y):
        state = _renforge_editor_state()
        width = int(getattr(renpy.config, "screen_width", 1920) or 1920)
        height = int(getattr(renpy.config, "screen_height", 1080) or 1080)
        label_w = 220
        label_h = 32
        raw_x = int(pointer_x) + 14
        raw_y = int(pointer_y) + 14
        x = max(0, min(width - label_w, raw_x))
        y = max(0, min(height - label_h, raw_y))
        distance = abs(x - int(pointer_x)) + abs(y - int(pointer_y))
        alpha = 0.35 if distance < 30 else 1.0
        state.label_rect = [x, y, label_w, label_h]
        state.label_alpha = alpha
        selected = state.selected_widget_id or "none"
        state.label_text = "id=%s x=%d y=%d" % (selected, x, y)


    def _renforge_editor_anchor_lines(selected_key):
        anchors_x = []
        anchors_y = []
        for candidate in _renforge_editor_focus_candidates():
            if candidate.get("editor_owned"):
                continue
            runtime_key = candidate.get("runtime_key")
            if runtime_key is None:
                continue
            if selected_key is not None and runtime_key == selected_key:
                continue
            rect = candidate.get("rect") or []
            if len(rect) != 4:
                continue
            left = int(rect[0])
            top = int(rect[1])
            width = int(rect[2])
            height = int(rect[3])
            anchors_x.extend([left, left + width // 2, left + width])
            anchors_y.extend([top, top + height // 2, top + height])
        return anchors_x, anchors_y


    def _renforge_editor_apply_snap(desired_x, desired_y, shift):
        state = _renforge_editor_state()
        selected_key = state.selected_runtime_key
        if shift:
            state.snap_anchor_x = None
            state.snap_anchor_y = None
            state.guide_x = None
            state.guide_y = None
            return int(desired_x), int(desired_y), {"snapped_x": False, "snapped_y": False}
        anchors_x, anchors_y = _renforge_editor_anchor_lines(selected_key)
        snapped_x = int(desired_x)
        snapped_y = int(desired_y)
        snap_x = state.snap_anchor_x
        snap_y = state.snap_anchor_y
        if snap_x is not None and abs(int(desired_x) - int(snap_x)) <= _SNAP_RELEASE:
            snapped_x = int(snap_x)
        else:
            state.snap_anchor_x = None
            snap_x = None
            closest = None
            for anchor in anchors_x:
                distance = abs(int(desired_x) - int(anchor))
                if closest is None or distance < closest[0]:
                    closest = (distance, int(anchor))
            if closest is not None and closest[0] <= _SNAP_ACQUIRE:
                state.snap_anchor_x = closest[1]
                snapped_x = closest[1]
                snap_x = closest[1]
        if snap_y is not None and abs(int(desired_y) - int(snap_y)) <= _SNAP_RELEASE:
            snapped_y = int(snap_y)
        else:
            state.snap_anchor_y = None
            snap_y = None
            closest = None
            for anchor in anchors_y:
                distance = abs(int(desired_y) - int(anchor))
                if closest is None or distance < closest[0]:
                    closest = (distance, int(anchor))
            if closest is not None and closest[0] <= _SNAP_ACQUIRE:
                state.snap_anchor_y = closest[1]
                snapped_y = closest[1]
                snap_y = closest[1]
        state.guide_x = state.snap_anchor_x
        state.guide_y = state.snap_anchor_y
        return snapped_x, snapped_y, {
            "snapped_x": state.snap_anchor_x is not None,
            "snapped_y": state.snap_anchor_y is not None,
        }


    def _renforge_editor_apply_preview(x, y, *, shift=False, allow_snap=True):
        state = _renforge_editor_state()
        if not state.selected_screen or not state.selected_widget_id:
            return {"ok": False, "error": "NO_SELECTION"}
        desired_x = int(x)
        desired_y = int(y)
        snap_detail = {"snapped_x": False, "snapped_y": False}
        if allow_snap:
            snapped_x, snapped_y, snap_detail = _renforge_editor_apply_snap(desired_x, desired_y, bool(shift))
        else:
            state.snap_anchor_x = None
            state.snap_anchor_y = None
            state.guide_x = None
            state.guide_y = None
            snapped_x, snapped_y = desired_x, desired_y
        renpy.show_screen(
            state.selected_screen,
            _layer="screens",
            _widget_properties={state.selected_widget_id: {"xpos": int(snapped_x), "ypos": int(snapped_y)}},
        )
        state.preview_position = [int(snapped_x), int(snapped_y)]
        state.last_preview_method = "_widget_properties"
        _renforge_editor_set_label(state.pointer[0], state.pointer[1])
        renpy.restart_interaction()
        return {
            "ok": True,
            "x": int(snapped_x),
            "y": int(snapped_y),
            "method": "_widget_properties",
            "snap": snap_detail,
            "guide_x": state.guide_x,
            "guide_y": state.guide_y,
        }


    def _renforge_editor_restore_preview():
        state = _renforge_editor_state()
        if not state.selected_screen:
            return {"ok": False, "error": "NO_SELECTION"}
        renpy.show_screen(state.selected_screen, _layer="screens")
        state.preview_position = None
        state.last_restore_method = "_widget_properties_revert"
        renpy.restart_interaction()
        return {"ok": True, "restored": True, "method": "_widget_properties_revert"}


    def _renforge_editor_resolve_selected_candidate():
        state = _renforge_editor_state()
        selected_key = state.selected_runtime_key
        if not isinstance(selected_key, builtins.dict):
            return None, "NO_SELECTION"
        matches = []
        loose_matches = []
        selected_screen = selected_key.get("screen")
        selected_widget = selected_key.get("widget_id")
        selected_source = tuple(selected_key.get("source_location") or [])
        for candidate in _renforge_editor_focus_candidates():
            if candidate.get("editor_owned"):
                continue
            key = candidate.get("runtime_key")
            if key == selected_key:
                matches.append(candidate)
                continue
            if isinstance(key, builtins.dict):
                if (
                    key.get("screen") == selected_screen
                    and key.get("widget_id") == selected_widget
                    and tuple(key.get("source_location") or []) == selected_source
                ):
                    loose_matches.append(candidate)
        if len(matches) == 0:
            if len(loose_matches) == 1:
                return loose_matches[0], None
            if len(loose_matches) > 1:
                return None, "MULTI_INSTANCE_UNSUPPORTED"
            return None, "NO_MATCHING_INSTANCE"
        if len(matches) > 1:
            return None, "MULTI_INSTANCE_UNSUPPORTED"
        return matches[0], None


    def _renforge_editor_accept_observation(observation):
        state = _renforge_editor_state()
        state.accepted_observations.append(observation)
        if len(state.accepted_observations) > 20:
            state.accepted_observations[:] = state.accepted_observations[-20:]


    def _renforge_editor_select(x, y):
        state = _renforge_editor_state()
        for candidate in reversed(_renforge_editor_focus_candidates()):
            if candidate.get("editor_owned"):
                continue
            rect = candidate.get("rect") or []
            if len(rect) != 4:
                continue
            if not (rect[0] <= int(x) < rect[0] + rect[2] and rect[1] <= int(y) < rect[1] + rect[3]):
                continue
            state.pointer = [int(x), int(y)]
            runtime_key = candidate.get("runtime_key")
            if not isinstance(runtime_key, builtins.dict):
                state.selected_runtime_key = None
                state.selected_lock_reason = candidate.get("resolve_error") or "UNMEASURED"
                _renforge_editor_set_label(x, y)
                return {"ok": False, "lock_reason": state.selected_lock_reason}
            lock = _renforge_editor_validate_runtime_key(runtime_key)
            if lock is not None:
                state.selected_runtime_key = runtime_key
                state.selected_widget_id = runtime_key.get("widget_id")
                state.selected_screen = runtime_key.get("screen")
                state.selected_lock_reason = lock
                _renforge_editor_set_label(x, y)
                observation, _ignore = _renforge_editor_observation_for_candidate(candidate)
                return {
                    "ok": False,
                    "lock_reason": lock,
                    "selected": {"widget_id": runtime_key.get("widget_id"), "screen": runtime_key.get("screen")},
                    "observation": observation,
                }
            observation, observe_error = _renforge_editor_observation_for_candidate(candidate)
            if observation is None:
                state.selected_lock_reason = observe_error or "UNMEASURED"
                _renforge_editor_set_label(x, y)
                return {"ok": False, "lock_reason": state.selected_lock_reason}
            runtime_key = observation["runtime_key"]
            state.selected_runtime_key = runtime_key
            state.selected_widget_id = runtime_key.get("widget_id")
            state.selected_screen = runtime_key.get("screen")
            state.selected_lock_reason = None
            state.selected_original_position = [int(rect[0]), int(rect[1])]
            _renforge_editor_accept_observation(observation)
            _renforge_editor_set_label(x, y)
            return {
                "ok": True,
                "selected": {"widget_id": state.selected_widget_id, "screen": state.selected_screen},
                "observation": observation,
            }
        return {"ok": False, "error": "NO_FOCUSABLE_TARGET"}


    def _renforge_editor_nudge(dx, dy, shift):
        state = _renforge_editor_state()
        base = state.preview_position or state.selected_original_position
        if base is None:
            candidate, error = _renforge_editor_resolve_selected_candidate()
            if candidate is None:
                return {"ok": False, "error": error}
            rect = candidate.get("rect") or []
            if len(rect) != 4:
                return {"ok": False, "error": "UNMEASURED"}
            base = [int(rect[0]), int(rect[1])]
        step = 10 if shift else 1
        x = int(base[0]) + int(dx) * step
        y = int(base[1]) + int(dy) * step
        return _renforge_editor_apply_preview(x, y, shift=shift, allow_snap=False)


    class _RenforgeEditorEventCatcher(renpy.Displayable):
        def render(self, width, height, st, at):
            render = renpy.Render(int(max(1, width)), int(max(1, height)))
            return render

        def event(self, event, x, y, st):
            return _renforge_editor_handle_event(event, x, y, st)


    _renforge_editor_event_catcher_singleton = _RenforgeEditorEventCatcher()
    _renforge_editor_event_catcher_singleton._renforge_editor_owner = _EDITOR_OWNER


    def _renforge_editor_event_catcher():
        return _renforge_editor_event_catcher_singleton


    def _renforge_editor_event_shift(event):
        mod = getattr(event, "mod", 0)
        if pygame is not None:
            shift_mask = getattr(pygame, "KMOD_SHIFT", 0)
            if shift_mask and mod & shift_mask:
                return True
        return bool(getattr(event, "shift", False))


    def _renforge_editor_event_pos(event, x, y):
        pos = getattr(event, "pos", None)
        if builtins.isinstance(pos, (builtins.list, tuple)) and len(pos) >= 2:
            return int(pos[0]), int(pos[1])
        return int(x), int(y)


    def _renforge_editor_apply_drag_from_pointer(pointer_x, pointer_y, shift):
        state = _renforge_editor_state()
        if not state.drag_active:
            base = state.preview_position or state.selected_original_position
            if base is None:
                candidate, error = _renforge_editor_resolve_selected_candidate()
                if candidate is None:
                    return {"ok": False, "error": error}
                rect = candidate.get("rect") or []
                if len(rect) != 4:
                    return {"ok": False, "error": "UNMEASURED"}
                base = [int(rect[0]), int(rect[1])]
            state.drag_active = True
            state.drag_offset = [int(pointer_x) - int(base[0]), int(pointer_y) - int(base[1])]
        desired_x = int(pointer_x) - int(state.drag_offset[0])
        desired_y = int(pointer_y) - int(state.drag_offset[1])
        return _renforge_editor_apply_preview(desired_x, desired_y, shift=shift, allow_snap=True)


    def _renforge_editor_end_drag():
        state = _renforge_editor_state()
        state.drag_active = False
        state.drag_offset = [0, 0]
        return {"ok": True}


    def _renforge_editor_exit():
        state = _renforge_editor_state()
        state.active = False
        state.drag_active = False
        state.snap_anchor_x = None
        state.snap_anchor_y = None
        state.guide_x = None
        state.guide_y = None
        renpy.hide_screen(_EDITOR_SCREEN, layer="screens")
        renpy.restart_interaction()
        return {"ok": True, "active": False}


    def _renforge_editor_handle_event(event, x, y, st):
        state = _renforge_editor_state()
        if not state.active:
            return None
        pointer_x, pointer_y = _renforge_editor_event_pos(event, x, y)
        state.pointer = [int(pointer_x), int(pointer_y)]
        _renforge_editor_set_label(pointer_x, pointer_y)
        event_type = getattr(event, "type", None)
        key = getattr(event, "key", None)
        shift = _renforge_editor_event_shift(event)
        if pygame is not None:
            if event_type == getattr(pygame, "MOUSEBUTTONDOWN", None) and getattr(event, "button", 0) == 1:
                _renforge_editor_apply_drag_from_pointer(pointer_x, pointer_y, shift)
                return None
            if event_type == getattr(pygame, "MOUSEMOTION", None) and state.drag_active:
                _renforge_editor_apply_drag_from_pointer(pointer_x, pointer_y, shift)
                return None
            if event_type == getattr(pygame, "MOUSEBUTTONUP", None) and getattr(event, "button", 0) == 1:
                _renforge_editor_end_drag()
                return None
            if event_type == getattr(pygame, "KEYDOWN", None):
                if key == getattr(pygame, "K_ESCAPE", None):
                    _renforge_editor_exit()
                    return None
                if key == getattr(pygame, "K_LEFT", None):
                    _renforge_editor_nudge(-1, 0, shift)
                    return None
                if key == getattr(pygame, "K_RIGHT", None):
                    _renforge_editor_nudge(1, 0, shift)
                    return None
                if key == getattr(pygame, "K_UP", None):
                    _renforge_editor_nudge(0, -1, shift)
                    return None
                if key == getattr(pygame, "K_DOWN", None):
                    _renforge_editor_nudge(0, 1, shift)
                    return None
        return None


    def _renforge_editor_fake_event(event_type, **attrs):
        event = types.SimpleNamespace(type=event_type)
        for name, value in attrs.items():
            setattr(event, name, value)
        return event


    def _renforge_editor_apply_coordinator_results():
        state = _renforge_editor_state()
        coordinator = _renforge_editor_ensure_coordinator()
        applied = []
        for item in coordinator.collect_nowait():
            applied_item = builtins.dict(item)
            applied_item["applied_thread_id"] = threading.get_ident()
            state.coordinator_applied.append(applied_item)
            state.last_coordinator_apply = applied_item
            applied.append(applied_item)
        if len(state.coordinator_applied) > 32:
            state.coordinator_applied[:] = state.coordinator_applied[-32:]
        return applied


    def _renforge_editor_periodic():
        state = _renforge_editor_state()
        if not state.active:
            return
        _renforge_editor_apply_coordinator_results()


    def _renforge_editor_h_start(payload):
        payload = payload or {}
        state = _renforge_editor_state()
        screen = payload.get("screen")
        if screen is None:
            return {"ok": False, "error": "screen is required"}
        screen = str(screen)
        state.main_thread_id = threading.get_ident()
        state.active = True
        state.screen = screen
        state.selected_runtime_key = None
        state.selected_widget_id = None
        state.selected_screen = None
        state.selected_lock_reason = None
        state.preview_position = None
        state.save_enabled = False
        state.opacity = 0.9
        state.label_text = "No selection"
        state.last_event_trace = []
        _renforge_editor_ensure_coordinator()
        renpy.show_screen(screen, _layer="screens")
        renpy.show_screen(_EDITOR_SCREEN, _layer="screens")
        renpy.restart_interaction()
        return {
            "ok": True,
            "active": True,
            "overlay_screen": _EDITOR_SCREEN,
            "owner": _EDITOR_OWNER,
            "save_enabled": False,
            "script_generation": int(state.script_generation),
        }


    def _renforge_editor_h_select(payload):
        payload = payload or {}
        x = payload.get("x")
        y = payload.get("y")
        if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
            return {"ok": False, "error": "x and y must be integers"}
        return _renforge_editor_select(x, y)


    def _renforge_editor_h_drag(payload):
        payload = payload or {}
        points = payload.get("points")
        if not builtins.isinstance(points, (builtins.list, tuple)) or not points:
            return {"ok": False, "error": "points must be a non-empty list"}
        shift = bool(payload.get("shift", False))
        state = _renforge_editor_state()
        if not state.active:
            return {"ok": False, "error": "editor is not active"}
        samples = []
        if pygame is None:
            return {"ok": False, "error": "pygame_sdl2 is unavailable"}
        first = points[0]
        if not builtins.isinstance(first, (builtins.list, tuple)) or len(first) < 2:
            return {"ok": False, "error": "invalid point"}
        down = _renforge_editor_fake_event(
            pygame.MOUSEBUTTONDOWN,
            pos=(int(first[0]), int(first[1])),
            button=1,
            mod=getattr(pygame, "KMOD_SHIFT", 0) if shift else 0,
        )
        _renforge_editor_handle_event(down, int(first[0]), int(first[1]), 0.0)
        for point in points:
            if not builtins.isinstance(point, (builtins.list, tuple)) or len(point) < 2:
                return {"ok": False, "error": "invalid point"}
            px = int(point[0])
            py = int(point[1])
            motion = _renforge_editor_fake_event(
                pygame.MOUSEMOTION,
                pos=(px, py),
                rel=(0, 0),
                buttons=(1, 0, 0),
                mod=getattr(pygame, "KMOD_SHIFT", 0) if shift else 0,
            )
            _renforge_editor_handle_event(motion, px, py, 0.0)
            state_after = _renforge_editor_state()
            preview = list(state_after.preview_position or [])
            samples.append(
                {
                    "point": [px, py],
                    "preview_position": preview if len(preview) == 2 else None,
                    "guide_x": state_after.guide_x,
                    "guide_y": state_after.guide_y,
                }
            )
        last = points[-1]
        up = _renforge_editor_fake_event(
            pygame.MOUSEBUTTONUP,
            pos=(int(last[0]), int(last[1])),
            button=1,
            mod=getattr(pygame, "KMOD_SHIFT", 0) if shift else 0,
        )
        _renforge_editor_handle_event(up, int(last[0]), int(last[1]), 0.0)
        state.drag_active = False
        return {
            "ok": True,
            "event_method": "Displayable.event",
            "preview_method": state.last_preview_method,
            "samples": samples,
            "guide_x": state.guide_x,
            "guide_y": state.guide_y,
        }


    def _renforge_editor_h_key(payload):
        payload = payload or {}
        key_name = str(payload.get("key") or "").lower()
        repeat = int(payload.get("repeat", 1) or 1)
        shift = bool(payload.get("shift", False))
        if repeat < 1:
            repeat = 1
        state = _renforge_editor_state()
        if not state.active:
            return {"ok": False, "error": "editor is not active"}
        if pygame is None:
            return {"ok": False, "error": "pygame_sdl2 is unavailable"}
        key_map = {
            "left": getattr(pygame, "K_LEFT", None),
            "right": getattr(pygame, "K_RIGHT", None),
            "up": getattr(pygame, "K_UP", None),
            "down": getattr(pygame, "K_DOWN", None),
            "escape": getattr(pygame, "K_ESCAPE", None),
        }
        key_value = key_map.get(key_name)
        if key_value is None:
            return {"ok": False, "error": "unsupported key"}
        traces = []
        for _index in range(repeat):
            event = _renforge_editor_fake_event(
                pygame.KEYDOWN,
                key=key_value,
                mod=getattr(pygame, "KMOD_SHIFT", 0) if shift else 0,
            )
            _renforge_editor_handle_event(event, state.pointer[0], state.pointer[1], 0.0)
            traces.append({"key": key_name, "shift": shift})
        state.last_event_trace = traces
        return {"ok": True, "repeat": repeat, "shift": shift, "active": state.active}


    def _renforge_editor_h_observe_selected(payload):
        candidate, error = _renforge_editor_resolve_selected_candidate()
        if candidate is None:
            return {"ok": False, "error": error}
        observation, observe_error = _renforge_editor_observation_for_candidate(candidate)
        if observation is None:
            return {"ok": False, "error": observe_error or "UNMEASURED"}
        _renforge_editor_accept_observation(observation)
        return {"ok": True, "observation": observation}


    def _renforge_editor_h_restore_preview(payload):
        return _renforge_editor_restore_preview()


    def _renforge_editor_h_set_opacity(payload):
        payload = payload or {}
        opacity = payload.get("opacity")
        try:
            opacity = float(opacity)
        except Exception:
            return {"ok": False, "error": "opacity must be numeric"}
        opacity = max(0.05, min(1.0, opacity))
        state = _renforge_editor_state()
        state.opacity = opacity
        renpy.restart_interaction()
        return {"ok": True, "opacity": opacity}


    def _renforge_editor_h_pointer(payload):
        payload = payload or {}
        x = payload.get("x")
        y = payload.get("y")
        if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
            return {"ok": False, "error": "x and y must be integers"}
        state = _renforge_editor_state()
        state.pointer = [int(x), int(y)]
        _renforge_editor_set_label(int(x), int(y))
        renpy.restart_interaction()
        return {"ok": True, "pointer": [int(x), int(y)]}


    def _renforge_editor_h_validate_runtime_key(payload):
        payload = payload or {}
        try:
            if int(payload.get("instance_count", 1) or 1) > 1:
                return {"ok": False, "lock_reason": "MULTI_INSTANCE_UNSUPPORTED"}
        except Exception:
            return {"ok": False, "lock_reason": "MULTI_INSTANCE_UNSUPPORTED"}
        runtime_key = payload.get("runtime_key")
        lock = _renforge_editor_validate_runtime_key(runtime_key)
        if lock is None:
            return {"ok": True}
        return {"ok": False, "lock_reason": lock}


    def _renforge_editor_h_coordinator_submit(payload):
        payload = payload or {}
        state = _renforge_editor_state()
        coordinator = _renforge_editor_ensure_coordinator()
        request_id = coordinator.submit(payload.get("observation"))
        return {
            "ok": True,
            "request_id": request_id,
            "main_thread_id": state.main_thread_id or threading.get_ident(),
        }


    def _renforge_editor_h_coordinator_collect(payload):
        applied = _renforge_editor_apply_coordinator_results()
        return {"ok": True, "applied": applied}


    def _renforge_editor_h_status(payload):
        state = _renforge_editor_state()
        return {
            "ok": True,
            "active": bool(state.active),
            "save_enabled": bool(state.save_enabled),
            "selected_widget_id": state.selected_widget_id,
            "selected_runtime_key": state.selected_runtime_key,
            "selected_lock_reason": state.selected_lock_reason,
            "opacity": float(state.opacity),
            "guide_x": state.guide_x,
            "guide_y": state.guide_y,
            "last_preview_method": state.last_preview_method,
            "last_restore_method": state.last_restore_method,
            "accepted_observations": list(state.accepted_observations),
        }


    def _renforge_editor_h_stop(payload):
        return _renforge_editor_exit()


    def _renforge_editor_is_active():
        return bool(_renforge_editor_state().active)


    def _renforge_editor_opacity():
        return float(_renforge_editor_state().opacity)


    def _renforge_editor_guide_x():
        return _renforge_editor_state().guide_x


    def _renforge_editor_guide_y():
        return _renforge_editor_state().guide_y


    def _renforge_editor_label_snapshot():
        state = _renforge_editor_state()
        rect = list(state.label_rect or [20, 20, 220, 32])
        if len(rect) != 4:
            rect = [20, 20, 220, 32]
        return {
            "x": int(rect[0]),
            "y": int(rect[1]),
            "w": int(rect[2]),
            "h": int(rect[3]),
            "alpha": float(max(0.1, min(1.0, state.label_alpha * state.opacity))),
            "text": str(state.label_text or ""),
        }


    _renforge_editor_state()
    _renforge_editor_ensure_coordinator()
    if callable(getattr(renpy.config, "periodic_callbacks", None)):
        pass
    if all(getattr(callback, "__name__", "") != "_renforge_editor_periodic" for callback in renpy.config.periodic_callbacks):
        renpy.config.periodic_callbacks.append(_renforge_editor_periodic)

    handlers = globals().get("_RENFORGE_HANDLERS")
    if isinstance(handlers, builtins.dict):
        handlers["editor_task0_start"] = _renforge_editor_h_start
        handlers["editor_task0_stop"] = _renforge_editor_h_stop
        handlers["editor_task0_select"] = _renforge_editor_h_select
        handlers["editor_task0_drag"] = _renforge_editor_h_drag
        handlers["editor_task0_key"] = _renforge_editor_h_key
        handlers["editor_task0_observe_selected"] = _renforge_editor_h_observe_selected
        handlers["editor_task0_restore_preview"] = _renforge_editor_h_restore_preview
        handlers["editor_task0_set_opacity"] = _renforge_editor_h_set_opacity
        handlers["editor_task0_pointer"] = _renforge_editor_h_pointer
        handlers["editor_task0_validate_runtime_key"] = _renforge_editor_h_validate_runtime_key
        handlers["editor_task0_coordinator_submit"] = _renforge_editor_h_coordinator_submit
        handlers["editor_task0_coordinator_collect"] = _renforge_editor_h_coordinator_collect
        handlers["editor_task0_status"] = _renforge_editor_h_status
