from __future__ import annotations

import pytest

from renforge.editor_runner_status import is_reload_committed, is_reload_settled


@pytest.mark.parametrize("status_code", ["reload_committed", "reload_failed"])
def test_reload_settled_uses_structured_code_not_localized_text(status_code: str) -> None:
    assert is_reload_settled(
        {
            "save_in_progress": False,
            "status_code": status_code,
            "status_text": "Rechargement terminé" if status_code == "reload_committed" else "Échec du rechargement",
        }
    )


def test_reload_settled_rejects_saving_or_unstructured_english_text() -> None:
    assert not is_reload_settled(
        {"save_in_progress": True, "status_code": "reload_committed", "status_text": "Reload committed"}
    )
    assert not is_reload_settled(
        {"save_in_progress": False, "status_text": "Reload committed"}
    )


def test_reload_committed_ignores_localized_text() -> None:
    assert is_reload_committed(
        {
            "save_in_progress": False,
            "status_code": "reload_committed",
            "status_text": "Rechargement terminé",
            "script_generation": 4,
        },
        generation=4,
    )
