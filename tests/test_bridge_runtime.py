"""Exercise the real bridge.rpy RPC mechanism without a Ren'Py runtime.

The Python body of ``bridge.rpy`` is executed against a fake ``renpy`` module,
then driven end to end: the listener thread accepts a real ``BridgeClient``
connection, hands the request to a queue, and a drain loop (standing in for
Ren'Py's main-thread ``periodic_callbacks``) executes it and returns the reply.
"""

from __future__ import annotations

import base64
import threading
import time
import types
from pathlib import Path

import pytest

from renforge.bridge.client import BridgeClient, BridgeConfig, BridgeProtocolError


def _load_bridge_body():
    raw = Path(__file__).resolve().parents[1] / "src/renforge/bridge/bridge.rpy"
    lines = raw.read_text(encoding="utf-8").splitlines()
    assert lines[0].strip() == "init python:"
    return "\n".join(line[4:] if line.startswith("    ") else line for line in lines[1:])


class _FakeWidget:
    def __init__(self, text):
        self._text = text

    def _tts_all(self):
        return self._text


class _FakeRect:
    def __init__(self, left, top, width, height):
        self.left = left
        self.top = top
        self.width = width
        self.height = height


class _FakeSurface:
    def __init__(self, rect):
        self._rect = rect

    def get_bounding_rect(self, min_alpha=1):
        return self._rect


class _FakeImageButtonWidget:
    state_children = True

    def __init__(self, text, alpha_rect=(4, 6, 80, 14)):
        self._text = text
        self._alpha_rect = _FakeRect(*alpha_rect)
        self._child = object()
        self.style = types.SimpleNamespace(prefix="idle_")

    def _tts_all(self):
        return self._text

    def get_child(self):
        return self._child


class _FakeFocus:
    def __init__(self, text, x, y, w, h, widget=None):
        if widget is not None:
            self.widget = widget
        else:
            self.widget = _FakeWidget(text) if text is not None else None
        self.x, self.y, self.w, self.h = x, y, w, h


class _FakeInput:
    pass


def _fake_renpy(store):
    config = types.SimpleNamespace(
        basedir="",
        label_callbacks=[],
        periodic_callbacks=[],
        all_character_callbacks=[],
        exception_handler=None,
    )
    renpy = types.ModuleType("renpy")
    renpy.store = store
    renpy.config = config
    renpy.screenshot_to_bytes = lambda size: b"\x89PNG\r\n_fake_frame_"
    renpy.get_showing_tags = lambda: ["bg", "eileen"]
    renpy._queued_events = []
    renpy._pygame_events = []
    renpy._ran_actions = []
    renpy._invoked = []
    renpy.exports = types.SimpleNamespace(
        queue_event=lambda name, **kw: renpy._queued_events.append(name)
    )
    renpy.run = lambda action, *a, **k: renpy._ran_actions.append(action) or action
    renpy.invoke_in_main_thread = lambda fn, *a, **k: renpy._invoked.append((fn, a, k)) or fn(*a, **k)
    renpy.reload_script = lambda: renpy._invoked.append(("reload_script",))
    renpy.restart_interaction = lambda: renpy._invoked.append(("restart_interaction",))
    renpy.quit = lambda: renpy._invoked.append(("quit",))

    # Minimal focus + input system mirroring Ren'Py's runtime shape.
    load_button = _FakeImageButtonWidget("Load icon")
    focus_list = [
        _FakeFocus(None, None, None, None, None),  # the "default" whole-screen focus
        _FakeFocus("Alpha choice", 10, 10, 100, 20),
        _FakeFocus("Beta choice", 10, 40, 100, 20),
        _FakeFocus("Load icon", 200, 100, 100, 30, widget=load_button),
    ]
    renpy._focused_widget = None
    renpy.display = types.SimpleNamespace(
        focus=types.SimpleNamespace(
            focus_list=focus_list,
            get_focused=lambda: renpy._focused_widget,
        ),
        behavior=types.SimpleNamespace(Input=_FakeInput),
        interface=types.SimpleNamespace(mouse_focused=False, ignore_touch=False),
    )
    renpy._clicks = []
    renpy._moves = []

    def _find_focus(pattern):
        for focus in focus_list:
            if focus.widget is None:
                continue
            if pattern.lower() in focus.widget._tts_all().lower():
                return focus
        return None

    def _find_position(focus, _position):
        return (focus.x + focus.w // 2, focus.y + focus.h // 2)

    renpy.test = types.SimpleNamespace(
        testfocus=types.SimpleNamespace(find_focus=_find_focus, find_position=_find_position),
        testmouse=types.SimpleNamespace(
            click_mouse=lambda button, x, y: renpy._clicks.append((button, x, y)),
            move_mouse=lambda x, y: renpy._moves.append((x, y)),
        ),
    )

    # Displayable bounds + live repositioning. `_shown` maps a tag to its
    # rendered [x, y, w, h]; show() mutates it from the Transform's placement so
    # a reposition round-trips through get_image_bounds like the real engine.
    renpy._shown = {"eileen": [400, 300, 200, 400]}
    renpy.config.screen_width = 1920
    renpy.config.screen_height = 1080

    def _get_image_bounds(tag, layer=None):
        box = renpy._shown.get(tag)
        return tuple(box) if box else None

    renpy.get_image_bounds = _get_image_bounds

    class _FakeTransform:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    renpy.store.Transform = _FakeTransform

    def _show(name, at_list=None, layer=None, **kwargs):
        tag = str(name).split()[0]
        box = renpy._shown.setdefault(tag, [0, 0, 100, 100])
        for transform in at_list or []:
            placement = getattr(transform, "kwargs", {})
            if "xpos" in placement:
                box[0] = int(placement["xpos"])
            if "ypos" in placement:
                box[1] = int(placement["ypos"])

    renpy.show = _show

    def _render_to_surface(child, width, height, resize=True):
        for focus in focus_list:
            widget = getattr(focus, "widget", None)
            if widget is None or not callable(getattr(widget, "get_child", None)):
                continue
            if widget.get_child() is child and hasattr(widget, "_alpha_rect"):
                return _FakeSurface(widget._alpha_rect)
        return _FakeSurface(_FakeRect(0, 0, 0, 0))

    renpy.render_to_surface = _render_to_surface
    return renpy


@pytest.fixture
def running_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("RENFORGE_BRIDGE_TOKEN", "runtime-token")
    monkeypatch.setenv("RENFORGE_BRIDGE_PORT", "0")

    store = types.SimpleNamespace(score=7, player_name="Rin", _hidden="x")

    class _QuickSave:
        def __call__(self):
            return ("QuickSave",)

    class _QuickLoad:
        def __call__(self, confirm=True):
            return ("QuickLoad", confirm)

    store.QuickSave = _QuickSave()
    store.QuickLoad = _QuickLoad()

    renpy = _fake_renpy(store)
    renpy.config.basedir = str(tmp_path)

    class _FakeEvent:
        def __init__(self, event_type, attributes=None):
            self.type = event_type
            for name, value in (attributes or {}).items():
                setattr(self, name, value)

    pygame = types.ModuleType("pygame_sdl2")
    pygame.TEXTINPUT = 1
    pygame.KEYDOWN = 2
    pygame.KEYUP = 3
    pygame.MOUSEBUTTONDOWN = 4
    pygame.MOUSEBUTTONUP = 5
    pygame.K_F1 = 101
    pygame.K_F2 = 102
    pygame.K_F3 = 103
    pygame.K_F4 = 104
    pygame.K_F5 = 105
    pygame.K_F6 = 106
    pygame.K_F7 = 107
    pygame.K_F8 = 108
    pygame.K_F9 = 109
    pygame.K_F10 = 110
    pygame.K_F11 = 111
    pygame.K_F12 = 112
    pygame.KMOD_NONE = 0
    pygame.event = types.SimpleNamespace(
        Event=_FakeEvent,
        post=lambda event: renpy._pygame_events.append(event),
    )
    monkeypatch.setitem(__import__("sys").modules, "pygame_sdl2", pygame)

    # Bridge keeps runtime state on a sys.modules entry so saves stay picklable.
    import sys

    sys.modules.pop("_renforge_runtime", None)

    globs = {"__name__": "bridge_rpy", "renpy": renpy}
    exec(compile(_load_bridge_body(), "bridge.rpy", "exec"), globs)

    runtime = sys.modules.get("_renforge_runtime")
    bridge = getattr(runtime, "bridge", None)
    assert bridge is not None, "bridge did not start"

    # Stand in for Ren'Py's main thread: keep draining the request queue.
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            globs["renforge_drain_bridge"]()
            time.sleep(0.005)

    pump_thread = threading.Thread(target=pump, daemon=True)
    pump_thread.start()

    # Wait for the listener to publish bridge.json (as the real launcher does).
    info_path = tmp_path / ".renforge" / "bridge.json"
    for _ in range(300):
        if info_path.exists():
            break
        time.sleep(0.01)

    client = BridgeClient.from_project(tmp_path)
    env = types.SimpleNamespace(client=client, store=store, renpy=renpy, globs=globs)
    yield env

    stop.set()
    bridge.stop.set()


def test_ping_roundtrips_through_main_thread(running_bridge):
    assert running_bridge.client.ping().get("pong") is True


def test_get_state_reports_variables_and_showing(running_bridge):
    state = running_bridge.client.get_state()
    assert state["variables"]["score"] == 7
    assert state["variables"]["player_name"] == "Rin"
    assert "_hidden" not in state["variables"]  # private names are filtered
    assert state["showing_tags"] == ["bg", "eileen"]
    assert "metrics" not in state
    assert "audio" not in state


def test_get_state_includes_render_metrics_and_audio_channels_on_request(running_bridge):
    renpy = running_bridge.renpy
    renpy.display.interface.frame_times = [index / 10.0 for index in range(11)]
    renpy.display.im = types.SimpleNamespace(
        cache=types.SimpleNamespace(cache_size=1234, cache_limit=5678, cache={"a": 1, "b": 2})
    )
    renpy.get_physical_size = lambda: (2560, 1440)
    renpy.audio = types.SimpleNamespace(
        audio=types.SimpleNamespace(
            all_channels=[types.SimpleNamespace(name="music"), types.SimpleNamespace(name="custom")],
            channels={"music": object(), "custom": object()},
        )
    )
    renpy.music = types.SimpleNamespace(
        get_playing=lambda channel="music": {
            "music": "audio/theme.ogg",
            "custom": "audio/blip.wav",
        }.get(channel),
        get_volume=lambda channel="music": {"music": 0.75, "custom": 0.25}.get(channel),
        get_pause=lambda channel="music": channel == "custom",
    )

    state = running_bridge.client.get_state(include=["metrics", "audio"])

    assert state["metrics"]["render_time_ms"] == pytest.approx(100.0)
    assert state["metrics"]["fps"] == pytest.approx(10.0)
    assert state["metrics"]["image_cache_size"] == 1234
    assert state["metrics"]["window"] == {
        "logical": {"width": 1920, "height": 1080},
        "physical": {"width": 2560, "height": 1440},
    }
    assert state["audio"]["channels"] == {
        "music": {"playing": "audio/theme.ogg", "volume": 0.75, "pause": False},
        "custom": {"playing": "audio/blip.wav", "volume": 0.25, "pause": True},
    }
    assert running_bridge.client.get_metrics()["metrics"]["image_cache_size"] == 1234
    assert running_bridge.client.get_audio_state()["channels"]["music"]["playing"] == "audio/theme.ogg"


def test_get_state_rejects_unknown_include_values(running_bridge):
    reply = running_bridge.client.request("get_state", {"include": ["bogus"]})

    assert reply["ok"] is False
    assert "metrics" in reply["error"]


def test_get_state_include_accepts_wire_lists_when_store_list_is_revertable(running_bridge):
    # Ren'Py exposes its RevertableList as the unqualified ``list`` name in
    # store-backed init-python code. JSON decoding still returns a built-in
    # list, so validation must use builtins.list rather than that shadow.
    class _RevertableList(list):
        pass

    running_bridge.globs["list"] = _RevertableList

    reply = running_bridge.client.request("get_state", {"include": []})

    assert reply.get("error") is None
    assert "metrics" not in reply


def test_send_input_accepts_wire_scroll_when_store_dict_is_revertable(running_bridge):
    class _RevertableDict(dict):
        pass

    running_bridge.globs["dict"] = _RevertableDict

    reply = running_bridge.client.send_input(
        scroll={"x": 640, "y": 360, "direction": "down", "amount": 1}
    )

    assert reply["ok"] is True
    assert reply["mode"] == "scroll"
    assert reply["direction"] == "down"


def test_inspect_screen_reports_active_screen_contract_and_arguments(running_bridge):
    screen = types.SimpleNamespace(
        screen_name=("custom",),
        layer="overlay",
        scope={
            "count": 7,
            "title": "Demo",
            "_args": ("branch-a",),
            "_kwargs": {"enabled": True},
        },
    )
    running_bridge.renpy.get_screen = lambda name: screen if name == "custom" else None

    reply = running_bridge.client.inspect_screen("custom")

    assert reply["ok"] is True
    assert reply["active"] is True
    assert reply["name"] == "custom"
    assert reply["layer"] == "overlay"
    assert reply["scope"] == {"count": 7, "title": "Demo"}
    assert reply["arguments"] == {
        "args": ["branch-a"],
        "kwargs": {"enabled": True},
    }


def test_inspect_screen_reports_inactive_screen_clearly(running_bridge):
    running_bridge.renpy.get_screen = lambda _name: None

    reply = running_bridge.client.inspect_screen("missing")

    assert reply == {
        "ok": True,
        "active": False,
        "name": "missing",
        "error": "screen not showing: missing",
    }


def test_eval_and_set_var_mutate_real_store(running_bridge):
    client, store = running_bridge.client, running_bridge.store
    assert client.eval_expr("score * 2") == 14
    client.set_var("score", 99)
    assert store.score == 99
    assert client.get_var("score") == 99


def test_screenshot_returns_decoded_png_bytes(running_bridge):
    data = running_bridge.client.screenshot(320, 180)
    assert data.startswith(b"\x89PNG")


def test_screenshot_derives_the_missing_dimension_from_the_aspect_ratio(running_bridge):
    sizes = []

    def record(size):
        sizes.append(size)
        return b"\x89PNG\r\n_fake_frame_"

    running_bridge.renpy.screenshot_to_bytes = record

    # Logical screen is 1920x1080 (16:9) in the fake renpy module.
    running_bridge.client.screenshot(width=320)
    running_bridge.client.screenshot(height=270)
    running_bridge.client.screenshot()

    assert sizes == [(320, 180), (480, 270), None]


def test_screenshot_reports_when_the_aspect_ratio_is_unavailable(running_bridge):
    sizes = []

    def record(size):
        sizes.append(size)
        return b"\x89PNG\r\n_fake_frame_"

    running_bridge.renpy.screenshot_to_bytes = record
    running_bridge.renpy.config.screen_width = 0

    reply = running_bridge.client.request(
        "screenshot", {"width": 320, "height": 0}
    )

    # The frame comes back at native resolution, and the reply says so
    # instead of silently ignoring the requested size.
    assert sizes == [None]
    assert reply["note"] == "aspect ratio unavailable; captured at native resolution"


def test_bad_token_is_rejected(running_bridge):
    port = running_bridge.client._config.port
    wrong = BridgeClient(BridgeConfig(port=port, token="WRONG"))
    assert wrong.request("ping").get("error") == "bad_token"


def test_advance_posts_dismiss_event(running_bridge):
    assert running_bridge.client.advance().get("ok") is True
    assert "dismiss" in running_bridge.renpy._queued_events


def test_send_input_text_posts_textinput_per_character_and_submits(running_bridge):
    running_bridge.renpy._focused_widget = _FakeInput()

    reply = running_bridge.client.send_input(text="Alex", submit=True)

    assert reply == {
        "ok": True,
        "mode": "text",
        "characters": 4,
        "submitted": True,
    }
    assert [event.text for event in running_bridge.renpy._pygame_events] == list("Alex")
    assert all(event.type == 1 for event in running_bridge.renpy._pygame_events)
    assert "input_enter" in running_bridge.renpy._queued_events


def test_send_input_text_reports_missing_focused_input(running_bridge):
    reply = running_bridge.client.send_input(text="Alex")

    assert reply["ok"] is False
    assert "focused Ren'Py Input" in reply["error"]
    assert running_bridge.renpy._pygame_events == []


def test_send_input_text_focuses_visible_input_when_engine_has_no_current_focus(running_bridge):
    input_focus = _FakeFocus(None, 10, 10, 200, 30)
    input_focus.widget = _FakeInput()
    running_bridge.renpy.display.focus.focus_list.append(input_focus)
    running_bridge.renpy.display.focus.change_focus = lambda focus: setattr(
        running_bridge.renpy, "_focused_widget", focus.widget
    )

    reply = running_bridge.client.send_input(text="Alex", submit=True)

    assert reply == {
        "ok": True,
        "mode": "text",
        "characters": 4,
        "submitted": True,
    }
    assert [event.text for event in running_bridge.renpy._pygame_events] == list("Alex")
    assert "input_enter" in running_bridge.renpy._queued_events


def test_send_input_text_force_focuses_active_input_screen_widget(running_bridge):
    input_widget = _FakeInput()
    running_bridge.renpy.get_screen = lambda name: (
        types.SimpleNamespace(widgets={"input": input_widget}) if name == "input" else None
    )
    running_bridge.renpy.display.focus.force_focus = lambda widget: setattr(
        running_bridge.renpy, "_focused_widget", widget
    )

    reply = running_bridge.client.send_input(text="Alex", submit=True)

    assert reply == {
        "ok": True,
        "mode": "text",
        "characters": 4,
        "submitted": True,
    }
    assert [event.text for event in running_bridge.renpy._pygame_events] == list("Alex")
    assert "input_enter" in running_bridge.renpy._queued_events


def test_send_input_named_key_uses_readable_keymap_and_direct_pair(running_bridge):
    semantic = running_bridge.client.send_input(key="pageup")
    direct = running_bridge.client.send_input(key="f1")

    assert semantic == {"ok": True, "mode": "key", "key": "pageup", "event": "rollback"}
    assert running_bridge.renpy._queued_events[-1] == ["rollback", "viewport_pageup"]
    assert direct == {"ok": True, "mode": "key", "key": "f1", "keycode": 101}
    assert [(event.type, event.key) for event in running_bridge.renpy._pygame_events] == [
        (2, 101),
        (3, 101),
    ]


def test_send_input_unknown_key_is_explicit(running_bridge):
    reply = running_bridge.client.send_input(key="not-a-real-key")

    assert reply["ok"] is False
    assert "unknown key" in reply["error"]
    assert "pageup" in reply["error"]


def test_send_input_scroll_posts_logical_wheel_event(running_bridge):
    reply = running_bridge.client.send_input(
        scroll={"x": 123, "y": 456, "direction": "down"}
    )

    assert reply == {
        "ok": True,
        "mode": "scroll",
        "x": 123,
        "y": 456,
        "direction": "down",
        "amount": 1,
    }
    event = running_bridge.renpy._pygame_events[-1]
    assert event.type == 4
    assert event.button == 5
    assert event.pos == (123, 456)


def test_poll_events_captures_labels_and_say_lines(running_bridge):
    config = running_bridge.renpy.config
    # Fire Ren'Py's registered callbacks the way the engine would.
    for cb in config.label_callbacks:
        cb("chapter1", False)
    for cb in config.all_character_callbacks:
        cb("begin", what="Hello there.")
        cb("show", what="Hello there.")  # duplicate line must not double-record

    reply = running_bridge.client.poll_events()
    kinds = [(e["type"], e.get("label") or e.get("what")) for e in reply["events"]]
    assert ("label", "chapter1") in kinds
    assert ("say", "Hello there.") in kinds
    assert sum(1 for e in reply["events"] if e["type"] == "say") == 1

    # `since=cursor` returns only newer events.
    assert running_bridge.client.poll_events(since=reply["cursor"])["events"] == []


def test_list_choices_enumerates_focusable_text(running_bridge):
    texts = [c["text"] for c in running_bridge.client.list_choices()]
    assert texts == ["Alpha choice", "Beta choice", "Load icon"]  # the default focus is skipped


def test_select_choice_by_text_clicks_focus_center(running_bridge):
    reply = running_bridge.client.select_choice(text="Beta")
    assert reply["ok"] is True
    # Beta focus is at (10, 40) sized 100x20 -> center (60, 50).
    assert running_bridge.renpy._clicks[-1] == (1, 60, 50)
    # Unfocused Ren'Py windows zero click coords; select must re-enable mouse focus.
    assert running_bridge.renpy.display.interface.mouse_focused is True


def test_select_choice_by_index_resolves_text(running_bridge):
    reply = running_bridge.client.select_choice(index=0)
    assert reply["ok"] is True
    assert reply["text"] == "Alpha choice"


def test_select_choice_without_match_returns_error(running_bridge):
    reply = running_bridge.client.request("select_choice", {"text": "nope", "index": None})
    assert "error" in reply


def test_list_ui_elements_reports_bounds_and_semantic_fields(running_bridge):
    elements = running_bridge.client.list_ui_elements()
    assert [element["text"] for element in elements] == [
        "Alpha choice",
        "Beta choice",
        "Load icon",
    ]
    assert elements[0]["bounds"] == {"x": 10, "y": 10, "width": 100, "height": 20}
    assert elements[0]["center"] == {"x": 60, "y": 20}
    assert elements[0]["enabled"] is True
    assert elements[0]["visible"] is True
    assert elements[0]["type"] == "_FakeWidget"
    assert elements[0]["id"]
    assert "covered" in elements[0]
    assert "clickable" in elements[0]
    assert elements[0]["coordinate_space"] == "logical"
    info = running_bridge.client.list_ui_elements_info()
    assert info["elements"] == elements
    assert len(info["frame_id"]) == 64


def test_hit_test_reports_topmost_focusable(running_bridge):
    elements = running_bridge.client.list_ui_elements()
    target = elements[0]
    center = target["center"]
    hit = running_bridge.client.hit_test(center["x"], center["y"])
    assert hit["ok"] is True
    assert hit["topmost"]["id"] == target["id"]
    assert hit["coordinate_space"] == "logical"


def test_hover_element_moves_without_clicking(running_bridge):
    element = running_bridge.client.list_ui_elements()[1]
    hovered = running_bridge.client.hover_element(id=element["id"])
    assert hovered["ok"] is True
    assert hovered["hovered"] is True
    assert hovered["x"] == 60 and hovered["y"] == 50
    assert running_bridge.renpy._moves == [(60, 50)]
    assert running_bridge.renpy._clicks == []


def test_hover_element_frame_guard_blocks_motion(running_bridge):
    element = running_bridge.client.list_ui_elements()[1]
    mismatch = running_bridge.client.hover_element(id=element["id"], expected_frame_id="0" * 64)
    assert mismatch["ok"] is False
    assert "expected_frame_id" in mismatch["error"]
    assert running_bridge.renpy._moves == []


def test_get_ui_element_bounds_non_imagebutton_reports_unavailable(running_bridge):
    element = running_bridge.client.list_ui_elements()[0]
    reply = running_bridge.client.get_ui_element_bounds(id=element["id"])
    assert reply["ok"] is True
    assert reply["focus_bounds"] == element["bounds"]
    assert reply["painted_bounds"] is None
    assert reply["painted_bounds_available"] is False
    assert "ImageButton" in reply["painted_bounds_reason"]


def test_get_ui_element_bounds_imagebutton_reports_painted_bounds(running_bridge):
    element = running_bridge.client.list_ui_elements()[-1]
    reply = running_bridge.client.get_ui_element_bounds(id=element["id"])
    assert reply["ok"] is True
    assert reply["focus_bounds"] == {"x": 200, "y": 100, "width": 100, "height": 30}
    assert reply["painted_bounds"] == {"x": 204, "y": 106, "width": 80, "height": 14}
    assert reply["painted_bounds_available"] is True
    assert reply["painted_bounds_source"] == "rendered-alpha"
    assert reply["state"] == "idle"


def test_get_ui_element_bounds_frame_guard_blocks_lookup(running_bridge):
    element = running_bridge.client.list_ui_elements()[-1]
    mismatch = running_bridge.client.get_ui_element_bounds(
        id=element["id"],
        expected_frame_id="0" * 64,
    )
    assert mismatch["ok"] is False
    assert "expected_frame_id" in mismatch["error"]


def test_click_element_by_id_and_click_at_guards(running_bridge):
    element = running_bridge.client.list_ui_elements()[1]
    clicked = running_bridge.client.click_element(id=element["id"])
    assert clicked["ok"] is True
    assert clicked["x"] == 60 and clicked["y"] == 50

    frame_hash = running_bridge.client.screenshot_hash()
    guarded = running_bridge.client.click_at(
        123,
        77,
        expected_screenshot=frame_hash,
        expected_state={"current_label": None, "menu": False},
    )
    assert guarded["ok"] is True
    assert running_bridge.renpy._clicks[-1] == (1, 123, 77)

    mismatch = running_bridge.client.click_at(
        123,
        77,
        expected_screenshot="0" * 64,
    )
    assert mismatch["ok"] is False
    assert "expected_screenshot" in mismatch["error"]


def test_click_at_translates_screenshot_pixels_to_logical_coordinates(running_bridge):
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    import io

    image = image_module.new("RGB", (100, 50), "black")
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    running_bridge.renpy.screenshot_to_bytes = lambda _size: encoded.getvalue()
    running_bridge.renpy.config.screen_width = 1000
    running_bridge.renpy.config.screen_height = 500

    result = running_bridge.client.click_at(10, 20, coordinate_space="screenshot")

    assert result == {
        "ok": True,
        "x": 100,
        "y": 200,
        "coordinate_space": "screenshot",
    }
    assert running_bridge.renpy._clicks[-1] == (1, 100, 200)


def test_get_displayable_bounds_reports_logical_rect(running_bridge):
    reply = running_bridge.client.get_displayable_bounds("eileen")
    assert reply["ok"] is True
    assert reply["showing"] is True
    assert reply["bounds"] == {"x": 400, "y": 300, "width": 200, "height": 400}
    assert reply["center"] == {"x": 500, "y": 500}
    assert reply["coordinate_space"] == "logical"
    assert reply["screen"] == {"width": 1920, "height": 1080}


def test_get_displayable_bounds_missing_tag_is_a_control_result(running_bridge):
    reply = running_bridge.client.get_displayable_bounds("ghost")
    assert reply["ok"] is False
    assert reply["showing"] is False
    assert "not showing" in reply["error"]
    assert "eileen" in reply["showing_tags"]


def test_position_element_moves_tag_and_returns_new_bounds(running_bridge):
    reply = running_bridge.client.position_element("eileen", xpos=960, ypos=100)
    assert reply["ok"] is True
    assert reply["bounds"] == {"x": 960, "y": 100, "width": 200, "height": 400}
    # Integer positions are preserved as pixels, not coerced to a float
    # fraction of the screen.
    assert reply["applied"] == {"xpos": 960, "ypos": 100}
    # The move is durable: a follow-up measurement sees the new position.
    again = running_bridge.client.get_displayable_bounds("eileen")
    assert again["bounds"]["x"] == 960


def test_position_element_requires_a_placement_field(running_bridge):
    reply = running_bridge.client.position_element("eileen")
    assert reply["ok"] is False
    assert "placement" in reply["error"]


def test_position_element_rejects_a_hidden_tag(running_bridge):
    reply = running_bridge.client.position_element("ghost", xpos=10)
    assert reply["ok"] is False
    assert "not showing" in reply["error"]


def test_control_maps_toggle_auto_to_toggle_afm(running_bridge):
    reply = running_bridge.client.control("toggle_auto")
    assert reply["ok"] is True
    assert reply["event"] == "toggle_afm"
    assert "toggle_afm" in running_bridge.renpy._queued_events


def test_control_toggle_skip_queues_keymap_event(running_bridge):
    reply = running_bridge.client.control("toggle_skip")
    assert reply["ok"] is True
    assert reply["event"] == "toggle_skip"
    assert "toggle_skip" in running_bridge.renpy._queued_events


def test_control_quick_save_runs_action(running_bridge):
    reply = running_bridge.client.control("quick_save", interaction_id="qs-1")
    assert reply["ok"] is True
    assert reply["action"] == "quick_save"
    assert reply["interaction_id"] == "qs-1"
    assert reply["effect"]["event"] == "quick_save.completed"
    assert ("QuickSave",) in running_bridge.renpy._ran_actions
    events = running_bridge.client.poll_events()["events"]
    business = [e for e in events if e.get("type") == "quick_save.completed"]
    assert business
    assert business[-1]["correlation_id"] == "qs-1"


def test_control_quick_load_runs_action(running_bridge):
    reply = running_bridge.client.control("quick_load", interaction_id="ql-1")
    assert reply["ok"] is True
    assert reply["action"] == "quick_load"
    assert reply["effect"]["event"] == "quick_load.completed"
    assert ("QuickLoad", False) in running_bridge.renpy._ran_actions


def test_control_quit_uses_native_renpy_quit(running_bridge):
    reply = running_bridge.client.control("quit")

    assert reply["ok"] is True
    assert reply["action"] == "quit"
    assert ("quit",) in running_bridge.renpy._invoked


def test_control_rollback_emits_business_event(running_bridge):
    reply = running_bridge.client.control("rollback", interaction_id="rb-9")
    assert reply["ok"] is True
    assert reply["effect"]["event"] == "rollback.completed"
    events = running_bridge.client.poll_events()["events"]
    assert any(
        e.get("type") == "rollback.completed" and e.get("correlation_id") == "rb-9"
        for e in events
    )


def test_skip_watcher_emits_stopped_reason(running_bridge):
    import sys

    runtime = sys.modules["_renforge_runtime"]
    bridge = runtime.bridge
    bridge.prev_skipping = "slow"
    running_bridge.renpy.config.skipping = None
    running_bridge.renpy.get_screen = lambda name: object() if name == "choice" else None
    running_bridge.globs["renforge_drain_bridge"]()
    events = running_bridge.client.poll_events()["events"]
    stopped = [e for e in events if e.get("type") == "skip.stopped"]
    assert stopped
    assert stopped[-1]["reason"] in {"choice", "user_click", "explicit_stop", "unseen_dialogue"}


def test_control_unknown_action_preserves_bridge_error_payload(running_bridge):
    reply = running_bridge.client.control("not_an_action")

    assert reply == {
        "ok": False,
        "error": "unknown control action: not_an_action",
    }


def test_script_reload_reregisters_callbacks_on_surviving_bridge(running_bridge):
    import sys

    renpy = running_bridge.renpy
    bridge_before = sys.modules["_renforge_runtime"].bridge
    listeners_before = sum(
        1 for t in threading.enumerate() if t.name == "renforge.bridge.listener"
    )

    # renpy.reload_script() keeps the process — the listener thread, its socket
    # and the bridge entry in sys.modules all survive — but restores
    # renpy.config from its post-import backup and re-runs init blocks, wiping
    # every callback the bridge had registered.
    renpy.config.label_callbacks = []
    renpy.config.periodic_callbacks = []
    renpy.config.all_character_callbacks = []
    renpy.config.exception_handler = None
    del renpy.store.renforge_bridge_port

    exec(compile(_load_bridge_body(), "bridge.rpy", "exec"), {"__name__": "bridge_rpy", "renpy": renpy})

    # The live bridge and socket are reused: no second listener, same port.
    assert sys.modules["_renforge_runtime"].bridge is bridge_before
    listeners_after = sum(
        1 for t in threading.enumerate() if t.name == "renforge.bridge.listener"
    )
    assert listeners_after == listeners_before
    assert renpy.store.renforge_bridge_port == bridge_before.port

    # Every callback is back on the fresh config, exactly once.
    assert [cb.__name__ for cb in renpy.config.periodic_callbacks] == ["renforge_drain_bridge"]
    assert len(renpy.config.label_callbacks) == 1
    assert len(renpy.config.all_character_callbacks) == 1
    assert callable(renpy.config.exception_handler)

    # A second init pass over an intact config must not register duplicates.
    exec(compile(_load_bridge_body(), "bridge.rpy", "exec"), {"__name__": "bridge_rpy", "renpy": renpy})
    assert [cb.__name__ for cb in renpy.config.periodic_callbacks] == ["renforge_drain_bridge"]
    assert len(renpy.config.label_callbacks) == 1

    assert running_bridge.client.ping().get("pong") is True


def test_listener_survives_a_client_that_hangs_up_before_the_reply(running_bridge):
    # A client that times out and closes its socket mid-request (the norm
    # while reload_script blocks the main thread) makes the reply write blow
    # up in the listener; that must not kill the accept loop.
    globs = running_bridge.globs
    original_reply = globs["_renforge_reply"]
    calls = {"failed": False}

    def hung_up_reply(conn, obj):
        if not calls["failed"]:
            calls["failed"] = True
            raise OSError("client went away")
        return original_reply(conn, obj)

    globs["_renforge_reply"] = hung_up_reply
    try:
        with pytest.raises(BridgeProtocolError):
            running_bridge.client.ping()
    finally:
        globs["_renforge_reply"] = original_reply

    assert calls["failed"] is True
    assert running_bridge.client.ping().get("pong") is True


def test_save_slot_saves_named_state_with_extra_info(running_bridge):
    calls = {}
    running_bridge.renpy.can_save = lambda: True

    def save(slot, extra_info=""):
        calls.update(slot=slot, extra_info=extra_info)

    running_bridge.renpy.save = save

    reply = running_bridge.client.save_slot("branch-a", extra_info="before menu")

    assert reply == {
        "ok": True,
        "slot": "branch-a",
        "extra_info": "before menu",
    }
    assert calls == {"slot": "branch-a", "extra_info": "before menu"}


def test_save_slot_rejects_when_renpy_disallows_saving(running_bridge):
    running_bridge.renpy.can_save = lambda: False

    reply = running_bridge.client.save_slot("branch-a")

    assert reply == {
        "ok": False,
        "error": "saving is unavailable in the current game state",
    }


def test_save_slot_fallback_respects_disabled_save_config(running_bridge):
    running_bridge.renpy.config.save = False

    reply = running_bridge.client.save_slot("branch-a")

    assert reply == {
        "ok": False,
        "error": "saving is unavailable in the current game state",
    }


def test_save_slot_fallback_rejects_missing_runtime_objects(running_bridge):
    running_bridge.renpy.config = None
    running_bridge.renpy.store = None

    reply = running_bridge.client.save_slot("branch-a")

    assert reply == {
        "ok": False,
        "error": "saving is unavailable in the current game state",
    }


def test_load_slot_missing_name_returns_clean_error(running_bridge):
    running_bridge.renpy.can_load = lambda slot: False

    reply = running_bridge.client.load_slot("missing")

    assert reply == {
        "ok": False,
        "error": "save slot not found: missing",
    }


def test_load_slot_acknowledges_before_scheduling_control_flow(running_bridge):
    scheduled = []
    calls = {}

    class _LoadControl(Exception):
        pass

    running_bridge.renpy.can_load = lambda slot: True

    def load(slot):
        calls["slot"] = slot
        raise _LoadControl("load transfers control")

    running_bridge.renpy.load = load
    running_bridge.renpy.invoke_in_main_thread = lambda fn, *args, **kwargs: scheduled.append(
        (fn, args, kwargs)
    )

    reply = running_bridge.client.load_slot("branch-a")

    assert reply["ok"] is True
    assert reply["slot"] == "branch-a"
    assert "restored_label" in reply
    assert len(scheduled) == 1
    with pytest.raises(_LoadControl, match="transfers control"):
        scheduled[0][0](*scheduled[0][1], **scheduled[0][2])
    assert calls == {"slot": "branch-a"}


def test_list_slots_returns_metadata_without_loading_screenshots(running_bridge):
    calls = []
    running_bridge.renpy.list_slots = lambda regexp=None: ["branch-a", "branch-b"]
    running_bridge.renpy.slot_json = lambda slot: {
        "_save_name": "before menu" if slot == "branch-a" else "after choice",
    }
    running_bridge.renpy.slot_mtime = lambda slot: 12.5 if slot == "branch-a" else 13.5
    running_bridge.renpy.slot_screenshot = lambda slot: calls.append(slot) or pytest.fail(
        "list_slots must not load screenshots"
    )

    reply = running_bridge.client.list_slots(regexp="branch")

    assert reply == {
        "ok": True,
        "slots": [
            {"name": "branch-a", "extra_info": "before menu", "mtime": 12.5},
            {"name": "branch-b", "extra_info": "after choice", "mtime": 13.5},
        ],
    }
    assert calls == []


def test_list_slots_skips_corrupt_metadata_and_keeps_valid_slots(running_bridge):
    running_bridge.renpy.list_slots = lambda regexp=None: ["broken", "valid"]

    def slot_json(slot):
        if slot == "broken":
            raise ValueError("corrupt save metadata")
        return {"_save_name": "ok"}

    def slot_mtime(slot):
        if slot == "broken":
            raise OSError("inaccessible save")
        return 42.0

    running_bridge.renpy.slot_json = slot_json
    running_bridge.renpy.slot_mtime = slot_mtime

    reply = running_bridge.client.list_slots()

    assert reply == {
        "ok": True,
        "slots": [
            {"name": "broken", "extra_info": "", "mtime": None},
            {"name": "valid", "extra_info": "ok", "mtime": 42.0},
        ],
    }
