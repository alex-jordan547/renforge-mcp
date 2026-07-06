from pathlib import Path

import pytest

from renforge.ui import graph

try:
    from starlette.testclient import TestClient
except Exception:  # optional dependency
    TestClient = None


if TestClient is not None:
    from renforge.ui.server import create_ui_app


def _project_root(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    game = project / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text("label start:\n    return\n", encoding="utf-8")
    return project


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


def test_resolve_warp_target_uses_story_map_label(monkeypatch) -> None:
    story_map = {
        "ok": True,
        "nodes": [
            {
                "label": "start",
                "data": {"file": "script.rpy", "line": 42},
            }
        ],
    }
    monkeypatch.setattr(graph, "build_story_map", lambda _project_root: story_map)
    result = graph.resolve_warp_target("/tmp", "start")
    assert result["ok"] is True
    assert result["target"] == "script.rpy:42"


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
