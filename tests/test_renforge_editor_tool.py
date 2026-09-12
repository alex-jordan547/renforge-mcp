"""Public `renforge_editor` contract: status / select / save, no editor_task0_*."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from renforge.policy import RISK_MALFORMED, RISK_MUTATING, RISK_OBSERVATIONAL, classify
from renforge.tool_definitions import TOOL_DEFINITIONS
from renforge.tools import live


class _FakeEditorClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.evals: list[str] = []
        self.hash = "frame-aaa"
        self.status: dict[str, Any] = {
            "ok": True,
            "active": True,
            "selected_widget_id": None,
            "selected_lock_reason": None,
            "capabilities": {},
            "save_enabled": False,
            "status_code": "idle",
            "dirty_target_count": 0,
            "save_in_progress": False,
            "save_requested": False,
            "save_error": None,
        }
        self.select_reply: dict[str, Any] = {"ok": True}
        self.save_reply: dict[str, Any] = {"ok": False, "error": "NO_INTENTS"}

    def screenshot_hash(self, width: int = 0, height: int = 0) -> str:
        return self.hash

    def request(self, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        self.requests.append((command, payload))
        if command == "editor_task0_status":
            return dict(self.status)
        if command == "editor_task0_select":
            return dict(self.select_reply)
        if command == "editor_task0_save":
            return dict(self.save_reply)
        raise AssertionError(f"unexpected command {command}")

    def eval_expr(self, expr: str) -> Any:
        self.evals.append(expr)
        if "_renforge_to_logical_coordinates" in expr:
            return [4, 6]
        return {"ok": True}


def _install_client(monkeypatch: pytest.MonkeyPatch, client: _FakeEditorClient) -> None:
    monkeypatch.setattr(live, "_with_client", lambda _path, fn: fn(client))


def test_public_catalog_includes_renforge_editor() -> None:
    definition = TOOL_DEFINITIONS["renforge_editor"]
    assert definition.parameter_schemas["action"]["enum"] == ["status", "select", "save"]
    assert "Never call private" in definition.description
    assert "editor_task0_*" in definition.description
    assert set(definition.parameters) == {
        "project_path",
        "action",
        "x",
        "y",
        "coordinate_space",
        "expected_frame_id",
    }


def test_policy_classifies_editor_actions() -> None:
    assert classify("renforge_editor", {"action": "status"}) == (
        "renforge_editor.status",
        RISK_OBSERVATIONAL,
    )
    assert classify("renforge_editor", {"action": "select"})[1] == RISK_MUTATING
    assert classify("renforge_editor", {"action": "save"})[1] == RISK_MUTATING
    assert classify("renforge_editor", {"action": "undo"})[1] == RISK_MALFORMED


def test_status_returns_public_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeEditorClient()
    client.status["selected_widget_id"] = "start_btn"
    client.status["capabilities"] = {"move": True}
    _install_client(monkeypatch, client)

    result = live.editor("/tmp/game", "status")

    assert result["ok"] is True
    assert result["action"] == "status"
    assert result["selected_widget_id"] == "start_btn"
    assert result["capabilities"] == {"move": True}
    assert result["lock_reason"] is None
    assert result["frame_id"] == "frame-aaa"
    assert "editor_task0" not in json.dumps(result)


def test_select_uses_overlay_hit_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeEditorClient()
    client.status["selected_widget_id"] = "start_btn"
    client.status["capabilities"] = {"move": True}
    _install_client(monkeypatch, client)

    result = live.editor("/tmp/game", "select", x=12, y=34)

    assert result["ok"] is True
    assert result["capabilities"]["move"] is True
    assert ("editor_task0_select", {"x": 12, "y": 34, "coordinate_space": "logical"}) in client.requests
    assert "editor_task0" not in json.dumps(result)


def test_select_refuses_stale_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeEditorClient()
    _install_client(monkeypatch, client)

    result = live.editor(
        "/tmp/game",
        "select",
        x=12,
        y=34,
        expected_frame_id="stale-frame",
    )

    assert result["ok"] is False
    assert result["error"] == "expected_frame_id guard failed"
    assert result["frame_id"] == "frame-aaa"
    assert client.requests == []


def test_save_refuses_locked_target(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeEditorClient()
    client.status["selected_widget_id"] = "who"
    client.status["selected_lock_reason"] = "STYLE_POSITION_VARIANT_UNSUPPORTED"
    client.status["capabilities"] = {"move": False}
    _install_client(monkeypatch, client)

    result = live.editor("/tmp/game", "save", x=260, y=30)

    assert result["ok"] is False
    assert result["error"] == "STYLE_POSITION_VARIANT_UNSUPPORTED"
    assert result["lock_reason"] == "STYLE_POSITION_VARIANT_UNSUPPORTED"
    assert all(command != "editor_task0_save" for command, _payload in client.requests)
    assert not any("_renforge_editor_apply_preview" in expr for expr in client.evals)


def test_save_without_dirty_intent_does_not_claim_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeEditorClient()
    client.status["selected_widget_id"] = "start_btn"
    client.status["capabilities"] = {"move": True}
    client.save_reply = {"ok": False, "error": "SAVE_UNAVAILABLE"}
    _install_client(monkeypatch, client)

    result = live.editor("/tmp/game", "save")

    assert result["ok"] is False
    assert result["error"] == "NO_INTENTS"
    assert ("editor_task0_save", {}) in client.requests


def test_save_with_destination_applies_preview_then_overlay_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeEditorClient()
    client.status["selected_widget_id"] = "start_btn"
    client.status["capabilities"] = {"move": True}
    client.status["save_enabled"] = True
    client.save_reply = {"ok": True, "request_id": "commit-1"}
    _install_client(monkeypatch, client)

    result = live.editor("/tmp/game", "save", x=80, y=90)

    assert any("_renforge_editor_apply_preview(80, 90, shift=False)" in expr for expr in client.evals)
    assert ("editor_task0_save", {}) in client.requests
    assert result["action"] == "save"
    assert "editor_task0" not in json.dumps(result)


@pytest.mark.skipif(
    not os.environ.get("RENFORGE_EDITOR_TOOL_LIVE"),
    reason="set RENFORGE_EDITOR_TOOL_LIVE=1 to run the launched-fixture path",
)
def test_public_editor_against_launched_fixture(tmp_path: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.editor_live_common import DEMO_COPY_IGNORE
    from renforge.editor_task0_runner import FIXTURE_SCREEN, inject_editor_task0_resources
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    destination = tmp_path / "demo"
    shutil.copytree(
        Path(__file__).resolve().parents[1] / "examples" / "demo_game",
        destination,
        ignore=DEMO_COPY_IGNORE,
    )
    inject_editor_task0_resources(destination)
    sdk = get_or_install_sdk("8.5.3", project_root=destination)
    project = RenpyProject(destination)
    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
        session.client.eval_expr(f'renpy.show_screen("{FIXTURE_SCREEN}", _layer="screens")')
        time.sleep(0.3)
        status = live.editor(str(destination), "status")
        assert status["ok"] is True
        assert "capabilities" in status
        assert "editor_task0" not in json.dumps(status)

        selected = live.editor(str(destination), "select", x=230, y=250)
        assert selected["ok"] is True
        assert selected["selected_widget_id"]
        assert "editor_task0" not in json.dumps(selected)

        stale = live.editor(
            str(destination),
            "select",
            x=230,
            y=250,
            expected_frame_id="stale-frame",
        )
        assert stale["ok"] is False
        assert stale["error"] == "expected_frame_id guard failed"

        if selected.get("lock_reason"):
            refused = live.editor(str(destination), "save")
            assert refused["ok"] is False
            assert refused["error"] == selected["lock_reason"]
        else:
            clean = live.editor(str(destination), "save")
            assert clean["ok"] is False
            assert clean["error"] == "NO_INTENTS"


def test_unknown_action_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeEditorClient()
    _install_client(monkeypatch, client)

    result = live.editor("/tmp/game", "drag")

    assert result["ok"] is False
    assert "status, select, or save" in result["error"]
    assert client.requests == []
