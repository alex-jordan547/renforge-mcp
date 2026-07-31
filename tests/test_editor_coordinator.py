from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from renforge.editor import EditorCoordinator, RuntimeProbe
from renforge.editor.exceptions import EditorError
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
    # A ``renpy.py`` launcher, never ``renpy.sh``: the SDK resolver only accepts
    # ``renpy.exe`` or ``renpy.py`` on Windows, and runs the ``.py`` form through
    # ``sys.executable`` on every platform. ``lint`` runs against the *shadow*
    # copy, so these fixture flags are read from the copied project root.
    launcher = sdk_root / "renpy.py"
    launcher.write_text(
        "import pathlib\n"
        "import sys\n"
        "\n"
        "project = pathlib.Path(sys.argv[1])\n"
        "if (sys.argv[2] if len(sys.argv) > 2 else '') != 'lint':\n"
        "    raise SystemExit(0)\n"
        "for name, stream in (('.lint_stderr', sys.stderr), ('.lint_stdout', sys.stdout)):\n"
        "    fixture = project / name\n"
        "    if fixture.is_file():\n"
        "        stream.write(fixture.read_text(encoding='utf-8'))\n"
        "if (project / '.lint_touch').is_file():\n"
        "    (project / 'game' / '_shadow_artifact.txt').write_text('touch\\n', encoding='utf-8')\n"
        "if (project / '.lint_fail').is_file():\n"
        "    sys.stderr.write('lint-failed\\n')\n"
        "    raise SystemExit(1)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
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


def _write_recovered_staged_transaction(
    transaction_root: Path,
    *,
    transaction_id: str,
    state: str,
    original_bytes: bytes,
    staged_bytes: bytes,
    generation: int = 1,
    source_relative_path: str = "script.rpy",
    session_id: str = "recovery-session",
) -> None:
    tx_dir = transaction_root / transaction_id
    original_path = tx_dir / "original" / Path(source_relative_path)
    staged_path = tx_dir / "staged" / Path(source_relative_path)
    original_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(original_bytes)
    staged_path.write_bytes(staged_bytes)
    manifest = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "session_id": session_id,
        "state": state,
        "source_relative_path": source_relative_path,
        "generation": generation,
        "original_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "staged_sha256": hashlib.sha256(staged_bytes).hexdigest(),
        "expected_targets": [],
        "uncertain_paths": [],
        "diagnostics": {},
    }
    manifest_path = tx_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")


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


# Explicit CI recv budget for the commit helper. The fixture SDK lint is a
# trivial subprocess, but loaded Windows runners can still exceed the 2s
# create_connection default used by most tests. Non-commit paths keep 2s.
_COMMIT_SOCKET_TIMEOUT_SECONDS = 10.0


def _commit(
    sock: socket.socket,
    auth: dict[str, Any],
    analysis: dict[str, Any],
    *,
    x: int,
    y: int,
    request_id: str = "co-1",
) -> dict[str, Any]:
    # Temporarily raise the socket timeout so slow Windows CI hosts can finish
    # the commit path (including fixture shadow lint) without client timeouts.
    previous_timeout = sock.gettimeout()
    sock.settimeout(max(float(previous_timeout or 0.0), _COMMIT_SOCKET_TIMEOUT_SECONDS))
    try:
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
    finally:
        sock.settimeout(previous_timeout)


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


def test_analyze_target_normalizes_game_prefixed_source_location(tmp_path: Path) -> None:
    project, _ = _make_project(tmp_path)
    observation = _base_observation()
    observation["runtime_key"]["source_location"] = ["game/script.rpy", 2]
    observation["rect"] = [112, 110, 120, 40]
    for ancestor in observation["runtime_key"]["ancestry"]:
        ancestor["source_location"] = ["game/script.rpy", ancestor["source_location"][1]]
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-3",
            "object_id": "obj-independent-3",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            reply = _analyze(sock, auth, observation, request_id="an-game-prefix")
            assert reply["ok"] is True
            assert reply["result"]["lock_reason"] is None
            source_key = reply["result"]["source_key"]
            assert source_key["relative_path"] == "script.rpy"
            assert reply["result"]["original_position"] == [12, 10]
            for ancestor in source_key["ancestry"]:
                assert ancestor["source_location"][0] == "script.rpy"
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


def test_reload_handshake_rolls_back_when_rebind_is_ambiguous(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    observation = _base_observation(script_generation=20)
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-ambiguous",
            "object_id": "obj-independent-ambiguous",
        },
        attest_reply={"ok": False, "error": "AMBIGUOUS_REBIND"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    baseline = source.read_bytes()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-ambiguous")
            commit = _commit(sock, auth, analysis, x=300, y=301, request_id="co-ambiguous")
            transaction_id = commit["result"]["transaction_id"]

            _send_json(
                sock,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "connection_id": auth["connection_id"],
                    "request_id": "hs-ambiguous",
                    "command": "reload_handshake",
                    "payload": {
                        "transaction_id": transaction_id,
                        "script_generation": 21,
                    },
                },
            )
            handshake = _recv_json(sock)
            assert handshake["ok"] is False
            assert handshake["error"]["code"] == "ATTESTATION_FAILED"
            assert source.read_bytes() == baseline

            status = _commit_status(sock, auth, transaction_id, request_id="st-ambiguous")
            assert status["result"]["state"] == "rolled_back"
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


def test_runtime_key_ordinal_drift_is_ignored_for_single_static_instance(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    observation = _base_observation()
    observation["runtime_key"]["instance_discriminator"] = {
        "kind": "static",
        "instance_count": 1,
        "ordinal": 6,
    }
    drifted_runtime_key = json.loads(json.dumps(observation["runtime_key"]))
    drifted_runtime_key["instance_discriminator"]["ordinal"] = 7
    probe = _Probe(
        observe_reply={
            **observation,
            "runtime_key": drifted_runtime_key,
            "frame_id": "independent-frame-7",
            "object_id": "obj-independent-7",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-ordinal-drift")
            assert analysis["ok"] is True
            assert analysis["result"]["lock_reason"] is None
            assert analysis["result"]["capabilities"] == {"move": True}

            probe.observe_reply["runtime_key"]["widget_id"] = "changed_btn"
            commit = _commit(sock, auth, analysis, x=30, y=40, request_id="co-stable-mismatch")
            assert commit["ok"] is False
            assert commit["error"]["code"] == "RUNTIME_KEY_MISMATCH"
    finally:
        coordinator.close()


def test_recover_staged_transaction_with_matching_original_bytes(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    sdk = _make_sdk(tmp_path)
    coordinator = EditorCoordinator(project, sdk)
    transaction_root = coordinator._transaction_root
    transaction_id = "recovered-staged-original"
    original_bytes = source.read_bytes()
    staged_bytes = original_bytes.replace(b'"Play"', b'"Recovered"')
    _write_recovered_staged_transaction(
        transaction_root,
        transaction_id=transaction_id,
        state="staged",
        original_bytes=original_bytes,
        staged_bytes=staged_bytes,
    )
    coordinator._recover_transactions()
    status = coordinator.close()
    assert status["transactions"][transaction_id] == "rolled_back"
    assert status["recovered"] == [transaction_id]
    assert coordinator._transactions[transaction_id].uncertain_paths == []
    assert source.read_bytes() == original_bytes


def test_recover_staged_transaction_with_matching_staged_bytes_rolls_back(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    sdk = _make_sdk(tmp_path)
    coordinator = EditorCoordinator(project, sdk)
    transaction_root = coordinator._transaction_root
    transaction_id = "recovered-staged-matching-staged"
    original_bytes = source.read_bytes()
    staged_bytes = original_bytes.replace(b'"Play"', b'"Recovered"')
    source.write_bytes(staged_bytes)
    _write_recovered_staged_transaction(
        transaction_root,
        transaction_id=transaction_id,
        state="staged",
        original_bytes=original_bytes,
        staged_bytes=staged_bytes,
    )
    coordinator._recover_transactions()
    status = coordinator.close()
    assert status["transactions"][transaction_id] == "rolled_back"
    assert source.read_bytes() == original_bytes


def test_recover_staged_transaction_conflict_keeps_tampered_source(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    sdk = _make_sdk(tmp_path)
    coordinator = EditorCoordinator(project, sdk)
    transaction_root = coordinator._transaction_root
    transaction_id = "recovered-staged-conflict"
    original_bytes = source.read_bytes()
    staged_bytes = original_bytes.replace(b'"Play"', b'"Recovered"')
    source.write_bytes(b"tampered\n")
    _write_recovered_staged_transaction(
        transaction_root,
        transaction_id=transaction_id,
        state="staged",
        original_bytes=original_bytes,
        staged_bytes=staged_bytes,
    )
    coordinator._recover_transactions()
    status = coordinator.close()
    assert status["transactions"][transaction_id] == "rollback_conflict"
    assert coordinator._transactions[transaction_id].uncertain_paths == [ "script.rpy" ]
    assert source.read_bytes() == b"tampered\n"


def test_close_fails_closed_while_a_handler_is_still_running(tmp_path: Path) -> None:
    """A handler still executing a command must not be reported as a clean stop.

    ``BridgeSession`` only keeps the project lock when ``close()`` signals
    failure. Reporting success here lets it kill Ren'Py, remove the artifacts and
    release the lock while the surviving handler can still reach
    ``atomic_write_file`` and publish source into a stopped session.
    """
    project, _source = _make_project(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class _BlockingProbe(_Probe):
        def observe(self, runtime_key: dict[str, Any], *, deadline: float) -> dict[str, Any]:
            entered.set()
            release.wait(timeout=10.0)
            return super().observe(runtime_key, deadline=deadline)

    observation = _base_observation()
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(_BlockingProbe(observe_reply=observation))
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=5.0) as sock:
            auth = _auth(sock, endpoint)
            # Send without reading: the handler must still be inside the command
            # when close() runs.
            _send_json(
                sock,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "connection_id": auth["connection_id"],
                    "request_id": "an-blocking",
                    "command": "analyze_target",
                    "payload": {"observation": observation},
                },
            )
            assert entered.wait(timeout=5.0), "handler never entered the probe"

            with pytest.raises(EditorError) as excinfo:
                coordinator.close(timeout=0.2)
            assert excinfo.value.code == "SHUTDOWN_INCOMPLETE"
            assert excinfo.value.details["active_commands"] == 1

            # Still blocked: a second attempt must fail again. This is what proves
            # the survivor stayed tracked — the handler discards itself from
            # _connection_threads in its own finally, so once released a retry
            # would succeed even if the first close() had dropped the reference.
            with pytest.raises(EditorError) as retry_excinfo:
                coordinator.close(timeout=0.2)
            assert retry_excinfo.value.code == "SHUTDOWN_INCOMPLETE"
            assert retry_excinfo.value.details["active_commands"] == 1

        release.set()
        assert coordinator.close(timeout=10.0)["transactions"] == {}
    finally:
        release.set()
        coordinator.close(timeout=10.0)


class _TimeoutProbeSocket:
    """Socket stand-in that records settimeout without performing I/O."""

    def __init__(self, initial_timeout: float | None) -> None:
        self._timeout = initial_timeout
        self.settimeout_calls: list[float | None] = []

    def gettimeout(self) -> float | None:
        return self._timeout

    def settimeout(self, value: float | None) -> None:
        self.settimeout_calls.append(value)
        self._timeout = value


def test_commit_helper_raises_and_restores_socket_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    sock = _TimeoutProbeSocket(initial_timeout=2.0)
    auth = {"connection_id": "c1", "session_id": "s1"}
    analysis = {
        "result": {
            "analysis_id": "a1",
            "source_key": {"path": "script.rpy", "line": 2, "baseline_sha256": "deadbeef"},
        }
    }
    module = sys.modules[__name__]

    def fake_send(target: Any, payload: dict[str, Any]) -> None:
        assert target is sock
        assert target.gettimeout() == _COMMIT_SOCKET_TIMEOUT_SECONDS
        assert payload["command"] == "commit"

    def fake_recv(target: Any) -> dict[str, Any]:
        assert target is sock
        assert target.gettimeout() == _COMMIT_SOCKET_TIMEOUT_SECONDS
        return {"ok": True, "result": {"transaction_id": "tx", "state": "published"}}

    monkeypatch.setattr(module, "_send_json", fake_send)
    monkeypatch.setattr(module, "_recv_json", fake_recv)

    reply = _commit(sock, auth, analysis, x=1, y=2, request_id="co-timeout-budget")
    assert reply["ok"] is True
    assert sock.settimeout_calls == [_COMMIT_SOCKET_TIMEOUT_SECONDS, 2.0]
    assert sock.gettimeout() == 2.0


def test_commit_helper_keeps_higher_existing_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    existing = _COMMIT_SOCKET_TIMEOUT_SECONDS + 10.0
    sock = _TimeoutProbeSocket(initial_timeout=existing)
    auth = {"connection_id": "c1", "session_id": "s1"}
    analysis = {
        "result": {
            "analysis_id": "a1",
            "source_key": {"path": "script.rpy", "line": 2, "baseline_sha256": "deadbeef"},
        }
    }
    module = sys.modules[__name__]

    def fake_send(target: Any, payload: dict[str, Any]) -> None:
        assert target.gettimeout() == existing

    def fake_recv(target: Any) -> dict[str, Any]:
        assert target.gettimeout() == existing
        return {"ok": True, "result": {"transaction_id": "tx", "state": "published"}}

    monkeypatch.setattr(module, "_send_json", fake_send)
    monkeypatch.setattr(module, "_recv_json", fake_recv)

    reply = _commit(sock, auth, analysis, x=1, y=2, request_id="co-timeout-keep")
    assert reply["ok"] is True
    assert sock.settimeout_calls == [existing, existing]
    assert sock.gettimeout() == existing


def test_commit_helper_restores_timeout_when_recv_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    sock = _TimeoutProbeSocket(initial_timeout=None)
    auth = {"connection_id": "c1", "session_id": "s1"}
    analysis = {
        "result": {
            "analysis_id": "a1",
            "source_key": {"path": "script.rpy", "line": 2, "baseline_sha256": "deadbeef"},
        }
    }
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_send_json", lambda *_args, **_kwargs: None)

    def boom(_target: Any) -> dict[str, Any]:
        raise TimeoutError("simulated recv failure")

    monkeypatch.setattr(module, "_recv_json", boom)

    with pytest.raises(TimeoutError, match="simulated recv failure"):
        _commit(sock, auth, analysis, x=1, y=2, request_id="co-timeout-restore")

    assert sock.settimeout_calls[0] == _COMMIT_SOCKET_TIMEOUT_SECONDS
    assert sock.settimeout_calls[-1] is None
    assert sock.gettimeout() is None


def test_send_json_swallows_closed_peer_errors(tmp_path: Path) -> None:
    project, _ = _make_project(tmp_path)
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))

    class _DeadPeer:
        def sendall(self, _data: bytes) -> None:
            raise BrokenPipeError(32, "Broken pipe")

    # Must not raise: closed-peer errors are expected after a client timeout.
    coordinator._send_json(_DeadPeer(), {"ok": True})  # type: ignore[arg-type]

    class _ResetPeer:
        def sendall(self, _data: bytes) -> None:
            raise ConnectionResetError(104, "Connection reset by peer")

    coordinator._send_json(_ResetPeer(), {"ok": True})  # type: ignore[arg-type]

    class _AbortedPeer:
        def sendall(self, _data: bytes) -> None:
            raise ConnectionAbortedError(53, "Software caused connection abort")

    coordinator._send_json(_AbortedPeer(), {"ok": True})  # type: ignore[arg-type]

    class _UnexpectedPeer:
        def sendall(self, _data: bytes) -> None:
            raise OSError(22, "Invalid argument")

    with pytest.raises(OSError, match="Invalid argument"):
        coordinator._send_json(_UnexpectedPeer(), {"ok": True})  # type: ignore[arg-type]
