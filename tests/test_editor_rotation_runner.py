from __future__ import annotations

from pathlib import Path

import renforge.editor_rotation_runner as rotation_runner


def test_manual_rotate_roundtrip_handles_replacement_length_change(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.rpy"
    baseline = (
        b'screen example():\n'
        b'    button id "rotation_target":\n'
        b'        add Transform(Solid("#fff"), rotate=9)\n'
    )
    fixture.write_bytes(baseline)

    report = rotation_runner._run_manual_rotate_roundtrip(fixture)

    assert report["rotate"] == {"before": 9, "patched": 10}
    assert report["outside_bytes_equal"] is True
    assert report["patch"]["patched_end"] == report["patch"]["original_end"] + 1
    assert report["matches_baseline"] is True
    assert fixture.read_bytes() == baseline


def test_save_and_rebind_preserves_structured_reload_status(tmp_path: Path, monkeypatch) -> None:
    fixture = tmp_path / "fixture.rpy"
    fixture.write_text(
        'screen example():\n'
        '    button id "rotation_target" xpos 100 ypos 200:\n'
        '        add Transform(Solid("#fff"), rotate=9)\n',
        encoding="utf-8",
    )
    statuses = iter(
        [
            {
                "ok": True,
                "save_in_progress": False,
                "status_code": "reload_committed",
                "status_text": "Rechargement terminé",
                "script_generation": 4,
            },
            {
                "ok": True,
                "selected_widget_id": "rotation_target",
                "selected_lock_reason": None,
                "selected_analysis_pending": False,
            },
        ]
    )
    def wait_for_status(_client, predicate, **_kwargs):
        status = next(statuses)
        assert predicate(status)
        return status

    monkeypatch.setattr(rotation_runner, "_wait_for_status", wait_for_status)

    class Client:
        def request(self, name: str) -> dict:
            assert name == "editor_task0_status"
            return {"ok": True, "script_generation": 3}

        def click_element(self, **_kwargs) -> dict:
            text = fixture.read_text(encoding="utf-8")
            fixture.write_text(text.replace("xpos 100 ypos 200", "xpos 105 ypos 207"), encoding="utf-8")
            return {"ok": True}

    report = rotation_runner._attempt_save_and_rebind(
        Client(),
        fixture_path=fixture,
        expected_move=[5, 7],
    )

    assert report["ok"] is True
    assert report["status_code"] == "reload_committed"


def test_save_failure_preserves_structured_reload_status(tmp_path: Path, monkeypatch) -> None:
    fixture = tmp_path / "fixture.rpy"
    fixture.write_text(
        'screen example():\n'
        '    button id "rotation_target" xpos 100 ypos 200:\n'
        '        add Transform(Solid("#fff"), rotate=9)\n',
        encoding="utf-8",
    )

    def wait_for_status(_client, predicate, **_kwargs):
        status = {
            "ok": True,
            "save_in_progress": False,
            "status_code": "reload_failed",
            "status_text": "Échec du rechargement",
            "save_error": "ATTESTATION_FAILED",
            "script_generation": 3,
        }
        assert predicate(status)
        return status

    monkeypatch.setattr(rotation_runner, "_wait_for_status", wait_for_status)

    class Client:
        def request(self, name: str) -> dict:
            assert name == "editor_task0_status"
            return {"ok": True, "script_generation": 3}

        def click_element(self, **_kwargs) -> dict:
            return {"ok": True}

    report = rotation_runner._attempt_save_and_rebind(
        Client(),
        fixture_path=fixture,
        expected_move=[5, 7],
    )

    assert report["ok"] is False
    assert report["reason"] == "write_chain_failed"
    assert report["status_code"] == "reload_failed"
    assert report["status_text"] == "Échec du rechargement"
    assert report["save_error"] == "ATTESTATION_FAILED"
