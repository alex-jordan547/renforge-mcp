"""Exercise the real bridge.rpy RPC mechanism without a Ren'Py runtime.

The Python body of ``bridge.rpy`` is executed against a fake ``renpy`` module,
then driven end to end: the listener thread accepts a real ``BridgeClient``
connection, hands the request to a queue, and a drain loop (standing in for
Ren'Py's main-thread ``periodic_callbacks``) executes it and returns the reply.
"""

from __future__ import annotations

import base64
import contextlib
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


def _load_editor_body():
    raw = Path(__file__).resolve().parents[1] / "src/renforge/bridge/editor.rpy"
    lines = raw.read_text(encoding="utf-8").splitlines()
    start = lines.index("init 1100 python:")
    body = []
    for line in lines[start + 1 :]:
        if line and not line.startswith("    ") and not line.startswith("#"):
            break
        body.append(line[4:] if line.startswith("    ") else line)
    return "\n".join(body)


class _FakeWidget:
    def __init__(self, text):
        self._text = text

    def _tts_all(self, raw=False):
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

    def _tts_all(self, raw=False):
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

    def _move_mouse(x, y):
        renpy._moves.append((x, y))
        renpy.test.testmouse.mouse_pos = (x, y)

    def _click_mouse(button, x, y):
        renpy._clicks.append((button, x, y))

    def _find_focus(pattern, raw=False):
        for focus in focus_list:
            if focus.widget is None:
                continue
            if pattern.lower() in focus.widget._tts_all(raw).lower():
                return focus
        return None

    def _find_position(focus, _position):
        return (focus.x + focus.w // 2, focus.y + focus.h // 2)

    renpy.test = types.SimpleNamespace(
        testfocus=types.SimpleNamespace(find_focus=_find_focus, find_position=_find_position),
        testmouse=types.SimpleNamespace(
            click_mouse=_click_mouse,
            move_mouse=_move_mouse,
            mouse_pos=None,
            mouse_buttons=[False, False, False],
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
    import stat

    from renforge.bridge.control import read_bridge_info, write_starting_bridge_info

    project_root = tmp_path.resolve(strict=True)
    session_id = "a" * 32
    token = "b" * 64

    write_starting_bridge_info(project_root, session_id=session_id, token=token)

    monkeypatch.setenv("RENFORGE_BRIDGE_TOKEN", token)
    monkeypatch.setenv("RENFORGE_BRIDGE_SESSION_ID", session_id)
    monkeypatch.setenv("RENFORGE_BRIDGE_PROJECT_ROOT", str(project_root))
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
    renpy.config.basedir = str(project_root)

    class _FakeEvent:
        def __init__(self, event_type, attributes=None):
            self.type = event_type
            for name, value in (attributes or {}).items():
                setattr(self, name, value)

    pygame = types.ModuleType("pygame_sdl2")
    pygame.TEXTINPUT = 1
    pygame.KEYDOWN = 2
    pygame.KEYUP = 3
    pygame.MOUSEMOTION = 6
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

    # Wait for the listener to publish ready metadata under the private control path.
    info_path = project_root / ".renforge" / "control" / "bridge.json"
    ready_info = None
    for _ in range(300):
        try:
            ready_info = read_bridge_info(
                project_root,
                require_ready=True,
                expected_session_id=session_id,
            )
            break
        except Exception:
            pass
        time.sleep(0.01)
    assert ready_info is not None, "bridge did not publish valid ready metadata"
    assert ready_info.session_id == session_id
    assert ready_info.token == token
    assert ready_info.project_root == str(project_root)
    assert ready_info.host == "127.0.0.1"
    assert 1 <= ready_info.port <= 65535
    # POSIX private mode is 0600. Windows st_mode is not a real Unix mode
    # (often 0o666); ownership is enforced via the protected DACL instead.
    import os

    if os.name != "nt":
        mode = stat.S_IMODE(info_path.lstat().st_mode)
        assert mode == 0o600
    else:
        from renforge.util.files import _win_validate_protected_dacl

        _win_validate_protected_dacl(info_path)
    assert not info_path.is_symlink()
    assert not (project_root / ".renforge" / "bridge.json").exists()

    client = BridgeClient(
        BridgeConfig(
            host=ready_info.host,
            port=ready_info.port,
            token=ready_info.token,
        )
    )
    env = types.SimpleNamespace(
        client=client,
        store=store,
        renpy=renpy,
        globs=globs,
        project_root=project_root,
        session_id=session_id,
        token=token,
    )
    yield env

    stop.set()
    bridge.stop.set()


def test_listener_listens_before_publishing_ready_metadata() -> None:
    """Clients must never observe a ready record before accept() can succeed."""
    body = _load_bridge_body()
    start = body.index("def _renforge_listener")
    end = body.index("def _renforge_install_callbacks", start)
    listener = body[start:end]

    assert listener.index("server.listen(5)") < listener.index(
        "if not _renforge_publish_ready"
    )


def _seed_starting_bridge(project_root: Path, *, session_id: str, token: str) -> None:
    from renforge.bridge.control import write_starting_bridge_info

    write_starting_bridge_info(project_root, session_id=session_id, token=token)


def _exec_bridge_with_env(tmp_path, monkeypatch, *, env: dict[str, str], seed_starting=True):
    import io
    import sys

    project_root = tmp_path.resolve(strict=True)
    session_id = env.get("RENFORGE_BRIDGE_SESSION_ID", "a" * 32)
    token = env.get("RENFORGE_BRIDGE_TOKEN", "b" * 64)
    if seed_starting:
        _seed_starting_bridge(project_root, session_id=session_id if len(session_id) == 32 else "a" * 32, token=token if len(token) == 64 else "b" * 64)

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if "RENFORGE_BRIDGE_PROJECT_ROOT" not in env:
        monkeypatch.setenv("RENFORGE_BRIDGE_PROJECT_ROOT", str(project_root))
    if "RENFORGE_BRIDGE_PORT" not in env:
        monkeypatch.setenv("RENFORGE_BRIDGE_PORT", "0")

    store = types.SimpleNamespace()
    renpy = _fake_renpy(store)
    renpy.config.basedir = str(project_root)

    pygame = types.ModuleType("pygame_sdl2")
    pygame.event = types.SimpleNamespace(Event=object, post=lambda *_a, **_k: None)
    monkeypatch.setitem(sys.modules, "pygame_sdl2", pygame)
    sys.modules.pop("_renforge_runtime", None)

    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)

    globs = {"__name__": "bridge_rpy", "renpy": renpy}
    exec(compile(_load_bridge_body(), "bridge.rpy", "exec"), globs)
    runtime = sys.modules.get("_renforge_runtime")
    bridge = getattr(runtime, "bridge", None)
    return project_root, bridge, stderr


def test_bridge_publishes_ready_under_private_control_path(running_bridge):
    info_path = running_bridge.project_root / ".renforge" / "control" / "bridge.json"
    assert info_path.exists()
    assert not (running_bridge.project_root / ".renforge" / "bridge.json").exists()
    assert running_bridge.client.ping().get("pong") is True


def test_bridge_startup_emits_info_conflict_when_starting_record_missing(tmp_path, monkeypatch):
    project_root, bridge, stderr = _exec_bridge_with_env(
        tmp_path,
        monkeypatch,
        env={
            "RENFORGE_BRIDGE_TOKEN": "b" * 64,
            "RENFORGE_BRIDGE_SESSION_ID": "a" * 32,
            "RENFORGE_BRIDGE_PROJECT_ROOT": str(tmp_path.resolve(strict=True)),
        },
        seed_starting=False,
    )
    assert bridge is not None
    for _ in range(300):
        if bridge.stop.is_set():
            break
        time.sleep(0.01)
    assert bridge.stop.is_set()
    assert "RENFORGE_BRIDGE_STARTUP_ERROR=BRIDGE_INFO_CONFLICT\n" in stderr.getvalue()
    assert not (project_root / ".renforge" / "control" / "bridge.json").exists()
    assert not (project_root / ".renforge" / "bridge.json").exists()


def test_bridge_startup_emits_identity_mismatch_when_session_differs(tmp_path, monkeypatch):
    project_root = tmp_path.resolve(strict=True)
    _seed_starting_bridge(project_root, session_id="a" * 32, token="b" * 64)
    _, bridge, stderr = _exec_bridge_with_env(
        tmp_path,
        monkeypatch,
        env={
            "RENFORGE_BRIDGE_TOKEN": "b" * 64,
            "RENFORGE_BRIDGE_SESSION_ID": "c" * 32,
            "RENFORGE_BRIDGE_PROJECT_ROOT": str(project_root),
        },
        seed_starting=False,
    )
    assert bridge is not None
    for _ in range(300):
        if bridge.stop.is_set():
            break
        time.sleep(0.01)
    assert bridge.stop.is_set()
    assert stderr.getvalue() == "RENFORGE_BRIDGE_STARTUP_ERROR=BRIDGE_MANIFEST_IDENTITY_MISMATCH\n"
    # Reserved starting record remains untouched on identity failure.
    payload = (project_root / ".renforge" / "control" / "bridge.json").read_text(encoding="utf-8")
    assert '"state":"starting"' in payload or '"state": "starting"' in payload


def test_bridge_startup_emits_identity_mismatch_for_invalid_env_identity(tmp_path, monkeypatch):
    project_root = tmp_path.resolve(strict=True)
    cases = [
        {
            "RENFORGE_BRIDGE_TOKEN": "not-a-token",
            "RENFORGE_BRIDGE_SESSION_ID": "a" * 32,
            "RENFORGE_BRIDGE_PROJECT_ROOT": str(project_root),
        },
        {
            "RENFORGE_BRIDGE_TOKEN": "b" * 64,
            "RENFORGE_BRIDGE_SESSION_ID": "short",
            "RENFORGE_BRIDGE_PROJECT_ROOT": str(project_root),
        },
        {
            "RENFORGE_BRIDGE_TOKEN": "b" * 64,
            "RENFORGE_BRIDGE_SESSION_ID": "a" * 32,
            "RENFORGE_BRIDGE_PROJECT_ROOT": "",
        },
        {
            "RENFORGE_BRIDGE_TOKEN": "b" * 64,
            "RENFORGE_BRIDGE_SESSION_ID": "a" * 32,
            "RENFORGE_BRIDGE_PROJECT_ROOT": "relative/path",
        },
    ]
    for env in cases:
        _, bridge, stderr = _exec_bridge_with_env(
            tmp_path,
            monkeypatch,
            env=env,
            seed_starting=False,
        )
        assert bridge is None
        assert stderr.getvalue() == "RENFORGE_BRIDGE_STARTUP_ERROR=BRIDGE_MANIFEST_IDENTITY_MISMATCH\n"


def test_bridge_startup_rejects_non_canonical_project_root(tmp_path, monkeypatch):
    project_root = tmp_path.resolve(strict=True)
    _seed_starting_bridge(project_root, session_id="a" * 32, token="b" * 64)
    alias = tmp_path.parent / (tmp_path.name + "-alias")
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    alias.symlink_to(project_root, target_is_directory=True)
    _, bridge, stderr = _exec_bridge_with_env(
        tmp_path,
        monkeypatch,
        env={
            "RENFORGE_BRIDGE_TOKEN": "b" * 64,
            "RENFORGE_BRIDGE_SESSION_ID": "a" * 32,
            "RENFORGE_BRIDGE_PROJECT_ROOT": str(alias),
        },
        seed_starting=False,
    )
    assert bridge is None
    assert stderr.getvalue() == "RENFORGE_BRIDGE_STARTUP_ERROR=BRIDGE_MANIFEST_IDENTITY_MISMATCH\n"


def test_bridge_startup_emits_info_conflict_when_bridge_info_is_symlink(tmp_path, monkeypatch):
    project_root = tmp_path.resolve(strict=True)
    control = project_root / ".renforge" / "control"
    import os

    if os.name == "nt":
        from renforge.util.files import ensure_private_directory

        ensure_private_directory(control)
    else:
        control.mkdir(parents=True)
        os.chmod(control, 0o700)
    victim = project_root / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    _seed_starting_bridge(project_root, session_id="a" * 32, token="b" * 64)
    info_path = control / "bridge.json"
    info_path.unlink()
    info_path.symlink_to(victim)

    _, bridge, stderr = _exec_bridge_with_env(
        tmp_path,
        monkeypatch,
        env={
            "RENFORGE_BRIDGE_TOKEN": "b" * 64,
            "RENFORGE_BRIDGE_SESSION_ID": "a" * 32,
            "RENFORGE_BRIDGE_PROJECT_ROOT": str(project_root),
        },
        seed_starting=False,
    )
    assert bridge is not None
    for _ in range(300):
        if bridge.stop.is_set():
            break
        time.sleep(0.01)
    assert bridge.stop.is_set()
    assert stderr.getvalue() == "RENFORGE_BRIDGE_STARTUP_ERROR=BRIDGE_INFO_CONFLICT\n"
    assert victim.read_text(encoding="utf-8") == "{}"


def test_bridge_startup_emits_info_conflict_when_renforge_ancestor_is_symlink(tmp_path, monkeypatch):
    project_root = tmp_path.resolve(strict=True)
    real_control_parent = project_root / "real-renforge"
    real_control_parent.mkdir()
    import os

    os.chmod(real_control_parent, 0o700)
    (project_root / ".renforge").symlink_to(real_control_parent, target_is_directory=True)
    # Place a control dir under the linked tree so only the .renforge ancestor is the link.
    control = real_control_parent / "control"
    control.mkdir()
    os.chmod(control, 0o700)
    (control / "bridge.json").write_text("{}", encoding="utf-8")
    os.chmod(control / "bridge.json", 0o600)

    _, bridge, stderr = _exec_bridge_with_env(
        tmp_path,
        monkeypatch,
        env={
            "RENFORGE_BRIDGE_TOKEN": "b" * 64,
            "RENFORGE_BRIDGE_SESSION_ID": "a" * 32,
            "RENFORGE_BRIDGE_PROJECT_ROOT": str(project_root),
        },
        seed_starting=False,
    )
    assert bridge is not None
    for _ in range(300):
        if bridge.stop.is_set():
            break
        time.sleep(0.01)
    assert bridge.stop.is_set()
    assert stderr.getvalue() == "RENFORGE_BRIDGE_STARTUP_ERROR=BRIDGE_INFO_CONFLICT\n"

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
    assert wrong.request("ping").get("error") == "authentication_failed"


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


def test_send_input_drag_posts_real_mouse_event_sequence(running_bridge):
    points = [[100, 200], [150, 200], [150, 240]]
    reply = running_bridge.client.send_input(
        drag={"points": points, "button": 3, "coordinate_space": "logical"}
    )

    assert reply == {"ok": True, "mode": "drag", "points": points, "button": 3}
    events = running_bridge.renpy._pygame_events
    assert [event.type for event in events] == [4, 6, 6, 5]
    assert events[0].button == 3
    assert events[0].pos == (100, 200)
    assert events[1].pos == (150, 200)
    assert events[1].rel == (50, 0)
    assert events[1].buttons == (0, 0, 1)
    assert events[2].pos == (150, 240)
    assert events[2].rel == (0, 40)
    assert events[2].buttons == (0, 0, 1)
    assert events[3].button == 3
    assert events[3].pos == (150, 240)
    assert running_bridge.renpy.display.interface.mouse_focused is True


def test_send_input_drag_validates_exclusive_and_non_empty_points(running_bridge):
    mixed = running_bridge.client.send_input(
        text="nope",
        drag={"points": [[1, 2], [3, 4]]},
    )
    empty = running_bridge.client.send_input(drag={"points": []})

    assert mixed["ok"] is False
    assert "exactly one" in mixed["error"]
    assert empty["ok"] is False
    assert "non-empty" in empty["error"]
    assert running_bridge.renpy._pygame_events == []


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



def test_list_choices_reads_renpy_85_tts_raw_signature(running_bridge):
    """Ren'Py 8.5 requires ``_tts_all(raw: bool)``; no-arg calls must not blank text."""

    class RawOnlyWidget:
        def __init__(self, text):
            self._text = text

        def _tts_all(self, raw):
            if not isinstance(raw, bool):
                raise TypeError("raw must be bool")
            return self._text

    focus_list = running_bridge.renpy.display.focus.focus_list
    # Replace the first real choice widget with an 8.5-style TTS surface.
    focus_list[1].widget = RawOnlyWidget("Lantern path")
    texts = [c["text"] for c in running_bridge.client.list_choices()]
    assert "Lantern path" in texts


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


def test_dispatch_mouse_click_delivers_down_and_up_to_focused_widget(running_bridge):
    globs = running_bridge.globs
    renpy = running_bridge.renpy
    pygame = globs["pygame"]
    seen = []

    class Focused:
        def event(self, event, x, y, st):
            seen.append((event, x, y, st))

    focus_module = renpy.display.focus
    original_get_focused = getattr(focus_module, "get_focused", None)
    original_mouse_handler = getattr(focus_module, "mouse_handler", None)
    focus_module.get_focused = lambda: Focused()
    focus_module.mouse_handler = lambda event, x, y, default: None
    try:
        assert globs["_renforge_dispatch_mouse_click"](60, 50) is True
    finally:
        if original_get_focused is None:
            delattr(focus_module, "get_focused")
        else:
            focus_module.get_focused = original_get_focused
        if original_mouse_handler is None:
            delattr(focus_module, "mouse_handler")
        else:
            focus_module.mouse_handler = original_mouse_handler

    down, up = seen[0][0], seen[1][0]
    assert [down.type, up.type] == [pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP]
    assert down.button == up.button == 1
    assert down.pos == up.pos == (60, 50)
    assert down.test is up.test is True
    assert [(item[1], item[2], item[3]) for item in seen] == [(60, 50, 0), (60, 50, 0)]


def test_dispatch_mouse_click_uses_focused_displayable_local_coordinates(running_bridge):
    globs = running_bridge.globs
    renpy = running_bridge.renpy
    seen = []

    class Focused:
        def event(self, event, x, y, st):
            seen.append((x, y, st))

    focused = Focused()
    focus_record = types.SimpleNamespace(widget=focused, x=40, y=30)
    focus_module = renpy.display.focus
    original_get_focused = getattr(focus_module, "get_focused", None)
    original_mouse_handler = getattr(focus_module, "mouse_handler", None)
    focus_module.focus_list.append(focus_record)
    focus_module.get_focused = lambda: focused
    focus_module.mouse_handler = lambda event, x, y, default: None
    try:
        assert globs["_renforge_dispatch_mouse_click"](70, 80) is True
    finally:
        focus_module.focus_list.remove(focus_record)
        if original_get_focused is None:
            delattr(focus_module, "get_focused")
        else:
            focus_module.get_focused = original_get_focused
        if original_mouse_handler is None:
            delattr(focus_module, "mouse_handler")
        else:
            focus_module.mouse_handler = original_mouse_handler

    assert seen == [(30, 50, 0), (30, 50, 0)]


def test_editor_reactivation_does_not_restore_a_previous_game_screen(
    running_bridge, monkeypatch
):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name, value in (
        ("RENFORGE_EDITOR_HOST", "127.0.0.1"),
        ("RENFORGE_EDITOR_PORT", "12345"),
        ("RENFORGE_EDITOR_TOKEN", "editor-token"),
        ("RENFORGE_EDITOR_PROTOCOL", "1"),
    ):
        monkeypatch.setenv(name, value)

    active_screens = {"page_a", "_renforge_editor_overlay"}
    shown_screens = []
    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width, height=height
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.session = {}
    renpy.get_screen = lambda name: object() if name in active_screens else None

    def show_screen(name, **_kwargs):
        shown_screens.append(name)
        active_screens.add(name)

    renpy.show_screen = show_screen
    renpy.hide_screen = lambda name, **_kwargs: active_screens.discard(name)

    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        state = globs["_renforge_editor_state"]()
        state.active = True
        state.screen = "page_a"
        state.editor_session_screen = "page_a"

        globs["_renforge_editor_exit"]()
        active_screens.discard("page_a")
        active_screens.add("page_b")
        assert globs["_renforge_editor_activate"]()["ok"] is True
        globs["_renforge_editor_periodic"]()

        assert "page_b" in active_screens
        assert "page_a" not in active_screens
        assert "page_a" not in shown_screens
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_periodic_restores_active_session_after_reload(
    running_bridge, monkeypatch
):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    active_screens = set()
    shown_screens = []
    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width, height=height
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.session = {}
    renpy.get_screen = lambda name: object() if name in active_screens else None

    def show_screen(name, **_kwargs):
        shown_screens.append(name)
        active_screens.add(name)

    renpy.show_screen = show_screen
    renpy.hide_screen = lambda name, **_kwargs: active_screens.discard(name)

    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        state = globs["_renforge_editor_state"]()
        state.active = True
        state.screen = "page_a"
        state.editor_session_screen = "page_a"
        state.save_in_progress = True

        globs["_renforge_editor_periodic"]()

        assert shown_screens == ["page_a", "_renforge_editor_overlay"]
        assert active_screens == {"page_a", "_renforge_editor_overlay"}
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_exit_reverts_unsaved_previews_before_clearing_state(
    running_bridge, monkeypatch
):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    active_screens = {"page_a", "_renforge_editor_overlay"}
    shown_screens = []
    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width, height=height
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.get_screen = lambda name: object() if name in active_screens else None

    def show_screen(name, **kwargs):
        shown_screens.append((name, kwargs))
        active_screens.add(name)

    renpy.show_screen = show_screen
    renpy.hide_screen = lambda name, **_kwargs: active_screens.discard(name)

    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        state = globs["_renforge_editor_state"]()
        state.active = True
        state.screen = "page_a"
        state.editor_session_screen = "page_a"
        state.targets["target"] = {
            "screen": "page_a",
            "widget_id": "choice",
            "runtime_baseline": [100, 200],
            "source_position": [100, 200],
            "position": [140, 230],
            "dirty": True,
        }

        result = globs["_renforge_editor_exit"]()

        assert result == {"ok": True, "active": False}
        assert shown_screens == [("page_a", {"_layer": "screens"})]
        assert state.targets == {}
        assert state.history_entries == []
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_exit_during_save_preserves_transaction_state(
    running_bridge, monkeypatch
):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    active_screens = {"page_a", "_renforge_editor_overlay"}
    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width, height=height
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.get_screen = lambda name: object() if name in active_screens else None
    renpy.show_screen = lambda name, **_kwargs: active_screens.add(name)
    renpy.hide_screen = lambda name, **_kwargs: active_screens.discard(name)

    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        state = globs["_renforge_editor_state"]()
        state.active = True
        state.screen = "page_a"
        state.editor_session_screen = "page_a"
        state.save_in_progress = True
        state.pending_transaction_id = "transaction-1"
        state.pending_commit_request_id = 41
        state.pending_reload_requested = True
        state.pending_handshake_generation = 7

        globs["pygame"].K_ESCAPE = 27
        result = globs["_renforge_editor_h_key"]({"key": "escape"})

        assert result == {"ok": False, "error": "SAVE_IN_PROGRESS", "active": True}
        assert state.active is True
        assert state.screen == "page_a"
        assert state.editor_session_screen == "page_a"
        assert state.save_in_progress is True
        assert state.pending_transaction_id == "transaction-1"
        assert state.pending_commit_request_id == 41
        assert state.pending_reload_requested is True
        assert state.pending_handshake_generation == 7
        assert "_renforge_editor_overlay" in active_screens
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_mouse_up_applies_final_drag_position_without_motion(
    running_bridge, monkeypatch
):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    pygame = globs["pygame"]
    screen_name = "drag_target_screen"
    baseline = [320, 220]

    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    ScreenDisplayable = type("ScreenDisplayable", (), {})
    Text = type("Text", (), {})
    screen = ScreenDisplayable()
    widget = Text()
    widget._location = ("game/screens.rpy", 12)
    screen.children = [widget]
    screen.widgets = {"drag_target": widget}
    focus = _FakeFocus("Drag target", baseline[0], baseline[1], 120, 60, widget=widget)
    focus.screen_name = screen_name
    renpy.display.focus.focus_list[:] = [focus]
    renpy.display.screen = types.SimpleNamespace(
        get_screen=lambda name: screen if name == screen_name else None
    )
    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width, height=height
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.show_screen = lambda *args, **kwargs: None

    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        state = globs["_renforge_editor_state"]()
        state.active = True
        center = [baseline[0] + focus.w // 2, baseline[1] + focus.h // 2]
        selected = globs["_renforge_editor_h_select"]({"x": center[0], "y": center[1]})
        runtime_key = selected["observation"]["runtime_key"]
        target_key = globs["_renforge_editor_target_key"](runtime_key)
        state.targets[target_key] = {
            "analysis_id": "analysis-drag-target",
            "source_key": {"relative_path": "screens.rpy", "line": 12},
            "capabilities": {"move": True, "resize": False},
            "screen": screen_name,
            "widget_id": "drag_target",
            "runtime_baseline": list(baseline),
            "source_position": list(baseline),
            "position": list(baseline),
            "dirty": False,
        }
        analyzed = globs["_renforge_editor_h_select"](
            {"x": center[0], "y": center[1]}
        )
        assert analyzed["ok"] is True
        assert state.preview_position == baseline
        assert state.current_capabilities == {"move": True, "resize": False}

        down = pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            {
                "button": 1,
                # SDL reports physical Retina pixels while Displayable.event
                # receives Ren'Py's already-normalized logical coordinates.
                "pos": (center[0] * 2, center[1] * 2),
                "mod": pygame.KMOD_NONE,
            },
        )
        up = pygame.event.Event(
            pygame.MOUSEBUTTONUP,
            {
                "button": 1,
                "pos": ((center[0] + 40) * 2, (center[1] + 30) * 2),
                "mod": pygame.KMOD_NONE,
            },
        )
        with pytest.raises(renpy.IgnoreEvent):
            globs["_renforge_editor_handle_event"](down, center[0], center[1], 0.0)
        with pytest.raises(renpy.IgnoreEvent):
            globs["_renforge_editor_handle_event"](
                up, center[0] + 40, center[1] + 30, 0.0
            )

        assert state.preview_position[0] == pytest.approx(baseline[0] + 40, abs=1)
        assert state.preview_position[1] == pytest.approx(baseline[1] + 30, abs=1)
        assert state.targets[state.selected_target_key]["dirty"] is True
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_drag_feedback_separates_distance_from_bounded_snap_guide(
    running_bridge, monkeypatch
):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width, height=height
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.show_screen = lambda *args, **kwargs: None
    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        state = globs["_renforge_editor_state"]()
        state.active = True
        state.drag_active = True
        state.drag_start_position = [100, 200]
        state.drag_offset = [10, 10]
        state.pointer = [111, 211]
        state.selected_original_position = [100, 200]
        state.preview_position = [100, 200]
        state.selected_rect = [100, 200, 40, 20]
        state.snap_candidates_x = [
            {"anchor": 120, "rect": [120, 260, 40, 40]}
        ]
        state.snap_candidates_y = []

        measurement = globs["_renforge_editor_measure_snapshot"]()
        snapped_x, snapped_y, detail = globs["_renforge_editor_apply_snap"](
            81, 200, False
        )
        guide = globs["_renforge_editor_guide_snapshot"]()
        held_x, held_y, held_detail = globs["_renforge_editor_apply_snap"](
            84, 210, False
        )
        held_guide = globs["_renforge_editor_guide_snapshot"]()
        globs["_renforge_editor_apply_snap"](84, 210, True)
        state.snap_candidates_x = []
        state.snap_candidates_y = [
            {"anchor": 230, "rect": [300, 230, 50, 40]}
        ]
        y_snapped_x, y_snapped_y, y_detail = globs[
            "_renforge_editor_apply_snap"
        ](100, 211, False)
        horizontal_guide = globs["_renforge_editor_guide_snapshot"]()

        assert measurement == {"dx": 1, "dy": 1}
        assert (snapped_x, snapped_y) == (80, 200)
        assert detail == {"snapped_x": True, "snapped_y": False}
        assert guide == {
            "line_x": [120, 200, 100],
            "line_y": None,
        }
        assert (held_x, held_y) == (80, 210)
        assert held_detail == {"snapped_x": True, "snapped_y": False}
        assert held_guide == {
            "line_x": [120, 210, 90],
            "line_y": None,
        }
        assert (y_snapped_x, y_snapped_y) == (100, 210)
        assert y_detail == {"snapped_x": False, "snapped_y": True}
        assert horizontal_guide == {
            "line_x": None,
            "line_y": [100, 230, 250],
        }
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_drag_deduplicates_target_rebuild_but_refreshes_measurement(
    running_bridge, monkeypatch
):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width, height=height
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    show_calls = []
    renpy.show_screen = lambda *args, **kwargs: show_calls.append((args, kwargs))
    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        state = globs["_renforge_editor_state"]()
        state.active = True
        state.drag_active = True
        state.selected_screen = "drag_target_screen"
        state.selected_widget_id = "drag_target"
        state.selected_target_key = "target"
        state.selected_lock_reason = None
        state.selected_original_position = [100, 200]
        state.selected_source_position = [100, 200]
        state.selected_rect = [100, 200, 40, 20]
        state.preview_position = [100, 200]
        state.targets["target"] = {
            "screen": "drag_target_screen",
            "widget_id": "drag_target",
            "runtime_baseline": [100, 200],
            "source_position": [100, 200],
            "position": [100, 200],
            "dirty": False,
        }

        first = globs["_renforge_editor_apply_preview"](
            101, 200, allow_snap=False
        )
        restart_count = len(
            [item for item in renpy._invoked if item == ("restart_interaction",)]
        )
        second = globs["_renforge_editor_apply_preview"](
            101, 200, allow_snap=False
        )

        assert first["ok"] is True
        assert second["ok"] is True
        assert len(show_calls) == 1
        assert len(
            [item for item in renpy._invoked if item == ("restart_interaction",)]
        ) == restart_count + 1
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_motion_applies_preview_immediately(running_bridge, monkeypatch):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    pygame = globs["pygame"]
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width, height=height
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    show_calls = []
    renpy.show_screen = lambda *args, **kwargs: show_calls.append((args, kwargs))
    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        state = globs["_renforge_editor_state"]()
        state.active = True
        state.drag_active = True
        state.drag_offset = [10, 10]
        state.drag_start_position = [100, 200]
        state.selected_screen = "drag_target_screen"
        state.selected_widget_id = "drag_target"
        state.selected_target_key = "target"
        state.selected_lock_reason = None
        state.selected_original_position = [100, 200]
        state.selected_source_position = [100, 200]
        state.selected_rect = [100, 200, 40, 20]
        state.preview_position = [100, 200]
        state.snap_candidates_x = []
        state.snap_candidates_y = []
        state.targets["target"] = {
            "screen": "drag_target_screen",
            "widget_id": "drag_target",
            "runtime_baseline": [100, 200],
            "source_position": [100, 200],
            "position": [100, 200],
            "dirty": False,
        }

        motion = pygame.event.Event(
            pygame.MOUSEMOTION,
            {"pos": (111, 210), "rel": (1, 0), "buttons": (1, 0, 0)},
        )
        with pytest.raises(renpy.IgnoreEvent):
            globs["_renforge_editor_handle_event"](motion, 111, 210, 0.0)

        assert state.preview_position == [101, 200]
        assert len(show_calls) == 1

        up = pygame.event.Event(
            pygame.MOUSEBUTTONUP,
            {
                "button": 1,
                "pos": (113, 213),
                "x": 113,
                "y": 213,
                "touch": False,
                "test": True,
                "mod": 0,
            },
        )
        with pytest.raises(renpy.IgnoreEvent):
            globs["_renforge_editor_handle_event"](up, 113, 213, 0.0)
        assert state.drag_active is False
        assert state.preview_position == [103, 203]
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_task0_drag_uses_displayable_event_path(running_bridge, monkeypatch):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    pygame = globs["pygame"]
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width, height=height
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.show_screen = lambda *args, **kwargs: None
    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        state = globs["_renforge_editor_state"]()
        state.active = True
        state.preview_position = [100, 200]
        event_types = []

        def handle_event(event, x, y, st):
            event_types.append(event.type)
            if event.type == pygame.MOUSEBUTTONDOWN:
                state.drag_active = True
            elif event.type == pygame.MOUSEMOTION:
                state.preview_position = [int(x), int(y)]
            elif event.type == pygame.MOUSEBUTTONUP:
                state.drag_active = False
            raise renpy.IgnoreEvent()

        globs["_renforge_editor_handle_event"] = handle_event
        globs["_renforge_editor_apply_drag_from_pointer"] = (
            lambda x, y, shift: {
                "ok": True,
                "preview_position": [int(x), int(y)],
            }
        )

        reply = globs["_renforge_editor_h_drag"](
            {"points": [[100, 200], [120, 200], [130, 210]]}
        )

        assert reply["ok"] is True
        assert event_types == [
            pygame.MOUSEBUTTONDOWN,
            pygame.MOUSEMOTION,
            pygame.MOUSEMOTION,
            pygame.MOUSEBUTTONUP,
        ]
        assert reply["preview_before_mouse_up"] == [130, 210]
        assert reply["drag_active_before_mouse_up"] is True
        assert state.drag_active is False
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_coordinator_survives_missing_global_queue(running_bridge, monkeypatch):
    renpy = running_bridge.renpy
    globs = dict(running_bridge.globs)
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width, height=height
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.show_screen = lambda *args, **kwargs: None
    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        coordinator = globs["_renforge_editor_ensure_coordinator"]()
        saved_queue = globs.get("queue")
        globs.pop("queue", None)
        try:
            # Force the Empty path that crashed after reload before any submit.
            time.sleep(0.15)
            assert coordinator.thread.is_alive()
        finally:
            if saved_queue is not None:
                globs["queue"] = saved_queue
        request_id = coordinator.submit({"probe": True})
        deadline = time.time() + 1.0
        collected = []
        while time.time() < deadline:
            collected = coordinator.collect_nowait()
            if collected:
                break
            time.sleep(0.02)
        assert coordinator.thread.is_alive()
        assert any(item.get("request_id") == request_id for item in collected)
    finally:
        globs["_renforge_editor_stop_coordinator"]()



def test_dispatch_mouse_click_delivers_up_after_down_is_ignored(running_bridge):
    globs = running_bridge.globs
    renpy = running_bridge.renpy
    pygame = globs["pygame"]
    seen = []

    class FakeIgnoreEvent(Exception):
        pass

    class Focused:
        def event(self, event, x, y, st):
            seen.append(event.type)
            if event.type == pygame.MOUSEBUTTONDOWN:
                raise FakeIgnoreEvent()

    focus_module = renpy.display.focus
    original_core_module = getattr(renpy.display, "core", None)
    core_module = original_core_module or types.SimpleNamespace()
    renpy.display.core = core_module
    original_get_focused = getattr(focus_module, "get_focused", None)
    original_mouse_handler = getattr(focus_module, "mouse_handler", None)
    original_ignore_event = getattr(core_module, "IgnoreEvent", None)
    focus_module.get_focused = lambda: Focused()
    focus_module.mouse_handler = lambda event, x, y, default: None
    core_module.IgnoreEvent = FakeIgnoreEvent
    try:
        assert globs["_renforge_dispatch_mouse_click"](22, 33) is True
    finally:
        if original_get_focused is None:
            delattr(focus_module, "get_focused")
        else:
            focus_module.get_focused = original_get_focused
        if original_mouse_handler is None:
            delattr(focus_module, "mouse_handler")
        else:
            focus_module.mouse_handler = original_mouse_handler
        if original_ignore_event is None:
            delattr(core_module, "IgnoreEvent")
        else:
            core_module.IgnoreEvent = original_ignore_event
        if original_core_module is None:
            delattr(renpy.display, "core")

    assert seen == [pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP]


def test_click_pointer_delivers_up_before_propagating_end_interaction(running_bridge):
    globs = running_bridge.globs
    renpy = running_bridge.renpy
    pygame = globs["pygame"]
    seen = []

    class FakeEndInteraction(Exception):
        def __init__(self, value):
            super().__init__(value)
            self.value = value

    class Focused:
        def event(self, event, x, y, st):
            seen.append(event.type)
            if event.type == pygame.MOUSEBUTTONDOWN:
                return "branch-result"
            return None

    def _end_interaction(value):
        raise FakeEndInteraction(value)

    focus_module = renpy.display.focus
    original_core_module = getattr(renpy.display, "core", None)
    core_module = original_core_module or types.SimpleNamespace()
    renpy.display.core = core_module
    original_get_focused = getattr(focus_module, "get_focused", None)
    original_mouse_handler = getattr(focus_module, "mouse_handler", None)
    original_end_interaction_class = getattr(core_module, "EndInteraction", None)
    original_end_interaction = getattr(renpy, "end_interaction", None)
    focus_module.get_focused = lambda: Focused()
    focus_module.mouse_handler = lambda event, x, y, default: None
    core_module.EndInteraction = FakeEndInteraction
    renpy.end_interaction = _end_interaction
    renpy.test.testmouse.mouse_pos = (91, 92)
    renpy.test.testmouse.mouse_buttons = [True, False, False]

    try:
        with pytest.raises(FakeEndInteraction) as raised:
            globs["_renforge_click_pointer"](44, 55)
    finally:
        if original_get_focused is None:
            delattr(focus_module, "get_focused")
        else:
            focus_module.get_focused = original_get_focused
        if original_mouse_handler is None:
            delattr(focus_module, "mouse_handler")
        else:
            focus_module.mouse_handler = original_mouse_handler
        if original_end_interaction_class is None:
            delattr(core_module, "EndInteraction")
        else:
            core_module.EndInteraction = original_end_interaction_class
        if original_end_interaction is None:
            delattr(renpy, "end_interaction")
        else:
            renpy.end_interaction = original_end_interaction
        if original_core_module is None:
            delattr(renpy.display, "core")

    assert raised.value.value == "branch-result"
    assert seen == [pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP]
    assert renpy.test.testmouse.mouse_pos is None
    assert renpy.test.testmouse.mouse_buttons == [False, False, False]


def test_click_at_preserves_reply_metadata_when_interaction_ends(running_bridge):
    globs = running_bridge.globs
    renpy = running_bridge.renpy
    pygame = globs["pygame"]

    class FakeEndInteraction(Exception):
        pass

    class Focused:
        def event(self, event, x, y, st):
            if event.type == pygame.MOUSEBUTTONUP:
                return "next-screen"
            return None

    def _end_interaction(value):
        raise FakeEndInteraction(value)

    focus_module = renpy.display.focus
    original_core_module = getattr(renpy.display, "core", None)
    core_module = original_core_module or types.SimpleNamespace()
    renpy.display.core = core_module
    original_get_focused = getattr(focus_module, "get_focused", None)
    original_mouse_handler = getattr(focus_module, "mouse_handler", None)
    original_end_interaction_class = getattr(core_module, "EndInteraction", None)
    original_end_interaction = getattr(renpy, "end_interaction", None)
    focus_module.get_focused = lambda: Focused()
    focus_module.mouse_handler = lambda event, x, y, default: None
    core_module.EndInteraction = FakeEndInteraction
    renpy.end_interaction = _end_interaction

    try:
        with pytest.raises(FakeEndInteraction) as raised:
            globs["_renforge_h_click_at"]({"x": 44, "y": 55})
    finally:
        if original_get_focused is None:
            delattr(focus_module, "get_focused")
        else:
            focus_module.get_focused = original_get_focused
        if original_mouse_handler is None:
            delattr(focus_module, "mouse_handler")
        else:
            focus_module.mouse_handler = original_mouse_handler
        if original_end_interaction_class is None:
            delattr(core_module, "EndInteraction")
        else:
            core_module.EndInteraction = original_end_interaction_class
        if original_end_interaction is None:
            delattr(renpy, "end_interaction")
        else:
            renpy.end_interaction = original_end_interaction
        if original_core_module is None:
            delattr(renpy.display, "core")

    assert getattr(raised.value, "renforge_result", None) == {
        "ok": True,
        "x": 44,
        "y": 55,
        "coordinate_space": "logical",
    }


def test_click_element_preserves_action_metadata_when_interaction_ends(running_bridge):
    globs = running_bridge.globs
    renpy = running_bridge.renpy
    pygame = globs["pygame"]
    target = running_bridge.client.list_ui_elements()[1]
    focus, element, error = globs["_renforge_resolve_ui_element"](
        {"id": target["id"]}, "click_element"
    )
    assert error is None

    class FakeEndInteraction(Exception):
        pass

    def _event(event, x, y, st):
        if event.type == pygame.MOUSEBUTTONUP:
            return "next-screen"
        return None

    def _end_interaction(value):
        raise FakeEndInteraction(value)

    focus_module = renpy.display.focus
    core_module = getattr(renpy.display, "core", None) or types.SimpleNamespace()
    original_core_module = getattr(renpy.display, "core", None)
    original_get_focused = getattr(focus_module, "get_focused", None)
    original_mouse_handler = getattr(focus_module, "mouse_handler", None)
    original_event = getattr(focus.widget, "event", None)
    original_end_interaction_class = getattr(core_module, "EndInteraction", None)
    original_end_interaction = getattr(renpy, "end_interaction", None)
    renpy.display.core = core_module
    focus_module.get_focused = lambda: focus.widget
    focus_module.mouse_handler = lambda event, x, y, default: None
    focus.widget.event = _event
    core_module.EndInteraction = FakeEndInteraction
    renpy.end_interaction = _end_interaction

    try:
        with pytest.raises(FakeEndInteraction) as raised:
            globs["_renforge_h_click_element"]({"id": target["id"]})
    finally:
        if original_get_focused is None:
            delattr(focus_module, "get_focused")
        else:
            focus_module.get_focused = original_get_focused
        if original_mouse_handler is None:
            delattr(focus_module, "mouse_handler")
        else:
            focus_module.mouse_handler = original_mouse_handler
        if original_event is None:
            delattr(focus.widget, "event")
        else:
            focus.widget.event = original_event
        if original_end_interaction_class is None:
            delattr(core_module, "EndInteraction")
        else:
            core_module.EndInteraction = original_end_interaction_class
        if original_end_interaction is None:
            delattr(renpy, "end_interaction")
        else:
            renpy.end_interaction = original_end_interaction
        if original_core_module is None:
            delattr(renpy.display, "core")

    result = getattr(raised.value, "renforge_result", None)
    assert result is not None
    assert result["ok"] is True
    assert result["action"] == element["action"]
    assert result["element"] == element
    assert result["received_by"] is not None
    assert result["x"] == target["center"]["x"]
    assert result["y"] == target["center"]["y"]


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


def test_list_ui_elements_uses_named_textbutton_widget_id(running_bridge):
    renpy = running_bridge.renpy
    named_text = _FakeWidget("Tools On")
    button = _FakeWidget("Tools On")
    button.child = named_text
    focus = _FakeFocus("Tools On", 10, 10, 100, 30, widget=button)
    focus.screen_name = "_renforge_editor_overlay"
    original_focus_list = list(renpy.display.focus.focus_list)
    original_get_screen = getattr(renpy, "get_screen", None)
    renpy.display.focus.focus_list[:] = [focus]
    renpy.get_screen = lambda name: (
        types.SimpleNamespace(widgets={"rf_tools": named_text})
        if name == "_renforge_editor_overlay"
        else None
    )

    try:
        elements = running_bridge.client.list_ui_elements(
            screen="_renforge_editor_overlay"
        )
        assert elements[0]["id"] == "rf_tools"
        _focus, element, error = running_bridge.globs[
            "_renforge_resolve_ui_element"
        ](
            {"id": "rf_tools", "screen": "_renforge_editor_overlay"},
            "click_element",
        )
        assert error is None
        assert element["id"] == "rf_tools"
    finally:
        renpy.display.focus.focus_list[:] = original_focus_list
        if original_get_screen is None:
            delattr(renpy, "get_screen")
        else:
            renpy.get_screen = original_get_screen


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
    assert hovered["ok"] is True, hovered
    assert hovered["hovered"] is True
    assert hovered["x"] == 60 and hovered["y"] == 50
    assert running_bridge.renpy._moves == [(60, 50)]
    assert running_bridge.renpy._clicks == []


def test_hover_element_prefers_set_mouse_pos_without_legacy_move_mouse(running_bridge):
    renpy = running_bridge.renpy
    focus_list = renpy.display.focus.focus_list
    calls = []
    previous_set_mouse_pos = getattr(renpy, "set_mouse_pos", None)
    previous_move_mouse = renpy.test.testmouse.move_mouse

    def _set_mouse_pos(x, y, *, duration):
        calls.append((x, y, duration))
        renpy._moves.append((x, y))

    def _legacy_move_mouse(x, y):
        renpy._moves.append((x, y))

    def _widget_at(x, y):
        for focus in reversed(focus_list):
            widget = getattr(focus, "widget", None)
            if widget is None:
                continue
            fx = getattr(focus, "x", None)
            fy = getattr(focus, "y", None)
            fw = getattr(focus, "w", None)
            fh = getattr(focus, "h", None)
            if fx is None or fy is None or fw is None or fh is None:
                continue
            if fx <= x < fx + fw and fy <= y < fy + fh:
                return widget
        return None

    def _focus_mouse_handler(event, x, y, _):
        renpy._focused_widget = _widget_at(int(x), int(y))

    try:
        renpy.display.focus.mouse_handler = _focus_mouse_handler
        renpy.set_mouse_pos = _set_mouse_pos
        renpy.test.testmouse.move_mouse = _legacy_move_mouse
        renpy.test.testmouse.mouse_pos = (99, 99)
        renpy.test.testmouse.mouse_buttons = [True, False, False]

        element = running_bridge.client.list_ui_elements()[1]
        hovered = running_bridge.client.hover_element(id=element["id"])
        assert hovered["ok"] is True
        assert hovered["method"] == "renpy"
        assert calls == [(60, 50, 0)]
        assert renpy._moves == [(60, 50)]
        assert renpy._focused_widget is focus_list[2].widget
        assert renpy.test.testmouse.mouse_pos is None
        assert renpy.test.testmouse.mouse_buttons == [False, False, False]

        running_bridge.renpy.display.focus.mouse_handler(types.SimpleNamespace(), 60, 20, False)
        assert renpy._focused_widget is focus_list[1].widget
    finally:
        renpy.set_mouse_pos = previous_set_mouse_pos
        renpy.test.testmouse.move_mouse = previous_move_mouse


def test_move_mouse_cleanup_when_exception_injected(running_bridge):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    focus = renpy.display.focus.focus_list[1]

    renpy.test.testmouse.mouse_pos = (42, 42)
    renpy.test.testmouse.mouse_buttons = [True, False, False]
    original_mouse_handler = getattr(renpy.display.focus, "mouse_handler", None)

    def _boom_mouse_handler(_event, _x, _y, _):
        raise RuntimeError("mouse-handler-bad")

    renpy.display.focus.mouse_handler = _boom_mouse_handler

    try:
        with pytest.raises(RuntimeError, match="mouse-handler-bad"):
            globs["_renforge_move_mouse"](focus)
    finally:
        if original_mouse_handler is not None:
            renpy.display.focus.mouse_handler = original_mouse_handler
        else:
            renpy.display.focus.__dict__.pop("mouse_handler", None)

    assert renpy.test.testmouse.mouse_pos is None
    assert renpy.test.testmouse.mouse_buttons == [False, False, False]


def test_hover_clears_testmouse_override_and_allows_physical_focus_changes(running_bridge):
    focus_list = running_bridge.renpy.display.focus.focus_list

    def _widget_at(x, y):
        for focus in reversed(focus_list):
            widget = getattr(focus, "widget", None)
            if widget is None:
                continue
            fx = getattr(focus, "x", None)
            fy = getattr(focus, "y", None)
            fw = getattr(focus, "w", None)
            fh = getattr(focus, "h", None)
            if fx is None or fy is None or fw is None or fh is None:
                continue
            if fx <= x < fx + fw and fy <= y < fy + fh:
                return widget
        return None

    def _focus_mouse_handler(event, x, y, _):
        mouse_pos = getattr(running_bridge.renpy.test.testmouse, "mouse_pos", None)
        if isinstance(mouse_pos, (tuple, list)) and len(mouse_pos) == 2:
            x, y = int(mouse_pos[0]), int(mouse_pos[1])
        running_bridge.renpy._focused_widget = _widget_at(x, y)

    running_bridge.renpy.display.focus.mouse_handler = _focus_mouse_handler
    running_bridge.renpy.test.testmouse.mouse_buttons = [True, False, False]
    element = running_bridge.client.list_ui_elements()[1]
    hovered = running_bridge.client.hover_element(id=element["id"])
    assert hovered["ok"] is True
    assert running_bridge.renpy.test.testmouse.mouse_pos is None
    assert running_bridge.renpy.test.testmouse.mouse_buttons == [False, False, False]
    assert running_bridge.renpy._focused_widget is focus_list[2].widget

    event = types.SimpleNamespace()
    running_bridge.renpy.display.focus.mouse_handler(event, 60, 20, False)
    assert running_bridge.renpy.test.testmouse.mouse_buttons == [False, False, False]
    assert running_bridge.renpy._focused_widget is focus_list[1].widget


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

    assert running_bridge.renpy._clicks[-1] == (1, 60, 50)

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


def test_click_element_click_at_and_select_choice_use_shared_pointer_path(running_bridge):
    element = running_bridge.client.list_ui_elements()[1]
    calls = {"click_mouse": 0}

    original_click_mouse = running_bridge.renpy.test.testmouse.click_mouse

    def _counted_click_mouse(button, x, y):
        calls["click_mouse"] += 1
        return original_click_mouse(button, x, y)

    running_bridge.renpy.test.testmouse.click_mouse = _counted_click_mouse

    try:
        clicked = running_bridge.client.click_element(id=element["id"])
        clicked_at = running_bridge.client.click_at(80, 90)
        selected = running_bridge.client.select_choice(text="Beta")
        assert clicked["ok"] is True
        assert clicked["x"] == 60
        assert clicked["y"] == 50
        assert clicked_at["ok"] is True
        assert clicked_at["x"] == 80
        assert clicked_at["y"] == 90
        assert clicked_at["coordinate_space"] == "logical"
        assert selected["ok"] is True
        assert selected["x"] == 60
        assert selected["y"] == 50
        assert calls["click_mouse"] == 3
        assert running_bridge.renpy._clicks[-3:] == [
            (1, 60, 50),
            (1, 80, 90),
            (1, 60, 50),
        ]
    finally:
        running_bridge.renpy.test.testmouse.click_mouse = original_click_mouse


def test_fallback_pointer_path_clears_sticky_mouse_state(running_bridge):
    renpy = running_bridge.renpy
    globs = running_bridge.globs

    previous_set_mouse_pos = getattr(renpy, "set_mouse_pos", None)
    previous_mouse = globs["pygame"]
    globs["pygame"] = None
    renpy.set_mouse_pos = None

    def _legacy_move_mouse(x, y):
        renpy._moves.append((x, y))
        renpy.test.testmouse.mouse_pos = (x, y)

    def _legacy_click_mouse(button, x, y):
        renpy._clicks.append((button, x, y))
        renpy.test.testmouse.mouse_pos = (x, y)
        renpy.test.testmouse.mouse_buttons = [True, False, False]

    renpy.test.testmouse.move_mouse = _legacy_move_mouse
    renpy.test.testmouse.click_mouse = _legacy_click_mouse

    try:
        element = running_bridge.client.list_ui_elements()[1]
        hovered = running_bridge.client.hover_element(id=element["id"])
        assert hovered["ok"] is True
        assert hovered["method"] == "renpy-test"
        assert renpy.test.testmouse.mouse_pos is None
        assert renpy.test.testmouse.mouse_buttons == [False, False, False]

        clicked = running_bridge.client.click_element(id=element["id"])
        assert clicked["ok"] is True
        assert renpy.test.testmouse.mouse_pos is None
        assert renpy.test.testmouse.mouse_buttons == [False, False, False]
        selected = running_bridge.client.select_choice(text="Beta")
        assert selected["ok"] is True
        assert renpy.test.testmouse.mouse_pos is None
        assert renpy.test.testmouse.mouse_buttons == [False, False, False]
    finally:
        renpy.set_mouse_pos = previous_set_mouse_pos
        globs["pygame"] = previous_mouse


def test_drain_bridge_cleans_testmouse_state_when_stop_is_set(running_bridge):
    import sys

    renpy = running_bridge.renpy
    renpy.test.testmouse.mouse_pos = (12, 34)
    renpy.test.testmouse.mouse_buttons = [True, False, False]

    bridge = sys.modules["_renforge_runtime"].bridge
    bridge.stop.set()

    running_bridge.globs["renforge_drain_bridge"]()

    assert renpy.test.testmouse.mouse_pos is None
    assert renpy.test.testmouse.mouse_buttons == [False, False, False]


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
    thread_before = bridge_before.thread
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

    # The live bridge and socket are reused: same thread object, no second bind.
    # Other fixtures may leave daemon listeners that exit mid-test (especially
    # on Windows), so only assert this bridge's thread and that we did not
    # spawn an extra listener.
    assert sys.modules["_renforge_runtime"].bridge is bridge_before
    assert bridge_before.thread is thread_before
    assert thread_before is not None and thread_before.is_alive()
    listeners_after = sum(
        1 for t in threading.enumerate() if t.name == "renforge.bridge.listener"
    )
    assert listeners_after <= listeners_before
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
    assert bridge_before.thread is thread_before

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


def test_listener_survives_store_wipe_that_undefines_socket(running_bridge):
    # renpy.reload_script() keeps the listener thread alive but wipes the
    # store (the __globals__ of init-python functions). Before the local
    # imports were added to _renforge_listener, the next accept() timeout
    # raised NameError on ``except socket.timeout:`` and silently killed the
    # thread — the bridge died while the game kept running. Simulate the wipe
    # by deleting the stdlib names from the listener's globals and confirm
    # the loop keeps accepting connections.
    globs = running_bridge.globs
    bridge = __import__("sys").modules["_renforge_runtime"].bridge
    thread = bridge.thread
    assert thread is not None and thread.is_alive()

    saved = {name: globs.pop(name) for name in ("socket", "json", "os") if name in globs}
    try:
        # Force the listener to hit its accept() timeout path by sleeping
        # past the 0.5s settimeout window; if the listener depended on the
        # store-level ``socket`` it would have crashed by now.
        time.sleep(0.7)
        assert thread.is_alive(), "listener thread died after store wipe"
        assert running_bridge.client.ping().get("pong") is True
    finally:
        globs.update(saved)


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


def test_editor_allowed_ancestry_accepts_bar_and_rejects_unknown_and_side(
    running_bridge, monkeypatch
):
    """Bar is the measured runtime class for bar/vbar; Side remains unproven."""
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(width=width, height=height)
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.session = {}
    renpy.get_screen = lambda name: None
    renpy.show_screen = lambda *a, **k: None
    renpy.hide_screen = lambda *a, **k: None

    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        allowed = globs["_ALLOWED_ANCESTRY_TYPES"]
        assert "Bar" in allowed
        assert "Side" not in allowed
        assert "VBox" in allowed  # layout containers stay classified, not open-ended

        validate = globs["_renforge_editor_validate_runtime_key"]
        bar_key = {
            "ancestry": [
                {"type": "ScreenDisplayable", "crop_state": "none"},
                {"type": "Fixed", "crop_state": "none"},
                {"type": "Bar", "crop_state": "none"},
            ]
        }
        assert validate(bar_key) is None

        unknown_key = {
            "ancestry": [
                {"type": "ScreenDisplayable", "crop_state": "none"},
                {"type": "UnknownWidget", "crop_state": "none"},
                {"type": "Bar", "crop_state": "none"},
            ]
        }
        assert validate(unknown_key) == "UNKNOWN_ANCESTRY_TYPE"

        side_key = {
            "ancestry": [
                {"type": "ScreenDisplayable", "crop_state": "none"},
                {"type": "Side", "crop_state": "none"},
                {"type": "Bar", "crop_state": "none"},
            ]
        }
        assert validate(side_key) == "UNKNOWN_ANCESTRY_TYPE"
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_reselect_resize_and_reset_use_the_selected_target_size(running_bridge):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(width=width, height=height)
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.show_screen = lambda *args, **kwargs: None
    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    state = globs["_renforge_editor_state"]()
    state.active = True
    state.preview_size = [300, 40]  # stale size from a previously selected target
    runtime_key = {"screen": "size_screen", "widget_id": "first_bar"}
    candidate = {"rect": [10, 20, 100, 20], "runtime_key": runtime_key}
    target_key = "first-bar-target"
    globs["_renforge_editor_target_key"] = lambda _runtime_key: target_key
    state.targets[target_key] = {
        "analysis_id": "analysis-first-bar",
        "source_key": {
            "relative_path": "screens.rpy",
            "line": 12,
            "size_mode": globs["_BAR_SIZE_MODE_XSIZE_YSIZE"],
        },
        "capabilities": {"move": True, "resize": True},
        "screen": "size_screen",
        "widget_id": "first_bar",
        "runtime_baseline": [10, 20],
        "source_position": [10, 20],
        "position": [10, 20],
        "runtime_size": [100, 20],
        "source_size": [100, 20],
        "size": [100, 20],
        "dirty": False,
    }
    globs["_renforge_editor_focus_candidates"] = lambda: [candidate]
    globs["_renforge_editor_validate_runtime_key"] = lambda _key: None
    globs["_renforge_editor_observation_for_candidate"] = lambda _candidate: (
        {"runtime_key": runtime_key},
        None,
    )
    globs["_renforge_editor_show_target_overrides"] = lambda _screen: None
    globs["_renforge_editor_set_label"] = lambda _x, _y: None
    running_bridge.renpy.restart_interaction = lambda: None

    selected = globs["_renforge_editor_select"](15, 25)
    assert selected["ok"] is True
    assert state.preview_size == [100, 20]
    assert state.selected_original_size == [100, 20]
    assert state.selected_rect == [10, 20, 100, 20]

    resized = globs["_renforge_editor_resize"](10, 2)
    assert resized == {"ok": True, "w": 110, "h": 22}
    assert state.targets[target_key]["size"] == [110, 22]

    reset = globs["_renforge_editor_reset_selected"]()
    assert reset["ok"] is True
    assert state.targets[target_key]["size"] == [100, 20]
    assert state.targets[target_key]["dirty"] is False
    assert state.history_entries[-1]["kind"] == "reset"

    undone = globs["_renforge_editor_undo"]()
    assert undone["ok"] is True
    assert state.targets[target_key]["size"] == [110, 22]


def test_editor_select_widget_keeps_the_requested_identity_when_widgets_overlap(
    running_bridge, monkeypatch
):
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width, height=height
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.show_screen = lambda *args, **kwargs: None
    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        behind = {
            "rect": [10, 20, 100, 20],
            "runtime_key": {"screen": "overlap_screen", "widget_id": "behind"},
            "editor_owned": False,
        }
        top = {
            "rect": [10, 20, 100, 20],
            "runtime_key": {"screen": "overlap_screen", "widget_id": "top"},
            "editor_owned": False,
        }
        state = globs["_renforge_editor_state"]()
        state.active = True
        state.editor_session_screen = None
        for widget_id in ("behind", "top"):
            state.targets[widget_id] = {
                "analysis_id": "analysis-" + widget_id,
                "source_key": {},
                "capabilities": {"move": True},
                "screen": "overlap_screen",
                "widget_id": widget_id,
                "runtime_baseline": [10, 20],
                "source_position": [10, 20],
                "position": [10, 20],
                "dirty": False,
            }

        globs["_renforge_editor_focus_candidates"] = lambda: [behind, top]
        globs["_renforge_editor_all_candidates"] = lambda: [behind, top]
        globs["_renforge_editor_hit_candidates"] = lambda _x, _y: [top, behind]
        globs["_renforge_editor_candidate_hit"] = lambda *_args: True
        globs["_renforge_editor_validate_runtime_key"] = lambda _key: None
        globs["_renforge_editor_observation_for_candidate"] = lambda candidate: (
            {"runtime_key": candidate["runtime_key"]},
            None,
        )
        globs["_renforge_editor_target_key"] = lambda key: key["widget_id"]
        globs["_renforge_editor_set_label"] = lambda _x, _y: None

        selected = globs["_renforge_editor_select_widget"](
            "overlap_screen", "behind"
        )

        assert selected["ok"] is True
        assert selected["selected"]["widget_id"] == "behind"
        assert state.selected_widget_id == "behind"
        assert state.editor_session_screen is None
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_status_exposes_current_host_capabilities(
    running_bridge,
    monkeypatch,
) -> None:
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        state = globs["_renforge_editor_state"]()
        state.current_analysis_id = "analysis-vbar"
        state.current_capabilities = {"move": True, "resize": False}

        status = globs["_renforge_editor_h_status"]({})
        assert status["current_capabilities"] == {"move": True, "resize": False}

        state.current_analysis_id = None
        assert globs["_renforge_editor_h_status"]({})["current_capabilities"] == {}
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_discovers_anonymous_text_from_screen_cache(
    running_bridge,
    monkeypatch,
) -> None:
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    ScreenDisplayable = type("ScreenDisplayable", (), {})
    Text = type("Text", (), {})
    widget = Text()
    widget._location = ("game/screens.rpy", 236)
    widget.style = types.SimpleNamespace(color="#ffffff", xpos=140, ypos=240)
    screen = ScreenDisplayable()
    screen.children = [widget]
    screen.child = widget
    screen.offsets = [(140, 240)]
    screen.widgets = {}
    screen.cache = {1786330708373445: types.SimpleNamespace(displayable=widget)}
    renpy.text = types.SimpleNamespace(text=types.SimpleNamespace(Text=Text))
    renpy.display.screen = types.SimpleNamespace(
        get_screen=lambda name: screen if name == "exploration_scene" else None
    )
    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(width=width, height=height)
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})

    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        globs["_renforge_editor_active_game_screens"] = lambda: ["exploration_scene"]
        globs["_renforge_editor_measure_text_rect"] = lambda _screen, _widget: [140, 240, 402, 27]
        globs["_renforge_editor_ui_color"] = lambda _name: "#ffffff"

        candidates = globs["_renforge_editor_text_candidates"]()

        assert len(candidates) == 1
        key = candidates[0]["runtime_key"]
        assert key["widget_id"] is None
        assert key["source_location"] == ["game/screens.rpy", 236]
        assert key["locator"] == {
            "kind": "source",
            "source_location": ["game/screens.rpy", 236],
            "statement_kind": "text",
        }
        assert candidates[0]["measurement_method"] == "scene_tree_text"
        rows = globs["_renforge_editor_tree_rows"]()["rows"]
        text_row = next(row for row in rows if row.get("label") == "text")
        assert text_row["id"] == ""
        assert text_row["selectable"] is True
        assert text_row["runtime_key"] == key
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_tree_marks_only_exact_runtime_representation_selected(
    running_bridge,
    monkeypatch,
) -> None:
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        class Node:
            def __init__(self, label: str, children=()):
                self.label = label
                self.children = list(children)

        child = Node("text")
        parent = Node("button", [child])
        focus_key = {
            "screen": "test_screen",
            "widget_id": "task0_target",
            "source_location": ["game/screens.rpy", 12],
            "instance_discriminator": {"kind": "static", "instance_count": 1},
            "ancestry": [{"type": "Button"}],
        }
        text_key = {
            **focus_key,
            "ancestry": [{"type": "Button"}, {"type": "Text"}],
        }
        globs["_renforge_editor_children"] = lambda node: node.children
        globs["_renforge_editor_tree_kind"] = lambda node: (
            ("B", "button", "interactive")
            if node is parent
            else ("T", "text", "content")
        )
        globs["_renforge_editor_tree_snippet"] = lambda *_args: ""
        globs["_renforge_editor_tree_badge_color"] = lambda _badge: "#fff"

        rows = []
        globs["_renforge_editor_tree_walk"](
            parent,
            0,
            (),
            {id(child): "task0_target"},
            {id(parent): focus_key, id(child): text_key},
            rows,
            "task0_target",
            focus_key,
            "test_screen",
            "test_screen",
            set(),
            {"total": 0, "count_truncated": False, "depth_truncated": False},
        )

        selected = [row for row in rows if row.get("selected")]
        assert len(selected) == 1
        assert selected[0]["runtime_key"] == focus_key
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_anonymous_text_preview_moves_the_resolved_runtime_widget(
    running_bridge,
    monkeypatch,
) -> None:
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)
    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(width=width, height=height)
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    renpy.show_screen = lambda *_args, **_kwargs: None
    renpy.redraw = lambda *_args, **_kwargs: None

    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        key = {
            "screen": "exploration_scene",
            "invocation_path": "exploration_scene",
            "widget_id": None,
            "source_location": ["game/screens.rpy", 236],
            "locator": {
                "kind": "source",
                "source_location": ["game/screens.rpy", 236],
                "statement_kind": "text",
            },
            "instance_discriminator": {"kind": "static", "instance_count": 1, "ordinal": 100000},
            "ancestry": [],
        }
        widget = types.SimpleNamespace(
            _location=("game/screens.rpy", 236),
            style=types.SimpleNamespace(xpos=140, ypos=240),
        )
        runtime_cache = types.SimpleNamespace(displayable=widget, constant=True)
        screen = types.SimpleNamespace(widgets={}, cache={1: runtime_cache})
        renpy.display.screen = types.SimpleNamespace(
            get_screen=lambda name: screen if name == "exploration_scene" else None
        )
        ast_node = types.SimpleNamespace(keyword_values={"xpos": 140, "ypos": 240})
        candidate = {
            "runtime_key": key,
            "focused_widget": widget,
            "editor_owned": False,
            "rect": [140, 240, 402, 27],
        }
        globs["_renforge_editor_all_candidates"] = lambda: [candidate]
        globs["_renforge_editor_text_candidates"] = lambda: [candidate]
        globs["_renforge_editor_ast_node_for_runtime_key"] = lambda _screen, _key: ast_node
        state = globs["_renforge_editor_state"]()
        state.selected_screen = "exploration_scene"
        state.selected_widget_id = None
        state.selected_runtime_key = key
        target_key = globs["_renforge_editor_target_key"](key)
        state.selected_target_key = target_key
        state.selected_lock_reason = None
        state.current_capabilities = {"move": True}
        state.targets[target_key] = {
            "analysis_id": "analysis-anonymous-text",
            "source_key": {"position_mode": "xy"},
            "capabilities": {"move": True},
            "runtime_key": key,
            "screen": "exploration_scene",
            "widget_id": None,
            "runtime_baseline": [140, 240],
            "source_position": [140, 240],
            "position": [140, 240],
            "dirty": False,
        }

        moved = globs["_renforge_editor_apply_preview"](
            196,
            284,
            shift=False,
            allow_snap=False,
            record=False,
        )

        assert moved["ok"] is True
        assert ast_node.keyword_values["xpos"] == 196
        assert ast_node.keyword_values["ypos"] == 284
        assert runtime_cache.constant is None
        assert state.targets[target_key]["dirty"] is True
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_focus_candidate_identity_does_not_require_an_authored_id(
    running_bridge,
    monkeypatch,
) -> None:
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)
    ScreenDisplayable = type("ScreenDisplayable", (), {})
    Button = type("Button", (), {})
    widget = Button()
    widget._location = ("game/screens.rpy", 250)
    widget.style = types.SimpleNamespace()
    screen = ScreenDisplayable()
    screen.children = [widget]
    screen.widgets = {}
    screen.cache = {99: types.SimpleNamespace(displayable=widget)}
    focus = _FakeFocus("Anonymous action", 100, 300, 180, 40, widget=widget)
    focus.screen_name = "exploration_scene"
    renpy.display.focus.focus_list[:] = [focus]
    renpy.display.screen = types.SimpleNamespace(
        get_screen=lambda name: screen if name == "exploration_scene" else None
    )
    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(width=width, height=height)
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})

    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        candidate = globs["_renforge_editor_focus_candidates"]()[0]
        key = candidate["runtime_key"]
        assert candidate["resolve_error"] is None
        assert key["widget_id"] is None
        assert key["source_location"] == ["game/screens.rpy", 250]
        assert key["locator"]["kind"] == "source"
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_locked_selection_clears_current_host_capabilities(
    running_bridge,
    monkeypatch,
) -> None:
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        state = globs["_renforge_editor_state"]()
        state.current_analysis_id = "analysis-editable"
        state.current_source_key = {"relative_path": "screens.rpy", "line": 12}
        state.current_capabilities = {"move": True, "resize": False}
        globs["_renforge_editor_focus_candidates"] = lambda: [
            {
                "rect": [0, 0, 20, 20],
                "runtime_key": {"screen": "test_screen", "widget_id": "locked"},
                "resolve_error": "SYNTHETIC_WIDGET_ID",
            }
        ]

        selected = globs["_renforge_editor_select"](5, 5)
        assert selected["lock_reason"] == "SYNTHETIC_WIDGET_ID"
        assert state.current_analysis_id is None
        assert state.current_source_key is None
        assert state.current_capabilities == {}
        assert globs["_renforge_editor_h_status"]({})["current_capabilities"] == {}
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_editor_lock_ui_never_exposes_internal_codes_or_expands_canvas_label(
    running_bridge,
    monkeypatch,
) -> None:
    renpy = running_bridge.renpy
    globs = running_bridge.globs
    for name in (
        "RENFORGE_EDITOR_HOST",
        "RENFORGE_EDITOR_PORT",
        "RENFORGE_EDITOR_TOKEN",
        "RENFORGE_EDITOR_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)

    renpy.config.after_load_callbacks = []
    renpy.Displayable = object
    renpy.Render = lambda width, height: types.SimpleNamespace(
        width=width,
        height=height,
    )
    renpy.IgnoreEvent = type("IgnoreEvent", (Exception,), {})
    exec(compile(_load_editor_body(), "editor.rpy", "exec"), globs)
    try:
        strings = {
            "lock.locked": "Locked",
            "lock.reason.xpos_literal_required": "Position must use a literal xpos value.",
        }
        globs["_renforge_editor_t"] = lambda key: strings.get(key, "[[%s]]" % key)
        state = globs["_renforge_editor_state"]()
        state.selected_widget_id = "demo_locked_expr"
        state.selected_runtime_key = {
            "screen": "village_gate_choices",
            "widget_id": "demo_locked_expr",
            "source_location": ["game/screens.rpy", 239],
        }
        state.selected_screen = "village_gate_choices"
        state.selected_rect = [22, 440, 329, 35]
        state.selected_original_position = [22, 440]
        state.preview_position = None
        state.selected_lock_reason = "XPOS_LITERAL_REQUIRED"

        globs["_renforge_editor_set_label"](22, 440)

        assert globs["_renforge_editor_label_snapshot"]()["text"] == (
            "id=demo_locked_expr x=22 y=440"
        )
        assert globs["_renforge_editor_lock_detail"]() == (
            "Locked — Position must use a literal xpos value."
        )
    finally:
        globs["_renforge_editor_stop_coordinator"]()


def test_bridge_rpy_windows_read_write_adapter_and_version_enforcement(tmp_path: Path, monkeypatch) -> None:
    import json
    import os

    store = types.SimpleNamespace()
    renpy = _fake_renpy(store)
    renpy.config.basedir = str(tmp_path)
    globs = {"__name__": "bridge_rpy", "renpy": renpy}
    globs["builtins"] = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__
    exec(compile(_load_bridge_body(), "bridge.rpy", "exec"), globs)

    project_root = str(tmp_path)
    control_dir = tmp_path / ".renforge" / "control"
    if os.name == "nt":
        from renforge.util.files import ensure_private_directory

        # Real Windows path: private control must carry the protected DACL.
        ensure_private_directory(control_dir)
    else:
        control_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "chmod"):
            os.chmod(control_dir, 0o700)

    bridge_info_file = control_dir / "bridge.json"

    # Test reading starting info with invalid version types (bool, float, str, None)
    for bad_ver in [True, 1.0, "1", None]:
        payload = {
            "schema_version": bad_ver,
            "protocol_version": 1,
            "state": "starting",
            "session_id": "0" * 32,
            "project_root": project_root,
            "host": "127.0.0.1",
            "port": 0,
            "token": "0" * 64,
        }
        if os.name == "nt":
            from renforge.util.files import atomic_write_private_json

            atomic_write_private_json(bridge_info_file, payload, max_bytes=16 * 1024)
        else:
            bridge_info_file.write_text(json.dumps(payload), encoding="utf-8")
            if hasattr(os, "chmod"):
                os.chmod(bridge_info_file, 0o600)

        with pytest.raises(OSError, match="bridge info version is invalid"):
            globs["_renforge_bridge_read_starting_info"](project_root)

    # Test Windows adapter execution path via seam mocking of Win32 wrappers
    calls = {
        "create_file": [],
        "close_handle": [],
        "get_file_type": [],
        "get_handle_attrs": [],
        "read_handle": [],
        "write_handle": [],
        "flush_handle": [],
        "replace_file": [],
        "set_dacl": [],
        "val_dacl": [],
    }

    dummy_handle = 1001
    file_contents = {}

    def fake_create_file(path, access, share_mode, creation_disposition, flags_and_attrs):
        calls["create_file"].append({
            "path": str(path),
            "access": access,
            "share_mode": share_mode,
            "creation_disposition": creation_disposition,
            "flags_and_attrs": flags_and_attrs,
        })
        return dummy_handle

    def fake_close_handle(handle):
        calls["close_handle"].append(handle)

    def fake_get_file_type(handle):
        calls["get_file_type"].append(handle)
        return 1  # FILE_TYPE_DISK

    def fake_get_handle_attrs(handle):
        calls["get_handle_attrs"].append(handle)
        return 0o20  # FILE_ATTRIBUTE_ARCHIVE (not a reparse point)

    def fake_read_handle(handle, max_bytes):
        calls["read_handle"].append((handle, max_bytes))
        path = calls["create_file"][-1]["path"]
        with open(path, "rb") as f:
            return f.read()

    def fake_write_handle(handle, data):
        calls["write_handle"].append((handle, data))
        path = calls["create_file"][-1]["path"]
        file_contents[path] = data
        # In-place ready rewrite writes through the open handle path.
        with open(path, "wb") as f:
            f.write(data)

    def fake_flush_handle(handle):
        calls["flush_handle"].append(handle)

    def fake_replace_file(replaced_path, replacement_path, flags=1):
        calls["replace_file"].append({
            "replaced": str(replaced_path),
            "replacement": str(replacement_path),
            "flags": flags,
        })
        if str(replacement_path) in file_contents:
            content = file_contents[str(replacement_path)]
        elif os.path.exists(replacement_path):
            with open(replacement_path, "rb") as f:
                content = f.read()
        else:
            content = b""
        with open(replaced_path, "wb") as f:
            f.write(content)
        if os.path.exists(replacement_path):
            os.unlink(replacement_path)

    def fake_win_set_dacl(p):
        calls["set_dacl"].append(str(p))

    def fake_win_val_dacl(p):
        calls["val_dacl"].append(str(p))

    def fake_truncate_handle(handle):
        calls.setdefault("truncate_handle", []).append(handle)

    @contextlib.contextmanager
    def simulate_nt():
        old_name = os.name
        os.name = "nt"
        try:
            yield
        finally:
            os.name = old_name

    globs["_renforge_bridge_win_create_file"] = fake_create_file
    globs["_renforge_bridge_win_close_handle"] = fake_close_handle
    globs["_renforge_bridge_win_get_file_type"] = fake_get_file_type
    globs["_renforge_bridge_win_get_handle_attributes"] = fake_get_handle_attrs
    globs["_renforge_bridge_win_read_handle"] = fake_read_handle
    globs["_renforge_bridge_win_write_handle"] = fake_write_handle
    globs["_renforge_bridge_win_flush_handle"] = fake_flush_handle
    globs["_renforge_bridge_win_replace_file"] = fake_replace_file
    globs["_renforge_bridge_win_truncate_handle"] = fake_truncate_handle
    globs["_renforge_bridge_win_set_protected_dacl"] = fake_win_set_dacl
    globs["_renforge_bridge_win_validate_protected_dacl"] = fake_win_val_dacl
    globs["_renforge_bridge_win_is_reparse"] = lambda p: False

    # Write valid starting info
    starting_payload = {
        "schema_version": 1,
        "protocol_version": 1,
        "state": "starting",
        "session_id": "a" * 32,
        "project_root": project_root,
        "host": "127.0.0.1",
        "port": 0,
        "token": "b" * 64,
    }
    bridge_info_file.write_text(json.dumps(starting_payload), encoding="utf-8")
    if hasattr(os, "chmod") and os.name != "nt":
        os.chmod(bridge_info_file, 0o600)

    # Validate read_starting_info under simulated Windows
    with simulate_nt():
        read_data = globs["_renforge_bridge_read_starting_info"](project_root)
    assert read_data["state"] == "starting"
    assert len(calls["create_file"]) == 1
    assert calls["create_file"][0]["access"] == 0x80000000
    assert calls["create_file"][0]["share_mode"] == 1
    assert calls["create_file"][0]["creation_disposition"] == 3
    assert calls["create_file"][0]["flags_and_attrs"] == 0x00200000
    assert dummy_handle in calls["close_handle"]

    # Write ready info under simulated Windows
    ready_payload = dict(starting_payload)
    ready_payload["state"] = "ready"
    ready_payload["port"] = 65000

    with simulate_nt():
        globs["_renforge_bridge_write_ready_info"](project_root, ready_payload)
    assert len(calls["create_file"]) == 2
    # Ready publish overwrites the reserved starting file in place.
    assert calls["create_file"][1]["access"] == 0xC0000000
    assert calls["create_file"][1]["share_mode"] == 0x00000007
    assert calls["create_file"][1]["creation_disposition"] == 3  # OPEN_EXISTING
    assert calls["create_file"][1]["flags_and_attrs"] == 0x00000080
    assert len(calls["flush_handle"]) == 1
    assert len(calls.get("truncate_handle", [])) == 1
    assert len(calls["replace_file"]) == 0
    assert len(calls["write_handle"]) >= 1
    assert json.loads(bridge_info_file.read_text(encoding="utf-8"))["port"] == 65000

    # Additional Win32 edge cases testing: reparse point rejection and cleanup on BaseException
    # 1. Reparse point rejection on starting handle
    def fake_reparse_handle_attrs(handle):
        return 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
    globs["_renforge_bridge_win_get_handle_attributes"] = fake_reparse_handle_attrs
    calls["close_handle"].clear()
    with pytest.raises(OSError, match="reparse point"):
        with simulate_nt():
            globs["_renforge_bridge_read_starting_info"](project_root)
    assert len(calls["close_handle"]) == 1

    # 2. Cleanup on BaseException during handle read
    globs["_renforge_bridge_win_get_handle_attributes"] = fake_get_handle_attrs
    def failing_read(handle, max_bytes):
        raise KeyboardInterrupt("Simulated interrupt during read")
    globs["_renforge_bridge_win_read_handle"] = failing_read
    calls["close_handle"].clear()
    with pytest.raises(KeyboardInterrupt):
        with simulate_nt():
            globs["_renforge_bridge_read_starting_info"](project_root)
    assert len(calls["close_handle"]) == 1
