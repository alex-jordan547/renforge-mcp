import json
from pathlib import Path


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_launch_game_delegates_to_matching_dashboard(tmp_path: Path, monkeypatch) -> None:
    from renforge import dashboard_client

    project = tmp_path / "game-project"
    calls = {}
    monkeypatch.setattr(
        dashboard_client,
        "dashboard_connection",
        lambda: {
            "project": str(project),
            "url": "http://127.0.0.1:8765/",
            "token": "secret token",
        },
    )

    def fake_urlopen(request, timeout: int):
        calls.update(
            url=request.full_url,
            payload=json.loads(request.data.decode("utf-8")),
            timeout=timeout,
        )
        return _Response({"ok": True, "current_label": "start"})

    monkeypatch.setattr(dashboard_client, "urlopen", fake_urlopen)

    result = dashboard_client.launch_game(
        str(project),
        version="8.3.7",
        warp="game/script.rpy:12",
    )

    assert result == {"ok": True, "current_label": "start", "via": "dashboard"}
    assert calls == {
        "url": "http://127.0.0.1:8765/api/live/launch?token=secret+token",
        "payload": {"version": "8.3.7", "warp": "game/script.rpy:12"},
        "timeout": 45,
    }


def test_launch_game_ignores_dashboard_for_another_project(tmp_path: Path, monkeypatch) -> None:
    from renforge import dashboard_client

    monkeypatch.setattr(
        dashboard_client,
        "dashboard_connection",
        lambda: {
            "project": str(tmp_path / "selected"),
            "url": "http://127.0.0.1:8765/",
            "token": "secret",
        },
    )
    monkeypatch.setattr(
        dashboard_client,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not connect")),
    )

    assert dashboard_client.launch_game(str(tmp_path / "other")) is None


def test_stop_game_delegates_to_matching_dashboard(tmp_path: Path, monkeypatch) -> None:
    from renforge import dashboard_client

    project = tmp_path / "game-project"
    calls = {}
    monkeypatch.setattr(
        dashboard_client,
        "dashboard_connection",
        lambda: {
            "project": str(project),
            "url": "http://127.0.0.1:8765/",
            "token": "secret token",
        },
    )

    def fake_urlopen(request, timeout: int):
        calls.update(
            url=request.full_url,
            payload=json.loads(request.data.decode("utf-8")),
            timeout=timeout,
        )
        return _Response({"ok": True, "was_running": True})

    monkeypatch.setattr(dashboard_client, "urlopen", fake_urlopen)

    result = dashboard_client.stop_game(str(project))

    assert result == {"ok": True, "was_running": True, "via": "dashboard"}
    assert calls == {
        "url": "http://127.0.0.1:8765/api/live/stop?token=secret+token",
        "payload": {},
        "timeout": 45,
    }
