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


def test_bridge_client_hover_element_helper():
    token = "hover-token"
    captured = {}

    def handler(line, conn):
        captured["request"] = json.loads(line)
        conn.sendall(b'{"ok": true, "hovered": true, "x": 10, "y": 20}\n')

    thread, port, sock = _start_test_server(handler)
    reply = BridgeClient(BridgeConfig(port=port, token=token)).hover_element(
        id="menu:button:Save", expected_frame_id="frame-1"
    )

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
    assert captured["request"] == {
        "token": token,
        "command": "hover_element",
        "payload": {
            "text": None,
            "id": "menu:button:Save",
            "exact": False,
            "expected_frame_id": "frame-1",
        },
    }
    assert reply["hovered"] is True


def test_bridge_client_get_ui_element_bounds_helper():
    token = "bounds-token"
    captured = {}

    def handler(line, conn):
        captured["request"] = json.loads(line)
        conn.sendall(
            b'{"ok": true, "focus_bounds": {"x": 1, "y": 2, "width": 3, "height": 4}, '
            b'"painted_bounds": null, "painted_bounds_available": false}\n'
        )

    thread, port, sock = _start_test_server(handler)
    reply = BridgeClient(BridgeConfig(port=port, token=token)).get_ui_element_bounds(
        id="menu:button:Load", expected_frame_id="frame-1"
    )

    thread.join(timeout=1.0)
    assert sock.fileno() == -1
    assert captured["request"] == {
        "token": token,
        "command": "get_ui_element_bounds",
        "payload": {
            "text": None,
            "id": "menu:button:Load",
            "exact": False,
            "expected_frame_id": "frame-1",
        },
    }
    assert reply["painted_bounds_available"] is False


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



def _control_project(tmp_path: Path, name: str = "project") -> Path:
    root = (tmp_path / name).resolve()
    (root / "game").mkdir(parents=True)
    return root


def _session_id(seed: str = "11") -> str:
    return (seed * 32)[:32]


def _token(seed: str = "aa") -> str:
    return (seed * 64)[:64]


def _write_ready_bridge_info(
    project_root: Path,
    *,
    session_id: str | None = None,
    token: str | None = None,
    port: int = 40123,
    **overrides: object,
) -> dict:
    from renforge.bridge.control import ensure_control_dir
    from renforge.util.files import atomic_write_private_json

    ensure_control_dir(project_root)
    payload = {
        "schema_version": 1,
        "protocol_version": 1,
        "state": "ready",
        "session_id": session_id or _session_id(),
        "project_root": str(project_root.resolve()),
        "host": "127.0.0.1",
        "port": port,
        "token": token or _token(),
    }
    payload.update(overrides)
    path = project_root / ".renforge" / "control" / "bridge.json"
    atomic_write_private_json(path, payload, max_bytes=16 * 1024)
    return payload


def test_bridge_info_starting_round_trip(tmp_path: Path) -> None:
    from renforge.bridge.control import BridgeInfo, read_bridge_info, write_starting_bridge_info

    root = _control_project(tmp_path)
    session_id = _session_id("ab")
    token = _token("cd")

    written = write_starting_bridge_info(root, session_id=session_id, token=token)
    assert isinstance(written, BridgeInfo)
    assert written.state == "starting"
    assert written.port == 0
    assert written.session_id == session_id
    assert written.token == token
    assert written.project_root == str(root.resolve())
    assert written.host == "127.0.0.1"
    assert written.schema_version == 1
    assert written.protocol_version == 1
    assert "token" not in repr(written)
    assert token not in repr(written)

    loaded = read_bridge_info(root, require_ready=False, expected_session_id=session_id)
    assert loaded == written


def test_bridge_info_ready_round_trip(tmp_path: Path) -> None:
    from renforge.bridge.control import read_bridge_info

    root = _control_project(tmp_path)
    payload = _write_ready_bridge_info(root, session_id=_session_id("ef"), token=_token("12"), port=45555)

    loaded = read_bridge_info(root, require_ready=True, expected_session_id=payload["session_id"])
    assert loaded.state == "ready"
    assert loaded.port == 45555
    assert loaded.session_id == payload["session_id"]
    assert loaded.token == payload["token"]
    assert loaded.project_root == str(root.resolve())
    assert payload["token"] not in repr(loaded)


def test_bridge_info_rejects_require_ready_on_starting(tmp_path: Path) -> None:
    from renforge.bridge.control import read_bridge_info, write_starting_bridge_info

    root = _control_project(tmp_path)
    write_starting_bridge_info(root, session_id=_session_id(), token=_token())

    with pytest.raises(BridgeProtocolError, match="^bridge metadata failed validation$") as excinfo:
        read_bridge_info(root, require_ready=True)
    assert "token" not in str(excinfo.value)


def test_bridge_info_rejects_expected_session_mismatch(tmp_path: Path) -> None:
    from renforge.bridge.control import read_bridge_info

    root = _control_project(tmp_path)
    _write_ready_bridge_info(root, session_id=_session_id("aa"))

    with pytest.raises(BridgeProtocolError, match="^bridge metadata failed validation$"):
        read_bridge_info(root, expected_session_id=_session_id("bb"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("token"),
        lambda p: p.update({"extra": "nope"}),
        lambda p: p.update({"schema_version": 2}),
        lambda p: p.update({"protocol_version": 0}),
        lambda p: p.update({"state": "running"}),
        lambda p: p.update({"session_id": "not-hex"}),
        lambda p: p.update({"token": "short"}),
        lambda p: p.update({"host": "0.0.0.0"}),
        lambda p: p.update({"port": 0}),
        lambda p: p.update({"port": 70000}),
        lambda p: p.update({"port": True}),
        lambda p: p.update({"project_root": "/tmp/other-project"}),
    ],
)
def test_bridge_info_rejects_invalid_ready_payload(tmp_path: Path, mutate) -> None:
    from renforge.bridge.control import ensure_control_dir, read_bridge_info
    from renforge.util.files import atomic_write_private_json

    root = _control_project(tmp_path)
    ensure_control_dir(root)
    payload = {
        "schema_version": 1,
        "protocol_version": 1,
        "state": "ready",
        "session_id": _session_id(),
        "project_root": str(root.resolve()),
        "host": "127.0.0.1",
        "port": 12345,
        "token": _token(),
    }
    mutate(payload)
    path = root / ".renforge" / "control" / "bridge.json"
    atomic_write_private_json(path, payload, max_bytes=16 * 1024)

    with pytest.raises(BridgeProtocolError, match="^bridge metadata failed validation$") as excinfo:
        read_bridge_info(root, require_ready=True)
    assert str(excinfo.value) == "bridge metadata failed validation"


def test_bridge_info_rejects_symlink_metadata_file(tmp_path: Path) -> None:
    from renforge.bridge.control import ensure_control_dir, read_bridge_info

    root = _control_project(tmp_path)
    control = ensure_control_dir(root)
    victim = tmp_path / "victim.json"
    victim.write_text('{"token":"leak-me"}', encoding="utf-8")
    target = control / "bridge.json"
    target.symlink_to(victim)

    with pytest.raises(BridgeProtocolError, match="^bridge metadata failed validation$") as excinfo:
        read_bridge_info(root, require_ready=False)
    assert "leak-me" not in str(excinfo.value)
    assert victim.read_text(encoding="utf-8") == '{"token":"leak-me"}'


def test_ensure_control_dir_rejects_symlink_control_path(tmp_path: Path) -> None:
    from renforge.bridge.control import ensure_control_dir
    from renforge.launch_env import LaunchError

    root = _control_project(tmp_path)
    renforge = root / ".renforge"
    renforge.mkdir()
    real = tmp_path / "elsewhere"
    real.mkdir()
    (renforge / "control").symlink_to(real)

    with pytest.raises(LaunchError) as excinfo:
        ensure_control_dir(root)
    assert excinfo.value.code == "BRIDGE_CONTROL_DIRECTORY_UNSAFE"
    assert excinfo.value.phase == "preparing_control_directory"


def test_write_starting_bridge_info_rejects_bad_identity(tmp_path: Path) -> None:
    from renforge.bridge.control import write_starting_bridge_info

    root = _control_project(tmp_path)
    with pytest.raises(BridgeProtocolError, match="^bridge metadata failed validation$"):
        write_starting_bridge_info(root, session_id="bad", token=_token())
    with pytest.raises(BridgeProtocolError, match="^bridge metadata failed validation$"):
        write_starting_bridge_info(root, session_id=_session_id(), token="bad")


def test_from_project_builds_client_from_ready_bridge_info(tmp_path: Path) -> None:
    root = _control_project(tmp_path)
    token = _token("f0")
    payload = _write_ready_bridge_info(
        root,
        session_id=_session_id("c0"),
        token=token,
        port=45678,
    )

    client = BridgeClient.from_project(root, timeout=2.5)

    assert client._config.host == "127.0.0.1"
    assert client._config.port == payload["port"]
    assert client._config.token == token
    assert client._config.timeout == 2.5


def test_from_project_rejects_starting_bridge_info(tmp_path: Path) -> None:
    from renforge.bridge.control import write_starting_bridge_info

    root = _control_project(tmp_path)
    write_starting_bridge_info(root, session_id=_session_id(), token=_token())

    with pytest.raises(BridgeProtocolError, match="^bridge metadata failed validation$") as excinfo:
        BridgeClient.from_project(root)
    assert "token" not in str(excinfo.value)


def test_from_project_ignores_legacy_bridge_json(tmp_path: Path) -> None:
    from renforge.bridge.control import ensure_control_dir

    root = _control_project(tmp_path)
    ensure_control_dir(root)
    legacy = root / ".renforge" / "bridge.json"
    legacy.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": 9999,
                "token": "legacy-token-must-not-be-used",
                "pid": 12345,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BridgeProtocolError, match="^bridge metadata failed validation$") as excinfo:
        BridgeClient.from_project(root)
    assert "legacy-token" not in str(excinfo.value)
    assert "12345" not in str(excinfo.value)
    assert json.loads(legacy.read_text(encoding="utf-8"))["token"] == "legacy-token-must-not-be-used"


def test_from_project_rejects_invalid_ready_payload_without_credentials(tmp_path: Path) -> None:
    root = _control_project(tmp_path)
    secret = _token("99")
    _write_ready_bridge_info(root, token=secret, host="0.0.0.0")

    with pytest.raises(BridgeProtocolError, match="^bridge metadata failed validation$") as excinfo:
        BridgeClient.from_project(root)
    message = str(excinfo.value)
    assert message == "bridge metadata failed validation"
    assert secret not in message


def test_from_project_rejects_non_int_version_types(tmp_path: Path) -> None:
    root = _control_project(tmp_path)
    for bad_version in [True, 1.0, "1", None]:
        _write_ready_bridge_info(root, schema_version=bad_version)
        with pytest.raises(BridgeProtocolError, match="^bridge metadata failed validation$"):
            BridgeClient.from_project(root)

        _write_ready_bridge_info(root, protocol_version=bad_version)
        with pytest.raises(BridgeProtocolError, match="^bridge metadata failed validation$"):
            BridgeClient.from_project(root)
