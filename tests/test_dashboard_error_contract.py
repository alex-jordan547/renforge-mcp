from pathlib import Path

import pytest

from renforge.ui.server import create_ui_app

try:
    from starlette.testclient import TestClient
except ModuleNotFoundError as exc:  # optional dependency
    if exc.name != "starlette" and not str(exc.name).startswith("starlette."):
        raise
    TestClient = None


if TestClient is not None:
    def _project_root(tmp_path: Path) -> Path:
        project = tmp_path / "project"
        game = project / "game"
        game.mkdir(parents=True)
        (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
        return project


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_unauthorized_returns_stable_error_payload(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.get("/api/project?token=wrong")

    assert response.status_code == 401
    payload = response.json()
    assert payload == {
        "ok": False,
        "error_code": "invalid_token",
        "details": {},
        "error": "invalid token",
    }


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_project_browser_unknown_root_is_machine_readable(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.get("/api/project/browser?token=token&root_id=non-existent-root")

    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "project_browser_unknown_root"
    assert payload["error"] == "unknown browse root"
    assert payload["details"]["root_id"] == "non-existent-root"


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_project_selection_rejects_path_outside_root(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    app = create_ui_app(project, ui_token="token")
    client = TestClient(app)

    response = client.post(
        "/api/project?token=token",
        json={"root_id": "current-project", "path": "../outside"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "project_folder_outside_root"
    assert payload["details"]["root_id"] == "current-project"
    assert payload["details"]["path"] == "../outside"


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_project_selection_rejects_unknown_folder(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.post(
        "/api/project?token=token",
        json={"root_id": "current-project", "path": "does-not-exist"},
    )

    assert response.status_code == 404
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "project_folder_not_found"
    assert payload["details"]["path"] == "does-not-exist"


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_project_selection_rejects_non_project_directory(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    (tmp_path / "not-a-project").mkdir()
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.post(
        "/api/project?token=token",
        json={"root_id": "project-parent", "path": "not-a-project"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "project_not_renpy_project"
    assert "path" in payload["details"]


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_project_selection_rejects_when_game_is_running(tmp_path: Path, monkeypatch) -> None:
    import renforge.ui.server as server

    project = _project_root(tmp_path)
    target = tmp_path / "other-project"
    (target / "game").mkdir(parents=True)
    (target / "game" / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")

    monkeypatch.setattr(server.live, "game_state", lambda _path: {"ok": True})
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.post(
        "/api/project?token=token",
        json={"root_id": "project-parent", "path": "other-project"},
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "project_switch_blocked"
    assert payload["details"]["running"] is True


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_coverage_reports_missing_file_with_code(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.get("/api/coverage?token=token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "coverage_file_missing"
    assert payload["error"].startswith("coverage file not found")


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_coverage_does_not_expose_parser_exception_details(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    coverage_file = project / ".renforge" / "autopilot.json"
    coverage_file.parent.mkdir()
    coverage_file.write_text("{invalid-json", encoding="utf-8")
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.get("/api/coverage?token=token")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "ok": False,
        "error_code": "coverage_read_failed",
        "details": {},
        "error": "cannot read coverage",
    }
    serialized = response.text
    assert "JSONDecodeError" not in serialized
    assert "invalid-json" not in serialized


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_screenshot_does_not_expose_runtime_exception_details(tmp_path: Path, monkeypatch) -> None:
    import renforge.ui.server as server

    project = _project_root(tmp_path)

    def fail_screenshot(*_args, **_kwargs):
        raise RuntimeError("private runtime detail at /private/project")

    monkeypatch.setattr(server.live, "screenshot_png", fail_screenshot)
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.post("/api/screenshot?token=token", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "ok": False,
        "error_code": "screenshot_failed",
        "details": {},
        "error": "screenshot failed",
    }
    assert "RuntimeError" not in response.text
    assert "private runtime detail" not in response.text


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_assets_reports_missing_game_root_with_code(tmp_path: Path) -> None:
    project = tmp_path / "without-game"
    project.mkdir()
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.get("/api/assets?token=token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "assets_game_root_missing"
    assert payload["error"].startswith("no game/")


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_file_rejects_path_outside_game_with_code(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.get("/api/file?token=token&path=../outside.rpy")

    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "file_path_out_of_bounds"
    assert payload["details"]["path"] == "../outside.rpy"
    assert payload["error"] == "path must be inside game/"


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_file_rejects_unknown_file(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.get("/api/file?token=token&path=game/missing.rpy")

    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "file_not_found"
    assert payload["details"]["path"] == "game/missing.rpy"
    assert payload["error"].startswith("path does not point to a file")


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_story_map_reports_missing_project_root_with_code(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing-root"
    client = TestClient(create_ui_app(missing_root, ui_token="token"))

    response = client.get("/api/story-map?token=token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "story_map_root_missing"
    assert payload["error"].startswith("Project root does not exist")


@pytest.mark.skipif(TestClient is None, reason="starlette not installed")
def test_api_warp_rejects_unknown_label_with_code(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    client = TestClient(create_ui_app(project, ui_token="token"))

    response = client.post("/api/warp?token=token", json={"target": "unknownlabel"})

    assert response.status_code == 400
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "warp_target_unknown"
    assert payload["details"]["target"] == "unknownlabel"
