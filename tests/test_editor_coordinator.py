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
from renforge.editor.source import peek_statement_kind
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
    w: int | None = None,
    h: int | None = None,
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
                            **({"w": w, "h": h} if w is not None and h is not None else {}),
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


def _wait_for_commit_state(
    sock: socket.socket,
    auth: dict[str, Any],
    transaction_id: str,
    expected_state: str,
    *,
    timeout: float = 3.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    attempt = 0
    last_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_status = _commit_status(
            sock,
            auth,
            transaction_id,
            request_id=f"wait-{transaction_id}-{attempt}",
        )
        assert last_status["ok"] is True
        if last_status["result"]["state"] == expected_state:
            return last_status
        attempt += 1
        time.sleep(0.05)
    raise AssertionError(
        f"transaction {transaction_id} did not reach {expected_state!r}: {last_status!r}"
    )


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
                    "layout-container",
                    lambda o: o["runtime_key"]["ancestry"].insert(
                        1,
                        {
                            **o["runtime_key"]["ancestry"][1],
                            "type": "MultiBox",
                            "layout": "vertical",
                        },
                    ),
                    "CONTAINER_POSITION_UNSUPPORTED",
                ),
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
                    # Issue #44 unlocked a single viewport; two compose two
                    # scroll offsets and were never measured.
                    "nested-viewport-ancestor",
                    lambda o: o["runtime_key"].__setitem__(
                        "ancestry",
                        [
                            o["runtime_key"]["ancestry"][0],
                            {**o["runtime_key"]["ancestry"][0], "index": 1, "type": "Viewport"},
                            {**o["runtime_key"]["ancestry"][0], "index": 2, "type": "Viewport"},
                            o["runtime_key"]["ancestry"][1],
                        ],
                    ),
                    "NESTED_VIEWPORT_UNSUPPORTED",
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
                    # Issue #45 unlocks pure transform_crop; composite stays locked.
                    "transform-crop-composite-state",
                    lambda o: o["runtime_key"]["ancestry"].__setitem__(
                        1,
                        {
                            **o["runtime_key"]["ancestry"][1],
                            "crop_state": "transform_crop_composite",
                        },
                    ),
                    "TRANSFORM_CROP_COMPOSITE_UNSUPPORTED",
                ),
            ]

            for index, (_name, mutate, expected_code) in enumerate(cases, start=1):
                sample = json.loads(json.dumps(observation))
                mutate(sample)
                reply = _analyze(sock, auth, sample, request_id=f"an-{index}")
                assert reply["ok"] is True
                assert reply["result"]["capabilities"] == {"move": False, "resize": False}
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


def test_button_statement_dispatches_through_analyze_and_commit(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    source.write_text(
        "screen test_screen:\n"
        '    button id "button_target" xpos 12 ypos 10:\n'
        '        text "Child content" xpos 7\n'
        "        action NullAction()\n",
        encoding="utf-8",
    )
    observation = _base_observation()
    observation["runtime_key"]["widget_id"] = "button_target"
    observation["rect"] = [12, 10, 120, 40]
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-button-frame",
            "object_id": "obj-independent-button",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-button")
            assert analysis["ok"] is True
            assert analysis["result"]["lock_reason"] is None
            assert analysis["result"]["original_position"] == [12, 10]
            assert analysis["result"]["source_key"]["statement_kind"] == "button"

            commit = _commit(sock, auth, analysis, x=333, y=444, request_id="co-button")
            assert commit["ok"] is True
            assert source.read_text(encoding="utf-8") == (
                "screen test_screen:\n"
                '    button id "button_target" xpos 333 ypos 444:\n'
                '        text "Child content" xpos 7\n'
                "        action NullAction()\n"
            )
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


def test_shadow_rejects_special_files_and_removes_partial_copy(tmp_path: Path) -> None:
    import os

    from renforge.editor.shadow import build_shadow_project

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs unavailable on this platform")
    project, _source = _make_project(tmp_path)
    special = project.root / "game" / "special.pipe"
    os.mkfifo(special)
    shadow_root = tmp_path / "shadow-special"

    with pytest.raises(EditorError) as excinfo:
        build_shadow_project(project, shadow_root=shadow_root, staged_replacements={})

    assert excinfo.value.code == "SHADOW_SPECIAL_FILE"
    assert not shadow_root.exists()


def test_shadow_enforces_file_quota_and_removes_partial_copy(
    monkeypatch, tmp_path: Path
) -> None:
    import renforge.editor.shadow as shadow

    project, _source = _make_project(tmp_path)
    (project.root / "extra.txt").write_text("extra\n", encoding="utf-8")
    shadow_root = tmp_path / "shadow-quota"
    monkeypatch.setattr(shadow, "MAX_SHADOW_FILES", 1)

    with pytest.raises(EditorError) as excinfo:
        shadow.build_shadow_project(
            project,
            shadow_root=shadow_root,
            staged_replacements={},
        )

    assert excinfo.value.code == "SHADOW_QUOTA_EXCEEDED"
    assert not shadow_root.exists()


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

            status = _wait_for_commit_state(sock, auth, tx, "rolled_back")
            assert source.read_bytes() == baseline

            analysis_2 = _analyze(sock, auth, observation, request_id="an-conflict")
            commit_2 = _commit(sock, auth, analysis_2, x=101, y=102, request_id="co-conflict")
            tx_2 = commit_2["result"]["transaction_id"]
            source.write_text("external\n", encoding="utf-8")

            conflict = _wait_for_commit_state(sock, auth, tx_2, "rollback_conflict")
            assert conflict["result"]["uncertain_paths"] == ["script.rpy"]
            assert source.read_text(encoding="utf-8") == "external\n"
    finally:
        coordinator.close()


class _RaisingAttestProbe(_Probe):
    """Mirrors BridgeRuntimeProbe: a bridge refusal arrives as a raised error."""

    def attest(self, **kwargs: Any) -> dict[str, Any]:
        super().attest(**kwargs)
        raise EditorError("RUNTIME_PROBE_FAILED", "TARGET_POSITION_MISMATCH")


def test_reload_handshake_rolls_back_when_attestation_raises(tmp_path: Path) -> None:
    """A refused attestation must restore the file before the failure is reported.

    The bridge signals a refusal by raising, not by returning a falsy reply, so
    the rollback has to sit on the exception path too. Otherwise the published
    bytes stay in the author's file until the attestation timer fires.
    """
    project, source = _make_project(tmp_path)
    original_text = source.read_text(encoding="utf-8")
    observation = _base_observation(script_generation=30)
    probe = _RaisingAttestProbe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-30",
            "object_id": "obj-independent-30",
        }
    )
    # Long timeout: the rollback must not depend on the timer firing.
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=120.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=5.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-attest-raise")
            commit = _commit(sock, auth, analysis, x=333, y=444, request_id="co-attest-raise")
            assert commit["ok"] is True
            assert "xpos 333 ypos 444" in source.read_text(encoding="utf-8")

            _send_json(
                sock,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "connection_id": auth["connection_id"],
                    "request_id": "hs-attest-raise",
                    "command": "reload_handshake",
                    "payload": {
                        "transaction_id": commit["result"]["transaction_id"],
                        "script_generation": 31,
                    },
                },
            )
            handshake = _recv_json(sock)
            assert handshake["ok"] is False
            assert probe.attest_calls
            assert source.read_text(encoding="utf-8") == original_text
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


def test_say_what_style_position_commit_preserves_crlf_gui_source(tmp_path: Path) -> None:
    root = tmp_path / "project"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    screens = game_dir / "screens.rpy"
    screens.write_text(
        'screen say(who, what):\n'
        '    text what id "what" style "say_dialogue"\n'
        '\n'
        'style say_dialogue:\n'
        '    xpos gui.dialogue_xpos\n'
        '    ypos gui.dialogue_ypos\n',
        encoding="utf-8",
    )
    gui = game_dir / "gui.rpy"
    original = (
        b"define gui.dialogue_xpos = gui.scale(268)\r\n"
        b"define gui.dialogue_ypos = gui.scale(50)\r\n"
    )
    gui.write_bytes(original)
    observation = {
        "runtime_key": {
            "screen": "say",
            "invocation_path": "say",
            "widget_id": "what",
            "source_location": ["screens.rpy", 2],
            "instance_discriminator": {"kind": "singleton", "instance_count": 1},
            "ancestry": [
                {
                    "index": 0,
                    "type": "ScreenDisplayable",
                    "source_location": ["screens.rpy", 1],
                    "screen_owner": "say",
                    "crop_state": "none",
                    "editor_owned": False,
                },
                {
                    "index": 1,
                    "type": "Text",
                    "source_location": ["screens.rpy", 2],
                    "screen_owner": "say",
                    "crop_state": "none",
                    "editor_owned": False,
                },
            ],
        },
        "rect": [268, 585, 500, 30],
        "measurement_method": "scene_tree_text",
        "frame_id": "say-frame",
        "script_generation": 4,
        "object_id": "say-what",
    }
    probe = _Probe(observe_reply={**observation, "frame_id": "say-independent"})
    coordinator = EditorCoordinator(
        RenpyProject(root),
        _make_sdk(tmp_path),
        attestation_timeout=2.0,
    )
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-say-crlf")
            assert analysis["ok"] is True
            assert analysis["result"]["capabilities"]["move"] is True

            commit = _commit(
                sock,
                auth,
                analysis,
                x=288,
                y=80,
                request_id="co-say-crlf",
            )
            assert commit["ok"] is True
            assert gui.read_bytes() == original.replace(b"268", b"288").replace(b"50", b"80")
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
            assert reply["result"]["capabilities"] == {"move": False, "resize": False}
    finally:
        coordinator.close()


def test_repetition_lock_outranks_a_source_form_lock(tmp_path: Path) -> None:
    """Issue #42: a repeated statement locks for being repeated, not for its form.

    Both gates are true for a repeated `use` whose position is an expression.
    Reporting the source-form reason would hide the one that actually blocks the
    edit, and would make the message depend on which gate ran last.
    """
    project, source = _make_project(tmp_path)
    source.write_text(
        "screen test_screen:\n"
        '    textbutton "Play" id "start_btn" xpos left_x ypos 10 action NullAction()\n',
        encoding="utf-8",
    )
    observation = _base_observation()
    observation["runtime_key"]["instance_discriminator"] = {
        "kind": "use",
        "instance_count": 2,
        "repeated": True,
        "instance_key": ["0", "17", "12"],
    }
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-42",
            "object_id": "obj-independent-42",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-repeat-precedence")
            assert analysis["ok"] is True
            assert analysis["result"]["lock_reason"]["code"] == "REPEATED_USE_UNSUPPORTED"
            assert analysis["result"]["capabilities"] == {"move": False, "resize": False}
    finally:
        coordinator.close()


def test_single_viewport_ancestor_no_longer_locks(tmp_path: Path) -> None:
    """Issue #44: one viewport is editable; the engine offsets its focus rects."""
    project, _ = _make_project(tmp_path)
    observation = _base_observation()
    observation["runtime_key"]["ancestry"].insert(
        1,
        {
            **observation["runtime_key"]["ancestry"][0],
            "index": 1,
            "type": "Viewport",
            "crop_state": "viewport",
        },
    )
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-44",
            "object_id": "obj-independent-44",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-viewport")
            assert analysis["ok"] is True
            assert analysis["result"]["lock_reason"] is None
            assert analysis["result"]["capabilities"] == {"move": True, "resize": False}
    finally:
        coordinator.close()


def test_pure_transform_crop_ancestor_no_longer_locks(tmp_path: Path) -> None:
    """Issue #45: pure Transform(crop=) is editable; Crop() is the same runtime object."""
    project, _ = _make_project(tmp_path)
    observation = _base_observation()
    observation["runtime_key"]["ancestry"].insert(
        1,
        {
            **observation["runtime_key"]["ancestry"][0],
            "index": 1,
            "type": "Transform",
            "crop_state": "transform_crop",
        },
    )
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-45",
            "object_id": "obj-independent-45",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-crop")
            assert analysis["ok"] is True
            assert analysis["result"]["lock_reason"] is None
            assert analysis["result"]["capabilities"] == {"move": True, "resize": False}
    finally:
        coordinator.close()


def test_transform_crop_composite_stays_locked(tmp_path: Path) -> None:
    """Issue #45: crop+rotate/zoom stays locked under a distinct code (#46 scope)."""
    project, _ = _make_project(tmp_path)
    observation = _base_observation()
    observation["runtime_key"]["ancestry"].insert(
        1,
        {
            **observation["runtime_key"]["ancestry"][0],
            "index": 1,
            "type": "Transform",
            "crop_state": "transform_crop_composite",
        },
    )
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-45b",
            "object_id": "obj-independent-45b",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-crop-composite")
            assert analysis["ok"] is True
            assert analysis["result"]["capabilities"] == {"move": False, "resize": False}
            assert analysis["result"]["lock_reason"]["code"] == "TRANSFORM_CROP_COMPOSITE_UNSUPPORTED"
    finally:
        coordinator.close()


def test_transform_crop_partial_stays_locked(tmp_path: Path) -> None:
    """Issue #45: partially crop-clipped targets stay locked (Codex P1)."""
    project, _ = _make_project(tmp_path)
    observation = _base_observation()
    observation["runtime_key"]["ancestry"].insert(
        1,
        {
            **observation["runtime_key"]["ancestry"][0],
            "index": 1,
            "type": "Transform",
            "crop_state": "transform_crop_partial",
        },
    )
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-45c",
            "object_id": "obj-independent-45c",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-crop-partial")
            assert analysis["ok"] is True
            assert analysis["result"]["capabilities"] == {"move": False, "resize": False}
            assert analysis["result"]["lock_reason"]["code"] == "TRANSFORM_CROP_PARTIAL_UNSUPPORTED"
    finally:
        coordinator.close()


def test_transform_crop_unproven_stays_locked(tmp_path: Path) -> None:
    """Issue #45: fail closed when full-visibility cannot be measured (Codex P2)."""
    project, _ = _make_project(tmp_path)
    observation = _base_observation()
    observation["runtime_key"]["ancestry"].insert(
        1,
        {
            **observation["runtime_key"]["ancestry"][0],
            "index": 1,
            "type": "Transform",
            "crop_state": "transform_crop_unproven",
        },
    )
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-45d",
            "object_id": "obj-independent-45d",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-crop-unproven")
            assert analysis["ok"] is True
            assert analysis["result"]["capabilities"] == {"move": False, "resize": False}
            assert analysis["result"]["lock_reason"]["code"] == "TRANSFORM_CROP_UNPROVEN"
    finally:
        coordinator.close()


def test_nested_transform_crop_stays_locked(tmp_path: Path) -> None:
    """Issue #45: two crop transforms in ancestry stay locked (Codex P2)."""
    project, _ = _make_project(tmp_path)
    observation = _base_observation()
    crop_node = {
        **observation["runtime_key"]["ancestry"][0],
        "type": "Transform",
        "crop_state": "transform_crop",
    }
    observation["runtime_key"]["ancestry"] = [
        observation["runtime_key"]["ancestry"][0],
        {**crop_node, "index": 1},
        {**crop_node, "index": 2},
        observation["runtime_key"]["ancestry"][1],
    ]
    probe = _Probe(
        observe_reply={
            **observation,
            "frame_id": "independent-frame-45e",
            "object_id": "obj-independent-45e",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analysis = _analyze(sock, auth, observation, request_id="an-crop-nested")
            assert analysis["ok"] is True
            assert analysis["result"]["capabilities"] == {"move": False, "resize": False}
            assert analysis["result"]["lock_reason"]["code"] == "NESTED_TRANSFORM_CROP_UNSUPPORTED"
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
            assert analysis["result"]["capabilities"] == {"move": True, "resize": False}

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


@pytest.mark.parametrize(
    ("initial_timeout", "expected_during_commit"),
    [
        (2.0, _COMMIT_SOCKET_TIMEOUT_SECONDS),
        (_COMMIT_SOCKET_TIMEOUT_SECONDS + 10.0, _COMMIT_SOCKET_TIMEOUT_SECONDS + 10.0),
    ],
)
def test_commit_helper_raises_and_restores_socket_timeout(
    monkeypatch: pytest.MonkeyPatch,
    initial_timeout: float,
    expected_during_commit: float,
) -> None:
    import sys

    sock = _TimeoutProbeSocket(initial_timeout=initial_timeout)
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
        assert target.gettimeout() == expected_during_commit
        assert payload["command"] == "commit"

    def fake_recv(target: Any) -> dict[str, Any]:
        assert target is sock
        assert target.gettimeout() == expected_during_commit
        return {"ok": True, "result": {"transaction_id": "tx", "state": "published"}}

    monkeypatch.setattr(module, "_send_json", fake_send)
    monkeypatch.setattr(module, "_recv_json", fake_recv)

    reply = _commit(sock, auth, analysis, x=1, y=2, request_id="co-timeout-budget")
    assert reply["ok"] is True
    assert sock.settimeout_calls == [expected_during_commit, initial_timeout]
    assert sock.gettimeout() == initial_timeout


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


@pytest.mark.parametrize(
    "error",
    [
        BrokenPipeError(32, "Broken pipe"),
        ConnectionResetError(104, "Connection reset by peer"),
        ConnectionAbortedError(53, "Software caused connection abort"),
    ],
)
def test_send_json_swallows_closed_peer_errors(tmp_path: Path, error: OSError) -> None:
    project, _ = _make_project(tmp_path)
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))

    class _ClosedPeer:
        def sendall(self, _data: bytes) -> None:
            raise error

    # Must not raise: closed-peer errors are expected after a client timeout.
    coordinator._send_json(_ClosedPeer(), {"ok": True})  # type: ignore[arg-type]


def test_read_frame_treats_connection_reset_as_eof(tmp_path: Path) -> None:
    project, _ = _make_project(tmp_path)
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))

    class _ResetPeer:
        def readline(self, _limit: int) -> bytes:
            raise ConnectionResetError(54, "Connection reset by peer")

    assert coordinator._read_frame(_ResetPeer(), 1024) == (None, "EOF")


def test_send_json_propagates_unexpected_oserror(tmp_path: Path) -> None:
    project, _ = _make_project(tmp_path)
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))

    class _UnexpectedPeer:
        def sendall(self, _data: bytes) -> None:
            raise OSError(22, "Invalid argument")

    with pytest.raises(OSError, match="Invalid argument"):
        coordinator._send_json(_UnexpectedPeer(), {"ok": True})  # type: ignore[arg-type]



def _make_imagebutton_project(tmp_path: Path) -> tuple[RenpyProject, Path]:
    root = tmp_path / "project_img"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    imagebutton id "start_btn" idle Solid("#4c6ef5", xysize=(80, 48)) '
        "xpos 12 ypos 10 action NullAction()\n",
        encoding="utf-8",
    )
    return RenpyProject(root), source


def test_analyze_and_commit_imagebutton_statement(tmp_path: Path) -> None:
    project, source = _make_imagebutton_project(tmp_path)
    observation = _base_observation(script_generation=12)
    observation["runtime_key"]["ancestry"][1]["type"] = "ImageButton"
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-img",
            "object_id": "obj-independent-img",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-img")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["capabilities"] == {"move": True, "resize": False}
            assert result["source_key"]["statement_kind"] == "imagebutton"
            assert result["original_position"] == [12, 10]

            committed = _commit(sock, auth, analyzed, x=40, y=50, request_id="co-img")
            assert committed.get("ok") is True, (
                f"error={committed.get('error')!r} diagnostics={committed.get('diagnostics')!r} full={committed!r}"
            )
            assert committed["result"]["state"] == "published"
            assert "xpos 40 ypos 50" in source.read_text(encoding="utf-8")
            assert 'imagebutton id "start_btn"' in source.read_text(encoding="utf-8")

            _send_json(
                sock,
                {
                    "protocol": "renforge-editor",
                    "version": 1,
                    "connection_id": auth["connection_id"],
                    "request_id": "hs-img",
                    "command": "reload_handshake",
                    "payload": {
                        "transaction_id": committed["result"]["transaction_id"],
                        "script_generation": 13,
                    },
                },
            )
            handshake = _recv_json(sock)
            assert handshake["ok"] is True
            assert handshake["result"]["state"] == "committed"
    finally:
        coordinator.close()

    assert "xpos 40 ypos 50" in source.read_text(encoding="utf-8")


def test_commit_rejects_mismatched_statement_kind(tmp_path: Path) -> None:
    project, source = _make_imagebutton_project(tmp_path)
    observation = _base_observation(script_generation=12)
    observation["runtime_key"]["ancestry"][1]["type"] = "ImageButton"
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-mismatch",
            "object_id": "obj-independent-mismatch",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-mismatch")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            mismatched_source_key = dict(result["source_key"])
            mismatched_source_key["statement_kind"] = "textbutton"
            with coordinator._lock:
                record = coordinator._analyses[result["analysis_id"]]
                record.source_key = mismatched_source_key
            analyzed["result"]["source_key"] = mismatched_source_key

            committed = _commit(sock, auth, analyzed, x=40, y=50, request_id="co-mismatch")
            assert committed["ok"] is False
            assert committed["error"]["code"] == "STATEMENT_KIND_MISMATCH"
            assert committed["error"]["message"] == "source_key statement_kind does not match source line"
            assert "xpos 12 ypos 10" in source.read_text(encoding="utf-8")
    finally:
        coordinator.close()


def test_analyze_rejects_unsupported_statement_kind(tmp_path: Path) -> None:
    project, source = _make_project(tmp_path)
    source.write_text(
        "screen test_screen:\n"
        '    frame id "start_btn" xpos 12 ypos 10:\n'
        '        text "x"\n',
        encoding="utf-8",
    )
    observation = _base_observation()
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-frame",
            "object_id": "obj-independent-frame",
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            reply = _analyze(sock, auth, observation, request_id="an-frame")
            assert reply["ok"] is True
            assert reply["result"]["capabilities"] == {"move": False, "resize": False}
            assert reply["result"]["lock_reason"]["code"] == "STATEMENT_KIND_MISMATCH"
    finally:
        coordinator.close()


def _make_bar_project(tmp_path: Path) -> tuple[RenpyProject, Path]:
    root = tmp_path / "project_bar"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    bar value StaticValue(50) range 100 id "start_btn" '
        "xpos 12 ypos 10 xsize 40 ysize 10\n",
        encoding="utf-8",
    )
    return RenpyProject(root), source


def test_analyze_and_commit_bar_statement(tmp_path: Path) -> None:
    project, source = _make_bar_project(tmp_path)
    observation = _base_observation(script_generation=12)
    observation["runtime_key"]["ancestry"][1]["type"] = "Bar"
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-bar",
            "object_id": "obj-independent-bar",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-bar")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["capabilities"] == {"move": True, "resize": True}
            assert result["source_key"]["statement_kind"] == "bar"
            assert result["source_key"]["size_mode"] == "xsize_ysize"
            assert result["original_position"] == [12, 10]
            assert result["original_size"] == [40, 10]

            committed = _commit(sock, auth, analyzed, x=40, y=50, request_id="co-bar")
            assert committed["ok"] is True
            assert committed["result"]["state"] == "published"
            text = source.read_text(encoding="utf-8")
            assert "xpos 40 ypos 50" in text
            assert 'bar value StaticValue(50) range 100 id "start_btn"' in text
            assert "xsize 40 ysize 10" in text
    finally:
        coordinator.close()




def test_analyze_and_commit_bar_resize(tmp_path: Path) -> None:
    project, source = _make_bar_project(tmp_path)
    observation = _base_observation(script_generation=12)
    observation["runtime_key"]["ancestry"][1]["type"] = "Bar"
    # Independent focus rect must carry positive width/height for resize unlock.
    observation["rect"] = [12, 10, 40, 10]
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-bar-resize",
            "object_id": "obj-independent-bar-resize",
            "rect": [12, 10, 40, 10],
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-bar-resize")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["capabilities"] == {"move": True, "resize": True}
            assert result["original_size"] == [40, 10]

            committed = _commit(
                sock,
                auth,
                analyzed,
                x=12,
                y=10,
                w=80,
                h=18,
                request_id="co-bar-resize",
            )
            assert committed["ok"] is True
            assert committed["result"]["state"] == "published"
            text = source.read_text(encoding="utf-8")
            assert "xpos 12 ypos 10" in text
            assert "xsize 80 ysize 18" in text
    finally:
        coordinator.close()


def test_bar_resize_commit_rejects_unmeasured_runtime_size(tmp_path: Path) -> None:
    project, source = _make_bar_project(tmp_path)
    baseline = source.read_bytes()
    observation = _base_observation(script_generation=12)
    observation["runtime_key"]["ancestry"][1]["type"] = "Bar"
    observation["rect"] = [12, 10, 0, 0]
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-bar-unmeasured-size",
            "object_id": "obj-independent-bar-unmeasured-size",
            "rect": [12, 10, 0, 0],
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-bar-unmeasured")
            assert analyzed["ok"] is True
            assert analyzed["result"]["capabilities"] == {"move": True, "resize": False}

            committed = _commit(
                sock,
                auth,
                analyzed,
                x=12,
                y=10,
                w=80,
                h=18,
                request_id="co-bar-unmeasured",
            )
            assert committed["ok"] is False
            assert committed["error"]["code"] == "ANALYSIS_RESIZE_UNSUPPORTED"
            assert source.read_bytes() == baseline
    finally:
        coordinator.close()


def test_bar_resize_locked_without_authored_size(tmp_path: Path) -> None:
    root = tmp_path / "project_bar_no_size"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    bar value StaticValue(50) range 100 id "start_btn" '
        "xpos 12 ypos 10\n",
        encoding="utf-8",
    )
    project = RenpyProject(root)
    observation = _base_observation(script_generation=12)
    observation["runtime_key"]["ancestry"][1]["type"] = "Bar"
    observation["rect"] = [12, 10, 40, 10]
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-bar-nosize",
            "object_id": "obj-independent-bar-nosize",
            "rect": [12, 10, 40, 10],
        }
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-bar-nosize")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["capabilities"] == {"move": True, "resize": False}
            assert result["source_key"].get("resize_lock_reason", {}).get("code") == "BAR_SIZE_NOT_DIRECTLY_AUTHORED"
    finally:
        coordinator.close()




def test_bar_resize_locked_xysize_and_constraint_forms(tmp_path: Path) -> None:
    cases = [
        (
            '    bar value StaticValue(50) range 100 id "start_btn" '
            "xpos 12 ypos 10 xysize (40, 10)\n",
            "BAR_XYSIZE_UNSUPPORTED",
        ),
        (
            '    bar value StaticValue(50) range 100 id "start_btn" '
            "xpos 12 ypos 10 xsize 40 ysize 10 xmaximum 100\n",
            "BAR_SIZE_CONSTRAINT_UNSUPPORTED",
        ),
    ]
    for index, (line, code) in enumerate(cases):
        root = tmp_path / f"project_bar_resize_lock_{index}"
        game_dir = root / "game"
        game_dir.mkdir(parents=True)
        source = game_dir / "script.rpy"
        source.write_text("screen test_screen:\n" + line, encoding="utf-8")
        project = RenpyProject(root)
        observation = _base_observation(script_generation=12)
        observation["runtime_key"]["ancestry"][1]["type"] = "Bar"
        observation["rect"] = [12, 10, 40, 10]
        probe = _Probe(
            observe_reply={
                **json.loads(json.dumps(observation)),
                "frame_id": f"independent-frame-bar-lock-{index}",
                "object_id": f"obj-independent-bar-lock-{index}",
                "rect": [12, 10, 40, 10],
            }
        )
        case_tmp = tmp_path / f"sdk_case_{index}"
        case_tmp.mkdir()
        coordinator = EditorCoordinator(project, _make_sdk(case_tmp), attestation_timeout=2.0)
        coordinator.attach_runtime_probe(probe)
        endpoint = coordinator.start()
        try:
            with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
                auth = _auth(sock, endpoint)
                analyzed = _analyze(sock, auth, observation, request_id=f"an-bar-lock-{index}")
                assert analyzed["ok"] is True
                result = analyzed["result"]
                assert result["lock_reason"] is None
                assert result["capabilities"] == {"move": True, "resize": False}
                assert result["source_key"].get("size_mode") is None
                assert result["source_key"].get("resize_lock_reason", {}).get("code") == code
        finally:
            coordinator.close()


def _make_vbar_project(tmp_path: Path) -> tuple[RenpyProject, Path]:
    root = tmp_path / "project_vbar"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    vbar value StaticValue(50) range 100 id "start_btn" '
        "xpos 12 ypos 10 xsize 24 ysize 200\n",
        encoding="utf-8",
    )
    return RenpyProject(root), source


def test_analyze_and_commit_vbar_statement_uses_source_kind(tmp_path: Path) -> None:
    project, source = _make_vbar_project(tmp_path)
    observation = _base_observation(script_generation=12)
    observation["runtime_key"]["ancestry"][1]["type"] = "Bar"
    observation["runtime_key"]["node_type"] = "bar"
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-vbar",
            "object_id": "obj-independent-vbar",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-vbar")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["capabilities"] == {"move": True, "resize": False}
            assert result["source_key"]["statement_kind"] == "vbar"
            assert result["original_position"] == [12, 10]

            committed = _commit(sock, auth, analyzed, x=40, y=50, request_id="co-vbar")
            assert committed["ok"] is True
            assert committed["result"]["state"] == "published"
            assert source.read_text(encoding="utf-8") == (
                "screen test_screen:\n"
                '    vbar value StaticValue(50) range 100 id "start_btn" '
                "xpos 40 ypos 50 xsize 24 ysize 200\n"
            )
    finally:
        coordinator.close()


def test_analyze_vbar_block_header_stays_locked(tmp_path: Path) -> None:
    project, source = _make_vbar_project(tmp_path)
    source.write_text(
        "screen test_screen:\n"
        '    vbar value StaticValue(50) range 100 id "start_btn" '
        "xpos 12 ypos 10 xsize 24 ysize 200:\n"
        "        null\n",
        encoding="utf-8",
    )
    observation = _base_observation(script_generation=12)
    observation["runtime_key"]["ancestry"][1]["type"] = "Bar"
    observation["runtime_key"]["node_type"] = "bar"
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-vbar-block",
            "object_id": "obj-independent-vbar-block",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-vbar-block")
            assert analyzed["ok"] is True
            assert analyzed["result"]["capabilities"] == {"move": False, "resize": False}
            assert analyzed["result"]["lock_reason"]["code"] == "MULTILINE_STATEMENT_REJECTED"
    finally:
        coordinator.close()


def _make_slider_project(tmp_path: Path) -> tuple[RenpyProject, Path]:
    root = tmp_path / "project_slider"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    bar value StaticValue(50) range 100 style "slider" id "start_btn" '
        "xpos 12 ypos 10 xsize 240 ysize 24\n",
        encoding="utf-8",
    )
    return RenpyProject(root), source


def test_analyze_and_commit_slider_statement_uses_source_kind(tmp_path: Path) -> None:
    project, source = _make_slider_project(tmp_path)
    observation = _base_observation(script_generation=12)
    observation["runtime_key"]["ancestry"][1]["type"] = "Bar"
    observation["runtime_key"]["node_type"] = "bar"
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-slider",
            "object_id": "obj-independent-slider",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-slider")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["capabilities"] == {"move": True, "resize": False}
            # Adapter identity is style-based; source keyword remains "bar".
            assert result["source_key"]["statement_kind"] == "slider"
            assert result["original_position"] == [12, 10]

            committed = _commit(sock, auth, analyzed, x=40, y=50, request_id="co-slider")
            assert committed["ok"] is True
            assert committed["result"]["state"] == "published"
            assert source.read_text(encoding="utf-8") == (
                "screen test_screen:\n"
                '    bar value StaticValue(50) range 100 style "slider" id "start_btn" '
                "xpos 40 ypos 50 xsize 240 ysize 24\n"
            )
    finally:
        coordinator.close()


def test_analyze_slider_block_header_stays_locked(tmp_path: Path) -> None:
    project, source = _make_slider_project(tmp_path)
    source.write_text(
        "screen test_screen:\n"
        '    bar value StaticValue(50) range 100 style "slider" id "start_btn" '
        "xpos 12 ypos 10 xsize 240 ysize 24:\n"
        "        null\n",
        encoding="utf-8",
    )
    observation = _base_observation(script_generation=12)
    observation["runtime_key"]["ancestry"][1]["type"] = "Bar"
    observation["runtime_key"]["node_type"] = "bar"
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-slider-block",
            "object_id": "obj-independent-slider-block",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-slider-block")
            assert analyzed["ok"] is True
            assert analyzed["result"]["capabilities"] == {"move": False, "resize": False}
            assert analyzed["result"]["lock_reason"]["code"] == "MULTILINE_STATEMENT_REJECTED"
    finally:
        coordinator.close()


def test_analyze_slider_locks_computed_style_container_without_runtime_type_inference(
    tmp_path: Path,
) -> None:
    project, source = _make_slider_project(tmp_path)
    cases = (
        (
            '    bar value StaticValue(50) range 100 style "slider" id "start_btn" '
            "xpos base_x ypos 10\n",
            "XPOS_LITERAL_REQUIRED",
            "slider",
            False,
            [
                {
                    "index": 0,
                    "type": "ScreenDisplayable",
                    "source_location": ["script.rpy", 1],
                    "screen_owner": "game",
                    "crop_state": "none",
                    "editor_owned": False,
                },
                {
                    "index": 1,
                    "type": "Bar",
                    "source_location": ["script.rpy", 2],
                    "screen_owner": "game",
                    "crop_state": "none",
                    "editor_owned": False,
                },
            ],
        ),
        (
            '    bar value StaticValue(50) range 100 style "slider" id "start_btn"\n',
            "BAR_STYLE_POSITION_UNSUPPORTED",
            "slider",
            False,
            [
                {
                    "index": 0,
                    "type": "ScreenDisplayable",
                    "source_location": ["script.rpy", 1],
                    "screen_owner": "game",
                    "crop_state": "none",
                    "editor_owned": False,
                },
                {
                    "index": 1,
                    "type": "Bar",
                    "source_location": ["script.rpy", 2],
                    "screen_owner": "game",
                    "crop_state": "none",
                    "editor_owned": False,
                },
            ],
        ),
        (
            '    bar value StaticValue(50) range 100 style "slider" id "start_btn" '
            "xpos 12 ypos 10\n",
            "CONTAINER_POSITION_UNSUPPORTED",
            "slider",
            True,
            [
                {
                    "index": 0,
                    "type": "ScreenDisplayable",
                    "source_location": ["script.rpy", 1],
                    "screen_owner": "game",
                    "crop_state": "none",
                    "editor_owned": False,
                },
                {
                    "index": 1,
                    "type": "VBox",
                    "source_location": ["script.rpy", 2],
                    "screen_owner": "game",
                    "crop_state": "none",
                    "editor_owned": False,
                    "layout": "vertical",
                },
                {
                    "index": 2,
                    "type": "Bar",
                    "source_location": ["script.rpy", 3],
                    "screen_owner": "game",
                    "crop_state": "none",
                    "editor_owned": False,
                },
            ],
        ),
    )
    sdk = _make_sdk(tmp_path)
    for index, (line, expected_code, expected_kind, source_key_present, ancestry) in enumerate(
        cases
    ):
        # Source keyword is always "bar"; adapter kind is style-specialized "slider".
        assert peek_statement_kind(line) == "bar"
        source.write_text("screen test_screen:\n" + line, encoding="utf-8")
        observation = _base_observation(script_generation=30 + index)
        observation["runtime_key"]["ancestry"] = ancestry
        observation["runtime_key"]["node_type"] = "bar"
        probe = _Probe(
            observe_reply={
                **json.loads(json.dumps(observation)),
                "frame_id": f"independent-frame-slider-lock-{index}",
                "object_id": f"obj-independent-slider-lock-{index}",
            }
        )
        coordinator = EditorCoordinator(project, sdk)
        coordinator.attach_runtime_probe(probe)
        endpoint = coordinator.start()
        try:
            with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
                auth = _auth(sock, endpoint)
                reply = _analyze(sock, auth, observation, request_id=f"an-slider-lock-{index}")
                assert reply["ok"] is True
                assert reply["result"]["capabilities"] == {"move": False, "resize": False}
                assert reply["result"]["lock_reason"]["code"] == expected_code
                source_key = reply["result"].get("source_key")
                if source_key_present:
                    assert source_key is not None
                    assert source_key["statement_kind"] == expected_kind
                else:
                    assert source_key is None
        finally:
            coordinator.close()


def test_analyze_bar_locks_computed_style_container_without_runtime_type_inference(
    tmp_path: Path,
) -> None:
    project, source = _make_bar_project(tmp_path)
    cases = (
        (
            '    bar value StaticValue(50) range 100 id "start_btn" xpos base_x ypos 10\n',
            "XPOS_LITERAL_REQUIRED",
            "bar",
            False,
            [{"index": 0, "type": "ScreenDisplayable", "source_location": ["script.rpy", 1],
              "screen_owner": "game", "crop_state": "none", "editor_owned": False},
             {"index": 1, "type": "Bar", "source_location": ["script.rpy", 2],
              "screen_owner": "game", "crop_state": "none", "editor_owned": False}],
        ),
        (
            '    bar value StaticValue(50) range 100 style "pos_style" id "start_btn"\n',
            "BAR_STYLE_POSITION_UNSUPPORTED",
            "bar",
            False,
            [{"index": 0, "type": "ScreenDisplayable", "source_location": ["script.rpy", 1],
              "screen_owner": "game", "crop_state": "none", "editor_owned": False},
             {"index": 1, "type": "Bar", "source_location": ["script.rpy", 2],
              "screen_owner": "game", "crop_state": "none", "editor_owned": False}],
        ),
        (
            '    bar value StaticValue(50) range 100 id "start_btn" xpos 12 ypos 10\n',
            "CONTAINER_POSITION_UNSUPPORTED",
            "bar",
            True,
            [
                {
                    "index": 0,
                    "type": "ScreenDisplayable",
                    "source_location": ["script.rpy", 1],
                    "screen_owner": "game",
                    "crop_state": "none",
                    "editor_owned": False,
                },
                {
                    "index": 1,
                    "type": "VBox",
                    "source_location": ["script.rpy", 2],
                    "screen_owner": "game",
                    "crop_state": "none",
                    "editor_owned": False,
                    "layout": "vertical",
                },
                {
                    "index": 2,
                    "type": "Bar",
                    "source_location": ["script.rpy", 3],
                    "screen_owner": "game",
                    "crop_state": "none",
                    "editor_owned": False,
                },
            ],
        ),
    )
    sdk = _make_sdk(tmp_path)
    for index, (line, expected_code, expected_kind, source_key_present, ancestry) in enumerate(cases):
        assert peek_statement_kind(line) == expected_kind
        source.write_text("screen test_screen:\n" + line, encoding="utf-8")
        observation = _base_observation(script_generation=20 + index)
        observation["runtime_key"]["ancestry"] = ancestry
        # Runtime node_type is always "bar" for both bar and vbar; host must not use it.
        observation["runtime_key"]["node_type"] = "bar"
        probe = _Probe(
            observe_reply={
                **json.loads(json.dumps(observation)),
                "frame_id": f"independent-frame-bar-lock-{index}",
                "object_id": f"obj-independent-bar-lock-{index}",
            }
        )
        coordinator = EditorCoordinator(project, sdk)
        coordinator.attach_runtime_probe(probe)
        endpoint = coordinator.start()
        try:
            with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
                auth = _auth(sock, endpoint)
                reply = _analyze(sock, auth, observation, request_id=f"an-bar-lock-{index}")
                assert reply["ok"] is True
                assert reply["result"]["capabilities"] == {"move": False, "resize": False}
                assert reply["result"]["lock_reason"]["code"] == expected_code
                source_key = reply["result"].get("source_key")
                if source_key_present:
                    assert source_key is not None
                    assert source_key["statement_kind"] == expected_kind
                else:
                    # Source analysis failed before source_key was recorded; the
                    # peeked keyword still proves bar/vbar were not conflated.
                    assert source_key is None
        finally:
            coordinator.close()

def _make_textbutton_block_project(tmp_path: Path) -> tuple[RenpyProject, Path]:
    root = tmp_path / "project_tb_block"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    textbutton "Play":\n'
        '        id "start_btn"\n'
        "        xpos 12\n"
        "        ypos 10\n"
        "        action NullAction()\n",
        encoding="utf-8",
    )
    return RenpyProject(root), source


def test_analyze_and_commit_textbutton_block_preserves_action_bytes(tmp_path: Path) -> None:
    project, source = _make_textbutton_block_project(tmp_path)
    observation = _base_observation(script_generation=12)
    # Runtime source_location points at the block header (line 2).
    observation["runtime_key"]["source_location"] = ["script.rpy", 2]
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-tb-block",
            "object_id": "obj-independent-tb-block",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-tb-block")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["capabilities"] == {"move": True, "resize": False}
            assert result["source_key"]["statement_kind"] == "textbutton"
            assert result["original_position"] == [12, 10]

            committed = _commit(sock, auth, analyzed, x=40, y=50, request_id="co-tb-block")
            assert committed["ok"] is True
            assert committed["result"]["state"] == "published"
            assert source.read_text(encoding="utf-8") == (
                "screen test_screen:\n"
                '    textbutton "Play":\n'
                '        id "start_btn"\n'
                "        xpos 40\n"
                "        ypos 50\n"
                "        action NullAction()\n"
            )
    finally:
        coordinator.close()


def test_analyze_textbutton_block_computed_position_stays_locked(tmp_path: Path) -> None:
    project, source = _make_textbutton_block_project(tmp_path)
    source.write_text(
        "screen test_screen:\n"
        '    textbutton "Play":\n'
        '        id "start_btn"\n'
        "        xpos base_x\n"
        "        ypos 10\n"
        "        action NullAction()\n",
        encoding="utf-8",
    )
    observation = _base_observation(script_generation=13)
    observation["runtime_key"]["source_location"] = ["script.rpy", 2]
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-tb-block-computed",
            "object_id": "obj-independent-tb-block-computed",
        },
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-tb-block-computed")
            assert analyzed["ok"] is True
            assert analyzed["result"]["capabilities"] == {"move": False, "resize": False}
            assert analyzed["result"]["lock_reason"]["code"] == "XPOS_LITERAL_REQUIRED"
    finally:
        coordinator.close()


def test_analyze_and_commit_textbutton_pos_preserves_form(tmp_path: Path) -> None:
    root = tmp_path / "project_pos"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    textbutton "Play" id "start_btn" pos (12, 10) action NullAction()\n',
        encoding="utf-8",
    )
    project = RenpyProject(root)
    observation = _base_observation(script_generation=12)
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-pos",
            "object_id": "obj-independent-pos",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-pos")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["capabilities"] == {"move": True, "resize": False}
            assert result["source_key"]["statement_kind"] == "textbutton"
            assert result["original_position"] == [12, 10]

            committed = _commit(sock, auth, analyzed, x=40, y=50, request_id="co-pos")
            assert committed["ok"] is True
            assert committed["result"]["state"] == "published"
            text = source.read_text(encoding="utf-8")
            assert text == (
                "screen test_screen:\n"
                '    textbutton "Play" id "start_btn" pos (40, 50) action NullAction()\n'
            )
            assert "xpos" not in text and "ypos" not in text
    finally:
        coordinator.close()


def test_analyze_textbutton_pos_non_literal_stays_locked(tmp_path: Path) -> None:
    root = tmp_path / "project_pos_lock"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    textbutton "Play" id "start_btn" pos (base_x, 10) action NullAction()\n',
        encoding="utf-8",
    )
    project = RenpyProject(root)
    observation = _base_observation(script_generation=13)
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-pos-lock",
            "object_id": "obj-independent-pos-lock",
        },
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path))
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-pos-lock")
            assert analyzed["ok"] is True
            assert analyzed["result"]["capabilities"] == {"move": False, "resize": False}
            assert analyzed["result"]["lock_reason"]["code"] == "POS_LITERAL_REQUIRED"
    finally:
        coordinator.close()


def test_analyze_and_commit_textbutton_align_preserves_form(tmp_path: Path) -> None:
    root = tmp_path / "project_align"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    textbutton "Play" id "start_btn" align (0.5, 0.5) action NullAction()\n',
        encoding="utf-8",
    )
    project = RenpyProject(root)
    # Ren'Py align also sets anchor: TL = 0.5 * (1280-80, 720-40) = (600, 340).
    observation = _base_observation(script_generation=12)
    observation["rect"] = [600, 340, 80, 40]
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-align",
            "object_id": "obj-independent-align",
            "rect": [600, 340, 80, 40],
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-align")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["capabilities"] == {"move": True, "resize": False}
            # original_position is runtime pixels for align delta formula
            assert result["original_position"] == [600, 340]
            assert result["source_key"]["position_mode"] == "align"
            assert result["source_key"]["align_authored"] == [0.5, 0.5]
            # extent = parent - widget = 1280-80; delta 24 => +24/1200 = 0.02
            assert result["source_key"]["align_widget_size"] == [80, 40]
            committed = _commit(sock, auth, analyzed, x=624, y=340, request_id="co-align")
            assert committed["ok"] is True
            text = source.read_text(encoding="utf-8")
            assert "align (0.52, 0.5)" in text
            assert "xpos" not in text and "ypos" not in text
    finally:
        coordinator.close()


def test_analyze_textbutton_align_locks_unproven_parent_geometry(tmp_path: Path) -> None:
    root = tmp_path / "project_align_parent"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    (game_dir / "script.rpy").write_text(
        "screen test_screen:\n"
        '    textbutton "Play" id "start_btn" align (0.5, 0.5) action NullAction()\n',
        encoding="utf-8",
    )
    project = RenpyProject(root)
    # Runtime TL inconsistent with full-screen 1280×720 parent for align 0.5 / 80×40.
    observation = _base_observation(script_generation=12)
    observation["rect"] = [100, 100, 80, 40]
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-align-parent",
            "object_id": "obj-independent-align-parent",
            "rect": [100, 100, 80, 40],
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-align-parent")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["capabilities"] == {"move": False, "resize": False}
            assert result["lock_reason"]["code"] == "ALIGN_PARENT_UNPROVEN"
    finally:
        coordinator.close()


def test_analyze_and_commit_textbutton_offset_preserves_form(tmp_path: Path) -> None:
    root = tmp_path / "project_offset"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    textbutton "Play" id "start_btn" offset (12, 10) action NullAction()\n',
        encoding="utf-8",
    )
    project = RenpyProject(root)
    # Runtime TL equals authored offset for base placement 0,0.
    observation = _base_observation(script_generation=14)
    observation["rect"] = [12, 10, 80, 40]
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-offset",
            "object_id": "obj-independent-offset",
            "rect": [12, 10, 80, 40],
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-offset")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["capabilities"] == {"move": True, "resize": False}
            assert result["original_position"] == [12, 10]
            assert result["source_key"]["position_mode"] == "offset"
            assert result["source_key"]["offset_authored"] == [12, 10]
            assert result["source_key"]["offset_runtime_baseline"] == [12, 10]
            # Move runtime TL by +20,+30 → offset becomes 32, 40.
            committed = _commit(sock, auth, analyzed, x=32, y=40, request_id="co-offset")
            assert committed["ok"] is True
            text = source.read_text(encoding="utf-8")
            assert "offset (32, 40)" in text
            assert "xpos" not in text and "ypos" not in text
    finally:
        coordinator.close()


def test_analyze_and_commit_textbutton_anchor_preserves_anchor_bytes(tmp_path: Path) -> None:
    root = tmp_path / "project_anchor"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    textbutton "Play" id "start_btn" xpos 12 ypos 10 '
        "anchor (0.5, 0.5) action NullAction()\n",
        encoding="utf-8",
    )
    project = RenpyProject(root)
    observation = _base_observation(script_generation=13)
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-anchor",
            "object_id": "obj-independent-anchor",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )
    coordinator = EditorCoordinator(project, _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-anchor")
            assert analyzed["ok"] is True
            assert analyzed["result"]["lock_reason"] is None
            committed = _commit(sock, auth, analyzed, x=40, y=50, request_id="co-anchor")
            assert committed["ok"] is True
            text = source.read_text(encoding="utf-8")
            assert text == (
                "screen test_screen:\n"
                '    textbutton "Play" id "start_btn" xpos 40 ypos 50 '
                "anchor (0.5, 0.5) action NullAction()\n"
            )
    finally:
        coordinator.close()


def _style_color_observation(*, script_generation: int = 20) -> dict[str, Any]:
    observation = _base_observation(script_generation=script_generation)
    runtime_key = observation["runtime_key"]
    runtime_key["widget_id"] = "style_color_target"
    runtime_key["ancestry"][-1]["type"] = "Text"
    observation["measurement_method"] = "scene_tree_text"
    observation["style_color"] = "#e22b2b"
    observation["rect"] = [240, 220, 286, 112]
    return observation


def test_anonymous_text_can_move_by_source_location_without_an_id(tmp_path: Path) -> None:
    root = tmp_path / "project_anonymous_text"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    source.write_text(
        "screen test_screen:\n"
        '    text "The gate is silent. Choose your path." xpos 140 ypos 240\n',
        encoding="utf-8",
    )
    observation = _base_observation(script_generation=20)
    observation["runtime_key"]["widget_id"] = None
    observation["runtime_key"]["locator"] = {
        "kind": "source",
        "source_location": ["script.rpy", 2],
        "statement_kind": "text",
    }
    observation["runtime_key"]["ancestry"][-1]["type"] = "Text"
    observation["measurement_method"] = "scene_tree_text"
    observation["rect"] = [140, 240, 402, 27]
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-anonymous-text",
            "object_id": "obj-independent-anonymous-text",
        }
    )
    coordinator = EditorCoordinator(RenpyProject(root), _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-anonymous-text")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["source_key"]["widget_id"] is None
            assert result["source_key"]["statement_kind"] == "text"
            assert result["source_key"]["position_mode"] == "xy"
            assert result["capabilities"]["move"] is True
            assert result["capabilities"].get("style_color") is None
            assert result["source_key"]["style_lock_reason"]["code"] == "STYLE_COLOR_NOT_DIRECTLY_AUTHORED"

            committed = _commit(
                sock,
                auth,
                analyzed,
                x=196,
                y=284,
                request_id="co-anonymous-text",
            )
            assert committed["ok"] is True
            patched = source.read_text(encoding="utf-8")
            assert patched == (
                "screen test_screen:\n"
                '    text "The gate is silent. Choose your path." xpos 196 ypos 284\n'
            )
            assert " id " not in patched
    finally:
        coordinator.close()


def _send_editor_command(
    sock: socket.socket,
    auth: dict[str, Any],
    *,
    command: str,
    payload: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
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
                "command": command,
                "payload": payload,
            },
        )
        return _recv_json(sock)
    finally:
        sock.settimeout(previous_timeout)


@pytest.mark.parametrize(
    ("baseline_color", "requested_color"),
    [
        ("#e22b2b", "#2457d6"),
        ("#e2b", "#25d"),
        ("#e22b2bff", "#2457d6ff"),
    ],
)
def test_text_style_color_commit_and_product_undo_are_transactional(
    tmp_path: Path,
    baseline_color: str,
    requested_color: str,
) -> None:
    root = tmp_path / "project_style_color"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    baseline = (
        "screen test_screen:\n"
        f'    text "STYLE" id "style_color_target" color "{baseline_color}" size 96 xpos 240 ypos 220\n'
    )
    source.write_text(baseline, encoding="utf-8")
    observation = _style_color_observation()
    observation["style_color"] = baseline_color
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-style-color",
            "object_id": "obj-independent-style-color",
        }
    )
    coordinator = EditorCoordinator(RenpyProject(root), _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-style-color")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            assert result["source_key"]["statement_kind"] == "text"
            assert result["source_key"]["style_mode"] == "literal_hex"
            assert result["source_key"]["style_color"] == baseline_color
            assert result["capabilities"] == {
                "move": True,
                "resize": False,
                "style_color": True,
                "style_color_preview": True,
                "style_color_commit": True,
                "style_color_undo": True,
                "style_color_attestation_rollback": True,
            }

            # A family mismatch must fail closed with a structured reply, never
            # escape the coordinator boundary and leave the client hanging.
            mismatched_color = "#25d" if len(baseline_color) != 4 else "#2457d6"
            mismatched = _send_editor_command(
                sock,
                auth,
                command="commit",
                payload={
                    "session_id": auth["session_id"],
                    "intents": [
                        {
                            "analysis_id": result["analysis_id"],
                            "source_key": result["source_key"],
                            "color": mismatched_color,
                        }
                    ],
                },
                request_id="style-color-family-mismatch",
            )
            assert mismatched["ok"] is False
            assert mismatched["error"]["code"] == "STYLE_COLOR_HEX_FAMILY_MISMATCH"
            assert source.read_text(encoding="utf-8") == baseline

            # The second independent observation sees the product preview.
            probe.observe_reply["style_color"] = requested_color
            probe.observe_reply["rect"] = [260, 230, 286, 112]

            committed = _send_editor_command(
                sock,
                auth,
                command="commit",
                payload={
                    "session_id": auth["session_id"],
                    "intents": [
                        {
                            "analysis_id": result["analysis_id"],
                            "source_key": result["source_key"],
                            "x": 260,
                            "y": 230,
                            "color": requested_color,
                        }
                    ],
                },
                request_id="co-style-color",
            )
            assert committed["ok"] is True
            transaction_id = committed["result"]["transaction_id"]
            assert source.read_text(encoding="utf-8") == (
                baseline.replace(baseline_color, requested_color)
                .replace("xpos 240 ypos 220", "xpos 260 ypos 230")
            )

            handshake = _send_editor_command(
                sock,
                auth,
                command="reload_handshake",
                payload={"transaction_id": transaction_id, "script_generation": 21},
                request_id="hs-style-color",
            )
            assert handshake["ok"] is True
            assert handshake["result"]["state"] == "committed"
            assert probe.attest_calls[-1]["expected_targets"][0]["style_color"] == requested_color
            assert probe.attest_calls[-1]["expected_targets"][0]["position"] == [260, 230]

            undo = _send_editor_command(
                sock,
                auth,
                command="undo_commit",
                payload={"session_id": auth["session_id"], "transaction_id": transaction_id},
                request_id="undo-style-color",
            )
            assert undo["ok"] is True
            undo_transaction_id = undo["result"]["transaction_id"]
            assert undo_transaction_id != transaction_id
            assert source.read_text(encoding="utf-8") == baseline

            undo_handshake = _send_editor_command(
                sock,
                auth,
                command="reload_handshake",
                payload={"transaction_id": undo_transaction_id, "script_generation": 22},
                request_id="hs-undo-style-color",
            )
            assert undo_handshake["ok"] is True
            assert undo_handshake["result"]["state"] == "committed"
            assert probe.attest_calls[-1]["expected_targets"][0]["style_color"] == baseline_color
            assert source.read_text(encoding="utf-8") == baseline
    finally:
        coordinator.close()


def test_text_style_color_refused_attestation_restores_original_bytes(tmp_path: Path) -> None:
    root = tmp_path / "project_style_color_refused"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    baseline = (
        "screen test_screen:\n"
        '    text "STYLE" id "style_color_target" color "#e22b2b" size 96 xpos 240 ypos 220\n'
    )
    source.write_text(baseline, encoding="utf-8")
    observation = _style_color_observation(script_generation=30)
    probe = _Probe(
        observe_reply={
            **json.loads(json.dumps(observation)),
            "frame_id": "independent-frame-style-refused",
            "object_id": "obj-independent-style-refused",
        },
        attest_reply={"ok": False, "state": "refused"},
    )
    coordinator = EditorCoordinator(RenpyProject(root), _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, observation, request_id="an-style-refused")
            assert analyzed["ok"] is True
            result = analyzed["result"]
            assert result["lock_reason"] is None
            probe.observe_reply["style_color"] = "#2457d6"
            committed = _send_editor_command(
                sock,
                auth,
                command="commit",
                payload={
                    "session_id": auth["session_id"],
                    "intents": [
                        {
                            "analysis_id": result["analysis_id"],
                            "source_key": result["source_key"],
                            "color": "#2457d6",
                        }
                    ],
                },
                request_id="co-style-refused",
            )
            assert committed["ok"] is True
            transaction_id = committed["result"]["transaction_id"]
            refused = _send_editor_command(
                sock,
                auth,
                command="reload_handshake",
                payload={"transaction_id": transaction_id, "script_generation": 31},
                request_id="hs-style-refused",
            )
            assert refused["ok"] is False
            assert refused["error"]["code"] == "ATTESTATION_FAILED"
            assert source.read_text(encoding="utf-8") == baseline
            status = _commit_status(sock, auth, transaction_id, request_id="st-style-refused")
            assert status["result"]["state"] == "rolled_back"
    finally:
        coordinator.close()


def test_zorder_structural_swap_commit_and_undo(tmp_path: Path) -> None:
    root = tmp_path / "project_zorder"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    baseline = (
        "screen zorder_test():\n"
        '    button id "zorder_target" xpos 220 ypos 220 xsize 180 ysize 100:\n'
        "        action NullAction()\n"
        '    button id "zorder_sibling" xpos 220 ypos 220 xsize 180 ysize 100:\n'
        "        action NullAction()\n"
    )
    source.write_text(baseline, encoding="utf-8")
    baseline_bytes = source.read_bytes()
    baseline_sha = hashlib.sha256(baseline_bytes).hexdigest()

    target_obs = {
        "rect": [220, 220, 180, 100],
        "script_generation": 10,
        "frame_id": "frame-target-1",
        "measurement_method": "focus_list",
        "runtime_key": {
            "displayable_name": "button",
            "screen": "zorder_test",
            "invocation_path": "zorder_test",
            "widget_id": "zorder_target",
            "source_location": ["game/script.rpy", 2],
            "instance_discriminator": {"kind": "singleton", "instance_count": 1},
            "ancestry": [
                {
                    "index": 0,
                    "type": "ScreenDisplayable",
                    "source_location": ["game/script.rpy", 1],
                    "screen_owner": "zorder_test",
                    "crop_state": "none",
                    "editor_owned": False,
                },
                {
                    "index": 1,
                    "type": "Button",
                    "source_location": ["game/script.rpy", 2],
                    "screen_owner": "zorder_test",
                    "crop_state": "none",
                    "editor_owned": False,
                },
            ],
        },
    }

    probe = _Probe(
        observe_reply={
            **target_obs,
            "frame_id": "frame-target-ind",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )

    coordinator = EditorCoordinator(RenpyProject(root), _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, target_obs, request_id="an-zorder")
            assert analyzed["ok"] is True
            res = analyzed["result"]
            assert res["lock_reason"] is None
            caps = res["capabilities"]
            assert caps["zorder_raise_adjacent_sibling"] is True
            assert caps["zorder_sibling_widget_id"] == "zorder_sibling"
            assert caps["zorder_sibling_line"] == 4

            # Submit structural swap intent
            committed = _send_editor_command(
                sock,
                auth,
                command="commit",
                payload={
                    "session_id": auth["session_id"],
                    "intents": [
                        {
                            "analysis_id": res["analysis_id"],
                            "source_key": res["source_key"],
                            "operation": "raise_adjacent_sibling",
                            "sibling_widget_id": "zorder_sibling",
                            "sibling_line": 4,
                        }
                    ],
                },
                request_id="co-zorder",
            )
            assert committed["ok"] is True
            tx_id = committed["result"]["transaction_id"]

            handshake = _send_editor_command(
                sock,
                auth,
                command="reload_handshake",
                payload={"transaction_id": tx_id, "script_generation": 11},
                request_id="hs-zorder",
            )
            assert handshake["ok"] is True
            assert handshake["result"]["state"] == "committed"

            swapped_bytes = source.read_bytes()
            assert swapped_bytes != baseline_bytes
            swapped_text = swapped_bytes.decode("utf-8")
            assert swapped_text.index("zorder_sibling") < swapped_text.index("zorder_target")

            # Perform undo
            undone = _send_editor_command(
                sock,
                auth,
                command="undo_commit",
                payload={"session_id": auth["session_id"], "transaction_id": tx_id},
                request_id="un-zorder",
            )
            assert undone["ok"] is True
            undo_tx_id = undone["result"]["transaction_id"]

            undo_handshake = _send_editor_command(
                sock,
                auth,
                command="reload_handshake",
                payload={"transaction_id": undo_tx_id, "script_generation": 12},
                request_id="hs-zorder-undo",
            )
            assert undo_handshake["ok"] is True
            assert undo_handshake["result"]["state"] == "committed"

            restored_bytes = source.read_bytes()
            assert restored_bytes == baseline_bytes
            assert hashlib.sha256(restored_bytes).hexdigest() == baseline_sha
    finally:
        coordinator.close()


def test_zorder_structural_swap_rejections(tmp_path: Path) -> None:
    root = tmp_path / "project_zorder_rej"
    game_dir = root / "game"
    game_dir.mkdir(parents=True)
    source = game_dir / "script.rpy"
    baseline = (
        "screen zorder_test():\n"
        '    button id "zorder_target" xpos 220 ypos 220 xsize 180 ysize 100:\n'
        "        action NullAction()\n"
        '    button id "zorder_sibling" xpos 220 ypos 220 xsize 180 ysize 100:\n'
        "        action NullAction()\n"
    )
    source.write_text(baseline, encoding="utf-8")

    target_obs = {
        "rect": [220, 220, 180, 100],
        "script_generation": 10,
        "frame_id": "frame-target-1",
        "measurement_method": "focus_list",
        "runtime_key": {
            "displayable_name": "button",
            "screen": "zorder_test",
            "invocation_path": "zorder_test",
            "widget_id": "zorder_target",
            "source_location": ["game/script.rpy", 2],
            "instance_discriminator": {"kind": "singleton", "instance_count": 1},
            "ancestry": [
                {
                    "index": 0,
                    "type": "ScreenDisplayable",
                    "source_location": ["game/script.rpy", 1],
                    "screen_owner": "zorder_test",
                    "crop_state": "none",
                    "editor_owned": False,
                },
                {
                    "index": 1,
                    "type": "Button",
                    "source_location": ["game/script.rpy", 2],
                    "screen_owner": "zorder_test",
                    "crop_state": "none",
                    "editor_owned": False,
                },
            ],
        },
    }

    probe = _Probe(
        observe_reply={
            **target_obs,
            "frame_id": "frame-target-ind",
        },
        attest_reply={"ok": True, "state": "all_targets_attested"},
    )

    coordinator = EditorCoordinator(RenpyProject(root), _make_sdk(tmp_path), attestation_timeout=2.0)
    coordinator.attach_runtime_probe(probe)
    endpoint = coordinator.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2.0) as sock:
            auth = _auth(sock, endpoint)
            analyzed = _analyze(sock, auth, target_obs, request_id="an-zorder-rej")
            res = analyzed["result"]

            # 1. Combining structural swap with position fields
            comb_res = _send_editor_command(
                sock,
                auth,
                command="commit",
                payload={
                    "session_id": auth["session_id"],
                    "intents": [
                        {
                            "analysis_id": res["analysis_id"],
                            "source_key": res["source_key"],
                            "operation": "raise_adjacent_sibling",
                            "sibling_widget_id": "zorder_sibling",
                            "sibling_line": 4,
                            "x": 230,
                            "y": 230,
                        }
                    ],
                },
                request_id="co-comb-rej",
            )
            assert comb_res["ok"] is False
            assert comb_res["error"]["code"] == "STRUCTURAL_INTENT_COMBINATION_REJECTED"

            # 2. Invalid non-adjacent sibling line
            non_adj_res = _send_editor_command(
                sock,
                auth,
                command="commit",
                payload={
                    "session_id": auth["session_id"],
                    "intents": [
                        {
                            "analysis_id": res["analysis_id"],
                            "source_key": res["source_key"],
                            "operation": "raise_adjacent_sibling",
                            "sibling_widget_id": "zorder_sibling",
                            "sibling_line": 999,
                        }
                    ],
                },
                request_id="co-nonadj-rej",
            )
            assert non_adj_res["ok"] is False
            assert non_adj_res["error"]["code"] in ("BUTTON_SIBLING_ORDER_INVALID", "SOURCE_LINE_INVALID", "BUTTON_BLOCK_REQUIRED", "TARGET_NOT_BUTTON")
    finally:
        coordinator.close()
