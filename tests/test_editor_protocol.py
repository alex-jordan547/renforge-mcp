from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from renforge.editor import EditorCoordinator
from renforge.project import RenpyProject
from renforge.sdk import RenpySdk


def _make_project(tmp_path: Path) -> RenpyProject:
    root = tmp_path / "project"
    (root / "game").mkdir(parents=True)
    (root / "game" / "script.rpy").write_text(
        'screen test_screen:\n    textbutton "Play" id "start_btn" xpos 12 ypos 10 action NullAction()\n',
        encoding="utf-8",
    )
    return RenpyProject(root)


def _make_sdk(tmp_path: Path) -> RenpySdk:
    sdk_root = tmp_path / "sdk"
    sdk_root.mkdir(parents=True)
    launcher = sdk_root / "renpy.sh"
    launcher.write_text(
        "#!/bin/sh\n"
        "project=\"$1\"\n"
        "cmd=\"$2\"\n"
        "if [ \"$cmd\" = \"lint\" ]; then\n"
        "  if [ -f \"$project/.lint_stderr\" ]; then cat \"$project/.lint_stderr\" 1>&2; fi\n"
        "  if [ -f \"$project/.lint_stdout\" ]; then cat \"$project/.lint_stdout\"; fi\n"
        "  if [ -f \"$project/.lint_touch\" ]; then echo touch > \"$project/game/_shadow_artifact.txt\"; fi\n"
        "  if [ -f \"$project/.lint_fail\" ]; then echo lint-failed 1>&2; exit 1; fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return RenpySdk(version="8.5.3", root=sdk_root)


def _recv_json(sock: socket.socket) -> dict:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            break
        data.extend(chunk)
    assert data, "expected JSON response line"
    return json.loads(data.decode("utf-8"))


def _send_json(sock: socket.socket, payload: dict) -> None:
    sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))


def test_protocol_requires_auth_first_and_rejects_truncated_or_oversized_frames(tmp_path: Path) -> None:
    coordinator = EditorCoordinator(_make_project(tmp_path), _make_sdk(tmp_path))
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            _send_json(
                sock,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "connection_id": "missing",
                    "request_id": "req-1",
                    "command": "commit_status",
                    "payload": {"transaction_id": "missing"},
                },
            )
            reply = _recv_json(sock)
            assert reply["ok"] is False
            assert reply["error"]["code"] == "AUTH_REQUIRED"

        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            huge_auth = {
                "protocol": "renforge-editor",
                "version": 1,
                "token": endpoint.token,
                "client_nonce": "n" * 5000,
            }
            _send_json(sock, huge_auth)
            reply = _recv_json(sock)
            assert reply["ok"] is False
            assert reply["error"]["code"] == "FRAME_TOO_LARGE"

        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            sock.sendall(b'{"protocol":"renforge-editor","version":1')
            sock.shutdown(socket.SHUT_WR)
            reply = _recv_json(sock)
            assert reply["ok"] is False
            assert reply["request_id"] is None
            assert reply["error"]["code"] == "TRUNCATED_FRAME"

        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            _send_json(
                sock,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "token": endpoint.token,
                    "client_nonce": "nonce-1",
                },
            )
            auth = _recv_json(sock)
            assert auth["ok"] is True
            _send_json(
                sock,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "connection_id": auth["connection_id"],
                    "request_id": "req-big",
                    "command": "commit_status",
                    "payload": {"transaction_id": "x" * (1024 * 1024)},
                },
            )
            reply = _recv_json(sock)
            assert reply["ok"] is False
            assert reply["request_id"] is None
            assert reply["error"]["code"] == "FRAME_TOO_LARGE"
    finally:
        coordinator.close()


def test_protocol_duplicate_request_ids_are_idempotent_then_fail_closed(tmp_path: Path) -> None:
    coordinator = EditorCoordinator(_make_project(tmp_path), _make_sdk(tmp_path))
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            _send_json(
                sock,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "token": endpoint.token,
                    "client_nonce": "nonce-2",
                },
            )
            auth = _recv_json(sock)
            assert auth["ok"] is True

            frame = {
                "protocol": "renforge-editor",
                "version": 1,
                "connection_id": auth["connection_id"],
                "request_id": "dup-1",
                "command": "commit_status",
                "payload": {"transaction_id": "missing"},
            }
            _send_json(sock, frame)
            first = _recv_json(sock)
            _send_json(sock, frame)
            second = _recv_json(sock)
            assert first == second

            changed = dict(frame)
            changed["payload"] = {"transaction_id": "different"}
            _send_json(sock, changed)
            dup_error = _recv_json(sock)
            assert dup_error["ok"] is False
            assert dup_error["error"]["code"] == "DUPLICATE_REQUEST_ID"
    finally:
        coordinator.close()


def test_protocol_reconnect_with_same_nonce(tmp_path: Path) -> None:
    coordinator = EditorCoordinator(_make_project(tmp_path), _make_sdk(tmp_path))
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as first:
            _send_json(
                first,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "token": endpoint.token,
                    "client_nonce": "stable-nonce",
                },
            )
            auth_1 = _recv_json(first)
            assert auth_1["ok"] is True
            session_id = auth_1["session_id"]

        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as second:
            _send_json(
                second,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "token": endpoint.token,
                    "client_nonce": "stable-nonce",
                },
            )
            auth_2 = _recv_json(second)
            assert auth_2["ok"] is True
            assert auth_2["session_id"] == session_id
    finally:
        coordinator.close()


def test_protocol_reconnect_duplicate_request_id_returns_cached_reply(tmp_path: Path) -> None:
    coordinator = EditorCoordinator(_make_project(tmp_path), _make_sdk(tmp_path))
    endpoint = coordinator.start()
    try:
        payload = {
            "protocol": "renforge-editor",
            "version": 1,
            "request_id": "same-id",
            "command": "commit_status",
            "payload": {"transaction_id": "missing"},
        }

        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as first:
            _send_json(
                first,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "token": endpoint.token,
                    "client_nonce": "nonce-r",
                },
            )
            auth_1 = _recv_json(first)
            _send_json(first, {**payload, "connection_id": auth_1["connection_id"]})
            reply_1 = _recv_json(first)

        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as second:
            _send_json(
                second,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "token": endpoint.token,
                    "client_nonce": "nonce-r",
                },
            )
            auth_2 = _recv_json(second)
            _send_json(second, {**payload, "connection_id": auth_2["connection_id"]})
            reply_2 = _recv_json(second)
        assert reply_2 == reply_1
    finally:
        coordinator.close()


def test_protocol_rejects_auth_frame_with_extra_fields(tmp_path: Path) -> None:
    coordinator = EditorCoordinator(_make_project(tmp_path), _make_sdk(tmp_path))
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            _send_json(
                sock,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "token": endpoint.token,
                    "client_nonce": "nonce-extra",
                    "extra": "boom",
                },
            )
            reply = _recv_json(sock)
            assert reply["ok"] is False
            assert reply["error"]["code"] == "AUTH_FRAME_SCHEMA_INVALID"
    finally:
        coordinator.close()


def test_protocol_close_stops_existing_connection_from_executing_new_commands(tmp_path: Path) -> None:
    coordinator = EditorCoordinator(_make_project(tmp_path), _make_sdk(tmp_path))
    endpoint = coordinator.start()
    with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
        _send_json(
            sock,
            {
                "protocol": "renforge-editor",
                "version": 1,
                "token": endpoint.token,
                "client_nonce": "nonce-close",
            },
        )
        auth = _recv_json(sock)
        coordinator.close()
        _send_json(
            sock,
            {
                "protocol": "renforge-editor",
                "version": 1,
                "connection_id": auth["connection_id"],
                "request_id": "after-close",
                "command": "commit_status",
                "payload": {"transaction_id": "missing"},
            },
        )
        sock.settimeout(0.3)
        with pytest.raises((socket.timeout, ConnectionError, OSError, AssertionError)):
            _ = _recv_json(sock)


def test_protocol_coalesces_inflight_duplicate_requests(tmp_path: Path) -> None:
    coordinator = EditorCoordinator(_make_project(tmp_path), _make_sdk(tmp_path))
    endpoint = coordinator.start()
    replies: list[dict] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(3)

    def worker(connection_nonce: str) -> None:
        try:
            with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
                _send_json(
                    sock,
                    {
                        "protocol": "renforge-editor",
                        "version": 1,
                        "token": endpoint.token,
                        "client_nonce": connection_nonce,
                    },
                )
                auth = _recv_json(sock)
                barrier.wait(timeout=2.0)
                _send_json(
                    sock,
                    {
                        "protocol": "renforge-editor",
                        "version": 1,
                        "connection_id": auth["connection_id"],
                        "request_id": "inflight-dup",
                        "command": "commit_status",
                        "payload": {"transaction_id": "missing"},
                    },
                )
                replies.append(_recv_json(sock))
        except BaseException as exc:  # pragma: no cover - asserted by test
            errors.append(exc)

    try:
        t1 = threading.Thread(target=worker, args=("nonce-a",), daemon=True)
        t2 = threading.Thread(target=worker, args=("nonce-a",), daemon=True)
        t1.start()
        t2.start()
        barrier.wait(timeout=2.0)
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)
        assert not errors
        assert len(replies) == 2
        assert replies[0] == replies[1]
    finally:
        coordinator.close()
