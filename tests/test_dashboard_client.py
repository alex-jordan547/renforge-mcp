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
        lambda *_a, **_k: {
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
    assert calls["url"] == "http://127.0.0.1:8765/api/live/launch?token=secret+token"
    assert calls["timeout"] == 45
    assert calls["payload"]["version"] == "8.3.7"
    assert calls["payload"]["warp"] == "game/script.rpy:12"
    assert calls["payload"]["editor"] is True
    assert calls["payload"]["display"] == "auto"
    assert calls["payload"]["audio"] == "auto"
    assert calls["payload"]["persistent"] is None
    assert calls["payload"]["home"] is None
    assert calls["payload"]["preferences"] is None
    assert calls["payload"]["cleanup_on_stop"] is True
    assert "language" not in calls["payload"]


def test_launch_game_includes_language_when_provided(tmp_path: Path, monkeypatch) -> None:
    from renforge import dashboard_client

    project = tmp_path / "game-project"
    calls = {}
    monkeypatch.setattr(
        dashboard_client,
        "dashboard_connection",
        lambda *_a, **_k: {
            "project": str(project),
            "url": "http://127.0.0.1:8765/",
            "token": "secret token",
        },
    )

    def fake_urlopen(request, timeout: int):
        calls.update(payload=json.loads(request.data.decode("utf-8")))
        return _Response({"ok": True})

    monkeypatch.setattr(dashboard_client, "urlopen", fake_urlopen)

    dashboard_client.launch_game(str(project), language="japanese")

    assert calls["payload"]["language"] == "japanese"


def test_dashboard_matching_applies_normcase(tmp_path: Path, monkeypatch) -> None:
    from renforge import dashboard_client

    project = tmp_path / "Game-Project"
    monkeypatch.setattr(
        dashboard_client,
        "dashboard_connection",
        lambda *_a, **_k: {
            "project": str(project).upper(),
            "url": "http://127.0.0.1:8765/",
            "token": "secret token",
        },
    )
    monkeypatch.setattr(
        dashboard_client.os.path,
        "normcase",
        lambda value: value.casefold(),
    )
    monkeypatch.setattr(
        dashboard_client,
        "urlopen",
        lambda *_args, **_kwargs: _Response({"ok": True}),
    )

    assert dashboard_client.launch_game(str(project)) == {
        "ok": True,
        "via": "dashboard",
    }


def test_launch_game_includes_editor_mode(tmp_path: Path, monkeypatch) -> None:
    from renforge import dashboard_client

    project = tmp_path / "game-project"
    calls = {}
    monkeypatch.setattr(
        dashboard_client,
        "dashboard_connection",
        lambda *_a, **_k: {
            "project": str(project),
            "url": "http://127.0.0.1:8765/",
            "token": "secret token",
        },
    )

    def fake_urlopen(request, timeout: int):
        calls.update(
            payload=json.loads(request.data.decode("utf-8")),
            timeout=timeout,
        )
        return _Response({"ok": True, "current_label": "start"})

    monkeypatch.setattr(dashboard_client, "urlopen", fake_urlopen)

    result = dashboard_client.launch_game(
        str(project),
        version="8.3.7",
        warp="game/script.rpy:12",
        editor=False,
    )

    assert result == {"ok": True, "current_label": "start", "via": "dashboard"}
    assert calls["timeout"] == 45
    assert calls["payload"]["version"] == "8.3.7"
    assert calls["payload"]["warp"] == "game/script.rpy:12"
    assert calls["payload"]["editor"] is False


def test_launch_game_ignores_dashboard_for_another_project(tmp_path: Path, monkeypatch) -> None:
    from renforge import dashboard_client

    monkeypatch.setattr(
        dashboard_client,
        "dashboard_connection",
        lambda *_a, **_k: {
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
        lambda *_a, **_k: {
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


def test_import_saves_posts_selection_to_matching_dashboard(tmp_path: Path, monkeypatch) -> None:
    from renforge import dashboard_client

    project = tmp_path / "game-project"
    calls = {}
    monkeypatch.setattr(
        dashboard_client,
        "dashboard_connection",
        lambda *_a, **_k: {
            "project": str(project),
            "url": "http://127.0.0.1:8765/",
            "token": "secret token",
        },
    )

    def fake_urlopen(request, timeout: int):
        calls.update(url=request.full_url, payload=json.loads(request.data.decode("utf-8")))
        return _Response({"ok": True})

    monkeypatch.setattr(dashboard_client, "urlopen", fake_urlopen)

    result = dashboard_client.import_saves(str(project), slots=["1-1"])

    assert calls["url"] == "http://127.0.0.1:8765/api/live/saves/import?token=secret+token"
    assert calls["payload"] == {"slot": None, "slots": ["1-1"], "pattern": None}
    assert result["via"] == "dashboard"


def test_delegated_import_keeps_the_dashboards_no_match_code(tmp_path: Path, monkeypatch) -> None:
    import contextlib

    import pytest

    starlette_testclient = pytest.importorskip("starlette.testclient")
    from renforge import dashboard_client
    from renforge.tools import live
    from renforge.ui.server import create_ui_app

    project = tmp_path / "game-project"
    (project / "game").mkdir(parents=True)
    (project / "game" / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    dest = tmp_path / "renforge-session-q" / "saves"
    dest.mkdir(parents=True)

    class _Isolation:
        savedir_mode = "temporary"
        session_root = dest.parent

    class _Session:
        temporary_savedir = dest
        isolation = _Isolation()

        @contextlib.contextmanager
        def holding_open(self):
            yield True

    # The dashboard process owns an isolated session; the slot does not exist.
    monkeypatch.setattr(live, "_SESSIONS", {live._key(project): _Session()})
    client = starlette_testclient.TestClient(create_ui_app(project, ui_token="tok"))
    body = client.post("/api/live/saves/import?token=tok", json={"slot": "9-9"}).json()

    monkeypatch.setattr(
        dashboard_client,
        "dashboard_connection",
        lambda *_a, **_k: {"project": str(project), "url": "http://127.0.0.1:8765/", "token": "tok"},
    )
    monkeypatch.setattr(dashboard_client, "urlopen", lambda _request, timeout: _Response(body))

    result = dashboard_client.import_saves(str(project), slot="9-9")

    assert result["ok"] is False
    assert result["code"] == "NO_MATCHING_SAVE_SLOTS"
    assert result["via"] == "dashboard"


def _connect(monkeypatch, project: Path) -> None:
    from renforge import dashboard_client

    monkeypatch.setattr(
        dashboard_client,
        "dashboard_connection",
        lambda *_a, **_k: {"project": str(project), "url": "http://127.0.0.1:8765/", "token": "tok"},
    )


def test_delegated_import_waits_longer_than_launch_and_stop(tmp_path: Path, monkeypatch) -> None:
    # An import copies whole save files while holding the session open; a
    # 45 s client timeout reported a failure for a copy that went on.
    from renforge import dashboard_client

    project = tmp_path / "game-project"
    _connect(monkeypatch, project)
    timeouts = {}

    def fake_urlopen(request, timeout):
        timeouts[request.full_url.split("?")[0].rsplit("/api/", 1)[1]] = timeout
        return _Response({"ok": True})

    monkeypatch.setattr(dashboard_client, "urlopen", fake_urlopen)
    dashboard_client.import_saves(str(project), slot="1-1")
    dashboard_client.launch_game(str(project))
    dashboard_client.stop_game(str(project))

    assert timeouts == {"live/saves/import": 300.0, "live/launch": 45.0, "live/stop": 45.0}


def test_delegated_import_failure_keeps_the_dashboards_shape(tmp_path: Path, monkeypatch) -> None:
    from urllib.error import URLError

    from renforge import dashboard_client

    project = tmp_path / "game-project"
    _connect(monkeypatch, project)
    body = {"ok": False, "code": "SAVE_IMPORT_FAILED", "error": "disk full", "files_touched": []}
    monkeypatch.setattr(dashboard_client, "urlopen", lambda _request, timeout: _Response(dict(body)))

    result = dashboard_client.import_saves(str(project), slot="1-1")

    assert result == {**body, "via": "dashboard", "operation": "saves_import"}

    def unreachable(_request, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(dashboard_client, "urlopen", unreachable)
    assert dashboard_client.import_saves(str(project), slot="1-1")["code"] == "DASHBOARD_REQUEST_FAILED"


def test_the_import_route_answers_malformed_selectors_in_the_saves_shape(
    tmp_path: Path, monkeypatch
) -> None:
    import contextlib

    import pytest

    starlette_testclient = pytest.importorskip("starlette.testclient")
    from renforge import dashboard_client
    from renforge.tools import live
    from renforge.ui.server import create_ui_app

    project = tmp_path / "game-project"
    (project / "game").mkdir(parents=True)
    (project / "game" / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    dest = tmp_path / "renforge-session-q" / "saves"
    dest.mkdir(parents=True)

    class _Isolation:
        savedir_mode = "temporary"
        session_root = dest.parent

    class _Session:
        temporary_savedir = dest
        isolation = _Isolation()

        @contextlib.contextmanager
        def holding_open(self):
            yield True

    monkeypatch.setattr(live, "_SESSIONS", {live._key(project): _Session()})
    client = starlette_testclient.TestClient(create_ui_app(project, ui_token="tok"))
    _connect(monkeypatch, project)
    for selection in ({"slot": 5}, {"slots": "1-1"}, {"slots": [1]}, {"pattern": ["*"]}):
        response = client.post("/api/live/saves/import?token=tok", json=selection)
        monkeypatch.setattr(
            dashboard_client,
            "urlopen",
            lambda _request, timeout, response=response: _Response(response.json()),
        )
        relayed = dashboard_client.import_saves(str(project), slot="1-1")
        assert relayed["ok"] is False, selection
        assert relayed["code"] == "SAVE_IMPORT_INVALID", selection
        assert "ready" not in relayed, selection


def test_every_local_import_failure_carries_a_code(tmp_path: Path, monkeypatch) -> None:
    from renforge.tools import live

    missing_project = live.saves(str(tmp_path / "nowhere"), "import", slot="1-1")
    assert missing_project["ok"] is False and missing_project.get("code"), missing_project
    (tmp_path / "game").mkdir()
    for kwargs in ({"slot": " "}, {"slots": ["1-1", ""]}, {"regexp": "x", "slot": "1-1"}):
        result = live.saves(str(tmp_path), "import", **kwargs)
        assert result["ok"] is False and result.get("code") == "SAVE_IMPORT_INVALID", kwargs
