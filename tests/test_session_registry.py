from pathlib import Path

import pytest


def test_dashboard_project_is_discoverable_across_processes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RENFORGE_RUNTIME_DIR", str(tmp_path / "runtime"))

    from renforge.session_registry import active_dashboard, dashboard_connection, publish_dashboard

    project = tmp_path / "visual-novel"
    publish_dashboard(project, url="http://127.0.0.1:8765/", token="secret-token")

    context = active_dashboard()
    assert context is not None
    assert context["project"] == str(project.resolve())
    assert context["url"] == "http://127.0.0.1:8765/"
    assert context["pid"] > 0
    assert "token" not in context
    assert dashboard_connection()["token"] == "secret-token"


def test_dashboard_lifespan_publishes_and_clears_its_project(tmp_path: Path, monkeypatch) -> None:
    testclient = pytest.importorskip("starlette.testclient")
    monkeypatch.setenv("RENFORGE_RUNTIME_DIR", str(tmp_path / "runtime"))

    from renforge.session_registry import active_dashboard
    from renforge.ui.server import create_ui_app

    project = tmp_path / "visual-novel"
    (project / "game").mkdir(parents=True)
    app = create_ui_app(project, ui_token="token", dashboard_url="http://127.0.0.1:8765/")

    with testclient.TestClient(app):
        assert active_dashboard()["project"] == str(project.resolve())
        from renforge.session_registry import dashboard_connection

        assert dashboard_connection()["token"] == "token"

    assert active_dashboard() is None


def test_dashboard_project_switch_updates_the_shared_context(tmp_path: Path, monkeypatch) -> None:
    testclient = pytest.importorskip("starlette.testclient")
    monkeypatch.setenv("RENFORGE_RUNTIME_DIR", str(tmp_path / "runtime"))

    from renforge.session_registry import active_dashboard
    from renforge.ui.server import create_ui_app

    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "game").mkdir(parents=True)
    (second / "game").mkdir(parents=True)
    app = create_ui_app(first, ui_token="token", dashboard_url="http://127.0.0.1:8765/")

    with testclient.TestClient(app) as client:
        response = client.post(
            "/api/project?token=token",
            json={"root_id": "project-parent", "path": "second"},
        )
        assert response.status_code == 200
        assert active_dashboard()["project"] == str(second.resolve())
