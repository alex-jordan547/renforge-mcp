from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_failed_gate_runner import (
    FIXTURE_SCREEN,
    inject_editor_failed_gate_resources,
    run_editor_failed_gate_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_FAILED_GATE_LIVE"),
    reason="set RENFORGE_FAILED_GATE_LIVE=1 to run the failed-gate UI live acceptance proof",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_failed_gate_resources(destination)
    return destination


def _open_editor(session) -> None:
    for _ in range(40):
        if session.client.inspect_screen("_renforge_editor_launcher").get("active") is True:
            break
        time.sleep(0.25)
    else:
        pytest.fail("editor launcher never became active")

    assert session.client.click_element(
        text="RF", exact=True, screen="_renforge_editor_launcher"
    ).get("ok") is True
    for _ in range(40):
        if session.client.inspect_screen("_renforge_editor_overlay").get("active") is True:
            break
        time.sleep(0.05)
    else:
        pytest.fail("editor overlay never became active")

    for _ in range(20):
        if session.client.eval_expr(f'renpy.has_screen("{FIXTURE_SCREEN}")') is True:
            break
        time.sleep(0.1)
    else:
        pytest.fail("failed-gate fixture screen never became available")


def test_failed_gate_ui_live_acceptance_proof(demo_copy: Path, tmp_path: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_failed_gate_fixture.rpy"
    output_dir = tmp_path / "captures"

    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
        _open_editor(session)
        report = run_editor_failed_gate_live_scenario(
            session.client,
            fixture_path=fixture_path,
            output_dir=output_dir,
        )

    # Acceptance criteria assertions:
    assert report["verdict"] == "pass"

    # 1. Missing source identity
    id_gate = report["gate_families"]["missing_identity"]
    assert id_gate["ok"] is False
    assert id_gate["lock_reason"] == "SYNTHETIC_WIDGET_ID"
    assert "SYNTHETIC_WIDGET_ID" in id_gate["label_text"]
    assert id_gate["selected_rect"][2] > 0 and id_gate["selected_rect"][3] > 0
    assert id_gate["save_enabled"] is False
    assert id_gate["drag_prevented"] is True
    assert Path(id_gate["frame"]["saved_path"]).exists()

    # 2. Clipping ancestry
    clip_gate = report["gate_families"]["clipping_ancestry"]
    assert clip_gate["ok"] is False
    assert clip_gate["lock_reason"] == "TRANSFORM_CROP_COMPOSITE_UNSUPPORTED"
    assert "TRANSFORM_CROP_COMPOSITE_UNSUPPORTED" in clip_gate["label_text"]
    assert clip_gate["selected_rect"][2] > 0 and clip_gate["selected_rect"][3] > 0
    assert clip_gate["save_enabled"] is False
    assert clip_gate["drag_prevented"] is True
    assert Path(clip_gate["frame"]["saved_path"]).exists()

    # 3. Repeated runtime instance
    rep_gate = report["gate_families"]["repeated_instance"]
    assert rep_gate["ok"] is False
    assert rep_gate["lock_reason"] == "LOOP_INSTANCE_UNSUPPORTED"
    assert "LOOP_INSTANCE_UNSUPPORTED" in rep_gate["label_text"]
    assert rep_gate["selected_rect"][2] > 0 and rep_gate["selected_rect"][3] > 0
    assert rep_gate["save_enabled"] is False
    assert rep_gate["drag_prevented"] is True
    assert Path(rep_gate["frame"]["saved_path"]).exists()

    # 4. Unlocked control baseline
    unlocked = report["unlocked_control"]
    assert unlocked["ok"] is True
    assert unlocked["selected_widget_id"] == "unlocked_control_target"
    assert Path(unlocked["frame"]["saved_path"]).exists()

    # 5. Base acceptance on game frame & source bytes (zero source bytes changed!)
    assert report["source_unchanged"] is True
    assert report["after_sha256"] == report["baseline_sha256"]
