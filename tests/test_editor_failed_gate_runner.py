from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image

from renforge.editor_failed_gate_runner import (
    FIXTURE_SCREEN,
    inject_editor_failed_gate_resources,
    probe_locked_target,
)


def test_inject_editor_failed_gate_resources(tmp_path: Path) -> None:
    target_path = inject_editor_failed_gate_resources(tmp_path)
    assert target_path.exists()
    assert target_path.name == "zz_renforge_editor_failed_gate_fixture.rpy"
    assert "screen renforge_editor_failed_gate_fixture():" in target_path.read_text("utf-8")


def test_probe_locked_target_verifies_ui_and_lock_reason(tmp_path: Path) -> None:
    client = MagicMock()
    client.request.side_effect = [
        {"ok": False, "lock_reason": "SYNTHETIC_WIDGET_ID"},  # select reply
        {"ok": False, "error": "SYNTHETIC_WIDGET_ID"},        # drag reply
    ]
    client.eval_expr.side_effect = [
        "id=none x=100 y=100 [SYNTHETIC_WIDGET_ID]",  # label_text
        [100, 100, 80, 40],                           # selected_rect
        False,                                        # save_enabled
    ]
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(255, 0, 0)).save(buf, format="PNG")
    client.screenshot.return_value = buf.getvalue()

    res = probe_locked_target(
        client,
        click_x=140,
        click_y=115,
        expected_lock_reason="SYNTHETIC_WIDGET_ID",
        target_name="identity",
        output_dir=tmp_path,
    )

    assert res["target_name"] == "identity"
    assert res["ok"] is False
    assert res["lock_reason"] == "SYNTHETIC_WIDGET_ID"
    assert res["save_enabled"] is False
    assert res["drag_prevented"] is True
    assert res["selected_rect"] == [100, 100, 80, 40]
    assert res["frame"]["sha256"] is not None
    assert (tmp_path / "failed_gate_identity.png").exists()
