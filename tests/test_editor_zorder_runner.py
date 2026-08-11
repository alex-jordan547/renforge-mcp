from __future__ import annotations

import pytest

from renforge.editor_zorder_runner import _require_reload_committed


@pytest.mark.parametrize("operation", ["z-order save", "z-order undo"])
def test_zorder_rejects_failed_reload(operation: str) -> None:
    with pytest.raises(AssertionError, match=operation):
        _require_reload_committed(
            {
                "save_in_progress": False,
                "status_code": "reload_failed",
                "status_text": "Échec du rechargement",
                "save_error": "ATTESTATION_FAILED",
            },
            operation=operation,
        )


def test_zorder_accepts_committed_reload_with_localized_text() -> None:
    status = {
        "save_in_progress": False,
        "status_code": "reload_committed",
        "status_text": "Rechargement terminé",
    }
    assert _require_reload_committed(status, operation="z-order save") is status
