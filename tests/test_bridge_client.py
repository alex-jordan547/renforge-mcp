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
