from __future__ import annotations

import threading
import time
import types

from renforge.tools import live


def test_inspect_screen_forwards_name_to_bridge_client(tmp_path, monkeypatch):
    (tmp_path / "game").mkdir()
    calls = {}

    class FakeClient:
        def inspect_screen(self, name):
            calls["name"] = name
            return {"ok": True, "active": False, "name": name}

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())

    result = live.inspect_screen(str(tmp_path), "custom")

    assert result == {"ok": True, "active": False, "name": "custom"}
    assert calls == {"name": "custom"}


def _running_session(language: str):
    process = types.SimpleNamespace(returncode=None)
    process.poll = lambda: process.returncode
    session = types.SimpleNamespace(
        editor=True,
        closed=False,
        process=process,
        client=types.SimpleNamespace(
            get_state=lambda: {"current_label": "start", "language": language}
        ),
    )

    def close():
        session.closed = True
        return {"cleaned": {}, "failed": []}

    session.close = close
    return session


def test_start_launch_keeps_the_language_warning_for_a_ready_session(tmp_path, monkeypatch):
    (tmp_path / "game").mkdir()
    session = _running_session("english")
    monkeypatch.setitem(live._SESSIONS, live._key(tmp_path), session)

    first = live.start_launch(
        str(tmp_path),
        lambda _root, _cancel: {
            "ok": True,
            "ready": True,
            "already_running": True,
            "requested_language": "english",
            "active_language": "english",
        },
        language="english",
        wait_timeout=1.0,
    )
    assert first["ready"] is True

    note = (
        "The requested language cannot be applied to the already-running session. "
        "Stop it and launch again to change language."
    )
    second = live.start_launch(
        str(tmp_path),
        lambda _root, _cancel: {
            "ok": True,
            "ready": True,
            "already_running": True,
            "requested_language": "german",
            "active_language": "english",
            "language_applied": False,
            "message": note,
        },
        language="german",
        wait_timeout=1.0,
    )

    assert second["ready"] is True
    assert second["already_running"] is True
    assert second["requested_language"] == "german"
    assert second["active_language"] == "english"
    assert second["language_applied"] is False
    assert second["message"] == note

    session.process.returncode = 0


def test_start_launch_echoes_requested_language(tmp_path):
    (tmp_path / "game").mkdir()

    result = live.start_launch(
        str(tmp_path),
        lambda _project_root, _cancel_event: {
            "ok": True,
            "ready": True,
            "current_label": "main_menu",
        },
        language="japanese",
        wait_timeout=1.0,
    )

    assert result["ok"] is True
    assert result["ready"] is True
    assert result["requested_language"] == "japanese"
    assert result["active_language"] is None


def test_launch_status_reports_requested_language_while_starting(tmp_path):
    (tmp_path / "game").mkdir()
    release = threading.Event()
    started = threading.Event()

    def delayed_launch(_project_root, _cancel_event):
        started.set()
        assert release.wait(2.0)
        return {"ok": True, "ready": True, "current_label": "main_menu"}

    result = live.start_launch(
        str(tmp_path),
        delayed_launch,
        language="japanese",
        wait_timeout=0.0,
    )
    assert started.wait(1.0)
    assert result["status"] == "starting"
    assert result["requested_language"] == "japanese"

    release.set()
    deadline = time.monotonic() + 2.0
    final = result
    while time.monotonic() < deadline:
        final = live.launch_status(str(tmp_path))
        if final.get("ready"):
            break
        time.sleep(0.01)

    assert final["ready"] is True
    assert final["requested_language"] == "japanese"
    assert final["active_language"] is None


def test_start_launch_does_not_claim_a_new_language_for_a_running_task(tmp_path):
    (tmp_path / "game").mkdir()
    release = threading.Event()
    started = threading.Event()

    def delayed_launch(_project_root, _cancel_event):
        started.set()
        assert release.wait(2.0)
        return {"ok": True, "ready": True, "current_label": "main_menu"}

    live.start_launch(
        str(tmp_path),
        delayed_launch,
        language="japanese",
        wait_timeout=0.0,
    )
    assert started.wait(1.0)
    try:
        conflict = live.start_launch(
            str(tmp_path),
            lambda _project_root, _cancel_event: {"ok": True, "ready": True},
            language="german",
            wait_timeout=0.0,
        )

        assert conflict["code"] == "LAUNCH_IN_PROGRESS"
        assert conflict["requested_language"] == "german"
        assert conflict["language_applied"] is False
    finally:
        release.set()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if live.launch_status(str(tmp_path)).get("ready"):
                break
            time.sleep(0.01)
        live.stop_game(str(tmp_path))


def _fresh_session(state):
    return types.SimpleNamespace(
        editor=True,
        display_mode="native",
        startup_ms=1,
        phases=[],
        environment={},
        temporary_savedir=None,
        headless=False,
        process=types.SimpleNamespace(poll=lambda: None),
        client=types.SimpleNamespace(get_state=lambda: state),
    )


def test_launch_game_echoes_language_from_a_fresh_session(tmp_path, monkeypatch):
    (tmp_path / "game").mkdir()
    calls = {}
    session = _fresh_session({"current_label": "start", "language": "japanese"})
    monkeypatch.setattr(live, "get_or_install_sdk", lambda *_args, **_kwargs: object())

    def fake_launch_with_bridge(_sdk, _project, **kwargs):
        calls.update(kwargs)
        return session

    monkeypatch.setattr(live, "launch_with_bridge", fake_launch_with_bridge)

    result = live.launch_game(str(tmp_path), language="japanese")

    assert calls["language"] == "japanese"
    assert result["requested_language"] == "japanese"
    assert result["active_language"] == "japanese"
    assert result["language_applied"] is True
    assert "warning" in result
    live._SESSIONS.pop(live._key(tmp_path), None)


def test_launch_game_keeps_language_unknown_when_the_bridge_does_not_report_it(tmp_path, monkeypatch):
    (tmp_path / "game").mkdir()
    session = _fresh_session({"current_label": "start"})
    monkeypatch.setattr(live, "get_or_install_sdk", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(live, "launch_with_bridge", lambda *_args, **_kwargs: session)

    result = live.launch_game(str(tmp_path), language="japanese")

    assert result["active_language"] is None
    assert result["language_applied"] is None
    assert "warning" in result
    live._SESSIONS.pop(live._key(tmp_path), None)


def test_launch_game_does_not_warn_when_the_language_is_isolated(tmp_path, monkeypatch):
    (tmp_path / "game").mkdir()
    session = _fresh_session({"current_label": "start", "language": "japanese"})
    monkeypatch.setattr(live, "get_or_install_sdk", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(live, "launch_with_bridge", lambda *_args, **_kwargs: session)

    result = live.launch_game(str(tmp_path), language="japanese", savedir="temporary")

    assert result["language_applied"] is True
    assert "warning" not in result
    live._SESSIONS.pop(live._key(tmp_path), None)


def test_launch_game_reports_language_cannot_change_a_running_session(tmp_path, monkeypatch):
    (tmp_path / "game").mkdir()
    session = types.SimpleNamespace(
        editor=True,
        process=types.SimpleNamespace(poll=lambda: None),
        client=types.SimpleNamespace(
            get_state=lambda: {"current_label": "start", "language": "english"}
        ),
    )
    monkeypatch.setitem(live._SESSIONS, live._key(tmp_path), session)

    result = live.launch_game(str(tmp_path), language="japanese")

    assert result["already_running"] is True
    assert result["requested_language"] == "japanese"
    assert result["active_language"] == "english"
    assert result["language_applied"] is False
    assert "cannot be applied" in result["message"]


def test_launch_game_reports_an_already_active_language_as_applied(tmp_path, monkeypatch):
    (tmp_path / "game").mkdir()
    session = _running_session("english")
    monkeypatch.setitem(live._SESSIONS, live._key(tmp_path), session)

    result = live.launch_game(str(tmp_path), language="english")

    assert result["already_running"] is True
    assert result["requested_language"] == "english"
    assert result["active_language"] == "english"
    assert result["language_applied"] is True
    assert result["already_active"] is True
    assert "message" not in result


def test_launch_game_reports_language_on_external_session_mode_mismatch(tmp_path, monkeypatch):
    (tmp_path / "game").mkdir()
    monkeypatch.setattr(
        live,
        "_client",
        lambda _path: types.SimpleNamespace(
            get_state=lambda: {"current_label": "start", "language": "english"}
        ),
    )

    result = live.launch_game(str(tmp_path), language="english")

    assert result["code"] == "SESSION_MODE_MISMATCH"
    assert result["requested_language"] == "english"
    assert result["active_language"] == "english"
    assert result["language_applied"] is False
    assert "already_active" not in result


def test_launch_game_reports_language_cannot_apply_when_project_is_locked(tmp_path, monkeypatch):
    (tmp_path / "game").mkdir()
    monkeypatch.setattr(live, "_SESSIONS", {})
    monkeypatch.setattr(
        live,
        "_client",
        lambda _path: types.SimpleNamespace(
            get_state=lambda: {"current_label": "start", "language": "english"}
        ),
    )

    result = live.launch_game(str(tmp_path), warp="game/script.rpy:1", language="english")

    assert result["code"] == "BRIDGE_PROJECT_LOCKED"
    assert result["requested_language"] == "english"
    assert result["active_language"] == "english"
    assert result["language_applied"] is False
    assert "already_active" not in result
    assert "was not applied" in result["message"]


def test_failed_launch_status_never_reports_a_language_as_applied(tmp_path):
    task = live._LaunchTask(tmp_path, requested_language="english")
    task.lifecycle = "failed"
    task.done_event.set()
    task.result = {"ok": False, "code": "LAUNCH_FAILED", "language_applied": True}

    result = live._launch_task_status(task)

    assert result["language_applied"] is False


def test_ready_launch_status_keeps_an_unreported_language_unknown(tmp_path):
    task = live._LaunchTask(tmp_path, requested_language="english")
    task.lifecycle = "ready"
    task.done_event.set()
    task.session = types.SimpleNamespace(
        editor=True,
        process=types.SimpleNamespace(poll=lambda: None),
        client=types.SimpleNamespace(get_state=lambda: {"current_label": "start"}),
    )
    task.result = {"ok": True, "ready": True, "current_label": "start"}

    result = live._launch_task_status(task)

    assert result["language_applied"] is None
