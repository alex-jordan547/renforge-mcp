from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any

import pytest

from renforge.editor import EditorCoordinator, RuntimeProbe
from renforge.project import RenpyProject
from renforge.sdk import RenpySdk


class _Probe(RuntimeProbe):
    def __init__(self, *, observe_reply: dict[str, Any], attest_reply: dict[str, Any] | None = None):
        self.observe_reply = observe_reply
        self.attest_reply = attest_reply or {"ok": True, "state": "all_targets_attested"}
        self.observe_calls: list[dict[str, Any]] = []
        self.attest_calls: list[dict[str, Any]] = []
        self._observe_counter = 0

    def observe(self, runtime_key: dict[str, Any], *, deadline: float) -> dict[str, Any]:
        self._observe_counter += 1
        self.observe_calls.append({"runtime_key": runtime_key, "deadline": deadline})
        reply = dict(self.observe_reply)
        frame_id = reply.get("frame_id")
        if isinstance(frame_id, str):
            reply["frame_id"] = f"{frame_id}-{self._observe_counter}"
        return reply

    def attest(
        self,
        *,
        transaction_id: str,
        script_generation: int,
        deadline: float,
        expected_targets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.attest_calls.append(
            {
                "transaction_id": transaction_id,
                "script_generation": script_generation,
                "deadline": deadline,
                "expected_targets": expected_targets,
            }
        )
        return dict(self.attest_reply)


def _make_project(tmp_path: Path) -> tuple[RenpyProject, Path]:
    root = tmp_path / "project"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    textbutton "Play" id "start_btn" xpos 12 ypos 10 action NullAction()\n',
        encoding="utf-8",
    )
    return RenpyProject(root), source


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


def _recv_json(sock: socket.socket) -> dict[str, Any]:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            break
        data.extend(chunk)
    assert data, "expected response line"
    return json.loads(data.decode("utf-8"))


def _send_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))


def _auth(sock: socket.socket, endpoint: Any, *, nonce: str = "nonce") -> dict[str, Any]:
    _send_json(
        sock,
        {
            "protocol": "renforge-editor",
            "version": 1,
            "token": endpoint.token,
            "client_nonce": nonce,
        },
    )
    auth = _recv_json(sock)
    assert auth["ok"] is True
    return auth


def _base_observation(script_generation: int = 4) -> dict[str, Any]:
    runtime_key = {
        "screen": "test_screen",
        "invocation_path": "test_screen",
        "widget_id": "start_btn",
        "source_location": ["script.rpy", 2],
        "instance_discriminator": {"kind": "singleton", "instance_count": 1},
        "ancestry": [
            {
                "index": 0,
                "type": "ScreenDisplayable",
                "source_location": ["script.rpy", 1],
                "screen_owner": "test_screen",
                "crop_state": "none",
                "editor_owned": False,
            },
            {
                "index": 1,
                "type": "Button",
                "source_location": ["script.rpy", 2],
                "screen_owner": "test_screen",
                "crop_state": "none",
                "editor_owned": False,
            },
        ],
    }
    return {
        "runtime_key": runtime_key,
        "rect": [12, 10, 120, 40],
        "measurement_method": "focus_list",
        "frame_id": "overlay-frame-1",
        "script_generation": script_generation,
        "object_id": "obj-overlay",
    }


def _analyze(sock: socket.socket, auth: dict[str, Any], observation: dict[str, Any], request_id: str = "an-1") -> dict[str, Any]:
    _send_json(
        sock,
        {
            "protocol": "renforge-editor",
            "version": 1,
            "connection_id": auth["connection_id"],
            "request_id": request_id,
            "command": "analyze_target",
            "payload": {"observation": observation},
        },
    )
    return _recv_json(sock)


def _commit(
    sock: socket.socket,
    auth: dict[str, Any],
    analysis: dict[str, Any],
    *,
    x: int,
    y: int,
    request_id: str = "co-1",
) -> dict[str, Any]:
    _send_json(
        sock,
        {
            "protocol": "renforge-editor",
            "version": 1,
            "connection_id": auth["connection_id"],
            "request_id": request_id,
            "command": "commit",
            "payload": {
                "session_id": auth["session_id"],
                "intents": [
                    {
                        "analysis_id": analysis["result"]["analysis_id"],
                        "source_key": analysis["result"]["source_key"],
                        "x": x,
                        "y": y,
                    }
                ],
            },
        },
    )
    return _recv_json(sock)


def _commit_status(sock: socket.socket, auth: dict[str, Any], transaction_id: str, request_id: str = "st-1") -> dict[str, Any]:
    _send_json(
        sock,
        {
            "protocol": "renforge-editor",
            "version": 1,
            "connection_id": auth["connection_id"],
            "request_id": request_id,
            "command": "commit_status",
            "payload": {"transaction_id": transaction_id},
        },
    )
    return _recv_json(sock)


def test_analyze_target_returns_lock_reasons_for_runtime_denials(tmp_path: Path) -> None:
    project, _ = _make_project(tmp_path)
    observation = _base_observation()
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-1",
            "object_id": "obj-independent",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)

            cases = [
                (
                    "unknown-ancestor-type",
                    lambda o: o["runtime_key"]["ancestry"].__setitem__(
                        1,
                        {
                            **o["runtime_key"]["ancestry"][1],
                            "type": "UnknownWidget",
                        },
                    ),
                    "ANCESTRY_TYPE_UNPROVEN",
                ),
                (
                    "loop-instance",
                    lambda o: o["runtime_key"].__setitem__(
                        "instance_discriminator",
                        {"kind": "loop", "instance_count": 1},
                    ),
                    "LOOP_INSTANCE_UNSUPPORTED",
                ),
                (
                    "repeated-use-instance",
                    lambda o: o["runtime_key"].__setitem__(
                        "instance_discriminator",
                        {"kind": "use", "repeated": True, "instance_count": 1},
                    ),
                    "REPEATED_USE_UNSUPPORTED",
                ),
                (
                    "viewport-ancestor",
                    lambda o: o["runtime_key"]["ancestry"].__setitem__(
                        1,
                        {
                            **o["runtime_key"]["ancestry"][1],
                            "type": "Viewport",
                        },
                    ),
                    "VIEWPORT_ANCESTRY_UNSUPPORTED",
                ),
                (
                    "crop-state",
                    lambda o: o["runtime_key"]["ancestry"].__setitem__(
                        1,
                        {
                            **o["runtime_key"]["ancestry"][1],
                            "crop_state": "crop",
                        },
                    ),
                    "CROP_ANCESTRY_UNSUPPORTED",
                ),
                (
                    "transform-crop-state",
                    lambda o: o["runtime_key"]["ancestry"].__setitem__(
                        1,
                        {
                            **o["runtime_key"]["ancestry"][1],
                            "crop_state": "transform_crop",
                        },
                    ),
                    "TRANSFORM_CROP_UNSUPPORTED",
                ),
            ]

            for index, (_name, mutate, expected_code) in enumerate(cases, start=1):
                sample = json.loads(json.dumps(observation))
                mutate(sample)
                reply = _analyze(sock, auth, sample, request_id=f"an-{index}")
                assert reply["ok"] is True
                assert reply["result"]["capabilities"] == {"move": False}
                assert reply["result"]["lock_reason"]["code"] == expected_code
    finally:
        coordinator.close()


def test_commit_refuses_stale_source_and_duplicate_intent_ids(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    observation = _base_observation()
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-2",
            "object_id": "obj-independent-2",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-ok")
            assert analysis["result"]["lock_reason"] is None
            source.write_text(
                source.read_text(encoding="utf-8") + "# external change\n",
                encoding="utf-8",
            )
            stale = _commit(sock, auth, analysis, x=200, y=201, request_id="co-stale")
            assert stale["ok"] is False
            assert stale["error"]["code"] == "STALE_SOURCE"

            _send_json(
                sock,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "connection_id": auth["connection_id"],
                    "request_id": "co-dup",
                    "command": "commit",
                    "payload": {
                        "session_id": auth["session_id"],
                        "intents": [
                            {
                                "analysis_id": analysis["result"]["analysis_id"],
                                "source_key": analysis["result"]["source_key"],
                                "x": 10,
                                "y": 11,
                            },
                            {
                                "analysis_id": analysis["result"]["analysis_id"],
                                "source_key": analysis["result"]["source_key"],
                                "x": 12,
                                "y": 13,
                            },
                        ],
                    },
                },
            )
            duplicate = _recv_json(sock)
            assert duplicate["ok"] is False
            assert duplicate["error"]["code"] == "DUPLICATE_ANALYSIS_ID"
    finally:
        coordinator.close()


def test_commit_shadow_isolation_and_validation_failure_diagnostics(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    (project.root / ".lint_touch").write_text("1\n", encoding="utf-8")
    (project.root / ".lint_fail").write_text("1\n", encoding="utf-8")
    (project.root / ".lint_stderr").write_text("E" * 70000, encoding="utf-8")

    observation = _base_observation()
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-3",
            "object_id": "obj-independent-3",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    baseline = source.read_bytes()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-fail")
            reply = _commit(sock, auth, analysis, x=50, y=60, request_id="co-fail")
            assert reply["ok"] is False
            assert reply["error"]["code"] == "VALIDATION_FAILED"
            tx = reply["error"]["details"]["transaction_id"]

            status = _commit_status(sock, auth, tx, request_id="st-fail")
            assert status["ok"] is True
            assert status["result"]["state"] == "failed"
            assert status["result"]["diagnostics"]["truncated"] is True
            assert len(status["result"]["diagnostics"]["stderr"]) <= 65536

        assert source.read_bytes() == baseline
        assert not (project.root / "game" / "_shadow_artifact.txt").exists()
    finally:
        coordinator.close()


def test_commit_timeout_rolls_back_and_conflict_is_fail_closed(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    observation = _base_observation(script_generation=7)
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-4",
            "object_id": "obj-independent-4",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=0.2)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    baseline = source.read_bytes()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-timeout")
            commit = _commit(sock, auth, analysis, x=90, y=91, request_id="co-timeout")
            assert commit["ok"] is True
            tx = commit["result"]["transaction_id"]

            time.sleep(0.35)
            status = _commit_status(sock, auth, tx, request_id="st-timeout")
            assert status["ok"] is True
            assert status["result"]["state"] == "rolled_back"
            assert source.read_bytes() == baseline

            analysis_2 = _analyze(sock, auth, observation, request_id="an-conflict")
            commit_2 = _commit(sock, auth, analysis_2, x=101, y=102, request_id="co-conflict")
            tx_2 = commit_2["result"]["transaction_id"]
            source.write_text("external\n", encoding="utf-8")

            time.sleep(0.35)
            conflict = _commit_status(sock, auth, tx_2, request_id="st-conflict")
            assert conflict["ok"] is True
            assert conflict["result"]["state"] == "rollback_conflict"
            assert conflict["result"]["uncertain_paths"] == ["script.rpy"]
            assert source.read_text(encoding="utf-8") == "external\n"
    finally:
        coordinator.close()


def test_reload_handshake_marks_committed_after_independent_attestation(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    observation = _base_observation(script_generation=12)
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-5",
            "object_id": "obj-independent-5",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-commit")
            commit = _commit(sock, auth, analysis, x=333, y=444, request_id="co-commit")
            assert commit["ok"] is True
            tx = commit["result"]["transaction_id"]
            assert "xpos 333 ypos 444" in source.read_text(encoding="utf-8")

            _send_json(
                sock,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "connection_id": auth["connection_id"],
                    "request_id": "hs-1",
                    "command": "reload_handshake",
                    "payload": {
                        "transaction_id": tx,
                        "script_generation": 13,
                    },
                },
            )
            handshake = _recv_json(sock)
            assert handshake["ok"] is True
            assert handshake["result"]["state"] == "committed"
            assert probe.attest_calls
    finally:
        coordinator.close()


@pytest.mark.parametrize(
    "crop_state,expected_code",
    [
        ("unknown", "ANCESTRY_CROP_UNPROVEN"),
        ("mystery", "ANCESTRY_CROP_UNPROVEN"),
    ],
)
def test_analyze_target_denies_unproven_crop_states(tmp_path: Path, crop_state: str, expected_code: str) -> None:
    project, _ = _make_project(tmp_path)
    observation = _base_observation()
    observation["runtime_key"]["ancestry"][1]["crop_state"] = crop_state
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-6",
            "object_id": "obj-independent-6",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint, nonce="nonce-unproven")
            reply = _analyze(sock, auth, observation, request_id="an-crop")
            assert reply["ok"] is True
            assert reply["result"]["lock_reason"]["code"] == expected_code
            assert reply["result"]["capabilities"] == {"move": False}
    finally:
        coordinator.close()
