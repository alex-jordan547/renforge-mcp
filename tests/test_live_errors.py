from __future__ import annotations

from pathlib import Path

from renforge.bridge.client import BridgeError
from renforge.tools import live


def test_get_errors_filters_bridge_error_events_and_preserves_cursor(tmp_path, monkeypatch):
    calls = {}

    class FakeClient:
        def poll_events(self, since=0):
            calls["since"] = since
            return {
                "events": [
                    {"seq": 1, "type": "say", "what": "hello"},
                    {"seq": 2, "type": "exception", "short": "boom"},
                    {"seq": 3, "type": "error", "message": "broken"},
                ],
                "cursor": 3,
            }

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())

    result = live.get_errors(str(tmp_path), since=7)

    assert result == {
        "ok": True,
        "events": [
            {"seq": 2, "type": "exception", "short": "boom"},
            {"seq": 3, "type": "error", "message": "broken"},
        ],
        "cursor": 3,
    }
    assert calls == {"since": 7}


def test_get_errors_tails_project_files_and_includes_dead_session_exit_code(tmp_path, monkeypatch):
    traceback = tmp_path / "traceback.txt"
    traceback.write_text("".join(f"line-{index}\n" for index in range(1, 106)), encoding="utf-8")
    errors = tmp_path / "errors.txt"
    errors.write_text("first error\nsecond error\n", encoding="utf-8")

    class FakeClient:
        def poll_events(self, since=0):
            raise BridgeError("bridge disconnected")

    class DeadProcess:
        def poll(self):
            return 17

    class Session:
        process = DeadProcess()

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())
    monkeypatch.setitem(live._SESSIONS, live._key(tmp_path), Session())

    result = live.get_errors(str(tmp_path))

    assert result["ok"] is True
    assert result["events"] == []
    assert result["exit_code"] == 17
    assert [item["name"] for item in result["files"]] == ["traceback.txt", "errors.txt"]
    traceback_record = result["files"][0]
    assert traceback_record["tail"].splitlines() == [f"line-{index}" for index in range(6, 106)]
    assert isinstance(traceback_record["mtime"], float)
    assert result["files"][1]["tail"] == "first error\nsecond error\n"


def test_get_errors_returns_clean_result_when_bridge_and_files_are_absent(tmp_path, monkeypatch):
    class FakeClient:
        def poll_events(self, since=0):
            raise BridgeError("bridge disconnected")

    monkeypatch.setattr(live, "_client", lambda _path: FakeClient())

    assert live.get_errors(str(tmp_path)) == {
        "ok": True,
        "events": [],
        "files": [],
        "message": "no errors found",
    }


def test_get_errors_does_not_follow_diagnostic_symlinks_outside_project(tmp_path):
    outside = tmp_path.parent / "outside-traceback.txt"
    outside.write_text("secret traceback\n", encoding="utf-8")
    (tmp_path / "traceback.txt").symlink_to(outside)

    assert live.get_errors(str(tmp_path)) == {
        "ok": True,
        "events": [],
        "files": [],
        "message": "no errors found",
    }
