import asyncio
import json
from pathlib import Path

import pytest

from renforge.ui import activity as activity_module
from renforge.ui import graph
from renforge.ui import poller
from renforge.tools import live as live_module

try:
    from starlette.testclient import TestClient
except ModuleNotFoundError as exc:  # optional dependency
    if exc.name != "starlette" and not str(exc.name).startswith("starlette."):
        raise
    TestClient = None


if TestClient is not None:
    from renforge.ui.server import create_ui_app


def _project_root(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    game = project / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    return project


def _project_at(path: Path) -> Path:
    game = path / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    return path


def test_resolve_game_file_path_rejects_traversal(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    result = graph.resolve_game_file_path(project, "../outside.rpy")
    assert result["ok"] is False
    assert "game/" in str(result["error"])


def test_resolve_game_file_path_reads_game_file(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    result = graph.resolve_game_file_path(project, "game/script.rpy")
    assert result["ok"] is True
    assert result["path"] == "game/script.rpy"
    assert result["text"].startswith("label start:")


def test_resolve_game_file_path_respects_size_limit(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    huge = project / "game" / "huge.rpy"
    huge.write_text("x" * 250_000, encoding="utf-8")
    result = graph.resolve_game_file_path(project, "huge.rpy", max_bytes=200_000)
    assert result["ok"] is False
    assert "too large" in str(result["error"])


def test_resolve_warp_target_prefers_file_spec() -> None:
    result = graph.resolve_warp_target("/tmp", "script.rpy:123")
    assert result["ok"] is True
    assert result["target"] == "script.rpy:123"


def test_resolve_warp_target_uses_project_label(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    result = graph.resolve_warp_target(str(project), "start")
    assert result["ok"] is True
    assert result["target"] == "game/script.rpy:1"


def test_resolve_warp_target_supports_unique_local_label(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    (project / "game" / "script.rpy").write_text(
        "label chapter:\n    return\nlabel .detail:\n    return\n",
        encoding="utf-8",
    )

    result = graph.resolve_warp_target(str(project), ".detail")

    assert result == {"ok": True, "target": "game/script.rpy:3"}


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_ui_translation_stats_requires_language(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)
    response = client.get("/api/translation-stats?token=token")
    assert response.status_code == 400
    assert response.json()["error"] == "language is required"


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_file_is_restricted_to_game_directory(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)
    response = client.get("/api/file?path=../outside.rpy&token=token")
    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert "inside game" in str(data["error"]).lower()


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_files_lists_game_scripts_recursively(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    game = project / "game"
    (game / "tl" / "french").mkdir(parents=True)
    (game / "tl" / "french" / "script.rpy").write_text("# tl\n", encoding="utf-8")
    (game / "notes.txt").write_text("not a script\n", encoding="utf-8")
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)
    response = client.get("/api/files?token=token")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["files"] == ["game/script.rpy", "game/tl/french/script.rpy"]


def test_api_screenshot_handles_missing_bridge_as_json_error(tmp_path: Path, monkeypatch) -> None:
    if TestClient is None:
        pytest.skip("starlette not installed")

    import renforge.ui.server as server

    def fail(*_args, **_kwargs):
        raise RuntimeError("bridge unavailable")

    project = _project_root(tmp_path)
    monkeypatch.setattr(server.live, "screenshot_png", fail)
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)
    response = client.post(
        "/api/screenshot?token=token",
        json={"width": 32, "height": 32},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert "RuntimeError" in payload["error"]


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_debug_events_polls_bridge_events(tmp_path: Path, monkeypatch) -> None:
    import renforge.ui.server as server

    project = _project_root(tmp_path)
    calls = {}

    def fake_poll_events(project_path: str, since: int = 0):
        calls["project_path"] = project_path
        calls["since"] = since
        return {
            "ok": True,
            "cursor": 12,
            "events": [{"seq": 12, "type": "label", "label": "start"}],
        }

    monkeypatch.setattr(server.live, "poll_events", fake_poll_events)
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)
    response = client.get("/api/debug/events?token=token&since=5")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "cursor": 12,
        "events": [{"seq": 12, "type": "label", "label": "start"}],
    }
    assert calls == {"project_path": str(project), "since": 5}


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_live_control_dispatches_runtime_action(tmp_path: Path, monkeypatch) -> None:
    import renforge.ui.server as server

    project = _project_root(tmp_path)
    calls = {}

    def fake_control(project_path: str, action: str):
        calls["project_path"] = project_path
        calls["action"] = action
        return {"ok": True, "action": action, "event": "toggle_skip"}

    monkeypatch.setattr(server.live, "control", fake_control)
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)
    response = client.post(
        "/api/live/control?token=token",
        json={"action": "toggle_skip"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "action": "toggle_skip", "event": "toggle_skip"}
    assert calls == {"project_path": str(project), "action": "toggle_skip"}


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_live_launch_dispatches_runtime_start(tmp_path: Path, monkeypatch) -> None:
    import renforge.ui.server as server

    project = _project_root(tmp_path)
    calls = {}

    def fake_launch(project_path: str, version: str = "stable", warp: str | None = None):
        calls.update(project_path=project_path, version=version, warp=warp)
        return {"ok": True, "already_running": False, "current_label": "start"}

    monkeypatch.setattr(server.live, "launch_game", fake_launch)
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)
    response = client.post(
        "/api/live/launch?token=token",
        json={"version": "stable"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "already_running": False, "current_label": "start"}
    assert calls == {"project_path": str(project), "version": "stable", "warp": None}


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_live_stop_dispatches_runtime_stop(tmp_path: Path, monkeypatch) -> None:
    import renforge.ui.server as server

    project = _project_root(tmp_path)
    calls = {}

    def fake_stop(project_path: str):
        calls["project_path"] = project_path
        return {"ok": True, "was_running": True}

    monkeypatch.setattr(server.live, "stop_game", fake_stop)
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)
    response = client.post("/api/live/stop?token=token", json={})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "was_running": True}
    assert calls == {"project_path": str(project)}


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_project_browser_opens_current_project_and_lists_project_parent(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    _project_at(tmp_path / "other-project")
    (tmp_path / "notes").mkdir()
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)

    response = client.get("/api/project/browser?token=token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["root_id"] == "current-project"
    assert payload["path"] == ""
    assert payload["project"] is True
    assert {entry["name"] for entry in payload["entries"]} == {"game"}

    parent = client.get("/api/project/browser?token=token&root_id=project-parent")
    assert parent.status_code == 200
    assert {entry["name"] for entry in parent.json()["entries"]} == {"other-project", "project", "notes"}
    assert next(entry for entry in parent.json()["entries"] if entry["name"] == "other-project")["project"] is True

    child = client.get("/api/project/browser?token=token&root_id=project-parent&path=other-project")
    assert child.status_code == 200
    assert child.json()["parent_path"] == ""


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_project_browser_rejects_paths_outside_root(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)

    response = client.get("/api/project/browser?token=token&path=../outside")

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "selected root" in response.json()["error"]


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_project_selection_rejects_non_project_directory(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    (tmp_path / "not-a-project").mkdir()
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)

    response = client.post(
        "/api/project?token=token",
        json={"root_id": "project-parent", "path": "not-a-project"},
    )

    assert response.status_code == 422
    assert "missing game/" in response.json()["error"]


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_project_selection_refuses_while_game_is_running(tmp_path: Path, monkeypatch) -> None:
    import renforge.ui.server as server

    project = _project_root(tmp_path)
    _project_at(tmp_path / "other-project")
    monkeypatch.setattr(server.live, "game_state", lambda _path: {"ok": True})
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)

    response = client.post(
        "/api/project?token=token",
        json={"root_id": "project-parent", "path": "other-project"},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "stop the running game before switching projects"
    assert client.get("/api/project?token=token").json()["project"] == str(project)


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_project_selection_switches_runtime_and_notifies_clients(tmp_path: Path, monkeypatch) -> None:
    import renforge.ui.server as server

    project = _project_root(tmp_path)
    target = _project_at(tmp_path / "other-project")
    broadcasts: list[dict] = []

    async def wait_for_stop(_root, _hub, stop_event):
        await stop_event.wait()

    async def record_broadcast(_self, payload):
        broadcasts.append(payload)

    monkeypatch.setattr(server.live, "game_state", lambda _path: {"ok": False})
    monkeypatch.setattr(server, "poll_bridge", wait_for_stop)
    monkeypatch.setattr(server, "tail_activity", wait_for_stop)
    monkeypatch.setattr(server.WebSocketHub, "broadcast", record_broadcast)
    app = create_ui_app(project, ui_token="token")

    with TestClient(app) as client:
        response = client.post(
            "/api/project?token=token",
            json={"root_id": "project-parent", "path": "other-project"},
        )
        assert response.status_code == 200
        assert response.json()["project"] == str(target)
        assert client.get("/api/project?token=token").json()["project"] == str(target)

    assert broadcasts[0]["kind"] == "project"
    assert broadcasts[0]["type"] == "project-changed"
    assert broadcasts[0]["payload"]["project"] == str(target)


def test_list_choices_filters_out_non_choice_screen_controls(monkeypatch, tmp_path) -> None:
    class FakeClient:
        def get_state(self):
            return {"menu": True}

        def list_choices(self):
            return [
                {"index": 0, "text": "history", "screen": "quick_menu"},
                {"index": 10, "text": "Continue", "screen": "choice"},
                {"index": 11, "text": "Retry", "screen": "choice"},
                {"index": 12, "text": "Auto", "screen": None},
            ]

    monkeypatch.setattr(live_module, "_client", lambda _path: FakeClient())
    result = live_module.list_choices(str(tmp_path))
    assert result == {
        "ok": True,
        "choices": [
            {"index": 10, "text": "Continue", "screen": "choice"},
            {"index": 11, "text": "Retry", "screen": "choice"},
        ],
    }


def test_list_choices_returns_empty_when_no_active_menu(monkeypatch, tmp_path) -> None:
    class FakeClient:
        def get_state(self):
            return {"menu": False}

        def list_choices(self):
            return [{"index": 0, "text": "should not see", "screen": "choice"}]

    monkeypatch.setattr(live_module, "_client", lambda _path: FakeClient())
    result = live_module.list_choices(str(tmp_path))
    assert result == {"ok": True, "choices": []}


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_activity_recent_endpoint_returns_tail_events_and_skips_invalid_lines(tmp_path: Path) -> None:
    project_root = _project_root(tmp_path)
    activity_path = project_root / ".renforge" / "activity.jsonl"
    activity_path.parent.mkdir(parents=True, exist_ok=True)
    activity_path.write_text(
        "\n".join(
            [
                json.dumps({"ts": 1000, "name": "ignored"}),
                "{broken json",
                json.dumps({"ts": 2000, "name": "alive"}),
                json.dumps({"ts": 3000, "name": "newest"}),
                "",
            ]
        ),
        encoding="utf-8",
    )

    app = create_ui_app(project_root, ui_token="token")
    client = TestClient(app)
    response = client.get("/api/timeline/recent?token=token&n=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "ok": True,
        "events": [
            {
                "kind": "activity",
                "type": "activity",
                "timestamp": 2000,
                "payload": {"ts": 2000, "name": "alive"},
            },
            {
                "kind": "activity",
                "type": "activity",
                "timestamp": 3000,
                "payload": {"ts": 3000, "name": "newest"},
            },
        ],
    }


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_activity_recent_endpoint_returns_empty_when_activity_file_missing(tmp_path: Path) -> None:
    project_root = _project_root(tmp_path)
    app = create_ui_app(project_root, ui_token="token")
    client = TestClient(app)
    response = client.get("/api/activity/recent?token=token")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"ok": True, "events": []}


class _RecordingHub:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def broadcast(self, payload: dict) -> None:
        self.messages.append(payload)


def test_tail_activity_broadcasts_stable_activity_envelope(tmp_path: Path, monkeypatch) -> None:
    project_root = _project_root(tmp_path)
    path = project_root / ".renforge" / "activity.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    activity = {"ts": 1234567890, "name": "tool-call"}
    path.write_text(json.dumps(activity) + "\n", encoding="utf-8")

    stop_event = asyncio.Event()
    hub = _RecordingHub()

    async def _stop_sleep(_delay: float) -> None:
        stop_event.set()

    monkeypatch.setattr(activity_module.asyncio, "sleep", _stop_sleep)
    asyncio.run(activity_module.tail_activity(project_root, hub, stop_event))

    assert hub.messages == [
        {
            "kind": "activity",
            "type": "activity",
            "timestamp": 1234567890,
            "payload": activity,
        }
    ]


def test_story_map_caches_after_first_build(tmp_path: Path, monkeypatch) -> None:
    project = _project_root(tmp_path)
    calls = {"scan": 0, "native": 0, "normalize": 0}

    def fake_scan(root: str):
        calls["scan"] += 1
        return {"labels": [], "graph": {"edges": []}}

    def fake_run_native(_sdk, _project):
        calls["native"] += 1
        return []

    def fake_normalize(_raw):
        calls["normalize"] += 1
        return []

    monkeypatch.setattr(graph, "scan_project", fake_scan)
    monkeypatch.setattr(graph, "run_native_dump", fake_run_native)
    monkeypatch.setattr(graph, "normalize_definitions", fake_normalize)
    monkeypatch.setattr(graph, "RenpyProject", lambda _root: object())
    monkeypatch.setattr(graph, "get_or_install_sdk", lambda: "sdk")

    first = graph.build_story_map(project)
    second = graph.build_story_map(project)

    assert first == second
    assert calls["scan"] == 1
    assert calls["native"] == 1
    assert calls["normalize"] == 1


def test_story_map_signature_changes_on_script_or_autopilot_change(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    root_sig = graph._story_map_signature(project)
    script = project / "game" / "script.rpy"
    script.write_text("label start:\n    pass\n", encoding="utf-8")
    changed_script_sig = graph._story_map_signature(project)
    assert root_sig != changed_script_sig

    autopilot_dir = project / ".renforge"
    autopilot_dir.mkdir(exist_ok=True)
    (autopilot_dir / "autopilot.json").write_text("{}", encoding="utf-8")
    with_autopilot_sig = graph._story_map_signature(project)
    assert changed_script_sig != with_autopilot_sig


class _FakePollerClient:
    def get_state(self):
        return {"current_label": "start"}

    def poll_events(self, _cursor: int):
        return {"events": [{"type": "label", "label": "start"}], "cursor": 1}


def test_poll_bridge_cycle_tracks_change_only_for_state_payload() -> None:
    assert poller._cycle_changed({"x": 1}, {}, []) is True
    assert poller._cycle_changed({}, {"x": 1}, []) is True
    assert poller._cycle_changed({}, {}, []) is False
    assert poller._cycle_changed({}, {}, [{}]) is True


def test_run_in_thread_forwards_keyword_arguments() -> None:
    # Regression: the poller connects with ``from_project(root, timeout=1.0)``.
    # A ``_run_in_thread(fn, *args)`` signature raises TypeError on the keyword,
    # which the poller swallows — silently killing the whole live WS channel.
    def probe(first: int, *, second: int) -> tuple[int, int]:
        return (first, second)

    assert asyncio.run(poller._run_in_thread(probe, 1, second=2)) == (1, 2)


def test_poll_bridge_connects_with_timeout_kwarg_and_broadcasts(tmp_path: Path, monkeypatch) -> None:
    hub = _RecordingHub()
    stop_event = asyncio.Event()
    captured: dict[str, object] = {}

    def fake_from_project(root, *, timeout):
        captured["root"] = root
        captured["timeout"] = timeout
        return _FakePollerClient()

    monkeypatch.setattr(poller.BridgeClient, "from_project", staticmethod(fake_from_project))

    async def _stop_sleep(_delay: float) -> None:
        stop_event.set()

    monkeypatch.setattr(poller.asyncio, "sleep", _stop_sleep)

    asyncio.run(poller.poll_bridge(tmp_path, hub, stop_event, poll_interval=0.0))

    assert captured["timeout"] == 1.0
    broadcast_types = [message["type"] for message in hub.messages]
    assert "state" in broadcast_types
    assert "event" in broadcast_types
    # Screenshots are served over HTTP, never streamed on the socket.
    assert "screenshot" not in broadcast_types
