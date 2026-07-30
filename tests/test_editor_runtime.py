from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

import pytest

from renforge.bridge.client import BridgeError
from renforge.editor.exceptions import EditorError
from renforge.editor import runtime as editor_runtime
from renforge.editor.runtime import BridgeRuntimeProbe


class _FakeBridgeClient:
    def __init__(self, responses: deque[Any], request_calls: list[dict[str, Any]]):
        self._responses = responses
        self._request_calls = request_calls

    def request(self, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_calls.append({"command": command, "payload": payload})
        next_response = self._responses.popleft()
        if isinstance(next_response, BaseException):
            raise next_response
        return next_response


def _build_factory(
    responses: list[Any],
    *,
    request_calls: list[dict[str, Any]],
    factory_calls: list[dict[str, Any]],
) -> Any:
    queue: deque[Any] = deque(responses)

    def _factory(_project_root: str | Path, timeout: float) -> _FakeBridgeClient:
        factory_calls.append({"project_root": Path(_project_root), "timeout": timeout})
        return _FakeBridgeClient(queue, request_calls)

    return _factory


def test_bridge_runtime_probe_observe_target_is_exact_command_and_payload(tmp_path: Path) -> None:
    runtime_key = {"widget_id": "foo"}
    responses = [
        {
            "ok": True,
            "observation": {
                "runtime_key": runtime_key,
                "rect": [1, 2, 3, 4],
                "measurement_method": "focus_list",
                "frame_id": "frame",
                "script_generation": 1,
            },
        }
    ]
    request_calls: list[dict[str, Any]] = []
    factory_calls: list[dict[str, Any]] = []
    probe = BridgeRuntimeProbe(
        tmp_path,
        client_factory=_build_factory(
            responses,
            request_calls=request_calls,
            factory_calls=factory_calls,
        ),
    )

    observation = probe.observe(runtime_key, deadline=editor_runtime.time.monotonic() + 1.0)

    assert observation == responses[0]["observation"]
    assert request_calls == [{"command": "editor_observe_target", "payload": {"runtime_key": runtime_key}}]
    assert request_calls[0]["payload"]["runtime_key"] is runtime_key
    assert factory_calls == [{"project_root": tmp_path, "timeout": pytest.approx(1.0, rel=1e-3)}]


def test_bridge_runtime_probe_attest_target_is_exact_command_and_payload(tmp_path: Path) -> None:
    responses = [
        {"ok": True, "state": "all_targets_attested"},
    ]
    request_calls: list[dict[str, Any]] = []
    factory_calls: list[dict[str, Any]] = []
    probe = BridgeRuntimeProbe(
        tmp_path,
        client_factory=_build_factory(
            responses,
            request_calls=request_calls,
            factory_calls=factory_calls,
        ),
    )

    result = probe.attest(
        transaction_id="tx-1",
        script_generation=4,
        deadline=editor_runtime.time.monotonic() + 0.75,
        expected_targets=[{"id": "target-1"}],
    )

    assert result == {"ok": True, "state": "all_targets_attested"}
    assert request_calls == [
        {
            "command": "editor_attest_targets",
            "payload": {
                "transaction_id": "tx-1",
                "script_generation": 4,
                "expected_targets": [{"id": "target-1"}],
            },
        }
    ]
    assert factory_calls == [{"project_root": tmp_path, "timeout": pytest.approx(0.75, rel=1e-3)}]


def test_bridge_runtime_probe_deadline_is_forwarded_to_client_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(editor_runtime.time, "monotonic", lambda: 100.0)

    request_calls: list[dict[str, Any]] = []
    factory_calls: list[dict[str, Any]] = []
    probe = BridgeRuntimeProbe(
        tmp_path,
        client_factory=_build_factory(
            [
                {
                    "ok": True,
                    "observation": {
                        "runtime_key": {"widget_id": "foo"},
                        "rect": [1, 2],
                        "measurement_method": "focus_list",
                        "frame_id": "frame",
                        "script_generation": 1,
                    },
                }
            ],
            request_calls=request_calls,
            factory_calls=factory_calls,
        ),
    )
    probe.observe({"widget_id": "foo"}, deadline=100.025)

    assert factory_calls == [{"project_root": tmp_path, "timeout": pytest.approx(0.025, abs=1e-6)}]


def test_bridge_runtime_probe_request_retries_and_deadline_short(tmp_path: Path) -> None:
    request_calls: list[dict[str, Any]] = []
    factory_calls: list[dict[str, Any]] = []
    probe = BridgeRuntimeProbe(
        tmp_path,
        client_factory=_build_factory(
            [
                BridgeError("temp failure"),
                {
                    "ok": True,
                    "observation": {
                        "runtime_key": {"widget_id": "foo"},
                        "rect": [1, 2],
                        "measurement_method": "focus_list",
                        "frame_id": "frame",
                        "script_generation": 1,
                    },
                },
            ],
            request_calls=request_calls,
            factory_calls=factory_calls,
        ),
        max_retries=1,
    )

    probe.observe({"widget_id": "foo"}, deadline=editor_runtime.time.monotonic() + 2.0)

    assert len(factory_calls) == 2
    assert request_calls[0]["command"] == "editor_observe_target"
    assert request_calls[1]["command"] == "editor_observe_target"


def test_bridge_runtime_probe_maps_bridge_error_codes_to_editor_error(tmp_path: Path) -> None:
    request_calls: list[dict[str, Any]] = []
    factory_calls: list[dict[str, Any]] = []
    probe = BridgeRuntimeProbe(
        tmp_path,
        client_factory=_build_factory(
            [{"ok": False, "error": {"code": "BROKEN", "message": "bad bridge"}}],
            request_calls=request_calls,
            factory_calls=factory_calls,
        ),
    )

    with pytest.raises(EditorError) as exc_info:
        probe.observe({"widget_id": "foo"}, deadline=editor_runtime.time.monotonic() + 1.0)

    assert exc_info.value.code == "BROKEN"
    assert exc_info.value.message == "bad bridge"


def test_bridge_runtime_probe_maps_malformed_replies_to_editor_error(tmp_path: Path) -> None:
    with pytest.raises(EditorError) as malformed:
        BridgeRuntimeProbe(
            tmp_path,
            client_factory=_build_factory(
                [123],
                request_calls=[],
                factory_calls=[],
            ),
        ).attest(transaction_id="tx", script_generation=1, deadline=editor_runtime.time.monotonic() + 1.0, expected_targets=[])
    assert malformed.value.code == "RUNTIME_PROBE_FAILED"


def test_bridge_runtime_probe_maps_missing_observation_to_editor_error(tmp_path: Path) -> None:
    with pytest.raises(EditorError) as missing:
        BridgeRuntimeProbe(
            tmp_path,
            client_factory=_build_factory(
                [{"ok": True}],
                request_calls=[],
                factory_calls=[],
            ),
        ).observe({"widget_id": "foo"}, deadline=editor_runtime.time.monotonic() + 1.0)
    assert missing.value.code == "INDEPENDENT_OBSERVATION_INVALID"
