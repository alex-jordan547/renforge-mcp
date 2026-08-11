from __future__ import annotations

from pathlib import Path

import renforge.editor_viewport_runner as viewport_runner


def test_scrolled_commit_preserves_structured_reload_status(tmp_path: Path, monkeypatch) -> None:
    fixture = tmp_path / "fixture.rpy"
    fixture.write_text(
        'screen renforge_editor_viewport_fixture():\n'
        '    textbutton "MOVE" id "viewport_target" xpos 100 ypos 200 action NullAction()\n',
        encoding="utf-8",
    )
    statuses = iter(
        [
            {
                "ok": True,
                "current_analysis_id": "analysis-1",
                "selected_widget_id": "viewport_target",
                "selected_lock_reason": None,
                "selected_original_position": [100, 200],
            },
            {"ok": True, "preview_position": [112, 200]},
            {
                "ok": True,
                "save_in_progress": False,
                "status_code": "reload_failed",
                "status_text": "Reload failed",
                "save_error": "ATTESTATION_FAILED",
            },
        ]
    )
    monkeypatch.setattr(viewport_runner, "scroll_to", lambda *_args, **_kwargs: 120.0)
    monkeypatch.setattr(
        viewport_runner,
        "wait_bounds",
        lambda *_args, **_kwargs: {"x": 100, "y": 80, "width": 80, "height": 40},
    )
    monkeypatch.setattr(viewport_runner, "_wait_for_status", lambda *_args, **_kwargs: next(statuses))

    class Client:
        def request(self, _name: str, _payload=None) -> dict:
            return {"ok": True}

        def click_element(self, **_kwargs) -> dict:
            return {"ok": True}

        def eval_expr(self, _expression: str) -> float:
            return 0.0

    report = viewport_runner.run_editor_viewport_scrolled_commit(
        Client(),
        fixture_path=fixture,
        scroll=120,
    )

    assert report["status_code"] == "reload_failed"
    assert report["status_text"] == "Reload failed"
    assert report["source_unchanged"] is True
