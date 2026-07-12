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
    #   RENFORGE_BRIDGE_TOKEN  required; the bridge stays off if unset
    #   RENFORGE_BRIDGE_HOST   default 127.0.0.1
    #   RENFORGE_BRIDGE_PORT   default 0 (an ephemeral port is chosen)
    # On startup the chosen host/port/token are published to
    #   <project>/.renforge/bridge.json
    # so the client can discover them.

    import base64
    import builtins
    import collections
    import hashlib
    import json
    import os
    import queue
    import socket
    import struct
    import sys
    import threading
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
        def __init__(self, host, port, token, basedir):
            self.host = host
            self.port = port
            self.token = token
            self.basedir = basedir
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

        def push_event(self, kind, data):
            self.event_seq += 1
            record = {"seq": self.event_seq, "type": kind}
            record.update(data)
            self.events.append(record)

    def _renforge_jsonable(value):
        """Best-effort conversion of a Python value to something JSON-safe."""
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [_renforge_jsonable(v) for v in value]
        if isinstance(value, dict):
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
        return list(dict.fromkeys(include)), None

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
        if not isinstance(raw_kwargs, dict):
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

    def _renforge_h_get_state(payload):
        include, include_error = _renforge_state_includes(payload)
        if include_error is not None:
            return {"ok": False, "error": include_error}
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
            "variables": _renforge_store_snapshot(),
        }
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
        size = (width, height) if (width and height) else None
        data = renpy.screenshot_to_bytes(size)  # PNG bytes
        return {
            "format": "png",
            "base64": base64.b64encode(data).decode("ascii"),
            # The digest lets an external client use the exact frame it
            # inspected as an optimistic click guard, without storing image
            # data in the bridge process.
            "sha256": hashlib.sha256(data).hexdigest(),
        }

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
        supplied = [name for name in ("text", "key", "scroll") if name in payload and payload.get(name) is not None]
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

        scroll = payload.get("scroll")
        if not isinstance(scroll, dict):
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
            renpy.exports.queue_event(event_name)
            return {"ok": True, "action": action, "event": event_name}
        if action == "quick_save":
            quick_save = getattr(renpy.store, "QuickSave", None)
            if not callable(quick_save):
                return {"ok": False, "error": "QuickSave is unavailable", "action": action}
            _renforge_run_action(quick_save())
            return {"ok": True, "action": action}
        if action == "quick_load":
            quick_load = getattr(renpy.store, "QuickLoad", None)
            if not callable(quick_load):
                return {"ok": False, "error": "QuickLoad is unavailable", "action": action}
            # Load raises FullRestartException; schedule it so the interaction
            # loop can propagate engine control-flow instead of catching it here.
            load_action = quick_load(confirm=False)

            def _do_quick_load():
                _renforge_run_action(load_action)

            _renforge_invoke(_do_quick_load)
            return {"ok": True, "action": action}
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
            return {"ok": False, "error": "save failed: %s" % exc}

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
        return {"ok": True, "slot": slot}

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
            if isinstance(metadata, dict):
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
        """Best-effort accessible text for a Ren'Py focus widget."""
        if widget is None:
            return ""
        text = None
        for method_name in ("_tts_all", "get_text"):
            method = getattr(widget, method_name, None)
            if not callable(method):
                continue
            try:
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
            for attr_name in ("id", "widget_id", "focus_id", "name", "key"):
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

    def _renforge_focusable_elements():
        """Return ``(focus, element)`` pairs for visible focus rectangles.

        ``focus_list`` is Ren'Py's authoritative list of controls that can
        receive pointer/keyboard focus.  It already excludes hidden screens;
        zero-sized and off-layout entries are omitted here.  IDs are supplied
        by the displayable when possible and otherwise are deterministic for
        the current focus list, so an agent can list and immediately click.
        """
        elements = []
        used_ids = {}
        try:
            focus_list = renpy.display.focus.focus_list
        except Exception:
            return elements
        for ordinal, focus in enumerate(focus_list):
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
            text = _renforge_focus_text(widget)
            screen = _renforge_screen_name(focus)
            role = _renforge_focus_type(focus, widget)
            element_id = _renforge_explicit_focus_id(focus, widget)
            if not element_id:
                # Include text/type/screen where available so IDs remain useful
                # across a redraw; ordinal disambiguates duplicate labels.
                base = "%s:%s:%s" % (screen or "screen", role, text or ordinal)
                element_id = base
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
                "bounds": bounds,
                "center": {"x": x + w // 2, "y": y + h // 2},
                "enabled": _renforge_focus_enabled(focus, widget),
                "visible": True,
                "index": ordinal,
            }
            elements.append((focus, element))
        return elements

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

    def _renforge_click_focus(focus):
        """Click a focus center through Ren'Py's synthetic test input path."""
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
        testmouse = getattr(getattr(renpy, "test", None), "testmouse", None)
        click_mouse = getattr(testmouse, "click_mouse", None)
        if not callable(click_mouse):
            raise RuntimeError("Ren'Py synthetic mouse API is unavailable")
        click_mouse(1, x, y)
        return x, y

    def _renforge_h_click_element(payload):
        payload = payload or {}
        wanted_id = payload.get("id") or payload.get("element_id")
        wanted_text = payload.get("text")
        if wanted_text == "":
            wanted_text = None
        exact = bool(payload.get("exact", False))
        wanted_screen = payload.get("screen")
        expected_frame_id = payload.get("expected_frame_id") or payload.get("expected_screenshot")
        if wanted_id is None and wanted_text is None:
            return {"ok": False, "error": "click_element requires text or id"}
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
                if exact:
                    if actual_text.casefold() != wanted_text.casefold():
                        continue
                elif wanted_text.casefold() not in actual_text.casefold():
                    continue
            candidates.append((focus, element))
        if not candidates:
            return {"ok": False, "error": "no UI element matching %r/%r" % (wanted_text, wanted_id)}
        if len(candidates) > 1:
            return {
                "ok": False,
                "error": "ambiguous UI element; provide an id or exact text",
                "matches": [item[1] for item in candidates],
            }

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
        focus, element = candidates[0]
        if not element.get("enabled", True):
            return {"ok": False, "error": "UI element is disabled", "element": element}
        x, y = _renforge_click_focus(focus)
        result = {
            "ok": True,
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

    def _renforge_state_matches(actual, expected):
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
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
        if isinstance(expected, dict):
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
        testmouse = getattr(getattr(renpy, "test", None), "testmouse", None)
        click_mouse = getattr(testmouse, "click_mouse", None)
        if not callable(click_mouse):
            return {"ok": False, "error": "Ren'Py synthetic mouse API is unavailable"}
        click_mouse(1, x, y)
        result = {"ok": True, "x": x, "y": y, "coordinate_space": coordinate_space}
        if screenshot_digest is not None:
            result["sha256"] = screenshot_digest
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
        # Select a menu option by visible text (preferred) or by index, by
        # resolving the focusable and simulating a mouse click on it — the same
        # path Ren'Py's own test framework uses.
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
        if text is not None:
            focus = renpy.test.testfocus.find_focus(text)
            chosen = text
        elif index is not None:
            choices = _renforge_focusable_choices()
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

        renpy.test.testmouse.click_mouse(1, x, y)
        return {"ok": True, "text": chosen, "x": x, "y": y}

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
        "click_at": _renforge_h_click_at,
        "get_displayable_bounds": _renforge_h_get_displayable_bounds,
        "show_displayable": _renforge_h_show_displayable,
    }

    def renforge_drain_bridge():
        # Runs on the MAIN thread via config.periodic_callbacks.
        bridge = _renforge_runtime.bridge
        if bridge is None:
            return
        while True:
            try:
                req = bridge.requests.get_nowait()
            except queue.Empty:
                break
            handler = _RENFORGE_HANDLERS.get(req.command)
            try:
                if handler is None:
                    req.error = "unknown_command: %s" % req.command
                else:
                    req.result = handler(req.payload)
            except Exception as exc:
                req.error = "%s: %s" % (type(exc).__name__, exc)
            finally:
                req.event.set()

    # --- listener: background thread --------------------------------------

    def _renforge_reply(conn, obj):
        conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def _renforge_publish(bridge, port):
        try:
            out_dir = os.path.join(bridge.basedir, ".renforge")
            os.makedirs(out_dir, exist_ok=True)
            tmp = os.path.join(out_dir, "bridge.json.tmp")
            final = os.path.join(out_dir, "bridge.json")
            with open(tmp, "w") as fp:
                json.dump(
                    {"host": bridge.host, "port": port, "token": bridge.token, "pid": os.getpid()},
                    fp,
                )
            os.replace(tmp, final)
        except Exception:
            pass

    def _renforge_listener(bridge):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((bridge.host, bridge.port))
            bridge.port = server.getsockname()[1]
            # Plain int only — never hang the bridge object off the store.
            setattr(renpy.store, "renforge_bridge_port", bridge.port)
            _renforge_publish(bridge, bridge.port)
            server.listen(5)

            while not bridge.stop.is_set():
                try:
                    server.settimeout(0.5)
                    conn, _ = server.accept()
                except socket.timeout:
                    continue

                with conn:
                    line = conn.makefile("r", encoding="utf-8").readline()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        _renforge_reply(conn, {"error": "invalid_json"})
                        continue
                    if msg.get("token") != bridge.token:
                        _renforge_reply(conn, {"error": "bad_token", "ok": False})
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
        finally:
            server.close()

    def renforge_start_bridge():
        if getattr(_renforge_runtime, "bridge", None) is not None:
            return

        token = os.environ.get("RENFORGE_BRIDGE_TOKEN", "")
        token = "" if token is None else str(token).strip()
        if not token:
            return

        host = os.environ.get("RENFORGE_BRIDGE_HOST", "127.0.0.1")
        try:
            port = int(os.environ.get("RENFORGE_BRIDGE_PORT", "0") or "0")
        except (TypeError, ValueError):
            port = 0

        basedir = getattr(renpy.config, "basedir", "") or os.getcwd()
        bridge = _RenforgeBridge(host, port, token, basedir)
        _renforge_runtime.bridge = bridge

        def _renforge_on_label(name, abnormal):
            bridge.current_label = name
            bridge.push_event("label", {"label": name})

        def _renforge_on_say(event, **kwargs):
            # Callbacks fire several times per line ("begin"/"show"/"end"); record
            # the text once, on the first event that carries it.
            what = kwargs.get("what")
            if event in ("begin", "show") and what and what != bridge.last_say:
                bridge.last_say = what
                bridge.push_event("say", {"what": what})

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

        thread = threading.Thread(
            target=_renforge_listener,
            args=(bridge,),
            daemon=True,
            name="renforge.bridge.listener",
        )
        bridge.thread = thread
        thread.start()

    renforge_start_bridge()
