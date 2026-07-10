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

from renforge.bridge.client import BridgeClient, BridgeConfig


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


class _FakeFocus:
    def __init__(self, text, x, y, w, h):
        self.widget = _FakeWidget(text) if text is not None else None
        self.x, self.y, self.w, self.h = x, y, w, h


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
    focus_list = [
        _FakeFocus(None, None, None, None, None),  # the "default" whole-screen focus
        _FakeFocus("Alpha choice", 10, 10, 100, 20),
        _FakeFocus("Beta choice", 10, 40, 100, 20),
    ]
    renpy.display = types.SimpleNamespace(
        focus=types.SimpleNamespace(focus_list=focus_list),
        interface=types.SimpleNamespace(mouse_focused=False, ignore_touch=False),
    )
    renpy._clicks = []

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
            click_mouse=lambda button, x, y: renpy._clicks.append((button, x, y))
        ),
    )
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


def test_eval_and_set_var_mutate_real_store(running_bridge):
    client, store = running_bridge.client, running_bridge.store
    assert client.eval_expr("score * 2") == 14
    client.set_var("score", 99)
    assert store.score == 99
    assert client.get_var("score") == 99


def test_screenshot_returns_decoded_png_bytes(running_bridge):
    data = running_bridge.client.screenshot(320, 180)
    assert data.startswith(b"\x89PNG")


def test_bad_token_is_rejected(running_bridge):
    port = running_bridge.client._config.port
    wrong = BridgeClient(BridgeConfig(port=port, token="WRONG"))
    assert wrong.request("ping").get("error") == "bad_token"


def test_advance_posts_dismiss_event(running_bridge):
    assert running_bridge.client.advance().get("ok") is True
    assert "dismiss" in running_bridge.renpy._queued_events


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
    assert texts == ["Alpha choice", "Beta choice"]  # the default focus is skipped


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
    reply = running_bridge.client.control("quick_save")
    assert reply == {"ok": True, "action": "quick_save"}
    assert ("QuickSave",) in running_bridge.renpy._ran_actions


def test_control_quick_load_runs_action(running_bridge):
    reply = running_bridge.client.control("quick_load")
    assert reply == {"ok": True, "action": "quick_load"}
    assert ("QuickLoad", False) in running_bridge.renpy._ran_actions


def test_control_quit_uses_native_renpy_quit(running_bridge):
    reply = running_bridge.client.control("quit")

    assert reply == {"ok": True, "action": "quit"}
    assert ("quit",) in running_bridge.renpy._invoked
