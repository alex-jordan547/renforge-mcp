import json
import socket
import threading
from pathlib import Path

import pytest

from renforge.bridge.client import BridgeClient, BridgeConfig, BridgeProtocolError


def _start_test_server(handler):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(1)

    def run_server():
        try:
            conn, _ = sock.accept()
            with conn:
                line = conn.makefile("r", encoding="utf-8").readline()
                handler(line, conn)
        finally:
            sock.close()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    return thread, port, sock


def test_bridge_client_ping_roundtrip():
    token = "unit-token"
    captured = {}

    def handler(line, conn):
        request = json.loads(line)
        captured["request"] = request
        response = {
            "ok": True,
            "command": request.get("command"),
            "payload": request.get("payload"),
        }
        conn.sendall((json.dumps(response) + "\n").encode("utf-8"))

    thread, port, sock = _start_test_server(handler)
    client = BridgeClient(BridgeConfig(port=port, token=token))
    reply = client.request("ping", {"x": 1})

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
    assert captured["request"]["token"] == token
    assert captured["request"]["command"] == "ping"
    assert captured["request"]["payload"] == {"x": 1}
    assert reply == {"ok": True, "command": "ping", "payload": {"x": 1}}


def test_bridge_client_ping_helper():
    token = "ping-token"

    def handler(line, conn):
        request = json.loads(line)
        assert request["command"] == "ping"
        assert request["token"] == token
        conn.sendall(b'{"pong": true}\n')

    thread, port, sock = _start_test_server(handler)
    assert BridgeClient(BridgeConfig(port=port, token=token)).ping() == {"pong": True}
    thread.join(timeout=1.0)
    assert sock.fileno() == -1


def test_bridge_client_game_state_forwards_optional_includes():
    token = "state-token"
    captured = {}

    def handler(line, conn):
        request = json.loads(line)
        captured["request"] = request
        conn.sendall(b'{"ok": true, "metrics": {"fps": 60.0}}\n')

    thread, port, sock = _start_test_server(handler)
    reply = BridgeClient(BridgeConfig(port=port, token=token)).get_state(
        include=("metrics",)
    )

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
    assert captured["request"] == {
        "token": token,
        "command": "get_state",
        "payload": {"include": ["metrics"]},
    }
    assert reply == {"ok": True, "metrics": {"fps": 60.0}}


def test_bridge_client_inspect_screen_helper():
    token = "screen-token"
    captured = {}

    def handler(line, conn):
        request = json.loads(line)
        captured["request"] = request
        conn.sendall(
            b'{"ok": true, "active": true, "name": "custom", "layer": "screens", "scope": {}, "arguments": {"args": [], "kwargs": {}}}\n'
        )

    thread, port, sock = _start_test_server(handler)
    reply = BridgeClient(BridgeConfig(port=port, token=token)).inspect_screen("custom")

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
    assert captured["request"] == {
        "token": token,
        "command": "inspect_screen",
        "payload": {"name": "custom"},
    }
    assert reply["active"] is True


def test_bridge_client_control_helper():
    token = "control-token"
    captured = {}

    def handler(line, conn):
        request = json.loads(line)
        captured["request"] = request
        conn.sendall(b'{"ok": true, "action": "toggle_skip", "event": "toggle_skip"}\n')

    thread, port, sock = _start_test_server(handler)
    reply = BridgeClient(BridgeConfig(port=port, token=token)).control("toggle_skip")

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
    assert captured["request"] == {
        "token": token,
        "command": "control",
        "payload": {"action": "toggle_skip"},
    }
    assert reply == {"ok": True, "action": "toggle_skip", "event": "toggle_skip"}


def test_bridge_client_save_slot_helper():
    token = "save-token"
    captured = {}

    def handler(line, conn):
        request = json.loads(line)
        captured["request"] = request
        conn.sendall(b'{"ok": true, "slot": "branch-a", "extra_info": "before menu"}\n')

    thread, port, sock = _start_test_server(handler)
    reply = BridgeClient(BridgeConfig(port=port, token=token)).save_slot(
        "branch-a", extra_info="before menu"
    )

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
    assert captured["request"] == {
        "token": token,
        "command": "save_slot",
        "payload": {"slot": "branch-a", "extra_info": "before menu"},
    }
    assert reply == {"ok": True, "slot": "branch-a", "extra_info": "before menu"}


@pytest.mark.parametrize(
    "method,args",
    [("save_slot", ("branch-a",)), ("load_slot", ("branch-a",)), ("list_slots", ())],
)
def test_bridge_client_save_helpers_normalize_bridge_errors(method, args):
    token = "save-error-token"

    def handler(_line, conn):
        conn.sendall(b'{"error": "stale bridge"}\n')

    thread, port, sock = _start_test_server(handler)
    client = BridgeClient(BridgeConfig(port=port, token=token))

    reply = getattr(client, method)(*args)

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
    assert reply == {"ok": False, "error": "stale bridge"}


def test_bridge_client_load_slot_helper():
    token = "load-token"
    captured = {}

    def handler(line, conn):
        request = json.loads(line)
        captured["request"] = request
        conn.sendall(b'{"ok": true, "slot": "branch-a"}\n')

    thread, port, sock = _start_test_server(handler)
    reply = BridgeClient(BridgeConfig(port=port, token=token)).load_slot("branch-a")

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
    assert captured["request"] == {
        "token": token,
        "command": "load_slot",
        "payload": {"slot": "branch-a"},
    }
    assert reply == {"ok": True, "slot": "branch-a"}


def test_bridge_client_list_slots_helper():
    token = "list-token"
    captured = {}

    def handler(line, conn):
        request = json.loads(line)
        captured["request"] = request
        conn.sendall(
            b'{"ok": true, "slots": [{"name": "branch-a", "extra_info": "before menu", "mtime": 12.5}]}\n'
        )

    thread, port, sock = _start_test_server(handler)
    reply = BridgeClient(BridgeConfig(port=port, token=token)).list_slots(regexp="branch")

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
    assert captured["request"] == {
        "token": token,
        "command": "list_slots",
        "payload": {"regexp": "branch"},
    }
    assert reply == {
        "ok": True,
        "slots": [{"name": "branch-a", "extra_info": "before menu", "mtime": 12.5}],
    }


def test_bridge_client_send_input_helper():
    token = "input-token"
    captured = {}

    def handler(line, conn):
        request = json.loads(line)
        captured["request"] = request
        conn.sendall(b'{"ok": true, "mode": "text", "characters": 4, "submitted": true}\n')

    thread, port, sock = _start_test_server(handler)
    reply = BridgeClient(BridgeConfig(port=port, token=token)).send_input(
        text="Alex", submit=True
    )

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
    assert captured["request"] == {
        "token": token,
        "command": "send_input",
        "payload": {"text": "Alex", "submit": True},
    }
    assert reply == {"ok": True, "mode": "text", "characters": 4, "submitted": True}


def test_bridge_client_invalid_json_response():
    token = "bad-json-token"

    def handler(line, conn):
        conn.sendall(b"not-json\n")

    thread, port, sock = _start_test_server(handler)
    client = BridgeClient(BridgeConfig(port=port, token=token))

    with pytest.raises(BridgeProtocolError):
        client.ping()

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
