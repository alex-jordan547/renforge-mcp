init python:
    # RenForge in-game bridge.
    #
    # Injected temporarily into <project>/game/ by the launcher and removed
    # afterwards. Opens a localhost TCP server (token-authenticated) so an
    # external client can inspect and drive the running game.
    #
    # Threading model: the socket listener runs on a background thread, but any
    # call into the Ren'Py API MUST happen on the main thread. Each request is
    # therefore handed to the main thread through a queue and executed inside a
    # `config.periodic_callbacks` drain; the listener thread blocks on a
    # per-request Event until the result is ready, then writes the reply.
    #
    # Configuration comes from the environment:
    #   RENFORGE_BRIDGE_TOKEN        required 64-lowercase-hex auth token
    #   RENFORGE_BRIDGE_SESSION_ID   required 32-lowercase-hex session id
    #   RENFORGE_BRIDGE_PROJECT_ROOT required canonical absolute project root
    #   RENFORGE_BRIDGE_PORT         default 0 (an ephemeral port is chosen)
    # The launcher reserves starting metadata at
    #   <project>/.renforge/control/bridge.json
    # Before serving, the bridge validates that record and publishes ready.

    import base64
    import builtins
    import collections
    import hashlib
    import hmac as _hmac
    import json
    import os
    import queue
    import socket
    import struct
    import sys
    import threading
    import time
    import types

    try:
        import pygame_sdl2 as pygame
    except Exception:
        # A real Ren'Py SDK always provides pygame_sdl2. Keeping the import
        # optional lets the bridge's non-engine RPC tests load this file with a
        # minimal fake runtime; input commands report a clear error if events
        # cannot be posted.
        pygame = None

    # Keep runtime state off renpy.store / rollback. `init python` top-level
    # names become store fields; a Queue/lock inside the bridge is not picklable
    # and would break QuickSave. A dedicated sys.modules entry is never saved.
    if "_renforge_runtime" not in sys.modules:
        sys.modules["_renforge_runtime"] = types.ModuleType("_renforge_runtime")
    _renforge_runtime = sys.modules["_renforge_runtime"]
    if not hasattr(_renforge_runtime, "bridge"):
        _renforge_runtime.bridge = None

    class _RenforgeRequest(object):
        # NB: no __slots__ — Ren'Py forbids slotted classes in init python
        # (they are incompatible with its rollback machinery).
        def __init__(self, command, payload):
            self.command = command
            self.payload = payload
            self.event = threading.Event()
            self.result = None
            self.error = None

    class _RenforgeBridge(object):
        def __init__(self, host, port, token, project_root, session_id):
            self.host = host
            self.port = port
            self.token = token
            self.project_root = project_root
            self.session_id = session_id
            self.requests = queue.Queue()
            self.stop = threading.Event()
            self.thread = None
            self.current_label = None
            # Pushed events buffer (main-thread only): dialogue lines, label
            # entries and exceptions. Clients retrieve them via `poll_events`.
            self.events = collections.deque(maxlen=1000)
            self.event_seq = 0
            self.last_say = None
            self.prev_exception_handler = None
            # Correlation id for the command currently executing on the main
            # thread; business events inherit it so agents can attribute effects.
            self.current_correlation_id = None
            self.prev_skipping = None
            self.prev_afm = None
            self.prev_history_index = None
            self.interaction_counter = 0
            self._skip_reason_hint = None

        def push_event(self, kind, data):
            self.event_seq += 1
            record = {
                "seq": self.event_seq,
                "type": kind,
                "timestamp": time.time(),
            }
            if self.current_correlation_id is not None:
                record["correlation_id"] = self.current_correlation_id
            if data:
                record.update(data)
            self.events.append(record)
            return record

    def _renforge_jsonable(value):
        """Best-effort conversion of a Python value to something JSON-safe."""
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [_renforge_jsonable(v) for v in value]
        if isinstance(value, builtins.dict):
            return {str(k): _renforge_jsonable(v) for k, v in value.items()}
        return repr(value)

    def _renforge_store_snapshot():
        snapshot = {}
        for name, value in list(vars(renpy.store).items()):
            if name.startswith("_"):
                continue
            if callable(value):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                continue
            snapshot[name] = value
        return snapshot

    _RENFORGE_STATE_INCLUDES = ("metrics", "audio")

    def _renforge_state_includes(payload):
        """Validate the optional, compact sections requested with get_state."""
        payload = payload or {}
        if "include" not in payload or payload.get("include") is None:
            return [], None
        include = payload.get("include")
        if isinstance(include, str) or not isinstance(include, (builtins.list, tuple)):
            return [], "include must be a list containing only: metrics, audio"
        unknown = [name for name in include if name not in _RENFORGE_STATE_INCLUDES]
        if unknown:
            return [], "include contains unsupported values: %s (supported: metrics, audio)" % ", ".join(str(name) for name in unknown)
        return list(builtins.dict.fromkeys(include)), None

    def _renforge_size(value):
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return None
        try:
            return {"width": int(value[0]), "height": int(value[1])}
        except (TypeError, ValueError, OverflowError):
            return None

    def _renforge_physical_size():
        get_size = getattr(renpy, "get_physical_size", None)
        if callable(get_size):
            try:
                size = _renforge_size(get_size())
                if size is not None:
                    return size
            except Exception:
                pass

        draw = getattr(getattr(renpy, "display", None), "draw", None)
        get_size = getattr(draw, "get_physical_size", None)
        if callable(get_size):
            try:
                size = _renforge_size(get_size())
                if size is not None:
                    return size
            except Exception:
                pass

        preferences = getattr(getattr(renpy, "game", None), "preferences", None)
        size = _renforge_size(getattr(preferences, "physical_size", None))
        if size is not None:
            return size

        config = getattr(renpy, "config", None)
        width = getattr(config, "physical_width", None)
        height = getattr(config, "physical_height", None)
        if width and height:
            return _renforge_size((width, height))
        return None

    def _renforge_h_get_metrics(payload):
        """Return inexpensive frame, image-cache, and window diagnostics."""
        interface = getattr(getattr(renpy, "display", None), "interface", None)
        frame_times = list(getattr(interface, "frame_times", None) or [])
        intervals = []
        for previous, current in zip(frame_times, frame_times[1:]):
            try:
                delta = float(current) - float(previous)
            except (TypeError, ValueError):
                continue
            if delta > 0:
                intervals.append(delta)

        fps = 0.0
        if intervals:
            recent = intervals[-10:]
            average = sum(recent) / len(recent)
            if average > 0:
                fps = 1.0 / average

        render_time_ms = None
        get_render_time = getattr(renpy, "get_render_time", None)
        if callable(get_render_time):
            try:
                render_time_ms = float(get_render_time()) * 1000.0
            except (TypeError, ValueError, OverflowError):
                render_time_ms = None
        if render_time_ms is None:
            render_time_ms = (intervals[-1] * 1000.0) if intervals else 0.0

        image_cache_size = 0
        image_cache_entries = 0
        image_cache_limit = None
        image_module = getattr(getattr(renpy, "display", None), "im", None)
        image_cache = getattr(image_module, "cache", None)
        if image_cache is not None:
            get_total_size = getattr(image_cache, "get_total_size", None)
            try:
                if callable(get_total_size):
                    image_cache_size = get_total_size()
                else:
                    image_cache_size = getattr(image_cache, "cache_size", 0)
                image_cache_entries = len(getattr(image_cache, "cache", {}) or {})
                image_cache_limit = getattr(image_cache, "cache_limit", None)
            except Exception:
                image_cache_size = 0

        config = getattr(renpy, "config", None)
        logical = _renforge_size((
            getattr(config, "screen_width", None),
            getattr(config, "screen_height", None),
        ))
        return {
            "render_time_ms": _renforge_jsonable(render_time_ms),
            "fps": _renforge_jsonable(fps),
            "image_cache_size": _renforge_jsonable(image_cache_size),
            "image_cache_entries": _renforge_jsonable(image_cache_entries),
            "image_cache_limit": _renforge_jsonable(image_cache_limit),
            "window": {
                "logical": logical,
                "physical": _renforge_physical_size(),
            },
        }

    def _renforge_audio_channel_names():
        names = []
        audio = getattr(getattr(renpy, "audio", None), "audio", None)
        for channel in list(getattr(audio, "all_channels", None) or []):
            name = getattr(channel, "name", channel)
            if name is not None and str(name) not in names:
                names.append(str(name))
        for name in list((getattr(audio, "channels", None) or {}).keys()):
            if str(name) not in names:
                names.append(str(name))
        if not names:
            names = ["music", "sound", "voice"]
        return names

    def _renforge_audio_value(music, channel, method_name):
        method = getattr(music, method_name, None)
        if not callable(method):
            return None
        try:
            return _renforge_jsonable(method(channel=channel))
        except TypeError:
            try:
                return _renforge_jsonable(method(channel))
            except Exception:
                return None
        except Exception:
            return None

    def _renforge_h_get_audio_state(payload):
        """Return one compact record for every registered audio channel."""
        music = getattr(renpy, "music", None)
        audio = getattr(getattr(renpy, "audio", None), "audio", None)
        channels = getattr(audio, "channels", None) or {}
        result = {}
        for name in _renforge_audio_channel_names():
            channel = channels.get(name)
            playing = _renforge_audio_value(music, name, "get_playing")
            volume = _renforge_audio_value(music, name, "get_volume")
            pause = _renforge_audio_value(music, name, "get_pause")
            if channel is not None:
                if volume is None:
                    volume = _renforge_jsonable(getattr(channel, "actual_volume", None))
                    if volume is None:
                        volume = _renforge_jsonable(getattr(channel, "chan_volume", None))
                if pause is None:
                    context = getattr(channel, "context", None)
                    pause = _renforge_jsonable(getattr(context, "pause", None))
            result[name] = {
                "playing": playing,
                "volume": volume,
                "pause": pause,
            }
        return result

    def _renforge_screen_display_name(displayable, fallback):
        raw_name = getattr(displayable, "screen_name", None)
        if isinstance(raw_name, (list, tuple)):
            raw_name = " ".join(str(part) for part in raw_name)
        if raw_name:
            return str(raw_name)
        return fallback

    def _renforge_h_inspect_screen(payload):
        payload = payload or {}
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            return {"ok": False, "error": "screen name is required"}
        name = name.strip()
        get_screen = getattr(renpy, "get_screen", None)
        if not callable(get_screen):
            return {"ok": False, "error": "screen inspection is unavailable"}
        try:
            displayable = get_screen(name)
        except Exception as exc:
            return {"ok": False, "error": "could not inspect screen %s: %s" % (name, exc)}
        if displayable is None:
            return {
                "ok": True,
                "active": False,
                "name": name,
                "error": "screen not showing: %s" % name,
            }

        raw_scope = getattr(displayable, "scope", {}) or {}
        try:
            scope_items = raw_scope.items()
        except AttributeError:
            scope_items = []
        scope = {}
        for key, value in scope_items:
            if str(key) in ("_args", "_kwargs", "_scope", "_name", "_debug"):
                continue
            scope[str(key)] = _renforge_jsonable(value)

        raw_args = raw_scope.get("_args", ()) if hasattr(raw_scope, "get") else ()
        raw_kwargs = raw_scope.get("_kwargs", {}) if hasattr(raw_scope, "get") else {}
        if raw_args is None:
            raw_args = ()
        if not isinstance(raw_args, (list, tuple)):
            raw_args = (raw_args,)
        if not isinstance(raw_kwargs, builtins.dict):
            raw_kwargs = {}
        arguments = {
            "args": _renforge_jsonable(list(raw_args)),
            "kwargs": _renforge_jsonable(raw_kwargs),
        }
        return {
            "ok": True,
            "active": True,
            "name": _renforge_screen_display_name(displayable, name),
            "layer": _renforge_jsonable(getattr(displayable, "layer", None)),
            "scope": scope,
            "arguments": arguments,
        }

    def _renforge_selected_store_variables(names):
        """Return a compact map of selected store paths (supports dotted names)."""
        selected = {}
        if not names:
            return selected
        store = renpy.store
        for raw_name in names:
            try:
                name = str(raw_name)
            except Exception:
                continue
            if not name:
                continue
            if "." not in name:
                if hasattr(store, name):
                    try:
                        selected[name] = _renforge_jsonable(getattr(store, name))
                    except Exception:
                        pass
                continue
            # Dotted path: walk attributes from renpy.store / renpy modules.
            parts = name.split(".")
            current = store
            if parts[0] == "config":
                current = renpy.config
                parts = parts[1:]
            elif parts[0] == "_preferences":
                current = getattr(store, "_preferences", None)
                parts = parts[1:]
            ok = current is not None
            for part in parts:
                if not ok:
                    break
                try:
                    current = getattr(current, part)
                except Exception:
                    ok = False
            if ok:
                try:
                    selected[name] = _renforge_jsonable(current)
                except Exception:
                    pass
        return selected

    def _renforge_h_get_state(payload):
        include, include_error = _renforge_state_includes(payload)
        if include_error is not None:
            return {"ok": False, "error": include_error}
        payload = payload or {}
        profile = payload.get("state_profile") or "full"
        try:
            profile = str(profile).strip().lower()
        except Exception:
            profile = "full"
        if profile not in ("minimal", "interaction", "debug", "full"):
            return {
                "ok": False,
                "error": "state_profile must be one of: minimal, interaction, debug, full",
            }
        try:
            showing = list(renpy.get_showing_tags())
        except Exception:
            showing = []
        try:
            menu_active = renpy.get_screen("choice") is not None
        except Exception:
            menu_active = False
        bridge = _renforge_runtime.bridge
        result = {
            "current_label": bridge.current_label if bridge is not None else None,
            "showing_tags": showing,
            "menu": menu_active,
            "state_profile": profile,
        }
        if bridge is not None and getattr(bridge, "last_say", None):
            result["dialogue"] = bridge.last_say
        try:
            result["skipping"] = _renforge_jsonable(getattr(renpy.config, "skipping", None))
        except Exception:
            pass
        try:
            prefs = getattr(renpy.store, "_preferences", None)
            if prefs is not None:
                result["auto"] = bool(getattr(prefs, "afm_enable", False))
        except Exception:
            pass

        extra_vars = payload.get("variables") or payload.get("variable_names") or []
        if isinstance(extra_vars, str):
            extra_vars = [extra_vars]
        if profile == "full":
            result["variables"] = _renforge_store_snapshot()
        elif profile in ("interaction", "debug"):
            names = [
                "config.skipping",
                "_preferences.skip_after_choices",
                "_preferences.skip_unseen",
                "_preferences.afm_enable",
            ]
            if extra_vars:
                names.extend(list(extra_vars))
            selected = _renforge_selected_store_variables(names)
            if selected:
                result["variables"] = selected
        elif extra_vars:
            selected = _renforge_selected_store_variables(list(extra_vars))
            if selected:
                result["variables"] = selected

        if "metrics" in include:
            result["metrics"] = _renforge_h_get_metrics({})
        if "audio" in include:
            result["audio"] = {"channels": _renforge_h_get_audio_state({})}
        return result

    # --- handlers: all run on the MAIN thread -----------------------------

    def _renforge_h_ping(payload):
        return {"ok": True, "pong": True}

    def _renforge_h_get_metrics_handler(payload):
        return {"ok": True, "metrics": _renforge_h_get_metrics(payload)}

    def _renforge_h_get_audio_state_handler(payload):
        return {"ok": True, "channels": _renforge_h_get_audio_state(payload)}

    def _renforge_h_eval(payload):
        expr = (payload or {}).get("expr", "")
        value = eval(expr, {"__builtins__": __builtins__}, vars(renpy.store))
        return {"expr": expr, "value": _renforge_jsonable(value)}

    def _renforge_h_get_var(payload):
        name = (payload or {}).get("name")
        return {"name": name, "value": _renforge_jsonable(getattr(renpy.store, name))}

    def _renforge_h_set_var(payload):
        name = (payload or {}).get("name")
        value = (payload or {}).get("value")
        setattr(renpy.store, name, value)
        return {"name": name, "value": value, "ok": True}

    def _renforge_h_screenshot(payload):
        payload = payload or {}
        width = int(payload.get("width", 0) or 0)
        height = int(payload.get("height", 0) or 0)
        # A single dimension keeps the game's aspect ratio.
        note = None
        logical_width = getattr(renpy.config, "screen_width", None)
        logical_height = getattr(renpy.config, "screen_height", None)
        if logical_width and logical_height:
            if width and not height:
                height = max(1, int(round(width * logical_height / float(logical_width))))
            elif height and not width:
                width = max(1, int(round(height * logical_width / float(logical_height))))
        elif (width and not height) or (height and not width):
            # Never silently ignore the requested size: without the logical
            # ratio the frame comes back at native resolution, so say so.
            width = height = 0
            note = "aspect ratio unavailable; captured at native resolution"
        size = (width, height) if (width and height) else None
        data = renpy.screenshot_to_bytes(size)  # PNG bytes
        reply = {
            "format": "png",
            "base64": base64.b64encode(data).decode("ascii"),
            # The digest lets an external client use the exact frame it
            # inspected as an optimistic click guard, without storing image
            # data in the bridge process.
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if note:
            reply["note"] = note
        return reply

    def _renforge_h_advance(payload):
        # Post a "dismiss" event (the keymap action that advances dialogue).
        # queue_event is documented as thread-safe; the interaction loop
        # consumes it on the next frame.
        renpy.exports.queue_event("dismiss")
        return {"ok": True}

    # Readable input names that are semantic Ren'Py keymap actions. Keeping
    # these as names (rather than SDK integer constants) means they continue
    # to respect a game's customized config.keymap.
    _RENFORGE_INPUT_KEYMAP = {
        "enter": ("input_enter", "dismiss", "button_select"),
        "return": ("input_enter", "dismiss", "button_select"),
        "esc": ("game_menu",),
        "escape": ("game_menu",),
        "up": ("focus_up", "input_up", "viewport_uparrow", "bar_up"),
        "down": ("focus_down", "input_down", "viewport_downarrow", "bar_down"),
        "left": ("focus_left", "input_left", "viewport_leftarrow", "bar_left"),
        "right": ("focus_right", "input_right", "viewport_rightarrow", "bar_right"),
        "pageup": ("rollback", "viewport_pageup"),
        "pagedown": ("rollforward", "viewport_pagedown"),
        "backspace": ("input_backspace",),
        "delete": ("input_delete", "save_delete"),
        "home": ("input_home",),
        "end": ("input_end",),
        "space": ("dismiss", "button_select"),
        "tab": ("toggle_skip",),
    }

    # A small explicit set of keys without a useful Ren'Py semantic action.
    # These are posted as real KEYDOWN/KEYUP pairs so custom screens can bind
    # them with a normal key statement.
    _RENFORGE_DIRECT_KEY_ATTRS = {
        "f1": "K_F1",
        "f2": "K_F2",
        "f3": "K_F3",
        "f4": "K_F4",
        "f5": "K_F5",
        "f6": "K_F6",
        "f7": "K_F7",
        "f8": "K_F8",
        "f9": "K_F9",
        "f10": "K_F10",
        "f11": "K_F11",
        "f12": "K_F12",
    }

    def _renforge_focused_input():
        """Return the focused Ren'Py Input, or an explicit diagnostic."""
        display = getattr(renpy, "display", None)
        focus = getattr(display, "focus", None)
        get_focused = getattr(focus, "get_focused", None)
        if not callable(get_focused):
            return None, "cannot verify focused Ren'Py Input (focus API unavailable)"
        try:
            widget = get_focused()
        except Exception as exc:
            return None, "cannot verify focused Ren'Py Input: %s" % exc

        behavior = getattr(display, "behavior", None)
        input_type = getattr(behavior, "Input", None)

        def _is_input(candidate):
            if candidate is None:
                return False
            if callable(input_type):
                try:
                    if isinstance(candidate, input_type):
                        return True
                except TypeError:
                    pass
            return getattr(getattr(candidate, "__class__", None), "__name__", "") == "Input"

        # Ren'Py can have an active Input screen without assigning keyboard
        # focus yet (notably after a warp under Xvfb). Select the visible Input
        # through the engine focus API before posting TEXTINPUT events.
        if widget is None:
            change_focus = getattr(focus, "change_focus", None)
            for candidate in list(getattr(focus, "focus_list", None) or []):
                candidate_widget = getattr(candidate, "widget", None)
                if not _is_input(candidate_widget) or not callable(change_focus):
                    continue
                try:
                    change_focus(candidate)
                    widget = get_focused()
                except Exception:
                    widget = None
                if _is_input(widget):
                    break

        if widget is None:
            get_screen = getattr(renpy, "get_screen", None)
            force_focus = getattr(focus, "force_focus", None)
            if callable(get_screen) and callable(force_focus):
                try:
                    input_screen = get_screen("input")
                    input_widget = getattr(input_screen, "widgets", {}).get("input")
                    if _is_input(input_widget):
                        force_focus(input_widget)
                        widget = get_focused()
                except Exception:
                    widget = None

        if widget is None:
            return None, "no focused Ren'Py Input; text was not sent"
        if _is_input(widget):
            return widget, None
        return None, "no focused Ren'Py Input; focused widget is %s" % (
            getattr(getattr(widget, "__class__", None), "__name__", "unknown"),
        )

    def _renforge_h_send_input(payload):
        payload = payload or {}
        supplied = [
            name
            for name in ("text", "key", "scroll", "drag")
            if name in payload and payload.get(name) is not None
        ]
        if len(supplied) != 1:
            return {
                "ok": False,
                "error": "exactly one of text, key, or scroll is required",
            }

        submit = payload.get("submit", False)
        if not isinstance(submit, bool):
            return {"ok": False, "error": "submit must be a boolean"}
        if supplied[0] != "text" and submit:
            return {"ok": False, "error": "submit is only valid with text input"}

        if supplied[0] == "text":
            text = payload.get("text")
            if not isinstance(text, str):
                return {"ok": False, "error": "text must be a string"}
            if pygame is None:
                return {"ok": False, "error": "pygame_sdl2 event API is unavailable"}
            _focused, focus_error = _renforge_focused_input()
            if focus_error is not None:
                return {"ok": False, "error": focus_error}
            for character in text:
                event = pygame.event.Event(pygame.TEXTINPUT, {"text": character})
                pygame.event.post(event)
            if submit:
                renpy.exports.queue_event("input_enter")
            return {
                "ok": True,
                "mode": "text",
                "characters": len(text),
                "submitted": submit,
            }

        if supplied[0] == "key":
            key = payload.get("key")
            if not isinstance(key, str) or not key.strip():
                return {"ok": False, "error": "key must be a non-empty string"}
            key = key.strip().casefold()
            semantic = _RENFORGE_INPUT_KEYMAP.get(key)
            if semantic is not None:
                renpy.exports.queue_event(list(semantic))
                return {"ok": True, "mode": "key", "key": key, "event": semantic[0]}

            attr_name = _RENFORGE_DIRECT_KEY_ATTRS.get(key)
            keycode = (
                getattr(pygame, attr_name, None)
                if pygame is not None and attr_name is not None
                else None
            )
            if keycode is None:
                supported = sorted(set(_RENFORGE_INPUT_KEYMAP) | set(_RENFORGE_DIRECT_KEY_ATTRS))
                return {
                    "ok": False,
                    "error": "unknown key %r; supported keys: %s" % (key, ", ".join(supported)),
                }
            if pygame is None:
                return {"ok": False, "error": "pygame_sdl2 event API is unavailable"}
            mod = getattr(pygame, "KMOD_NONE", 0)
            for event_type in (pygame.KEYDOWN, pygame.KEYUP):
                event = pygame.event.Event(
                    event_type,
                    {"key": keycode, "mod": mod, "unicode": "", "repeat": 0},
                )
                pygame.event.post(event)
            return {"ok": True, "mode": "key", "key": key, "keycode": keycode}


        if supplied[0] == "drag":
            drag = payload.get("drag")
            if not isinstance(drag, builtins.dict):
                return {"ok": False, "error": "drag must be an object with points and button"}
            raw_points = drag.get("points")
            if not isinstance(raw_points, builtins.list) or not raw_points:
                return {"ok": False, "error": "drag points must be a non-empty list"}
            coordinate_space = str(drag.get("coordinate_space", "logical") or "logical").casefold()
            if coordinate_space not in ("logical", "screenshot"):
                return {"ok": False, "error": "coordinate_space must be logical or screenshot"}
            button = drag.get("button", 1)
            if isinstance(button, bool) or not isinstance(button, builtins.int):
                return {"ok": False, "error": "drag button must be an integer"}
            frame_data = None
            if coordinate_space == "screenshot":
                frame_data = renpy.screenshot_to_bytes(None)
            logical_points = []
            for raw_point in raw_points:
                if not isinstance(raw_point, builtins.list) or len(raw_point) != 2:
                    return {"ok": False, "error": "drag points must be coordinate pairs"}
                raw_x, raw_y = raw_point
                if (
                    isinstance(raw_x, bool)
                    or not isinstance(raw_x, (builtins.int, builtins.float))
                    or isinstance(raw_y, bool)
                    or not isinstance(raw_y, (builtins.int, builtins.float))
                ):
                    return {"ok": False, "error": "drag points must be numeric"}
                try:
                    point_x = int(round(float(raw_x)))
                    point_y = int(round(float(raw_y)))
                except (TypeError, ValueError, OverflowError):
                    return {"ok": False, "error": "drag points must be numeric"}
                if point_x < 0 or point_y < 0:
                    return {"ok": False, "error": "drag coordinates must be non-negative"}
                point_x, point_y, frame_data, coordinate_error = _renforge_to_logical_coordinates(
                    point_x, point_y, coordinate_space, frame_data
                )
                if coordinate_error is not None:
                    return {"ok": False, "error": coordinate_error}
                logical_points.append([point_x, point_y])
            if pygame is None:
                return {"ok": False, "error": "pygame_sdl2 event API is unavailable"}
            interface = getattr(getattr(renpy, "display", None), "interface", None)
            if interface is not None:
                try:
                    interface.mouse_focused = True
                except Exception:
                    pass
                try:
                    interface.ignore_touch = False
                except Exception:
                    pass
            event_factory = getattr(getattr(pygame, "event", None), "Event", None)
            post = getattr(getattr(pygame, "event", None), "post", None)
            down_type = getattr(pygame, "MOUSEBUTTONDOWN", None)
            motion_type = getattr(pygame, "MOUSEMOTION", None)
            up_type = getattr(pygame, "MOUSEBUTTONUP", None)
            if (
                not callable(event_factory)
                or not callable(post)
                or down_type is None
                or motion_type is None
                or up_type is None
            ):
                return {"ok": False, "error": "pygame_sdl2 event API is unavailable"}

            def make_event(event_type, attrs):
                try:
                    return event_factory(event_type, **attrs)
                except TypeError:
                    return event_factory(event_type, attrs)

            first_x, first_y = logical_points[0]
            button_payload = {
                "button": button,
                "pos": (first_x, first_y),
                "x": first_x,
                "y": first_y,
                "touch": False,
                "test": True,
                "mod": 0,
            }
            post(make_event(down_type, button_payload))
            previous_x, previous_y = first_x, first_y
            held_buttons = tuple(int(button == candidate) for candidate in (1, 2, 3))
            for point_x, point_y in logical_points[1:]:
                post(
                    make_event(
                        motion_type,
                        {
                            "pos": (point_x, point_y),
                            "rel": (point_x - previous_x, point_y - previous_y),
                            "buttons": held_buttons,
                        },
                    )
                )
                previous_x, previous_y = point_x, point_y
            last_x, last_y = logical_points[-1]
            button_payload = {
                "button": button,
                "pos": (last_x, last_y),
                "x": last_x,
                "y": last_y,
                "touch": False,
                "test": True,
                "mod": 0,
            }
            post(make_event(up_type, button_payload))
            return {
                "ok": True,
                "mode": "drag",
                "points": logical_points,
                "button": button,
            }

        scroll = payload.get("scroll")
        if not isinstance(scroll, builtins.dict):
            return {"ok": False, "error": "scroll must be an object with x, y, and direction"}
        try:
            raw_x, raw_y = scroll.get("x"), scroll.get("y")
            if isinstance(raw_x, bool) or isinstance(raw_y, bool):
                raise ValueError
            x, y = int(round(float(raw_x))), int(round(float(raw_y)))
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "scroll requires numeric x and y"}
        if x < 0 or y < 0:
            return {"ok": False, "error": "scroll coordinates must be non-negative"}

        direction = scroll.get("direction")
        if direction is not None:
            direction = str(direction).casefold()
            direction = {"wheelup": "up", "wheeldown": "down"}.get(direction, direction)
            if direction not in ("up", "down"):
                return {"ok": False, "error": "scroll direction must be up or down"}

        amount = scroll.get("amount", scroll.get("delta", 1))
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            return {"ok": False, "error": "scroll amount must be a non-zero integer"}
        if isinstance(amount, float) and not amount.is_integer():
            return {"ok": False, "error": "scroll amount must be a non-zero integer"}
        amount = int(amount)
        if amount == 0:
            return {"ok": False, "error": "scroll amount must be a non-zero integer"}
        if direction is None:
            direction = "up" if amount < 0 else "down"
        amount = abs(amount)

        coordinate_space = str(scroll.get("coordinate_space", "logical") or "logical").casefold()
        if coordinate_space not in ("logical", "screenshot"):
            return {"ok": False, "error": "coordinate_space must be logical or screenshot"}
        frame_data = None
        if coordinate_space == "screenshot":
            frame_data = renpy.screenshot_to_bytes(None)
        x, y, frame_data, coordinate_error = _renforge_to_logical_coordinates(
            x, y, coordinate_space, frame_data
        )
        if coordinate_error is not None:
            return {"ok": False, "error": coordinate_error}
        if pygame is None:
            return {"ok": False, "error": "pygame_sdl2 event API is unavailable"}
        interface = getattr(getattr(renpy, "display", None), "interface", None)
        if interface is not None:
            try:
                interface.mouse_focused = True
            except Exception:
                pass
            try:
                interface.ignore_touch = False
            except Exception:
                pass
        button = 4 if direction == "up" else 5
        for _ in range(amount):
            event = pygame.event.Event(
                pygame.MOUSEBUTTONDOWN,
                {"button": button, "pos": (x, y), "x": x, "y": y},
            )
            pygame.event.post(event)
        return {
            "ok": True,
            "mode": "scroll",
            "x": x,
            "y": y,
            "direction": direction,
            "amount": amount,
        }

    def _renforge_invoke(fn):
        # Schedule work for the interaction loop. Prefer invoke_in_main_thread
        # when already on the main thread would re-enter drain; callers that
        # already run inside renforge_drain_bridge should call fn() directly
        # unless the work must raise engine control-flow exceptions (load/quit).
        invoke = getattr(renpy, "invoke_in_main_thread", None)
        if callable(invoke):
            invoke(fn)
        else:
            fn()

    def _renforge_run_action(action):
        run = getattr(renpy, "run", None)
        if not callable(run):
            run = getattr(getattr(renpy, "exports", None), "run", None)
        if not callable(run):
            raise RuntimeError("renpy.run is unavailable")
        return run(action)

    def _renforge_history_index():
        """Best-effort length of the rollback log."""
        game = getattr(renpy, "game", None)
        log = getattr(game, "log", None)
        entries = getattr(log, "log", None)
        if entries is None:
            return None
        try:
            return len(entries)
        except Exception:
            return None

    def _renforge_newest_quick_slot():
        newest = getattr(renpy, "newest_slot", None)
        if callable(newest):
            try:
                slot = newest("quick")
                if slot:
                    return str(slot)
            except Exception:
                pass
        # Fallbacks used across Ren'Py versions / templates.
        for candidate in ("quick-1", "quick-2", "quick-3", "_reload-1"):
            can_load = getattr(renpy, "can_load", None)
            if callable(can_load):
                try:
                    if can_load(candidate):
                        return candidate
                except Exception:
                    continue
        return "quick-1"

    def _renforge_emit_business(event_name, **data):
        bridge = _renforge_runtime.bridge
        if bridge is None:
            return None
        payload = {"event": event_name}
        payload.update(data)
        # type matches the business event name for easy filtering; event is kept
        # for doc-compatible clients that look for the nested field.
        return bridge.push_event(event_name, payload)

    def _renforge_correlation_from_payload(payload):
        payload = payload or {}
        for key in ("interaction_id", "correlation_id"):
            value = payload.get(key)
            if value is None or value == "":
                continue
            try:
                return str(value)
            except Exception:
                continue
        return None

    def _renforge_h_control(payload):
        payload = payload or {}
        action = str(payload.get("action", ""))
        # Names that exist on config.keymap / the default Keymap underlay.
        # Note: there is no "toggle_auto" or "quick_save"/"quick_load" keymap
        # entry — those used to return ok while doing nothing.
        key_events = {
            "advance": "dismiss",
            "rollback": "rollback",
            "toggle_skip": "toggle_skip",
            "toggle_auto": "toggle_afm",
            "toggle_afm": "toggle_afm",
            "game_menu": "game_menu",
            "hide_windows": "hide_windows",
        }
        if action in key_events:
            event_name = key_events[action]
            bridge = _renforge_runtime.bridge
            before_history = _renforge_history_index()
            before_skip = getattr(renpy.config, "skipping", None)
            before_afm = None
            try:
                prefs = getattr(renpy.store, "_preferences", None)
                before_afm = bool(getattr(prefs, "afm_enable", False)) if prefs is not None else None
            except Exception:
                before_afm = None
            renpy.exports.queue_event(event_name)
            result = {"ok": True, "action": action, "event": event_name}
            if action == "rollback":
                after_history = before_history
                if before_history is not None:
                    after_history = max(0, before_history - 1)
                business = _renforge_emit_business(
                    "rollback.completed",
                    from_history_index=before_history,
                    to_history_index=after_history,
                )
                if business is not None:
                    result["effect"] = {
                        "event": "rollback.completed",
                        "from_history_index": before_history,
                        "to_history_index": after_history,
                    }
            elif action == "toggle_skip":
                if bridge is not None:
                    # Hint for the watcher: next skip transition was agent-driven.
                    bridge._skip_reason_hint = "user_click"
                business = _renforge_emit_business(
                    "skip.changed",
                    previous=before_skip,
                    requested=True,
                )
                if business is not None:
                    result["effect"] = {"event": "skip.changed", "previous": before_skip}
            elif action in ("toggle_auto", "toggle_afm"):
                business = _renforge_emit_business(
                    "auto.changed",
                    previous=before_afm,
                    requested=True,
                )
                if business is not None:
                    result["effect"] = {"event": "auto.changed", "previous": before_afm}
            return result
        if action == "quick_save":
            quick_save = getattr(renpy.store, "QuickSave", None)
            if not callable(quick_save):
                _renforge_emit_business("quick_save.failed", reason="unavailable")
                return {"ok": False, "error": "QuickSave is unavailable", "action": action}
            try:
                _renforge_run_action(quick_save())
            except Exception as exc:
                _renforge_emit_business("quick_save.failed", reason=str(exc))
                return {"ok": False, "error": "quick_save failed: %s" % exc, "action": action}
            slot = _renforge_newest_quick_slot()
            path = None
            try:
                savedir = getattr(renpy.config, "savedir", None)
                if savedir and slot:
                    path = os.path.join(str(savedir), "%s.save" % slot)
            except Exception:
                path = None
            business = _renforge_emit_business(
                "quick_save.completed",
                slot=slot,
                path=path,
            )
            result = {"ok": True, "action": action, "slot": slot}
            if path:
                result["path"] = path
            if business is not None:
                result["effect"] = {
                    "event": "quick_save.completed",
                    "slot": slot,
                    "path": path,
                }
            return result
        if action == "quick_load":
            quick_load = getattr(renpy.store, "QuickLoad", None)
            if not callable(quick_load):
                _renforge_emit_business("quick_load.failed", reason="unavailable")
                return {"ok": False, "error": "QuickLoad is unavailable", "action": action}
            # Load raises FullRestartException; schedule it so the interaction
            # loop can propagate engine control-flow instead of catching it here.
            load_action = quick_load(confirm=False)
            slot = _renforge_newest_quick_slot()
            bridge = _renforge_runtime.bridge
            restored_label = bridge.current_label if bridge is not None else None
            restored_dialogue = bridge.last_say if bridge is not None else None

            def _do_quick_load():
                _renforge_run_action(load_action)

            _renforge_invoke(_do_quick_load)
            business = _renforge_emit_business(
                "quick_load.completed",
                slot=slot,
                restored_label=restored_label,
                restored_dialogue=restored_dialogue,
            )
            result = {
                "ok": True,
                "action": action,
                "slot": slot,
                "restored_label": restored_label,
            }
            if business is not None:
                result["effect"] = {
                    "event": "quick_load.completed",
                    "slot": slot,
                    "restored_label": restored_label,
                }
            return result
        if action == "reload_script":
            _renforge_invoke(renpy.reload_script)
            return {"ok": True, "action": action}
        if action == "restart_interaction":
            _renforge_invoke(renpy.restart_interaction)
            return {"ok": True, "action": action}
        if action == "quit":
            _renforge_invoke(renpy.quit)
            return {"ok": True, "action": action}
        return {"ok": False, "error": "unknown control action: %s" % action}

    def _renforge_h_save_slot(payload):
        payload = payload or {}
        slot = payload.get("slot")
        extra_info = payload.get("extra_info", "")
        if not isinstance(slot, str) or not slot.strip():
            return {"ok": False, "error": "save slot is required"}
        if extra_info is None:
            extra_info = ""
        if not isinstance(extra_info, str):
            return {"ok": False, "error": "extra_info must be a string"}

        can_save = getattr(renpy, "can_save", None)
        if callable(can_save):
            try:
                allowed = bool(can_save())
            except Exception as exc:
                return {
                    "ok": False,
                    "error": "cannot determine whether saving is available: %s" % exc,
                }
        else:
            config = getattr(renpy, "config", None)
            store = getattr(renpy, "store", None)
            allowed = bool(config and getattr(config, "save", True))
            allowed = allowed and not bool(store and getattr(store, "main_menu", False))
            allowed = allowed and not bool(store and getattr(store, "_in_replay", False))

        if not allowed:
            return {"ok": False, "error": "saving is unavailable in the current game state"}

        try:
            renpy.save(slot, extra_info=extra_info)
        except Exception as exc:
            _renforge_emit_business("save.failed", slot=slot, reason=str(exc))
            return {"ok": False, "error": "save failed: %s" % exc}

        _renforge_emit_business("save.completed", slot=slot, extra_info=extra_info)
        return {"ok": True, "slot": slot, "extra_info": extra_info}

    def _renforge_h_load_slot(payload):
        payload = payload or {}
        slot = payload.get("slot")
        if not isinstance(slot, str) or not slot.strip():
            return {"ok": False, "error": "save slot is required"}

        can_load = getattr(renpy, "can_load", None)
        if callable(can_load):
            try:
                exists = bool(can_load(slot))
            except Exception as exc:
                return {"ok": False, "error": "cannot inspect save slot: %s" % exc}
        else:
            list_slots = getattr(renpy, "list_slots", None)
            if not callable(list_slots):
                return {"ok": False, "error": "save slot lookup is unavailable"}
            try:
                exists = slot in list_slots()
            except Exception as exc:
                return {"ok": False, "error": "cannot inspect save slot: %s" % exc}

        if not exists:
            return {"ok": False, "error": "save slot not found: %s" % slot}

        load = getattr(renpy, "load", None)
        if not callable(load):
            return {"ok": False, "error": "save loading is unavailable"}

        def _do_load():
            load(slot)

        _renforge_invoke(_do_load)
        bridge = _renforge_runtime.bridge
        restored_label = bridge.current_label if bridge is not None else None
        _renforge_emit_business(
            "load.completed",
            slot=slot,
            restored_label=restored_label,
        )
        return {"ok": True, "slot": slot, "restored_label": restored_label}

    def _renforge_h_list_slots(payload):
        payload = payload or {}
        regexp = payload.get("regexp")
        if regexp is not None and not isinstance(regexp, str):
            return {"ok": False, "error": "regexp must be a string"}

        list_slots = getattr(renpy, "list_slots", None)
        if not callable(list_slots):
            return {"ok": False, "error": "save slot listing is unavailable"}
        try:
            slot_names = list_slots(regexp=regexp)
        except Exception as exc:
            return {"ok": False, "error": "could not list save slots: %s" % exc}

        slot_json = getattr(renpy, "slot_json", None)
        slot_mtime = getattr(renpy, "slot_mtime", None)
        slots = []
        for name in slot_names:
            try:
                metadata = slot_json(name) if callable(slot_json) else None
            except Exception:
                metadata = None
            extra_info = ""
            if isinstance(metadata, builtins.dict):
                extra_info = metadata.get("_save_name", "")
            try:
                mtime = slot_mtime(name) if callable(slot_mtime) else None
            except Exception:
                mtime = None
            slots.append(
                {
                    "name": str(name),
                    "extra_info": _renforge_jsonable(extra_info),
                    "mtime": _renforge_jsonable(mtime),
                }
            )

        return {"ok": True, "slots": slots}

    def _renforge_h_poll_events(payload):
        payload = payload or {}
        since = int(payload.get("since", 0) or 0)
        bridge = _renforge_runtime.bridge
        if bridge is None:
            return {"events": [], "cursor": 0}
        events = [e for e in list(bridge.events) if e["seq"] > since]
        cursor = bridge.event_seq
        return {"events": events, "cursor": cursor}

    def _renforge_screen_name(focus):
        scr = getattr(focus, "screen", None)
        name = getattr(scr, "screen_name", None)
        if name is None:
            name = getattr(focus, "screen_name", None)
        if not name:
            return None
        try:
            return name[0] if isinstance(name, (list, tuple)) else str(name)
        except Exception:
            return None

    def _renforge_focus_text(widget):
        """Best-effort accessible text for a Ren'Py focus widget.

        Ren'Py 8.5 changed ``Displayable._tts_all`` to require a ``raw``
        boolean (``_tts_all(self, raw: bool)``). Calling it with no arguments
        raises ``TypeError``, which earlier code swallowed — every button then
        looked unlabeled and choice selection / autopilot went blind. Prefer
        the 8.5 contract (``raw=False`` = spoken text); fall back to a no-arg
        call only for older displayables / ``get_text``.
        """
        if widget is None:
            return ""
        text = None
        for method_name in ("_tts_all", "get_text"):
            method = getattr(widget, method_name, None)
            if not callable(method):
                continue
            try:
                if method_name == "_tts_all":
                    try:
                        text = method(False)
                    except TypeError:
                        text = method()
                else:
                    text = method()
            except Exception:
                continue
            if text:
                break
        if text is None:
            for attr_name in ("text", "label", "caption", "value"):
                value = getattr(widget, attr_name, None)
                if value is not None and not callable(value):
                    text = value
                    if text:
                        break
        if isinstance(text, (list, tuple)):
            text = " ".join(str(part) for part in text if part is not None)
        if text is None:
            return ""
        try:
            return str(text).strip()
        except Exception:
            return ""

    def _renforge_focus_type(focus, widget):
        # Some Ren'Py displayables expose a semantic type; otherwise use the
        # displayable class name and normalize common controls to useful roles.
        raw = None
        # Prefer the concrete displayable. Ren'Py's Focus wrapper may expose a
        # generic ``kind='focus'`` marker which is less useful than the button
        # or input class that actually receives the click.
        for owner in (widget, focus):
            if owner is None:
                continue
            for attr_name in ("role", "kind", "widget_type", "displayable_type", "type"):
                value = getattr(owner, attr_name, None)
                if value is not None and not callable(value):
                    if str(value).casefold() in ("", "focus", "default"):
                        continue
                    raw = value
                    break
            if raw is not None:
                break
        if raw is None:
            raw = getattr(getattr(widget, "__class__", None), "__name__", "focus")
        try:
            name = str(raw)
        except Exception:
            name = "focus"
        lowered = name.casefold()
        for marker, role in (
            ("button", "button"),
            ("input", "input"),
            ("bar", "bar"),
            ("viewport", "viewport"),
            ("image", "image"),
            ("text", "text"),
        ):
            if marker in lowered:
                return role
        return name or "focus"

    def _renforge_focus_enabled(focus, widget):
        for owner in (focus, widget):
            if owner is None:
                continue
            for attr_name in ("enabled", "sensitive", "is_sensitive"):
                value = getattr(owner, attr_name, None)
                if value is None:
                    continue
                try:
                    value = value() if callable(value) else value
                except Exception:
                    continue
                return bool(value)
        return True

    def _renforge_explicit_focus_id(focus, widget):
        for owner in (focus, widget):
            if owner is None:
                continue
            for attr_name in ("mcp_id", "id", "widget_id", "focus_id", "name", "key"):
                value = getattr(owner, attr_name, None)
                if value is None or callable(value):
                    continue
                try:
                    value = str(value).strip()
                except Exception:
                    continue
                if value:
                    return value
        return None
    def _renforge_named_focus_id(screen_name, widget, cache):
        if not screen_name or widget is None:
            return None
        widget_ids = cache.get(screen_name)
        if widget_ids is None:
            widget_ids = {}
            try:
                screen = renpy.get_screen(screen_name)
                named_widgets = getattr(screen, "widgets", None) or {}
                for name, named_widget in named_widgets.items():
                    widget_ids.setdefault(id(named_widget), str(name))
            except Exception:
                pass
            cache[screen_name] = widget_ids
        for candidate in (widget, getattr(widget, "child", None)):
            if candidate is not None:
                widget_id = widget_ids.get(id(candidate))
                if widget_id:
                    return widget_id
        return None



    def _renforge_focus_action_name(focus, widget):
        """Best-effort human/action name for a focusable control."""
        for owner in (widget, focus):
            if owner is None:
                continue
            for attr_name in ("action", "clicked", "alternate", "hovered"):
                value = getattr(owner, attr_name, None)
                if value is None:
                    continue
                # Flatten single-item lists of actions.
                if isinstance(value, (list, tuple)) and len(value) == 1:
                    value = value[0]
                try:
                    if hasattr(value, "__class__"):
                        name = getattr(value.__class__, "__name__", None)
                        if name and name not in ("list", "tuple", "object"):
                            return str(name)
                    return str(value)
                except Exception:
                    continue
        return None

    def _renforge_focus_zorder(focus, widget, ordinal):
        for owner in (widget, focus):
            if owner is None:
                continue
            for attr_name in ("zorder", "z", "layer_zorder"):
                value = getattr(owner, attr_name, None)
                if value is None or callable(value):
                    continue
                try:
                    return int(value)
                except (TypeError, ValueError, OverflowError):
                    continue
        # Focus list order is bottom→top in Ren'Py; higher index wins hits.
        return ordinal

    def _renforge_bounds_contain(bounds, x, y):
        try:
            left = int(bounds.get("x", 0))
            top = int(bounds.get("y", 0))
            width = int(bounds.get("width", 0))
            height = int(bounds.get("height", 0))
        except (TypeError, ValueError, OverflowError, AttributeError):
            return False
        return left <= x < left + width and top <= y < top + height

    def _renforge_mark_coverage(elements):
        """Annotate each element with whether a higher-z control covers its center."""
        # elements is a list of (focus, element) sorted by focus list order.
        for index, (_focus, element) in enumerate(elements):
            center = element.get("center") or {}
            try:
                cx = int(center.get("x"))
                cy = int(center.get("y"))
            except (TypeError, ValueError, OverflowError):
                element["covered"] = False
                element["clickable"] = bool(element.get("enabled", True)) and bool(element.get("visible", True))
                continue
            covered = False
            for later_focus, later in elements[index + 1 :]:
                bounds = later.get("bounds") or {}
                if _renforge_bounds_contain(bounds, cx, cy):
                    covered = True
                    break
            element["covered"] = covered
            element["clickable"] = (
                bool(element.get("enabled", True))
                and bool(element.get("visible", True))
                and not covered
            )
        return elements

    def _renforge_focusable_elements(max_items=None, max_text_chars=None):
        """Return ``(focus, element)`` pairs for visible focus rectangles.

        ``focus_list`` is Ren'Py's authoritative list of controls that can
        receive pointer/keyboard focus.  It already excludes hidden screens;
        zero-sized and off-layout entries are omitted here.  IDs prefer an
        explicit ``mcp_id`` / widget id, then ``screen.action`` form, then a
        deterministic synthetic path so an agent can list and immediately click.
        """
        elements = []
        used_ids = {}
        named_widget_ids = {}
        try:
            focus_list = renpy.display.focus.focus_list
        except Exception:
            return elements
        for ordinal, focus in enumerate(focus_list):
            if max_items is not None and len(elements) >= max_items:
                break
            x = getattr(focus, "x", None)
            y = getattr(focus, "y", None)
            w = getattr(focus, "w", None)
            h = getattr(focus, "h", None)
            if x is None or y is None or w is None or h is None:
                continue
            try:
                x, y, w, h = int(x), int(y), int(w), int(h)
            except (TypeError, ValueError, OverflowError):
                continue
            if w <= 0 or h <= 0:
                continue

            widget = getattr(focus, "widget", None)
            raw_text = _renforge_focus_text(widget)
            raw_screen = _renforge_screen_name(focus)
            raw_role = _renforge_focus_type(focus, widget)
            raw_action_name = _renforge_focus_action_name(focus, widget)
            zorder = _renforge_focus_zorder(focus, widget, ordinal)
            element_id = _renforge_explicit_focus_id(focus, widget)
            if not element_id:
                element_id = _renforge_named_focus_id(
                    raw_screen,
                    widget,
                    named_widget_ids,
                )
            if not element_id:
                # Prefer screen.action (semantic) over ordinal-heavy paths.
                if raw_screen and raw_action_name:
                    element_id = "%s.%s" % (raw_screen, raw_action_name)
                elif raw_screen and raw_text:
                    element_id = "%s.%s" % (raw_screen, raw_text)
                else:
                    element_id = "%s:%s:%s" % (
                        raw_screen or "screen",
                        raw_role,
                        raw_text or ordinal,
                    )
            element_id = _renforge_scene_string(element_id, _RENFORGE_SCENE_MAX_ID_CHARS)
            text = raw_text
            screen = raw_screen
            role = raw_role
            action_name = raw_action_name
            if max_text_chars is not None:
                text = _renforge_scene_string(text, max_text_chars)
                screen = _renforge_scene_string(screen, max_text_chars)
                role = _renforge_scene_string(role, max_text_chars)
                action_name = _renforge_scene_string(action_name, max_text_chars)
            count = used_ids.get(element_id, 0)
            used_ids[element_id] = count + 1
            if count:
                element_id = "%s#%s" % (element_id, count + 1)

            bounds = {"x": x, "y": y, "width": w, "height": h}
            element = {
                "id": element_id,
                "text": text or None,
                "type": role,
                "role": role,
                "screen": screen,
                "action": action_name,
                "bounds": bounds,
                "center": {"x": x + w // 2, "y": y + h // 2},
                "zorder": zorder,
                "enabled": _renforge_focus_enabled(focus, widget),
                "visible": True,
                "index": ordinal,
                "coordinate_space": "logical",
            }
            if max_text_chars is not None:
                element["_raw_type"] = raw_role
                element["_raw_screen"] = raw_screen
            elements.append((focus, element))
        return _renforge_mark_coverage(elements)

    def _renforge_focusable_choices():
        # Keep the historical choices API (text + compact index) unchanged;
        # generic UI enumeration above is intentionally broader and includes
        # controls without text.
        choices = []
        for focus, element in _renforge_focusable_elements():
            text = element.get("text")
            if text:
                choices.append((focus, text, element.get("screen")))
        return choices

    def _renforge_h_list_choices(payload):
        choices = _renforge_focusable_choices()
        return {"choices": [{"index": i, "text": t, "screen": s} for i, (_f, t, s) in enumerate(choices)]}

    def _renforge_h_list_ui_elements(payload):
        payload = payload or {}
        requested_screen = payload.get("screen")
        requested_text = payload.get("text")
        requested_type = payload.get("type", payload.get("element_type"))
        if requested_screen is not None:
            requested_screen = str(requested_screen).casefold()
        if requested_text is not None:
            requested_text = str(requested_text).casefold()
        if requested_type is not None:
            requested_type = str(requested_type).casefold()

        elements = []
        for _focus, element in _renforge_focusable_elements():
            if requested_screen and str(element.get("screen") or "").casefold() != requested_screen:
                continue
            if requested_type:
                kind = str(element.get("type") or "").casefold()
                role = str(element.get("role") or "").casefold()
                if requested_type not in (kind, role):
                    continue
            if requested_text:
                text = str(element.get("text") or "").casefold()
                if requested_text not in text:
                    continue
            elements.append(element)
        result = {"elements": elements}
        try:
            frame = renpy.screenshot_to_bytes(None)
            result["frame_id"] = hashlib.sha256(frame).hexdigest()
            width = getattr(renpy.config, "screen_width", None)
            height = getattr(renpy.config, "screen_height", None)
            if width and height:
                result["screenshot"] = {"width": int(width), "height": int(height)}
        except Exception:
            pass
        return result

    def _renforge_is_end_interaction(exc):
        end_interaction = getattr(
            getattr(getattr(renpy, "display", None), "core", None),
            "EndInteraction",
            None,
        )
        return end_interaction is not None and isinstance(exc, end_interaction)


    def _renforge_reset_testmouse_state():
        testmouse = getattr(getattr(renpy, "test", None), "testmouse", None)
        if testmouse is None:
            return
        try:
            setattr(testmouse, "mouse_pos", None)
        except Exception:
            pass
        mouse_buttons = getattr(testmouse, "mouse_buttons", None)
        if mouse_buttons is None:
            return
        if isinstance(mouse_buttons, dict):
            for key in list(mouse_buttons.keys()):
                mouse_buttons[key] = False
            return
        if isinstance(mouse_buttons, tuple):
            mouse_buttons = list(mouse_buttons)
            setattr(testmouse, "mouse_buttons", mouse_buttons)
        if isinstance(mouse_buttons, list):
            for index in range(len(mouse_buttons)):
                try:
                    mouse_buttons[index] = False
                except Exception:
                    mouse_buttons[index] = 0


    def _renforge_dispatch_mouse_motion(px, py):
        if pygame is None:
            return False
        event_type = getattr(pygame, "MOUSEMOTION", None)
        event_factory = getattr(getattr(pygame, "event", None), "Event", None)
        post = getattr(getattr(pygame, "event", None), "post", None)
        if event_type is None or not callable(event_factory):
            return False
        payload = {"pos": (px, py), "rel": (0, 0), "buttons": (0, 0, 0)}
        try:
            event = event_factory(event_type, **payload)
        except TypeError:
            event = event_factory(event_type, payload)
        if callable(post):
            post(event)
        mouse_handler = getattr(
            getattr(getattr(renpy, "display", None), "focus", None), "mouse_handler", None
        )
        if callable(mouse_handler):
            mouse_handler(event, px, py, False)
        return True

    def _renforge_dispatch_mouse_click(x, y, button=1):
        if pygame is None:
            return False
        event_factory = getattr(getattr(pygame, "event", None), "Event", None)
        if event_factory is None:
            return False
        event_type = getattr(pygame, "MOUSEBUTTONDOWN", None)
        up_type = getattr(pygame, "MOUSEBUTTONUP", None)
        if event_type is None or up_type is None:
            return False
        payload = {
            "button": button,
            "pos": (x, y),
            "x": x,
            "y": y,
            "touch": False,
            "test": True,
            "mod": 0,
        }
        try:
            down = event_factory(event_type, **payload)
            up = event_factory(up_type, **payload)
        except TypeError:
            down = event_factory(event_type, payload)
            up = event_factory(up_type, payload)
        focus_module = getattr(getattr(renpy, "display", None), "focus", None)
        mouse_handler = getattr(focus_module, "mouse_handler", None)
        get_focused = getattr(focus_module, "get_focused", None)
        if not callable(mouse_handler) or not callable(get_focused):
            return False
        mouse_handler(down, x, y, False)
        focused = get_focused()
        event_handler = getattr(focused, "event", None)
        if not callable(event_handler):
            return False
        local_x, local_y = x, y
        for focus in reversed(list(getattr(focus_module, "focus_list", []) or [])):
            if getattr(focus, "widget", None) is not focused:
                continue
            focus_x = getattr(focus, "x", None)
            focus_y = getattr(focus, "y", None)
            if focus_x is not None and focus_y is not None:
                local_x, local_y = x - focus_x, y - focus_y
            break
        ignore_event = getattr(getattr(renpy, "display", None), "core", None)
        ignore_event = getattr(ignore_event, "IgnoreEvent", None)
        no_interaction_result = object()
        interaction_result = no_interaction_result
        for event in (down, up):
            try:
                rv = event_handler(event, local_x, local_y, 0)
                if rv is not None and interaction_result is no_interaction_result:
                    interaction_result = rv
            except Exception as exc:
                if ignore_event is None or not isinstance(exc, ignore_event):
                    raise
        if interaction_result is not no_interaction_result:
            end_interaction = getattr(renpy, "end_interaction", None)
            if callable(end_interaction):
                end_interaction(interaction_result)
        return True

    def _renforge_click_pointer(x, y):
        interface = getattr(getattr(renpy, "display", None), "interface", None)
        if interface is not None:
            try:
                interface.mouse_focused = True
            except Exception:
                pass
            try:
                interface.ignore_touch = False
            except Exception:
                pass

        _renforge_reset_testmouse_state()
        try:
            _renforge_dispatch_mouse_motion(x, y)
            if _renforge_dispatch_mouse_click(x, y):
                return "renpy"

            testmouse = getattr(getattr(renpy, "test", None), "testmouse", None)
            click_mouse = getattr(testmouse, "click_mouse", None)
            if callable(click_mouse):
                click_mouse(1, x, y)
                return "renpy-test"
            raise RuntimeError("Ren'Py synthetic mouse API is unavailable")
        finally:
            _renforge_reset_testmouse_state()

    def _renforge_click_focus(focus):
        """Click a focus center through shared synthetic input path."""
        fx = getattr(focus, "x", None)
        fy = getattr(focus, "y", None)
        fw = getattr(focus, "w", None)
        fh = getattr(focus, "h", None)
        if fx is not None and fy is not None and fw and fh:
            x = int(fx + fw // 2)
            y = int(fy + fh // 2)
        else:
            find_position = getattr(getattr(renpy, "test", None), "testfocus", None)
            find_position = getattr(find_position, "find_position", None)
            if not callable(find_position):
                raise RuntimeError("Ren'Py focus position API is unavailable")
            px, py = find_position(focus, (None, None))
            x, y = int(px), int(py)

        try:
            _renforge_click_pointer(x, y)
        except Exception as exc:
            if not _renforge_is_end_interaction(exc):
                raise
            setattr(exc, "renforge_pointer", (x, y))
            raise
        return x, y

    def _renforge_resolve_ui_element(payload, action):
        payload = payload or {}
        wanted_id = payload.get("id") or payload.get("element_id")
        wanted_text = payload.get("text")
        if wanted_text == "":
            wanted_text = None
        exact = bool(payload.get("exact", False))
        wanted_screen = payload.get("screen")
        if wanted_id is None and wanted_text is None:
            return None, None, {"ok": False, "error": "%s requires text or id" % action}
        if wanted_id is not None:
            wanted_id = str(wanted_id)
        if wanted_text is not None:
            wanted_text = str(wanted_text)
        if wanted_screen is not None:
            wanted_screen = str(wanted_screen).casefold()

        candidates = []
        for focus, element in _renforge_focusable_elements():
            if wanted_screen and str(element.get("screen") or "").casefold() != wanted_screen:
                continue
            if wanted_id is not None and str(element.get("id")) != wanted_id:
                continue
            if wanted_text is not None:
                actual_text = str(element.get("text") or "")
                matches = actual_text.casefold() == wanted_text.casefold() if exact else wanted_text.casefold() in actual_text.casefold()
                if not matches:
                    continue
            candidates.append((focus, element))
        if not candidates:
            return None, None, {"ok": False, "error": "no UI element matching %r/%r" % (wanted_text, wanted_id)}
        if len(candidates) > 1:
            return None, None, {
                "ok": False,
                "error": "ambiguous UI element; provide an id or exact text",
                "matches": [item[1] for item in candidates],
            }
        return candidates[0][0], candidates[0][1], None

    def _renforge_h_click_element(payload):
        payload = payload or {}
        expected_frame_id = payload.get("expected_frame_id") or payload.get("expected_screenshot")
        focus, element, error = _renforge_resolve_ui_element(payload, "click_element")
        if error is not None:
            return error

        screenshot_digest = None
        if expected_frame_id not in (None, ""):
            data = renpy.screenshot_to_bytes(None)
            matches, screenshot_digest = _renforge_screenshot_guard_matches(expected_frame_id, data)
            if not matches:
                return {
                    "ok": False,
                    "error": "expected_frame_id guard failed",
                    "sha256": screenshot_digest,
                }
        if not element.get("enabled", True):
            return {"ok": False, "error": "UI element is disabled", "element": element}
        pending_end_interaction = None
        try:
            x, y = _renforge_click_focus(focus)
        except Exception as exc:
            if not _renforge_is_end_interaction(exc):
                raise
            pending_end_interaction = exc
            x, y = getattr(exc, "renforge_pointer")
        # Report which focusable actually owns this coordinate (coverage).
        hit = _renforge_hit_stack(x, y)
        topmost = hit.get("topmost")
        action_name = element.get("action")
        # Native quick-menu actions often run as a result of the click; emit a
        # correlated business event when the action name is recognizable so
        # wait_for_effect can resolve without inspecting files.
        if action_name:
            lowered = str(action_name).casefold()
            if "quicksave" in lowered or lowered == "quick_save":
                slot = _renforge_newest_quick_slot()
                _renforge_emit_business("quick_save.completed", slot=slot, source="click_element")
            elif "quickload" in lowered or lowered == "quick_load":
                slot = _renforge_newest_quick_slot()
                bridge = _renforge_runtime.bridge
                _renforge_emit_business(
                    "quick_load.completed",
                    slot=slot,
                    restored_label=bridge.current_label if bridge is not None else None,
                    source="click_element",
                )
            elif "rollback" in lowered or lowered == "back":
                history = _renforge_history_index()
                _renforge_emit_business(
                    "rollback.completed",
                    from_history_index=history,
                    to_history_index=(history - 1) if history is not None else None,
                    source="click_element",
                )
            elif "skip" in lowered:
                bridge = _renforge_runtime.bridge
                if bridge is not None:
                    bridge._skip_reason_hint = "user_click"
                _renforge_emit_business("skip.changed", requested=True, source="click_element")
            elif "auto" in lowered or "afm" in lowered:
                _renforge_emit_business("auto.changed", requested=True, source="click_element")
        result = {
            "ok": True,
            "id": element.get("id"),
            "text": element.get("text"),
            "type": element.get("type"),
            "screen": element.get("screen"),
            "action": action_name,
            "bounds": element.get("bounds"),
            "x": x,
            "y": y,
            "coordinate_space": "logical",
            "element": element,
            "received_by": topmost,
        }
        if topmost is not None and topmost.get("id") != element.get("id"):
            result["warning"] = (
                "The intended button may be covered by another interactive displayable."
            )
        if screenshot_digest is not None:
            result["sha256"] = screenshot_digest
        if pending_end_interaction is not None:
            setattr(pending_end_interaction, "renforge_result", result)
            raise pending_end_interaction
        return result

    def _renforge_hit_stack(x, y):
        """Return topmost and underneath focusables containing point (x, y)."""
        hits = []
        for _focus, element in _renforge_focusable_elements():
            bounds = element.get("bounds") or {}
            if _renforge_bounds_contain(bounds, x, y):
                hits.append(element)
        # focus_list is bottom→top; last entry is topmost.
        topmost = hits[-1] if hits else None
        underneath = list(reversed(hits[:-1])) if len(hits) > 1 else []
        result = {
            "ok": True,
            "x": x,
            "y": y,
            "coordinate_space": "logical",
            "topmost": topmost,
            "underneath": underneath,
        }
        if topmost is not None and underneath:
            result["warning"] = (
                "The intended button is covered by another interactive displayable."
                if False
                else "Multiple interactive displayables overlap this point."
            )
        return result

    def _renforge_h_hit_test(payload):
        payload = payload or {}
        try:
            raw_x, raw_y = payload.get("x"), payload.get("y")
            if isinstance(raw_x, bool) or isinstance(raw_y, bool):
                raise ValueError
            x, y = int(round(float(raw_x))), int(round(float(raw_y)))
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "hit_test requires numeric x and y"}
        if x < 0 or y < 0:
            return {"ok": False, "error": "hit_test coordinates must be non-negative"}

        coordinate_space = str(payload.get("coordinate_space", "logical") or "logical").casefold()
        if coordinate_space not in ("logical", "screenshot"):
            return {"ok": False, "error": "coordinate_space must be logical or screenshot"}

        frame_data = None
        x, y, frame_data, coordinate_error = _renforge_to_logical_coordinates(
            x, y, coordinate_space, frame_data
        )
        if coordinate_error is not None:
            return {"ok": False, "error": coordinate_error}

        result = _renforge_hit_stack(x, y)
        result["coordinate_space"] = "logical"
        if coordinate_space == "screenshot":
            result["requested_coordinate_space"] = "screenshot"
        return result

    def _renforge_move_mouse(focus):
        fx = getattr(focus, "x", None)
        fy = getattr(focus, "y", None)
        fw = getattr(focus, "w", None)
        fh = getattr(focus, "h", None)
        if fx is not None and fy is not None and fw and fh:
            x = int(fx + fw // 2)
            y = int(fy + fh // 2)
        else:
            find_position = getattr(getattr(renpy, "test", None), "testfocus", None)
            find_position = getattr(find_position, "find_position", None)
            if not callable(find_position):
                raise RuntimeError("Ren'Py focus position API is unavailable")
            px, py = find_position(focus, (None, None))
            x, y = int(px), int(py)

        interface = getattr(getattr(renpy, "display", None), "interface", None)
        if interface is not None:
            try:
                interface.mouse_focused = True
            except Exception:
                pass
            try:
                interface.ignore_touch = False
            except Exception:
                pass

        set_mouse_pos = getattr(renpy, "set_mouse_pos", None)
        testmouse = getattr(getattr(renpy, "test", None), "testmouse", None)
        move_mouse = getattr(testmouse, "move_mouse", None)
        restart_interaction = getattr(renpy, "restart_interaction", None)
        used_native_set_mouse = False

        _renforge_reset_testmouse_state()
        try:
            if callable(set_mouse_pos):
                try:
                    set_mouse_pos(x, y, duration=0)
                except TypeError:
                    try:
                        set_mouse_pos(x, y, 0)
                    except TypeError:
                        try:
                            set_mouse_pos(x, y)
                        except TypeError:
                            pass
                        else:
                            used_native_set_mouse = True
                    else:
                        used_native_set_mouse = True
                else:
                    used_native_set_mouse = True
                if used_native_set_mouse:
                    _renforge_dispatch_mouse_motion(x, y)
                    if callable(restart_interaction):
                        restart_interaction()
                    return x, y, "renpy"

            if callable(move_mouse):
                try:
                    move_mouse(x, y)
                except TypeError:
                    pass
                else:
                    _renforge_dispatch_mouse_motion(x, y)
                    if callable(restart_interaction):
                        restart_interaction()
                    return x, y, "renpy-test"

            if not _renforge_dispatch_mouse_motion(x, y):
                raise RuntimeError("hover unavailable: pygame mouse-motion API is unavailable")
            if callable(restart_interaction):
                restart_interaction()
            return x, y, "pygame"
        finally:
            _renforge_reset_testmouse_state()

    def _renforge_h_hover_element(payload):
        payload = payload or {}
        expected_frame_id = payload.get("expected_frame_id") or payload.get("expected_screenshot")
        focus, element, error = _renforge_resolve_ui_element(payload, "hover_element")
        if error is not None:
            return error
        screenshot_digest = None
        if expected_frame_id not in (None, ""):
            data = renpy.screenshot_to_bytes(None)
            matches, screenshot_digest = _renforge_screenshot_guard_matches(expected_frame_id, data)
            if not matches:
                return {
                    "ok": False,
                    "error": "expected_frame_id guard failed",
                    "sha256": screenshot_digest,
                }
        if not element.get("enabled", True):
            return {"ok": False, "error": "UI element is disabled", "element": element}
        try:
            x, y, method = _renforge_move_mouse(focus)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc), "element": element}
        result = {
            "ok": True,
            "hovered": True,
            "method": method,
            "id": element.get("id"),
            "text": element.get("text"),
            "type": element.get("type"),
            "screen": element.get("screen"),
            "bounds": element.get("bounds"),
            "x": x,
            "y": y,
            "element": element,
        }
        if screenshot_digest is not None:
            result["sha256"] = screenshot_digest
        return result

    def _renforge_rect_components(rect):
        left = getattr(rect, "left", None)
        top = getattr(rect, "top", None)
        width = getattr(rect, "width", None)
        height = getattr(rect, "height", None)
        if left is None or top is None or width is None or height is None:
            try:
                left, top, width, height = rect[0], rect[1], rect[2], rect[3]
            except (TypeError, IndexError, ValueError):
                raise ValueError("unsupported rect type")
        return int(left), int(top), int(width), int(height)

    def _renforge_h_get_ui_element_bounds(payload):
        payload = payload or {}
        expected_frame_id = payload.get("expected_frame_id") or payload.get("expected_screenshot")
        focus, element, error = _renforge_resolve_ui_element(payload, "get_ui_element_bounds")
        if error is not None:
            return error
        screenshot_digest = None
        if expected_frame_id not in (None, ""):
            data = renpy.screenshot_to_bytes(None)
            matches, screenshot_digest = _renforge_screenshot_guard_matches(expected_frame_id, data)
            if not matches:
                return {
                    "ok": False,
                    "error": "expected_frame_id guard failed",
                    "sha256": screenshot_digest,
                }

        bounds = element.get("bounds")
        result = {
            "ok": True,
            "id": element.get("id"),
            "text": element.get("text"),
            "type": element.get("type"),
            "screen": element.get("screen"),
            "focus_bounds": bounds,
            "painted_bounds": None,
            "painted_bounds_available": False,
            "coordinate_space": "logical",
        }
        widget = getattr(focus, "widget", None)
        if widget is None or not hasattr(widget, "state_children") or not callable(getattr(widget, "get_child", None)):
            result["painted_bounds_reason"] = "element does not expose ImageButton state children"
        else:
            render_to_surface = getattr(renpy, "render_to_surface", None)
            if not callable(render_to_surface):
                result["painted_bounds_reason"] = "renpy.render_to_surface is unavailable"
            else:
                try:
                    child = widget.get_child()
                    width = int(bounds.get("width", 0))
                    height = int(bounds.get("height", 0))
                    surface = render_to_surface(child, width, height, resize=True)
                    get_bounding_rect = getattr(surface, "get_bounding_rect", None)
                    if not callable(get_bounding_rect):
                        raise RuntimeError("rendered surface has no alpha bounds API")
                    try:
                        rect = get_bounding_rect(min_alpha=1)
                    except TypeError:
                        rect = get_bounding_rect()
                    left, top, painted_width, painted_height = _renforge_rect_components(rect)
                    if painted_width > 0 and painted_height > 0:
                        result["painted_bounds"] = {
                            "x": int(bounds["x"]) + left,
                            "y": int(bounds["y"]) + top,
                            "width": painted_width,
                            "height": painted_height,
                        }
                        result["painted_bounds_available"] = True
                        result["painted_bounds_source"] = "rendered-alpha"
                        result["state"] = str(getattr(getattr(widget, "style", None), "prefix", "")).rstrip("_") or None
                    else:
                        result["painted_bounds_reason"] = "rendered ImageButton is fully transparent"
                except Exception as exc:
                    result["painted_bounds_reason"] = "%s: %s" % (type(exc).__name__, exc)
        if screenshot_digest is not None:
            result["sha256"] = screenshot_digest
        return result

    def _renforge_state_matches(actual, expected):
        if isinstance(expected, builtins.dict):
            if not isinstance(actual, builtins.dict):
                return False
            for key, value in expected.items():
                if key not in actual or not _renforge_state_matches(actual[key], value):
                    return False
            return True
        if isinstance(expected, (list, tuple)):
            return isinstance(actual, (list, tuple)) and len(actual) == len(expected) and all(
                _renforge_state_matches(a, e) for a, e in zip(actual, expected)
            )
        return actual == expected

    def _renforge_screenshot_guard_matches(expected, data):
        digest = hashlib.sha256(data).hexdigest()
        if isinstance(expected, builtins.dict):
            expected = expected.get(
                "sha256",
                expected.get(
                    "hash",
                    expected.get("frame_id", expected.get("id", expected.get("base64"))),
                ),
            )
        if isinstance(expected, bytes):
            return expected == data, digest
        if not isinstance(expected, str) or not expected.strip():
            return False, digest
        value = expected.strip()
        if value.casefold().startswith("sha256:"):
            value = value.split(":", 1)[1].strip()
        if value.casefold() == digest.casefold():
            return True, digest
        try:
            decoded = base64.b64decode(value, validate=True)
        except Exception:
            decoded = None
        return decoded == data, digest

    def _renforge_png_dimensions(data):
        if not isinstance(data, bytes) or len(data) < 24:
            return None, None
        if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            return None, None
        try:
            return struct.unpack(">II", data[16:24])
        except Exception:
            return None, None

    def _renforge_to_logical_coordinates(x, y, coordinate_space, frame_data=None):
        """Convert screenshot pixels through the same seam used by click_at."""
        if coordinate_space == "logical":
            return x, y, frame_data, None
        if coordinate_space != "screenshot":
            return x, y, frame_data, "coordinate_space must be logical or screenshot"
        if frame_data is None:
            frame_data = renpy.screenshot_to_bytes(None)
        pixel_width, pixel_height = _renforge_png_dimensions(frame_data)
        logical_width = getattr(renpy.config, "screen_width", None)
        logical_height = getattr(renpy.config, "screen_height", None)
        if not pixel_width or not pixel_height or not logical_width or not logical_height:
            return x, y, frame_data, "screenshot coordinate space is unavailable"
        x = int(round(x * float(logical_width) / float(pixel_width)))
        y = int(round(y * float(logical_height) / float(pixel_height)))
        return x, y, frame_data, None

    def _renforge_h_click_at(payload):
        payload = payload or {}
        try:
            raw_x, raw_y = payload.get("x"), payload.get("y")
            if isinstance(raw_x, bool) or isinstance(raw_y, bool):
                raise ValueError
            x, y = int(round(float(raw_x))), int(round(float(raw_y)))
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "click_at requires numeric x and y"}
        if x < 0 or y < 0:
            return {"ok": False, "error": "click_at coordinates must be non-negative"}

        coordinate_space = str(payload.get("coordinate_space", "logical") or "logical").casefold()
        if coordinate_space not in ("logical", "screenshot"):
            return {"ok": False, "error": "coordinate_space must be logical or screenshot"}

        expected_state = payload.get("expected_state")
        if expected_state is not None:
            state = _renforge_h_get_state({})
            if not _renforge_state_matches(state, expected_state):
                return {"ok": False, "error": "expected_state guard failed", "state": state}

        expected_screenshot = payload.get("expected_screenshot") or payload.get("expected_frame_id")
        screenshot_digest = None
        frame_data = None
        if expected_screenshot not in (None, ""):
            frame_data = renpy.screenshot_to_bytes(None)
            matches, screenshot_digest = _renforge_screenshot_guard_matches(expected_screenshot, frame_data)
            if not matches:
                return {
                    "ok": False,
                    "error": "expected_screenshot guard failed",
                    "sha256": screenshot_digest,
                }

        x, y, frame_data, coordinate_error = _renforge_to_logical_coordinates(
            x, y, coordinate_space, frame_data
        )
        if coordinate_error is not None:
            return {"ok": False, "error": coordinate_error}

        pending_end_interaction = None
        try:
            _renforge_click_pointer(x, y)
        except Exception as exc:
            if not _renforge_is_end_interaction(exc):
                raise
            pending_end_interaction = exc
        result = {"ok": True, "x": x, "y": y, "coordinate_space": coordinate_space}
        if screenshot_digest is not None:
            result["sha256"] = screenshot_digest
        if pending_end_interaction is not None:
            setattr(pending_end_interaction, "renforge_result", result)
            raise pending_end_interaction
        return result

    def _renforge_h_get_displayable_bounds(payload):
        # Report where a shown image tag was actually rendered, in Ren'Py
        # logical coordinates. This closes the pixel-perfect loop: instead of
        # eyeballing a sprite on a screenshot, a caller can measure its real
        # position and size after a show/reposition.
        payload = payload or {}
        tag = payload.get("tag")
        if not tag:
            return {"ok": False, "error": "get_displayable_bounds requires a tag"}
        tag = str(tag)
        layer = payload.get("layer")
        layer = str(layer) if layer else None
        get_bounds = getattr(renpy, "get_image_bounds", None)
        if not callable(get_bounds):
            return {"ok": False, "error": "renpy.get_image_bounds is unavailable"}
        try:
            if layer:
                bounds = get_bounds(tag, layer=layer)
            else:
                bounds = get_bounds(tag)
        except Exception as exc:
            return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        try:
            showing = list(renpy.get_showing_tags(layer)) if layer else list(renpy.get_showing_tags())
        except Exception:
            showing = []
        if not bounds:
            return {
                "ok": False,
                "error": "tag %r is not showing" % tag,
                "tag": tag,
                "showing": False,
                "showing_tags": showing,
            }
        x, y, w, h = bounds
        x, y, w, h = int(x), int(y), int(w), int(h)
        result = {
            "ok": True,
            "tag": tag,
            "showing": True,
            "bounds": {"x": x, "y": y, "width": w, "height": h},
            "center": {"x": x + w // 2, "y": y + h // 2},
            "coordinate_space": "logical",
        }
        if layer:
            result["layer"] = layer
        screen_width = getattr(renpy.config, "screen_width", None)
        screen_height = getattr(renpy.config, "screen_height", None)
        if screen_width and screen_height:
            result["screen"] = {"width": int(screen_width), "height": int(screen_height)}
        return result

    _RENFORGE_POSITION_FIELDS = (
        "xpos", "ypos", "xanchor", "yanchor",
        "xalign", "yalign", "xoffset", "yoffset",
        "zoom", "rotate",
    )

    def _renforge_h_show_displayable(payload):
        # Reposition an already-showing image tag at runtime and return where it
        # landed. This turns "edit .rpy, relaunch, look, guess the offset" into
        # an interactive loop: converge on live coordinates, then write the
        # final values into the script. The tag keeps its current attributes,
        # so `show eileen happy` stays happy after a nudge.
        payload = payload or {}
        tag = payload.get("tag")
        if not tag:
            return {"ok": False, "error": "position_element requires a tag"}
        tag = str(tag)
        layer = payload.get("layer")
        layer = str(layer) if layer else None

        transform_kwargs = {}
        for field in _RENFORGE_POSITION_FIELDS:
            value = payload.get(field)
            if value is None:
                continue
            # Preserve int vs float: Ren'Py reads an int position as absolute
            # pixels and a float as a fraction of the screen (xpos 600 == 600px,
            # xpos 0.5 == halfway). Coercing to float would turn "600 pixels"
            # into 600x the screen width.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return {"ok": False, "error": "%s must be a number" % field}
            transform_kwargs[field] = value
        if not transform_kwargs:
            return {"ok": False, "error": "position_element requires at least one placement field"}

        try:
            showing = list(renpy.get_showing_tags(layer)) if layer else list(renpy.get_showing_tags())
        except Exception:
            showing = []
        if tag not in showing:
            return {
                "ok": False,
                "error": "tag %r is not showing; show it first" % tag,
                "tag": tag,
                "showing_tags": showing,
            }

        transform_cls = getattr(renpy.store, "Transform", None)
        if transform_cls is None:
            return {"ok": False, "error": "Transform is unavailable in the store"}
        show = getattr(renpy, "show", None)
        if not callable(show):
            return {"ok": False, "error": "renpy.show is unavailable"}
        try:
            transform = transform_cls(**transform_kwargs)
            if layer:
                show(tag, at_list=[transform], layer=layer)
            else:
                show(tag, at_list=[transform])
        except Exception as exc:
            return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}

        # get_image_bounds reads the last drawn frame; force a render so the
        # reported bounds reflect the show we just applied rather than the
        # previous position.
        restart = getattr(renpy, "restart_interaction", None)
        if callable(restart):
            try:
                restart()
            except Exception:
                pass
        try:
            renpy.screenshot_to_bytes(None)
        except Exception:
            pass

        result = _renforge_h_get_displayable_bounds({"tag": tag, "layer": layer})
        result["applied"] = transform_kwargs
        return result

    def _renforge_h_select_choice(payload):
        # Select a menu option by visible text (preferred) or by index, then
        # simulate a mouse click on the focus rect. Matching uses our own
        # focus enumeration + ``_renforge_focus_text`` so we stay aligned with
        # Ren'Py 8.5's ``_tts_all(raw)`` contract — ``renpy.test.testfocus.find_focus``
        # also gained a required ``raw`` argument in 8.5 and is intentionally
        # avoided here.
        #
        # Important: when the Ren'Py window is unfocused (common while driving
        # the game from the dashboard), Interface.mouse_focused is False and
        # core.py forces click coords to (-1, -1), so clicks never hit buttons.
        # Force mouse focus for the synthetic click so choices still work.
        payload = payload or {}
        text = payload.get("text")
        index = payload.get("index")

        focus = None
        chosen = None
        choices = _renforge_focusable_choices()
        if text is not None:
            needle = str(text).casefold()
            exact = []
            partial = []
            for candidate_focus, candidate_text, _screen in choices:
                hay = str(candidate_text).casefold()
                if hay == needle:
                    exact.append((candidate_focus, candidate_text))
                elif needle in hay:
                    partial.append((len(hay), candidate_focus, candidate_text))
            if exact:
                focus, chosen = exact[0]
            elif partial:
                partial.sort(key=lambda item: item[0])
                _length, focus, chosen = partial[0]
        elif index is not None:
            idx = int(index)
            if 0 <= idx < len(choices):
                focus, chosen, _screen = choices[idx]

        if focus is None:
            return {"ok": False, "error": "no choice matching %r/%r" % (text, index)}

        # Click the button's center directly. The focus rect is already in the
        # click coordinate space, and the center reliably hits the button —
        # unlike find_position, whose focus_at_point check can fail mid-transition.
        fx = getattr(focus, "x", None)
        if fx is not None and getattr(focus, "w", None) and getattr(focus, "h", None):
            x = int(focus.x + focus.w // 2)
            y = int(focus.y + focus.h // 2)
        else:
            px, py = renpy.test.testfocus.find_position(focus, (None, None))
            x, y = int(px), int(py)

        interface = getattr(getattr(renpy, "display", None), "interface", None)
        if interface is not None:
            try:
                interface.mouse_focused = True
            except Exception:
                pass
            try:
                interface.ignore_touch = False
            except Exception:
                pass

        pending_end_interaction = None
        try:
            _renforge_click_pointer(x, y)
        except Exception as exc:
            if not _renforge_is_end_interaction(exc):
                raise
            pending_end_interaction = exc
        result = {"ok": True, "text": chosen, "x": x, "y": y}
        if pending_end_interaction is not None:
            setattr(pending_end_interaction, "renforge_result", result)
            raise pending_end_interaction
        return result

    # -- scene_tree: full-scene perception for non-multimodal agents -------
    #
    # list_ui_elements only sees focusables. scene_tree walks every layer's
    # scene list and reports each displayable's real logical bounds using the
    # same mechanism as renpy.get_image_bounds (render_for_size sizes the
    # surface, displayable.place positions it). Focusable controls are merged
    # from the focus list; non-focusable text is recovered by a guarded
    # descent through container children/offsets. detail/layers/types/screen
    # filter the returned set; an omitted-count hint always reports what was
    # perceived but not returned so an agent can widen precisely.
    _RENFORGE_SCENE_MAX_NODES = 4000
    _RENFORGE_SCENE_MAX_DEPTH = 48
    _RENFORGE_SCENE_MAX_TEXT_CHARS = 4096
    _RENFORGE_SCENE_MAX_ID_CHARS = 256
    _RENFORGE_SCENE_SEMANTIC_TYPES = ("image", "text", "button", "bar", "input", "imagemap", "hotspot")
    def _renforge_scene_string(value, max_chars):
        if value is None:
            return None
        try:
            text = str(value)
        except Exception:
            return None
        return text[:max_chars] + ("…" if len(text) > max_chars else "")



    def _renforge_scene_window():
        w = getattr(renpy.config, "screen_width", 0) or 0
        h = getattr(renpy.config, "screen_height", 0) or 0
        return int(w), int(h)

    def _renforge_scene_type(d):
        try:
            if isinstance(d, renpy.text.text.Text):
                return "text"
        except Exception:
            pass
        name = getattr(getattr(d, "__class__", None), "__name__", "") or ""
        lowered = name.casefold()
        for marker, node_type in (
            ("imagebutton", "button"), ("textbutton", "button"), ("button", "button"),
            ("imagemap", "imagemap"), ("bar", "bar"), ("input", "input"),
            ("text", "text"), ("frame", "container"), ("window", "container"),
            ("viewport", "container"), ("vbox", "container"), ("hbox", "container"),
            ("fixed", "container"), ("grid", "container"), ("side", "container"),
            ("multibox", "container"), ("screen", "container"),
            ("solid", "image"), ("image", "image"), ("transform", "image"),
        ):
            if marker in lowered:
                return node_type
        return name or "other"

    def _renforge_scene_place(d, width, height, st, at):
        render_for_size = getattr(getattr(renpy.display, "render", None), "render_for_size", None)
        place = getattr(getattr(renpy.display, "displayable", None), "place", None)
        get_placement = getattr(d, "get_placement", None)
        if not callable(render_for_size) or not callable(place) or not callable(get_placement):
            return None
        try:
            surf = render_for_size(d, width, height, st, at)
            sw = getattr(surf, "width", None)
            sh = getattr(surf, "height", None)
            if sw is None or sh is None:
                sw, sh = surf.get_size()
            x, y = place(width, height, float(sw), float(sh), get_placement())
            return (int(round(x)), int(round(y)), int(round(sw)), int(round(sh)))
        except Exception:
            return None

    def _renforge_scene_text(d, max_chars):
        tts = getattr(d, "_tts_all", None)
        if callable(tts):
            text = None
            try:
                text = tts(False)
            except TypeError:
                try:
                    text = tts()
                except Exception:
                    text = None
            except Exception:
                text = None
            if text:
                spoken = str(text).strip()
                if spoken:
                    return spoken[:max_chars] + ("…" if len(spoken) > max_chars else "")
        raw = getattr(d, "text", None)
        try:
            if isinstance(raw, (list, tuple)):
                parts = []
                remaining = max_chars + 1
                for part in raw:
                    if isinstance(part, str) and remaining > 0:
                        parts.append(part[:remaining])
                        remaining -= len(parts[-1])
                joined = "".join(parts).strip()
            elif raw is not None:
                joined = str(raw).strip()
            else:
                joined = ""
        except Exception:
            joined = ""
        if not joined:
            return None
        return joined[:max_chars] + ("…" if len(joined) > max_chars else "")

    def _renforge_scene_node(node_id, node_type, layer, screen, bounds, text=None, action=None, enabled=None):
        node = {
            "id": node_id,
            "type": node_type,
            "layer": layer,
            "screen": screen,
            "visible": True,
            "coordinate_space": "logical",
        }
        if bounds is not None:
            x, y, w, h = bounds
            node["bounds"] = {"x": x, "y": y, "width": w, "height": h}
            node["center"] = {"x": x + w // 2, "y": y + h // 2}
            node["bounds_available"] = True
        else:
            node["bounds"] = None
            node["center"] = None
            node["bounds_available"] = False
            node["bounds_reason"] = "could not size/place displayable"
        if text is not None:
            node["text"] = text
        if action is not None:
            node["action"] = action
        if enabled is not None:
            node["enabled"] = enabled
        return node

    def _renforge_color_hex(color):
        try:
            if isinstance(color, str):
                return color
            if isinstance(color, (tuple, list)) and len(color) >= 3:
                return "#%02X%02X%02X" % (int(color[0]), int(color[1]), int(color[2]))
        except Exception:
            pass
        return str(color)[:24]

    def _renforge_scene_style(d):
        style = getattr(d, "style", None)
        if style is None:
            return None
        out = {}
        color = getattr(style, "color", None)
        if color is not None:
            out["color"] = _renforge_color_hex(color)
        size = getattr(style, "size", None)
        if isinstance(size, int):
            out["size"] = size
        font = getattr(style, "font", None)
        if font:
            out["font"] = str(font)[:60]
        bg = getattr(style, "background", None)
        if bg is not None:
            out["background"] = str(bg)[:60]
        return out or None

    def _renforge_scene_overflow(d):
        style = getattr(d, "style", None)
        render_for_size = getattr(getattr(renpy.display, "render", None), "render_for_size", None)
        if not callable(render_for_size):
            return {"available": False, "reason": "render API unavailable"}
        try:
            surf = render_for_size(d, 1000000, 1000000, 0, 0)
            nw = int(getattr(surf, "width", 0) or 0)
            nh = int(getattr(surf, "height", 0) or 0)
        except Exception as exc:
            return {"available": False, "reason": "render failed: %s" % exc}
        xmax = getattr(style, "xmaximum", None) if style is not None else None
        ymax = getattr(style, "ymaximum", None) if style is not None else None
        ox = int(nw - xmax) if isinstance(xmax, int) and nw > xmax else 0
        oy = int(nh - ymax) if isinstance(ymax, int) and nh > ymax else 0
        return {
            "available": True,
            "overflow": bool(ox or oy),
            "overflow_px": max(ox, oy),
            "clipped": bool(ox or oy),
            "natural": {"width": nw, "height": nh},
        }

    def _renforge_scene_finalize(nodes, include):
        want_style = "style" in include
        want_overflow = "overflow" in include
        for node in nodes:
            d = node.pop("_d", None)
            if d is None:
                continue
            if want_style:
                style = _renforge_scene_style(d)
                if style:
                    node["style"] = style
            if want_overflow and node.get("type") == "text":
                node["overflow"] = _renforge_scene_overflow(d)

    def _renforge_scene_descend_children(d, base_x, base_y, layer, screen, zorder, seen, out, counters, depth):
        children = getattr(d, "children", None)
        if not children:
            child = getattr(d, "child", None)
            if child is None:
                return
            if depth >= counters["_max_depth"]:
                counters["_omitted_max_depth"] += 1
                return
            _renforge_scene_descend(child, base_x, base_y, layer, screen, zorder, seen, out, counters, depth + 1)
            return
        if depth >= counters["_max_depth"]:
            counters["_omitted_max_depth"] += len(children)
            return
        offsets = getattr(d, "offsets", None)
        for index, child in enumerate(children):
            if len(out) >= counters["_max_nodes"]:
                counters["_omitted_max_nodes"] += 1
                break
            ox = oy = 0
            if offsets and index < len(offsets):
                try:
                    ox, oy = int(offsets[index][0]), int(offsets[index][1])
                except Exception:
                    ox = oy = 0
            _renforge_scene_descend(child, base_x + ox, base_y + oy, layer, screen, zorder, seen, out, counters, depth + 1)

    def _renforge_scene_descend(d, base_x, base_y, layer, screen, zorder, seen, out, counters, depth):
        if len(out) >= counters["_max_nodes"]:
            counters["_omitted_max_nodes"] += 1
            return
        if d is None or id(d) in seen:
            return
        seen.add(id(d))
        node_type = _renforge_scene_type(d)
        key = (layer, screen or "", node_type)
        ordinal = counters.get(key, 0)
        counters[key] = ordinal + 1
        screen_id = _renforge_scene_string(screen, _RENFORGE_SCENE_MAX_ID_CHARS)
        if screen_id:
            node_id = "%s/%s.%s#%d" % (layer, screen_id, node_type, ordinal)
        else:
            node_id = "%s/%s#%d" % (layer, node_type, ordinal)
        type_filter = counters["_flt_types"]
        eligible = (
            (node_type.casefold() in type_filter)
            if type_filter is not None
            else _renforge_scene_detail_ok(node_type, counters["_detail"])
        )
        eligible = eligible and (
            counters["_flt_layers"] is None or layer in counters["_flt_layers"]
        )
        text = None
        bounds = None
        if eligible:
            text = (
                _renforge_scene_text(d, counters["_max_text_chars"])
                if node_type == "text"
                else None
            )
            try:
                aw, ah = _renforge_scene_window()
                surf = renpy.display.render.render_for_size(d, aw, ah, 0, 0)
                w = int(getattr(surf, "width", 0) or 0)
                h = int(getattr(surf, "height", 0) or 0)
                if w and h:
                    bounds = (int(base_x), int(base_y), w, h)
            except Exception:
                bounds = None
        node = _renforge_scene_node(node_id, node_type, layer, screen, bounds, text=text)
        node["zorder"] = zorder
        node["_d"] = d
        out.append(node)
        _renforge_scene_descend_children(d, base_x, base_y, layer, screen, zorder, seen, out, counters, depth)

    def _renforge_scene_unique_ids(nodes, max_text_chars):
        used = set()
        for node in nodes:
            node.setdefault("_raw_type", node.get("type"))
            node.setdefault("_raw_layer", node.get("layer"))
            node.setdefault("_raw_screen", node.get("screen"))
            for key in ("text", "screen", "action", "tag", "layer", "type"):
                if node.get(key) is not None:
                    node[key] = _renforge_scene_string(node[key], max_text_chars)
            base = _renforge_scene_string(
                node.get("id") or "node",
                _RENFORGE_SCENE_MAX_ID_CHARS,
            )
            candidate = base
            suffix_number = 2
            while candidate in used:
                suffix = "#%d" % suffix_number
                candidate = base[:max(1, _RENFORGE_SCENE_MAX_ID_CHARS - len(suffix))] + suffix
                suffix_number += 1
            node["id"] = candidate
            used.add(candidate)


    def _renforge_scene_detail_ok(node_type, detail):
        if detail == "raw":
            return True
        if detail == "layout":
            return node_type in _RENFORGE_SCENE_SEMANTIC_TYPES or node_type == "container"
        return node_type in _RENFORGE_SCENE_SEMANTIC_TYPES

    def _renforge_scene_limit(payload, name, default, minimum):
        try:
            requested = int(payload.get(name, default))
        except Exception:
            requested = default
        return min(default, max(minimum, requested))


    def _renforge_h_scene_tree(payload):
        payload = payload or {}
        detail = str(payload.get("detail") or "semantic").casefold()
        if detail not in ("semantic", "layout", "raw"):
            detail = "semantic"
        flt_layers = payload.get("layers")
        flt_layers = set(str(x) for x in flt_layers) if flt_layers else None
        flt_types = payload.get("types")
        flt_types = set(str(x).casefold() for x in flt_types) if flt_types else None
        flt_screen = payload.get("screen")
        flt_screen = str(flt_screen) if flt_screen else None
        flt_ids = payload.get("ids")
        flt_ids = set(str(x) for x in flt_ids) if flt_ids else None
        include = payload.get("include")
        include = set(str(x).casefold() for x in include) if include else set()

        max_depth = _renforge_scene_limit(payload, "max_depth", _RENFORGE_SCENE_MAX_DEPTH, 0)
        max_nodes = _renforge_scene_limit(payload, "max_nodes", _RENFORGE_SCENE_MAX_NODES, 1)
        max_text_chars = _renforge_scene_limit(
            payload, "max_text_chars", _RENFORGE_SCENE_MAX_TEXT_CHARS, 16
        )
        width, height = _renforge_scene_window()
        seen = set()
        nodes = []
        counters = {
            "_max_depth": max_depth,
            "_max_nodes": max_nodes,
            "_max_text_chars": max_text_chars,
            "_detail": detail,
            "_flt_layers": flt_layers,
            "_flt_types": flt_types,
            "_flt_screen": flt_screen,
            "_flt_ids": flt_ids,
            "_omitted_max_depth": 0,
            "_omitted_max_nodes": 0,
        }

        # Focusable controls first, so the descent skips their widgets.
        try:
            focusables = _renforge_focusable_elements(max_nodes + 1, max_text_chars)
        except Exception:
            focusables = []
        if len(focusables) > max_nodes:
            counters["_omitted_max_nodes"] += len(focusables) - max_nodes
            for _focus, element in focusables[:max_nodes]:
                element["covered"] = None
                element["clickable"] = None
                element["coverage_reason"] = "max_nodes"
            focusables = focusables[:max_nodes]
        for focus, element in focusables:
            if len(nodes) >= max_nodes:
                counters["_omitted_max_nodes"] += len(focusables) - len(nodes)
                break
            widget = getattr(focus, "widget", None)
            if widget is not None:
                seen.add(id(widget))
            node = dict(element)
            node.setdefault("layer", "screens")
            node["bounds_available"] = node.get("bounds") is not None
            node.pop("index", None)
            node.pop("role", None)
            node["_d"] = widget
            nodes.append(node)

        # Every layer's top-level displayables, with real logical bounds.
        try:
            sl = renpy.game.context().scene_lists
        except Exception:
            sl = None
        try:
            now = renpy.display.core.get_time()
        except Exception:
            now = 0.0
        if sl is not None:
            layer_map = getattr(sl, "layers", {}) or {}
            try:
                ordered = list(getattr(renpy.display.scenelists, "ordered_layers", None) or renpy.config.layers)
            except Exception:
                ordered = list(layer_map.keys())
            layer_names = [l for l in ordered if l in layer_map] + [l for l in layer_map if l not in ordered]
            layer_counts = {}
            limit_hit = False
            for layer in layer_names:
                if limit_hit:
                    break
                for sle in layer_map.get(layer, []):
                    if len(nodes) >= max_nodes:
                        counters["_omitted_max_nodes"] += 1
                        limit_hit = True
                        break
                    d = getattr(sle, "displayable", None)
                    if d is None or id(d) in seen:
                        continue
                    seen.add(id(d))
                    st = (now - sle.show_time) if getattr(sle, "show_time", None) else 0
                    at = (now - sle.animation_time) if getattr(sle, "animation_time", None) else 0
                    bounds = _renforge_scene_place(d, width, height, st, at)
                    node_type = _renforge_scene_type(d)
                    tag = getattr(sle, "tag", None)
                    tag_text = _renforge_scene_string(tag, max_text_chars)
                    tag_id = _renforge_scene_string(tag, _RENFORGE_SCENE_MAX_ID_CHARS)
                    screen_name = None
                    sn = getattr(d, "screen_name", None)
                    if isinstance(sn, (tuple, list)) and sn:
                        screen_name = str(sn[0])
                    elif sn:
                        screen_name = str(sn)
                    elif layer == "screens" and tag:
                        screen_name = str(tag)
                    if tag:
                        node_id = "%s/%s" % (layer, tag_id)
                    else:
                        idx = layer_counts.get((layer, node_type), 0)
                        layer_counts[(layer, node_type)] = idx + 1
                        node_id = "%s/%s#%d" % (layer, node_type, idx)
                    text = (
                        _renforge_scene_text(d, max_text_chars)
                        if node_type == "text"
                        else None
                    )
                    node = _renforge_scene_node(node_id, node_type, layer, screen_name, bounds, text=text)
                    node["zorder"] = int(getattr(sle, "zorder", 0) or 0)
                    if tag:
                        node["tag"] = tag_text
                    node["_d"] = d
                    nodes.append(node)
                    bx = bounds[0] if bounds is not None else 0
                    by = bounds[1] if bounds is not None else 0
                    _renforge_scene_descend_children(d, bx, by, layer, screen_name, node["zorder"], seen, nodes, counters, 0)

        _renforge_scene_unique_ids(nodes, max_text_chars)
        _renforge_scene_finalize(nodes, include)

        perceived_by_type = {}
        perceived_by_layer = {}
        for n in nodes:
            perceived_by_type[n["type"]] = perceived_by_type.get(n["type"], 0) + 1
            lk = n.get("layer") or "?"
            perceived_by_layer[lk] = perceived_by_layer.get(lk, 0) + 1

        returned = []
        for n in nodes:
            raw_type = n.get("_raw_type")
            raw_layer = n.get("_raw_layer")
            raw_screen = n.get("_raw_screen")
            if flt_types is None and not _renforge_scene_detail_ok(raw_type, detail):
                continue
            if flt_layers is not None and raw_layer not in flt_layers:
                continue
            if flt_types is not None and str(raw_type).casefold() not in flt_types:
                continue
            if flt_screen is not None and raw_screen != flt_screen:
                continue
            if flt_ids is not None and (str(n.get("id")) not in flt_ids):
                continue
            returned.append(n)

        ret_by_type = {}
        ret_by_layer = {}
        for n in returned:
            ret_by_type[n["type"]] = ret_by_type.get(n["type"], 0) + 1
            lk = n.get("layer") or "?"
            ret_by_layer[lk] = ret_by_layer.get(lk, 0) + 1
        omit_by_type = {t: perceived_by_type[t] - ret_by_type.get(t, 0)
                        for t in perceived_by_type if perceived_by_type[t] - ret_by_type.get(t, 0) > 0}
        omit_by_layer = {l: perceived_by_layer[l] - ret_by_layer.get(l, 0)
                         for l in perceived_by_layer if perceived_by_layer[l] - ret_by_layer.get(l, 0) > 0}
        for n in nodes:
            n.pop("_raw_type", None)
            n.pop("_raw_layer", None)
            n.pop("_raw_screen", None)
        omit_by_reason = {}
        if counters["_omitted_max_depth"]:
            omit_by_reason["max_depth"] = counters["_omitted_max_depth"]
        if counters["_omitted_max_nodes"]:
            omit_by_reason["max_nodes"] = counters["_omitted_max_nodes"]

        return {
            "ok": True,
            "coordinate_space": "logical",
            "window": {"width": width, "height": height},
            "detail": detail,
            "nodes": returned,
            "counts": {"perceived": len(nodes), "returned": len(returned)},
            "omitted": {
                "by_type": omit_by_type,
                "by_layer": omit_by_layer,
                "by_reason": omit_by_reason,
            },
            "truncated": bool(omit_by_reason),
            "limits": {
                "max_depth": max_depth,
                "max_nodes": max_nodes,
                "max_text_chars": max_text_chars,
            },
        }

    _RENFORGE_HANDLERS = {
        "ping": _renforge_h_ping,
        "get_state": _renforge_h_get_state,
        "get_metrics": _renforge_h_get_metrics_handler,
        "get_audio_state": _renforge_h_get_audio_state_handler,
        "inspect_screen": _renforge_h_inspect_screen,
        "eval": _renforge_h_eval,
        "get_var": _renforge_h_get_var,
        "set_var": _renforge_h_set_var,
        "screenshot": _renforge_h_screenshot,
        "advance": _renforge_h_advance,
        "send_input": _renforge_h_send_input,
        "control": _renforge_h_control,
        "save_slot": _renforge_h_save_slot,
        "load_slot": _renforge_h_load_slot,
        "list_slots": _renforge_h_list_slots,
        "poll_events": _renforge_h_poll_events,
        "list_choices": _renforge_h_list_choices,
        "select_choice": _renforge_h_select_choice,
        "list_ui_elements": _renforge_h_list_ui_elements,
        "click_element": _renforge_h_click_element,
        "hover_element": _renforge_h_hover_element,
        "get_ui_element_bounds": _renforge_h_get_ui_element_bounds,
        "click_at": _renforge_h_click_at,
        "hit_test": _renforge_h_hit_test,
        "get_displayable_bounds": _renforge_h_get_displayable_bounds,
        "show_displayable": _renforge_h_show_displayable,
        "scene_tree": _renforge_h_scene_tree,
    }

    def _renforge_skip_stop_reason():
        """Infer why Skip stopped from the current interactive context."""
        bridge = _renforge_runtime.bridge
        if bridge is not None and getattr(bridge, "_skip_reason_hint", None):
            hint = bridge._skip_reason_hint
            bridge._skip_reason_hint = None
            return hint
        try:
            if renpy.get_screen("choice") is not None:
                return "choice"
        except Exception:
            pass
        try:
            # Unseen dialogue policy left skip off after a line the player has not seen.
            prefs = getattr(renpy.store, "_preferences", None)
            if prefs is not None and not bool(getattr(prefs, "skip_unseen", True)):
                return "unseen_dialogue"
        except Exception:
            pass
        try:
            if not renpy.is_in_test() and getattr(renpy.context(), "current", None) is None:
                return "end_of_context"
        except Exception:
            pass
        return "explicit_stop"

    def _renforge_watch_runtime_effects():
        """Emit skip/auto business events when engine state changes."""
        bridge = _renforge_runtime.bridge
        if bridge is None:
            return
        try:
            skipping = getattr(renpy.config, "skipping", None)
        except Exception:
            skipping = None
        prev_skip = bridge.prev_skipping
        if prev_skip and not skipping:
            screen = None
            try:
                if renpy.get_screen("choice") is not None:
                    screen = "choice"
            except Exception:
                pass
            _renforge_emit_business(
                "skip.stopped",
                reason=_renforge_skip_stop_reason(),
                screen=screen,
                previous=prev_skip,
            )
        elif skipping and not prev_skip:
            _renforge_emit_business("skip.started", mode=skipping)
        bridge.prev_skipping = skipping

        try:
            prefs = getattr(renpy.store, "_preferences", None)
            afm = bool(getattr(prefs, "afm_enable", False)) if prefs is not None else None
        except Exception:
            afm = None
        if afm is not None and bridge.prev_afm is not None and afm != bridge.prev_afm:
            _renforge_emit_business("auto.changed", enabled=afm)
        if afm is not None:
            bridge.prev_afm = afm

        history = _renforge_history_index()
        if history is not None:
            bridge.prev_history_index = history

    def renforge_drain_bridge():
        # Runs on the MAIN thread via config.periodic_callbacks.
        bridge = _renforge_runtime.bridge
        if bridge is None:
            return
        if bridge.stop.is_set():
            _renforge_reset_testmouse_state()
            return
        _renforge_watch_runtime_effects()
        while True:
            try:
                req = bridge.requests.get_nowait()
            except queue.Empty:
                break
            handler = _RENFORGE_HANDLERS.get(req.command)
            correlation = None
            explicit_correlation = None
            propagate = None
            try:
                if handler is None:
                    req.error = "unknown_command: %s" % req.command
                else:
                    explicit_correlation = _renforge_correlation_from_payload(req.payload)
                    correlation = explicit_correlation
                    if correlation is None and req.command in (
                        "control",
                        "click_element",
                        "save_slot",
                        "load_slot",
                    ):
                        # Auto ids keep business events attributable even when
                        # the caller omitted interaction_id; only explicit ids
                        # are echoed on the command reply.
                        bridge.interaction_counter += 1
                        correlation = "%s-%s" % (req.command, bridge.interaction_counter)
                    bridge.current_correlation_id = correlation
                    result = handler(req.payload)
                    if (
                        isinstance(result, builtins.dict)
                        and explicit_correlation is not None
                    ):
                        result = builtins.dict(result)
                        result.setdefault("interaction_id", explicit_correlation)
                    req.result = result
            except Exception as exc:
                end_interaction = getattr(
                    getattr(getattr(renpy, "display", None), "core", None),
                    "EndInteraction",
                    None,
                )
                if end_interaction is not None and isinstance(exc, end_interaction):
                    preserved_result = getattr(exc, "renforge_result", None)
                    if isinstance(preserved_result, builtins.dict):
                        preserved_result = builtins.dict(preserved_result)
                        preserved_result["ended_interaction"] = True
                        if explicit_correlation is not None:
                            preserved_result.setdefault("interaction_id", explicit_correlation)
                        req.result = preserved_result
                    else:
                        req.result = {"ok": True, "ended_interaction": True}
                    propagate = exc
                else:
                    req.error = "%s: %s" % (type(exc).__name__, exc)
            finally:
                bridge.current_correlation_id = None
                _renforge_reset_testmouse_state()
                req.event.set()
            if propagate is not None:
                raise propagate

    # --- listener: background thread --------------------------------------

    def _renforge_reply(conn, obj):
        # Local import: the listener thread survives renpy.reload_script(),
        # which wipes the store (the __globals__ of init-python functions).
        # A free-var reference to ``json`` would raise NameError mid-reload.
        # ``import`` reads from sys.modules, which the reload never touches.
        import json as _json
        conn.sendall((_json.dumps(obj) + "\n").encode("utf-8"))

    _RENFORGE_BRIDGE_INFO_MAX_BYTES = 16 * 1024
    _RENFORGE_BRIDGE_STARTUP_ERROR_PREFIX = "RENFORGE_BRIDGE_STARTUP_ERROR="
    _RENFORGE_BRIDGE_STARTUP_PUBLICATION_FAILED = "BRIDGE_MANIFEST_PUBLICATION_FAILED"
    _RENFORGE_BRIDGE_STARTUP_INFO_CONFLICT = "BRIDGE_INFO_CONFLICT"
    _RENFORGE_BRIDGE_STARTUP_IDENTITY_MISMATCH = "BRIDGE_MANIFEST_IDENTITY_MISMATCH"
    _RENFORGE_BRIDGE_INFO_KEYS = (
        "schema_version",
        "protocol_version",
        "state",
        "session_id",
        "project_root",
        "host",
        "port",
        "token",
    )

    def _renforge_bridge_startup_error(code):
        import sys as _sys
        try:
            _sys.stderr.write("%s%s\n" % (_RENFORGE_BRIDGE_STARTUP_ERROR_PREFIX, code))
            _sys.stderr.flush()
        except Exception:
            pass

    def _renforge_bridge_is_session_id(value):
        if not isinstance(value, str) or len(value) != 32:
            return False
        for ch in value:
            if ch not in "0123456789abcdef":
                return False
        return True

    def _renforge_bridge_is_token(value):
        if not isinstance(value, str) or len(value) != 64:
            return False
        for ch in value:
            if ch not in "0123456789abcdef":
                return False
        return True

    def _renforge_bridge_info_path(project_root):
        import os as _os
        return _os.path.join(project_root, ".renforge", "control", "bridge.json")

    def _renforge_bridge_control_dir(project_root):
        import os as _os
        return _os.path.join(project_root, ".renforge", "control")

    def _renforge_bridge_win_create_file(path, access, share_mode, creation_disposition, flags_and_attrs):
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        # WinDLL + pointer-width template HANDLE (NULL) avoids 32-bit coercion.
        kernel32 = _ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            _wintypes.LPCWSTR,
            _wintypes.DWORD,
            _wintypes.DWORD,
            _ctypes.c_void_p,
            _wintypes.DWORD,
            _wintypes.DWORD,
            _ctypes.c_void_p,
        ]
        create_file.restype = _wintypes.HANDLE
        handle = create_file(
            str(path),
            access,
            share_mode,
            None,
            creation_disposition,
            flags_and_attrs,
            None,
        )
        invalid_val = getattr(_wintypes, "HANDLE", _ctypes.c_void_p)(-1).value
        if handle == invalid_val or handle is None or handle == 0 or handle == 0xFFFFFFFF or handle == 0xFFFFFFFFFFFFFFFF:
            err = _ctypes.get_last_error()
            raise OSError(err, "CreateFileW failed for %s" % path)
        return handle

    def _renforge_bridge_win_close_handle(handle):
        import ctypes as _ctypes
        if handle is None:
            return
        try:
            _ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass

    def _renforge_bridge_win_get_file_type(handle):
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        get_type = _ctypes.windll.kernel32.GetFileType
        get_type.argtypes = [_wintypes.HANDLE]
        get_type.restype = _wintypes.DWORD
        return int(get_type(handle))

    def _renforge_bridge_win_get_handle_attributes(handle):
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        class _BY_HANDLE_FILE_INFORMATION(_ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", _wintypes.DWORD),
                ("ftCreationTime", _wintypes.FILETIME),
                ("ftLastAccessTime", _wintypes.FILETIME),
                ("ftLastWriteTime", _wintypes.FILETIME),
                ("dwVolumeSerialNumber", _wintypes.DWORD),
                ("nFileSizeHigh", _wintypes.DWORD),
                ("nFileSizeLow", _wintypes.DWORD),
                ("nNumberOfLinks", _wintypes.DWORD),
                ("nFileIndexHigh", _wintypes.DWORD),
                ("nFileIndexLow", _wintypes.DWORD),
            ]

        info = _BY_HANDLE_FILE_INFORMATION()
        get_info = _ctypes.windll.kernel32.GetFileInformationByHandle
        get_info.argtypes = [_wintypes.HANDLE, _ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)]
        get_info.restype = _wintypes.BOOL
        if not get_info(handle, _ctypes.byref(info)):
            raise OSError(_ctypes.GetLastError(), "GetFileInformationByHandle failed")
        return int(info.dwFileAttributes)

    def _renforge_bridge_win_read_handle(handle, max_bytes):
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        read_file = _ctypes.windll.kernel32.ReadFile
        read_file.argtypes = [
            _wintypes.HANDLE,
            _ctypes.c_void_p,
            _wintypes.DWORD,
            _ctypes.POINTER(_wintypes.DWORD),
            _ctypes.c_void_p,
        ]
        read_file.restype = _wintypes.BOOL

        chunks = []
        remaining = max_bytes + 1
        while remaining > 0:
            size = min(65536, remaining)
            buf = _ctypes.create_string_buffer(size)
            read_bytes = _wintypes.DWORD(0)
            if not read_file(handle, buf, size, _ctypes.byref(read_bytes), None):
                raise OSError(_ctypes.GetLastError(), "ReadFile failed")
            if read_bytes.value == 0:
                break
            chunks.append(buf.raw[: read_bytes.value])
            remaining -= read_bytes.value
        return b"".join(chunks)

    def _renforge_bridge_win_write_handle(handle, data):
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        write_file = _ctypes.windll.kernel32.WriteFile
        write_file.argtypes = [
            _wintypes.HANDLE,
            _ctypes.c_void_p,
            _wintypes.DWORD,
            _ctypes.POINTER(_wintypes.DWORD),
            _ctypes.c_void_p,
        ]
        write_file.restype = _wintypes.BOOL

        written = 0
        while written < len(data):
            to_write = min(65536, len(data) - written)
            buf = _ctypes.create_string_buffer(data[written : written + to_write])
            chunk_written = _wintypes.DWORD(0)
            if not write_file(handle, buf, to_write, _ctypes.byref(chunk_written), None):
                raise OSError(_ctypes.GetLastError(), "WriteFile failed")
            if chunk_written.value == 0:
                raise OSError("short write on Win32 handle")
            written += chunk_written.value

    def _renforge_bridge_win_flush_handle(handle):
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        flush = _ctypes.windll.kernel32.FlushFileBuffers
        flush.argtypes = [_wintypes.HANDLE]
        flush.restype = _wintypes.BOOL
        if not flush(handle):
            raise OSError(_ctypes.GetLastError(), "FlushFileBuffers failed")

    def _renforge_bridge_win_replace_file(replaced_path, replacement_path, flags=1):
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes
        import os as _os

        # Replacing a private (protected-DACL) destination via MoveFileEx
        # REPLACE_EXISTING returns ERROR_ACCESS_DENIED (5) on Windows CI even
        # when the caller owns FA. Clear attributes, remove the destination,
        # then rename the temp into place — same effective outcome as replace.
        kernel32 = _ctypes.WinDLL("kernel32", use_last_error=True)
        set_attrs = kernel32.SetFileAttributesW
        set_attrs.argtypes = [_wintypes.LPCWSTR, _wintypes.DWORD]
        set_attrs.restype = _wintypes.BOOL
        FILE_ATTRIBUTE_NORMAL = 0x80

        for candidate in (replaced_path, replacement_path):
            try:
                set_attrs(str(candidate), FILE_ATTRIBUTE_NORMAL)
            except Exception:
                pass

        if _os.path.lexists(replaced_path) or _renforge_bridge_path_is_symlink(replaced_path):
            try:
                _os.unlink(replaced_path)
            except OSError as exc:
                raise OSError(getattr(exc, "winerror", None) or exc.errno or 5,
                              "failed to remove previous bridge info: %s" % replaced_path) from exc

        move_file = kernel32.MoveFileExW
        move_file.argtypes = [_wintypes.LPCWSTR, _wintypes.LPCWSTR, _wintypes.DWORD]
        move_file.restype = _wintypes.BOOL
        # MOVEFILE_WRITE_THROUGH only — destination is already gone.
        if move_file(str(replacement_path), str(replaced_path), 0x8):
            return True
        err = _ctypes.get_last_error()
        # Fallback: Python's os.replace (MoveFileEx REPLACE_EXISTING).
        try:
            _os.replace(str(replacement_path), str(replaced_path))
            return True
        except OSError:
            pass
        raise OSError(err, "MoveFileExW failed for %s -> %s" % (replacement_path, replaced_path))

    def _renforge_bridge_win_is_reparse(path):
        import os as _os
        try:
            st = _os.lstat(path)
            attrs = getattr(st, "st_file_attributes", None)
            if attrs is not None:
                return bool(attrs & 0x400)
        except OSError:
            pass
        try:
            import ctypes as _ctypes
            from ctypes import wintypes as _wintypes
            get_attrs = _ctypes.windll.kernel32.GetFileAttributesW
            get_attrs.argtypes = [_wintypes.LPCWSTR]
            get_attrs.restype = _wintypes.DWORD
            val = int(get_attrs(str(path)))
            if val != 0xFFFFFFFF:
                return bool(val & 0x400)
        except Exception:
            pass
        return False

    def _renforge_bridge_win_advapi_kernel():
        """Load kernel32/advapi32 with WinDLL so GetLastError is reliable."""
        import ctypes as _ctypes

        kernel32 = _ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = _ctypes.WinDLL("advapi32", use_last_error=True)
        return kernel32, advapi32

    def _renforge_bridge_win_bind_sid_apis(kernel32, advapi32):
        """Declare pointer-width argtypes for SID helpers.

        Without argtypes, ctypes coerces PSID through c_int (32-bit). On
        Windows 64-bit that raises OverflowError and the bridge fails closed
        as BRIDGE_INFO_CONFLICT while validating the private control DACL.
        """
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        if getattr(advapi32, "_renforge_sid_bound", False):
            return

        advapi32.OpenProcessToken.argtypes = [
            _wintypes.HANDLE,
            _wintypes.DWORD,
            _ctypes.POINTER(_wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = _wintypes.BOOL

        advapi32.GetTokenInformation.argtypes = [
            _wintypes.HANDLE,
            _wintypes.DWORD,
            _ctypes.c_void_p,
            _wintypes.DWORD,
            _ctypes.POINTER(_wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = _wintypes.BOOL

        advapi32.ConvertSidToStringSidW.argtypes = [
            _ctypes.c_void_p,
            _ctypes.POINTER(_wintypes.LPWSTR),
        ]
        advapi32.ConvertSidToStringSidW.restype = _wintypes.BOOL

        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            _wintypes.LPCWSTR,
            _wintypes.DWORD,
            _ctypes.POINTER(_ctypes.c_void_p),
            _ctypes.POINTER(_wintypes.ULONG),
        ]
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = _wintypes.BOOL

        # SetFileSecurityW ignores PROTECTED_DACL; SetNamedSecurityInfoW sets it.
        advapi32.SetNamedSecurityInfoW.argtypes = [
            _wintypes.LPWSTR,
            _wintypes.DWORD,
            _wintypes.DWORD,
            _ctypes.c_void_p,
            _ctypes.c_void_p,
            _ctypes.c_void_p,
            _ctypes.c_void_p,
        ]
        advapi32.SetNamedSecurityInfoW.restype = _wintypes.DWORD

        advapi32.GetSecurityDescriptorDacl.argtypes = [
            _ctypes.c_void_p,
            _ctypes.POINTER(_wintypes.BOOL),
            _ctypes.POINTER(_ctypes.c_void_p),
            _ctypes.POINTER(_wintypes.BOOL),
        ]
        advapi32.GetSecurityDescriptorDacl.restype = _wintypes.BOOL

        advapi32.GetNamedSecurityInfoW.argtypes = [
            _wintypes.LPCWSTR,
            _wintypes.DWORD,
            _wintypes.DWORD,
            _ctypes.POINTER(_ctypes.c_void_p),
            _ctypes.POINTER(_ctypes.c_void_p),
            _ctypes.POINTER(_ctypes.c_void_p),
            _ctypes.POINTER(_ctypes.c_void_p),
            _ctypes.POINTER(_ctypes.c_void_p),
        ]
        advapi32.GetNamedSecurityInfoW.restype = _wintypes.DWORD

        advapi32.GetSecurityDescriptorControl.argtypes = [
            _ctypes.c_void_p,
            _ctypes.POINTER(_wintypes.DWORD),
            _ctypes.POINTER(_wintypes.DWORD),
        ]
        advapi32.GetSecurityDescriptorControl.restype = _wintypes.BOOL

        advapi32.GetAce.argtypes = [
            _ctypes.c_void_p,
            _wintypes.DWORD,
            _ctypes.POINTER(_ctypes.c_void_p),
        ]
        advapi32.GetAce.restype = _wintypes.BOOL

        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = _wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [_wintypes.HANDLE]
        kernel32.CloseHandle.restype = _wintypes.BOOL
        kernel32.LocalFree.argtypes = [_ctypes.c_void_p]
        kernel32.LocalFree.restype = _ctypes.c_void_p

        advapi32._renforge_sid_bound = True

    def _renforge_bridge_win_sid_to_string(advapi32, kernel32, sid_ptr):
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        if not sid_ptr:
            raise OSError("null SID pointer")
        if not isinstance(sid_ptr, _ctypes.c_void_p):
            sid_ptr = _ctypes.c_void_p(int(sid_ptr))
        string_sid = _wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid_ptr, _ctypes.byref(string_sid)):
            raise OSError(_ctypes.get_last_error(), "ConvertSidToStringSidW failed")
        try:
            value = string_sid.value
            if not value:
                raise OSError("ConvertSidToStringSidW returned an empty SID")
            return str(value)
        finally:
            if string_sid:
                kernel32.LocalFree(string_sid)

    def _renforge_bridge_win_current_sid():
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        kernel32, advapi32 = _renforge_bridge_win_advapi_kernel()
        _renforge_bridge_win_bind_sid_apis(kernel32, advapi32)

        token = _wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            0x0008,  # TOKEN_QUERY
            _ctypes.byref(token),
        ):
            raise OSError(_ctypes.get_last_error(), "OpenProcessToken failed")
        try:
            size = _wintypes.DWORD(0)
            # First call sizes the buffer; ERROR_INSUFFICIENT_BUFFER is expected.
            advapi32.GetTokenInformation(token, 1, None, 0, _ctypes.byref(size))
            if size.value == 0:
                raise OSError(_ctypes.get_last_error(), "GetTokenInformation size query failed")
            buf = _ctypes.create_string_buffer(size.value)
            if not advapi32.GetTokenInformation(token, 1, buf, size.value, _ctypes.byref(size)):
                raise OSError(_ctypes.get_last_error(), "GetTokenInformation failed")

            class _SID_AND_ATTRIBUTES(_ctypes.Structure):
                _fields_ = [("Sid", _ctypes.c_void_p), ("Attributes", _wintypes.DWORD)]

            class _TOKEN_USER(_ctypes.Structure):
                _fields_ = [("User", _SID_AND_ATTRIBUTES)]

            user = _ctypes.cast(buf, _ctypes.POINTER(_TOKEN_USER)).contents
            return _renforge_bridge_win_sid_to_string(advapi32, kernel32, user.User.Sid)
        finally:
            kernel32.CloseHandle(token)

    def _renforge_bridge_win_set_protected_dacl(path):
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        kernel32, advapi32 = _renforge_bridge_win_advapi_kernel()
        _renforge_bridge_win_bind_sid_apis(kernel32, advapi32)

        sddl = "D:P(A;;FA;;;%s)(A;;FA;;;SY)(A;;FA;;;BA)" % _renforge_bridge_win_current_sid()
        sd = _ctypes.c_void_p()
        size = _wintypes.ULONG()
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, _ctypes.byref(sd), _ctypes.byref(size)
        ):
            raise OSError(
                _ctypes.get_last_error(),
                "ConvertStringSecurityDescriptorToSecurityDescriptorW failed",
            )
        try:
            dacl_present = _wintypes.BOOL(0)
            dacl_defaulted = _wintypes.BOOL(0)
            dacl = _ctypes.c_void_p()
            if not advapi32.GetSecurityDescriptorDacl(
                sd,
                _ctypes.byref(dacl_present),
                _ctypes.byref(dacl),
                _ctypes.byref(dacl_defaulted),
            ):
                raise OSError(_ctypes.get_last_error(), "GetSecurityDescriptorDacl failed")
            if not dacl_present or not dacl:
                raise OSError("security descriptor has no DACL")
            # SE_FILE_OBJECT=1; DACL_SECURITY_INFORMATION|PROTECTED_DACL_SECURITY_INFORMATION
            status = advapi32.SetNamedSecurityInfoW(
                str(path),
                1,
                0x00000004 | 0x80000000,
                None,
                None,
                dacl,
                None,
            )
            if status != 0:
                raise OSError(status, "SetNamedSecurityInfoW failed for %s" % path)
        finally:
            if sd:
                kernel32.LocalFree(sd)

    def _renforge_bridge_win_validate_protected_dacl(path):
        import ctypes as _ctypes
        from ctypes import wintypes as _wintypes

        kernel32, advapi32 = _renforge_bridge_win_advapi_kernel()
        _renforge_bridge_win_bind_sid_apis(kernel32, advapi32)

        class _ACL(_ctypes.Structure):
            _fields_ = [
                ("AclRevision", _wintypes.BYTE),
                ("Sbz1", _wintypes.BYTE),
                ("AclSize", _wintypes.WORD),
                ("AceCount", _wintypes.WORD),
                ("Sbz2", _wintypes.WORD),
            ]

        class _ACE_HEADER(_ctypes.Structure):
            _fields_ = [("AceType", _wintypes.BYTE), ("AceFlags", _wintypes.BYTE), ("AceSize", _wintypes.WORD)]

        sd = _ctypes.c_void_p()
        dacl = _ctypes.c_void_p()
        status = advapi32.GetNamedSecurityInfoW(
            str(path),
            1,  # SE_FILE_OBJECT
            0x00000004,  # DACL_SECURITY_INFORMATION
            None,
            None,
            _ctypes.byref(dacl),
            None,
            _ctypes.byref(sd),
        )
        if status != 0:
            raise OSError(status, "GetNamedSecurityInfoW failed for %s" % path)
        try:
            if not dacl:
                raise OSError("missing DACL on %s" % path)
            control = _wintypes.DWORD()
            revision = _wintypes.DWORD()
            if not advapi32.GetSecurityDescriptorControl(
                sd, _ctypes.byref(control), _ctypes.byref(revision)
            ):
                raise OSError(_ctypes.get_last_error(), "GetSecurityDescriptorControl failed")
            if not (control.value & 0x1000):  # SE_DACL_PROTECTED
                raise OSError("DACL is not protected on %s" % path)

            allowed = {_renforge_bridge_win_current_sid(), "S-1-5-18", "S-1-5-32-544"}
            protected = bool(control.value & 0x1000)  # SE_DACL_PROTECTED
            inherited_ace = False
            acl = _ctypes.cast(dacl, _ctypes.POINTER(_ACL)).contents
            for index in range(acl.AceCount):
                ace = _ctypes.c_void_p()
                if not advapi32.GetAce(dacl, index, _ctypes.byref(ace)):
                    raise OSError(_ctypes.get_last_error(), "GetAce failed")
                header = _ctypes.cast(ace, _ctypes.POINTER(_ACE_HEADER)).contents
                if header.AceType != 0:  # ACCESS_ALLOWED_ACE_TYPE
                    raise OSError("unexpected ACE type on %s" % path)
                if header.AceFlags & 0x10:  # INHERITED_ACE
                    inherited_ace = True
                # ACCESS_ALLOWED_ACE: header (4) + Mask (4) + SidStart
                sid_ptr = _ctypes.c_void_p(int(ace.value or 0) + 8)
                sid_text = _renforge_bridge_win_sid_to_string(advapi32, kernel32, sid_ptr)
                if sid_text not in allowed:
                    raise OSError("unexpected trustee on private path %s" % path)
            # Prefer SE_DACL_PROTECTED; accept explicit-only DACLs with no inherited ACEs.
            if not protected and inherited_ace:
                raise OSError("DACL is not protected on %s" % path)
            if not protected and acl.AceCount == 0:
                raise OSError("DACL is not protected on %s" % path)
        finally:
            if sd:
                kernel32.LocalFree(sd)

    def _renforge_bridge_path_is_symlink(path):
        import os as _os
        import stat as _stat

        try:
            st = _os.lstat(path)
            if _stat.S_ISLNK(st.st_mode):
                return True
        except FileNotFoundError:
            return False
        except OSError:
            pass
        if _os.name == "nt":
            return _renforge_bridge_win_is_reparse(path)
        return False

    def _renforge_bridge_validate_control_dir(project_root):
        """Validate existing private control path without creating or repairing it."""
        import os as _os
        import stat as _stat

        renforge_dir = _os.path.join(project_root, ".renforge")
        control_dir = _os.path.join(renforge_dir, "control")
        # Reject symlink/reparse ancestors before accepting control contents.
        if _renforge_bridge_path_is_symlink(renforge_dir):
            raise OSError("renforge directory must not be a symlink")
        if _renforge_bridge_path_is_symlink(control_dir):
            raise OSError("control directory must not be a symlink")
        try:
            st = _os.lstat(control_dir)
        except OSError:
            raise
        if not _stat.S_ISDIR(st.st_mode):
            raise OSError("control directory is not a directory")
        if _os.name == "nt":
            _renforge_bridge_win_validate_protected_dacl(control_dir)
        else:
            if hasattr(_os, "geteuid") and st.st_uid != _os.geteuid():
                raise OSError("control directory is not owned by the current user")
            if (st.st_mode & 0o777) != 0o700:
                raise OSError("control directory mode must be 0700")
        return control_dir

    def _renforge_bridge_validate_private_file_stat(st, path):
        import os as _os
        import stat as _stat

        if not _stat.S_ISREG(st.st_mode):
            raise OSError("bridge info is not a regular file: %s" % path)
        if _os.name == "nt":
            _renforge_bridge_win_validate_protected_dacl(path)
        else:
            if hasattr(_os, "geteuid") and st.st_uid != _os.geteuid():
                raise OSError("bridge info is not owned by the current user: %s" % path)
            if (st.st_mode & 0o777) != 0o600:
                raise OSError("bridge info mode must be 0600: %s" % path)

    def _renforge_bridge_read_starting_info(project_root):
        """Read and validate the reserved starting bridge.json without following links."""
        import json as _json
        import os as _os
        import stat as _stat

        control_dir = _renforge_bridge_validate_control_dir(project_root)
        path = _renforge_bridge_info_path(project_root)
        if _renforge_bridge_path_is_symlink(path):
            raise OSError("bridge info must not be a symlink")

        if _os.name == "nt":
            handle = None
            try:
                handle = _renforge_bridge_win_create_file(
                    path,
                    0x80000000,
                    0x00000001,
                    3,
                    0x00200000,
                )
                if _renforge_bridge_win_get_file_type(handle) != 1:
                    raise OSError("bridge info is not a regular disk file: %s" % path)
                attrs = _renforge_bridge_win_get_handle_attributes(handle)
                if attrs & 0x400:
                    raise OSError("bridge info is a reparse point: %s" % path)
                _renforge_bridge_win_validate_protected_dacl(path)
                payload = _renforge_bridge_win_read_handle(handle, _RENFORGE_BRIDGE_INFO_MAX_BYTES + 1)
            finally:
                if handle is not None:
                    _renforge_bridge_win_close_handle(handle)
        else:
            try:
                st = _os.lstat(path)
            except OSError:
                raise
            _renforge_bridge_validate_private_file_stat(st, path)
            if st.st_size > _RENFORGE_BRIDGE_INFO_MAX_BYTES:
                raise OSError("bridge info exceeds size limit")

            flags = _os.O_RDONLY
            if hasattr(_os, "O_NOFOLLOW"):
                flags |= _os.O_NOFOLLOW
            if hasattr(_os, "O_CLOEXEC"):
                flags |= _os.O_CLOEXEC
            fd = _os.open(path, flags)
            try:
                opened = _os.fstat(fd)
                _renforge_bridge_validate_private_file_stat(opened, path)
                if opened.st_dev != st.st_dev or opened.st_ino != st.st_ino:
                    raise OSError("bridge info identity changed during open")
                payload = _os.read(fd, _RENFORGE_BRIDGE_INFO_MAX_BYTES + 1)
            finally:
                try:
                    _os.close(fd)
                except OSError:
                    pass

        if len(payload) > _RENFORGE_BRIDGE_INFO_MAX_BYTES:
            raise OSError("bridge info exceeds size limit")
        try:
            data = _json.loads(payload.decode("utf-8"))
        except Exception:
            raise OSError("bridge info is not valid JSON")
        if not isinstance(data, builtins.dict):
            raise OSError("bridge info must be an object")
        if set(data.keys()) != set(_RENFORGE_BRIDGE_INFO_KEYS):
            raise OSError("bridge info keys are invalid")
        sv = data.get("schema_version")
        pv = data.get("protocol_version")
        if type(sv) is not int or isinstance(sv, bool) or sv != 1 or type(pv) is not int or isinstance(pv, bool) or pv != 1:
            raise OSError("bridge info version is invalid")
        if data.get("state") != "starting":
            raise OSError("bridge info is not in starting state")
        if data.get("host") != "127.0.0.1":
            raise OSError("bridge info host is invalid")
        port = data.get("port")
        if type(port) is not int or isinstance(port, bool) or port != 0:
            raise OSError("bridge info starting port must be 0")
        session_id = data.get("session_id")
        token = data.get("token")
        recorded_root = data.get("project_root")
        if not _renforge_bridge_is_session_id(session_id):
            raise OSError("bridge info session_id is invalid")
        if not _renforge_bridge_is_token(token):
            raise OSError("bridge info token is invalid")
        if not isinstance(recorded_root, str) or not recorded_root:
            raise OSError("bridge info project_root is invalid")
        if recorded_root != project_root:
            raise OSError("bridge info project_root mismatch")
        return {
            "schema_version": 1,
            "protocol_version": 1,
            "state": "starting",
            "session_id": session_id,
            "project_root": recorded_root,
            "host": "127.0.0.1",
            "port": 0,
            "token": token,
        }

    def _renforge_bridge_fsync_directory(directory):
        import os as _os

        if not hasattr(_os, "O_RDONLY"):
            return
        flags = _os.O_RDONLY
        if hasattr(_os, "O_DIRECTORY"):
            flags |= _os.O_DIRECTORY
        try:
            fd = _os.open(directory, flags)
        except OSError:
            return
        try:
            try:
                _os.fsync(fd)
            except OSError:
                pass
        finally:
            try:
                _os.close(fd)
            except OSError:
                pass

    def _renforge_bridge_write_ready_info(project_root, payload):
        """Atomically publish ready bridge.json into an existing private control dir."""
        import json as _json
        import os as _os
        import secrets as _secrets
        import stat as _stat

        control_dir = _renforge_bridge_validate_control_dir(project_root)
        path = _renforge_bridge_info_path(project_root)
        encoded = _json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _RENFORGE_BRIDGE_INFO_MAX_BYTES:
            raise OSError("ready bridge info exceeds size limit")

        if _renforge_bridge_path_is_symlink(path):
            raise OSError("bridge info destination must not be a symlink")
        if _os.path.lexists(path):
            try:
                st = _os.lstat(path)
            except OSError:
                raise
            _renforge_bridge_validate_private_file_stat(st, path)

        if _os.name == "nt":
            temp_path = None
            handle = None
            last_error = None
            for _ in range(32):
                candidate = _os.path.join(
                    control_dir,
                    ".bridge.json.%s.tmp" % _secrets.token_hex(8),
                )
                try:
                    handle = _renforge_bridge_win_create_file(
                        candidate,
                        0xC0000000,
                        0,
                        1,
                        0x00200000,
                    )
                except OSError as exc:
                    win_err = getattr(exc, "winerror", None) or (exc.args[0] if exc.args and isinstance(exc.args[0], int) else None)
                    if win_err in (8, 183):
                        last_error = exc
                        continue
                    raise OSError("failed to create private temporary bridge info") from exc
                temp_path = candidate
                break

            if handle is None or temp_path is None:
                raise OSError("failed to allocate private temporary bridge info") from last_error

            try:
                if _renforge_bridge_win_get_file_type(handle) != 1:
                    raise OSError("temporary bridge info is not a regular disk file")
                attrs = _renforge_bridge_win_get_handle_attributes(handle)
                if attrs & 0x400:
                    raise OSError("temporary bridge info is a reparse point")

                # Write then close before touching DACL: exclusive CreateFile handles
                # can make SetNamedSecurityInfo fail mid-publish on some runners.
                _renforge_bridge_win_write_handle(handle, encoded)
                _renforge_bridge_win_flush_handle(handle)
                _renforge_bridge_win_close_handle(handle)
                handle = None

                _renforge_bridge_win_set_protected_dacl(temp_path)
                _renforge_bridge_win_validate_protected_dacl(temp_path)

                if _renforge_bridge_path_is_symlink(path):
                    raise OSError("bridge info destination must not be a symlink")
                if _os.path.lexists(path):
                    _renforge_bridge_win_validate_protected_dacl(path)

                _renforge_bridge_win_replace_file(path, temp_path, flags=1)
                temp_path = None

                _renforge_bridge_win_set_protected_dacl(path)
                _renforge_bridge_win_validate_protected_dacl(path)
                if _renforge_bridge_win_is_reparse(path):
                    raise OSError("published bridge info must not be a reparse point")
            finally:
                if handle is not None:
                    _renforge_bridge_win_close_handle(handle)
                if temp_path is not None:
                    try:
                        if _os.path.lexists(temp_path) or _renforge_bridge_path_is_symlink(temp_path):
                            _os.unlink(temp_path)
                    except OSError:
                        pass
        else:
            flags = _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL
            if hasattr(_os, "O_NOFOLLOW"):
                flags |= _os.O_NOFOLLOW
            if hasattr(_os, "O_CLOEXEC"):
                flags |= _os.O_CLOEXEC

            temp_path = None
            fd = None
            last_error = None
            for _ in range(32):
                candidate = _os.path.join(
                    control_dir,
                    ".bridge.json.%s.tmp" % _secrets.token_hex(8),
                )
                try:
                    fd = _os.open(candidate, flags, 0o600)
                except FileExistsError as exc:
                    last_error = exc
                    continue
                except OSError as exc:
                    raise OSError("failed to create private temporary bridge info") from exc
                temp_path = candidate
                break
            if fd is None or temp_path is None:
                raise OSError("failed to allocate private temporary bridge info") from last_error

            try:
                opened = _os.fstat(fd)
                _renforge_bridge_validate_private_file_stat(opened, temp_path)
                written = 0
                while written < len(encoded):
                    chunk = _os.write(fd, encoded[written:])
                    if chunk <= 0:
                        raise OSError("short write while publishing bridge info")
                    written += chunk
                _os.fsync(fd)
                opened = _os.fstat(fd)
                _renforge_bridge_validate_private_file_stat(opened, temp_path)
                _os.close(fd)
                fd = None
                if _renforge_bridge_path_is_symlink(path):
                    raise OSError("bridge info destination must not be a symlink")
                _os.replace(temp_path, path)
                temp_path = None
                _renforge_bridge_fsync_directory(control_dir)
            finally:
                if fd is not None:
                    try:
                        _os.close(fd)
                    except OSError:
                        pass
                if temp_path is not None:
                    try:
                        if _os.path.lexists(temp_path) or _renforge_bridge_path_is_symlink(temp_path):
                            _os.unlink(temp_path)
                    except OSError:
                        pass

    def _renforge_publish_ready(bridge, port):
        """Validate reserved starting metadata and publish ready, or fail closed."""
        try:
            starting = _renforge_bridge_read_starting_info(bridge.project_root)
        except Exception:
            _renforge_bridge_startup_error(_RENFORGE_BRIDGE_STARTUP_INFO_CONFLICT)
            return False

        if (
            starting["session_id"] != bridge.session_id
            or starting["token"] != bridge.token
            or starting["project_root"] != bridge.project_root
            or starting["host"] != bridge.host
        ):
            _renforge_bridge_startup_error(_RENFORGE_BRIDGE_STARTUP_IDENTITY_MISMATCH)
            return False

        if type(port) is not int or isinstance(port, bool) or port < 1 or port > 65535:
            _renforge_bridge_startup_error(_RENFORGE_BRIDGE_STARTUP_PUBLICATION_FAILED)
            return False

        ready = {
            "schema_version": 1,
            "protocol_version": 1,
            "state": "ready",
            "session_id": bridge.session_id,
            "project_root": bridge.project_root,
            "host": "127.0.0.1",
            "port": port,
            "token": bridge.token,
        }
        try:
            _renforge_bridge_write_ready_info(bridge.project_root, ready)
        except Exception as exc:
            _renforge_bridge_startup_error(_RENFORGE_BRIDGE_STARTUP_PUBLICATION_FAILED)
            try:
                import sys as _sys
                detail = "%s: %s" % (type(exc).__name__, exc)
                # One line for CI logs; never include secrets (token is not in exc paths).
                _sys.stderr.write("RENFORGE_BRIDGE_STARTUP_DETAIL=%s\n" % detail.replace("\n", " ")[:500])
                _sys.stderr.flush()
            except Exception:
                pass
            return False
        return True

    def _renforge_listener(bridge):
        # The listener thread survives renpy.reload_script(), which restores
        # renpy.config from its post-import backup and *wipes the store* —
        # the __globals__ of init-python functions. A free-var reference to
        # ``socket`` then raises NameError when the 0.5s accept() timeout
        # fires ``except socket.timeout:``, silently killing the thread and
        # leaving the game running with a dead bridge. Local imports read
        # from sys.modules, which reload never touches, so the loop stays
        # alive across reloads. The helpers (_renforge_reply / publish /
        # _RenforgeRequest) are called inside the inner try/except, so a
        # transient NameError there is caught and only drops one connection;
        # they too use local imports for their stdlib references.
        import socket as _socket
        import json as _json
        server = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        server.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        published = False
        try:
            server.bind((bridge.host, bridge.port))
            bridge.port = server.getsockname()[1]
            # Plain int only — never hang the bridge object off the store.
            setattr(renpy.store, "renforge_bridge_port", bridge.port)
            if not _renforge_publish_ready(bridge, bridge.port):
                bridge.stop.set()
                return
            published = True
            server.listen(5)

            while not bridge.stop.is_set():
                try:
                    server.settimeout(0.5)
                    conn, _ = server.accept()
                except _socket.timeout:
                    continue
                except OSError:
                    # accept() can raise OSError (not just socket.timeout)
                    # on a recycled socket or a transient kernel error; never
                    # let it crash the loop the way a NameError on the bare
                    # ``socket`` name did before the local import.
                    continue

                try:
                    with conn:
                        # Bounded one-shot frame: 1 MiB max, 2s read budget.
                        conn.settimeout(2.0)
                        chunks = []
                        total = 0
                        max_bytes = 1 * 1024 * 1024
                        line = None
                        while True:
                            try:
                                piece = conn.recv(min(4096, max(1, max_bytes - total + 1)))
                            except Exception:
                                piece = b""
                            if not piece:
                                break
                            chunks.append(piece)
                            total += len(piece)
                            joined = b"".join(chunks)
                            if b"\n" in joined:
                                line = joined.split(b"\n", 1)[0]
                                break
                            if total > max_bytes:
                                _renforge_reply(conn, {"ok": False, "error": "authentication_failed"})
                                line = None
                                break
                        if not line:
                            continue
                        try:
                            msg = _json.loads(line.decode("utf-8"))
                        except Exception:
                            _renforge_reply(conn, {"ok": False, "error": "authentication_failed"})
                            continue
                        # builtins.dict: game code may shadow `dict` with a RevertableDict.
                        if not isinstance(msg, builtins.dict) or not isinstance(msg.get("token"), str):
                            _renforge_reply(conn, {"ok": False, "error": "authentication_failed"})
                            continue
                        provided = str(msg.get("token") or "")
                        expected = str(bridge.token or "")
                        try:
                            # Same-length only; mismatched lengths are invalid tokens.
                            token_ok = (
                                len(provided) == len(expected)
                                and _hmac.compare_digest(
                                    provided.encode("utf-8"),
                                    expected.encode("utf-8"),
                                )
                            )
                        except Exception:
                            token_ok = False
                        if not token_ok:
                            _renforge_reply(conn, {"ok": False, "error": "authentication_failed"})
                            continue

                        req = _RenforgeRequest(msg.get("command"), msg.get("payload"))
                        bridge.requests.put(req)
                        if req.event.wait(timeout=15.0):
                            if req.error is not None:
                                _renforge_reply(conn, {"error": req.error})
                            else:
                                _renforge_reply(conn, req.result)
                        else:
                            _renforge_reply(conn, {"error": "timeout_waiting_for_main_thread"})
                except Exception:
                    # One misbehaving connection — typically a client that
                    # timed out and hung up before the reply (the norm while
                    # reload_script blocks the main thread) — must never kill
                    # the accept loop and close the server socket with it.
                    continue
        except Exception:
            if not published:
                _renforge_bridge_startup_error(_RENFORGE_BRIDGE_STARTUP_PUBLICATION_FAILED)
                bridge.stop.set()
        finally:
            try:
                server.close()
            except Exception:
                pass

    def _renforge_install_callbacks(bridge):
        def _renforge_on_label(name, abnormal):
            bridge.current_label = name
            bridge.push_event("label", {"label": name})

        def _renforge_on_say(event, **kwargs):
            # Callbacks fire several times per line ("begin"/"show"/"end"); record
            # the text once, on the first event that carries it.
            what = kwargs.get("what")
            if event in ("begin", "show") and what and what != bridge.last_say:
                previous_say = bridge.last_say
                bridge.last_say = what
                bridge.push_event("say", {"what": what})
                try:
                    prefs = getattr(renpy.store, "_preferences", None)
                    afm = bool(getattr(prefs, "afm_enable", False)) if prefs is not None else False
                except Exception:
                    afm = False
                if afm:
                    bridge.interaction_counter += 1
                    _renforge_emit_business(
                        "auto.advanced",
                        from_interaction=bridge.interaction_counter - 1,
                        to_interaction=bridge.interaction_counter,
                        previous_dialogue=previous_say,
                        dialogue=what,
                    )

        def _renforge_exception_handler(short_msg, full_msg, traceback_fn):
            bridge.push_event("exception", {"short": short_msg, "full": full_msg})
            previous = bridge.prev_exception_handler
            if callable(previous):
                return previous(short_msg, full_msg, traceback_fn)
            return False  # not handled: let Ren'Py show its normal error screen

        renpy.config.label_callbacks.append(_renforge_on_label)
        renpy.config.all_character_callbacks.append(_renforge_on_say)
        bridge.prev_exception_handler = renpy.config.exception_handler
        renpy.config.exception_handler = _renforge_exception_handler
        renpy.config.periodic_callbacks.append(renforge_drain_bridge)

    def renforge_start_bridge():
        existing = getattr(_renforge_runtime, "bridge", None)
        if existing is not None:
            # renpy.reload_script() keeps the process — the listener thread,
            # its socket and this sys.modules entry all survive — but restores
            # renpy.config from its post-import backup before re-running this
            # init block. Every callback registered at first start is wiped
            # with it, so the bridge kept accepting connections that nothing
            # drained. Re-register on the fresh config and reuse the live
            # socket; never bind a second one.
            already_registered = any(
                getattr(callback, "__name__", "") == "renforge_drain_bridge"
                for callback in renpy.config.periodic_callbacks
            )
            if not already_registered:
                _renforge_install_callbacks(existing)
                setattr(renpy.store, "renforge_bridge_port", existing.port)
            return

        # Absent token means the launcher did not request a bridge — stay off.
        raw_token = os.environ.get("RENFORGE_BRIDGE_TOKEN")
        if raw_token is None:
            return

        token = "" if raw_token is None else str(raw_token).strip()
        session_id = os.environ.get("RENFORGE_BRIDGE_SESSION_ID", "")
        session_id = "" if session_id is None else str(session_id).strip()
        project_root = os.environ.get("RENFORGE_BRIDGE_PROJECT_ROOT", "")
        project_root = "" if project_root is None else str(project_root).strip()

        identity_ok = True
        if not _renforge_bridge_is_token(token) or not _renforge_bridge_is_session_id(session_id):
            identity_ok = False
        elif not project_root or not os.path.isabs(project_root):
            identity_ok = False
        else:
            try:
                if _renforge_bridge_path_is_symlink(project_root):
                    identity_ok = False
                elif not os.path.isdir(project_root):
                    identity_ok = False
                else:
                    canonical = os.path.realpath(project_root)
                    if canonical != project_root:
                        # Env must already be the canonical absolute root.
                        identity_ok = False
                    else:
                        project_root = canonical
            except OSError:
                identity_ok = False

        if not identity_ok:
            _renforge_bridge_startup_error(_RENFORGE_BRIDGE_STARTUP_IDENTITY_MISMATCH)
            return

        # Host is fixed to loopback; ignore any alternate host env.
        host = "127.0.0.1"
        try:
            port = int(os.environ.get("RENFORGE_BRIDGE_PORT", "0") or "0")
        except (TypeError, ValueError):
            port = 0
        if port < 0 or port > 65535:
            port = 0

        bridge = _RenforgeBridge(host, port, token, project_root, session_id)
        _renforge_runtime.bridge = bridge

        _renforge_install_callbacks(bridge)

        thread = threading.Thread(
            target=_renforge_listener,
            args=(bridge,),
            daemon=True,
            name="renforge.bridge.listener",
        )
        bridge.thread = thread
        thread.start()

    renforge_start_bridge()
