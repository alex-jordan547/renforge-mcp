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


def _fake_renpy(store):
    config = types.SimpleNamespace(basedir="", label_callbacks=[], periodic_callbacks=[])
    renpy = types.ModuleType("renpy")
    renpy.store = store
    renpy.config = config
    renpy.screenshot_to_bytes = lambda size: b"\x89PNG\r\n_fake_frame_"
    renpy.get_showing_tags = lambda: ["bg", "eileen"]
    renpy.display = types.SimpleNamespace()
    return renpy


@pytest.fixture
def running_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("RENFORGE_BRIDGE_TOKEN", "runtime-token")
    monkeypatch.setenv("RENFORGE_BRIDGE_PORT", "0")

    store = types.SimpleNamespace(score=7, player_name="Rin", _hidden="x")
    renpy = _fake_renpy(store)
    renpy.config.basedir = str(tmp_path)

    globs = {"__name__": "bridge_rpy", "renpy": renpy}
    exec(compile(_load_bridge_body(), "bridge.rpy", "exec"), globs)

    bridge = globs["_RENFORGE_BRIDGE"]
    assert bridge is not None, "bridge did not start"

    # Stand in for Ren'Py's main thread: keep draining the request queue.
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            globs["renforge_drain_bridge"]()
            time.sleep(0.005)

    pump_thread = threading.Thread(target=pump, daemon=True)
    pump_thread.start()

    # Wait for the listener to publish its port.
    for _ in range(200):
        if getattr(store, "renforge_bridge_port", None):
            break
        time.sleep(0.01)

    client = BridgeClient.from_project(tmp_path)
    yield client, store

    stop.set()
    bridge.stop.set()


def test_ping_roundtrips_through_main_thread(running_bridge):
    client, _ = running_bridge
    assert client.ping().get("pong") is True


def test_get_state_reports_variables_and_showing(running_bridge):
    client, _ = running_bridge
    state = client.get_state()
    assert state["variables"]["score"] == 7
    assert state["variables"]["player_name"] == "Rin"
    assert "_hidden" not in state["variables"]  # private names are filtered
    assert state["showing_tags"] == ["bg", "eileen"]


def test_eval_and_set_var_mutate_real_store(running_bridge):
    client, store = running_bridge
    assert client.eval_expr("score * 2") == 14
    client.set_var("score", 99)
    assert store.score == 99
    assert client.get_var("score") == 99


def test_screenshot_returns_decoded_png_bytes(running_bridge):
    client, _ = running_bridge
    data = client.screenshot(320, 180)
    assert data.startswith(b"\x89PNG")


def test_bad_token_is_rejected(running_bridge):
    client, _ = running_bridge
    port = client._config.port
    wrong = BridgeClient(BridgeConfig(port=port, token="WRONG"))
    assert wrong.request("ping").get("error") == "bad_token"
