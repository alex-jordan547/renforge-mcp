from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_style_color_runner import (
    FIXTURE_SCREEN,
    TARGET_ID,
    inject_editor_style_resources,
    run_editor_style_color_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_STYLE_COLOR_LIVE"),
    reason="set RENFORGE_STYLE_COLOR_LIVE=1 to run issue #50 style colour live gate",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_style_resources(destination)
    return destination


def _open_editor(session) -> None:
    for _ in range(40):
        if session.client.inspect_screen("_renforge_editor_launcher").get("active") is True:
            break
        time.sleep(0.2)
    else:
        pytest.fail("editor launcher never became active")

    assert (
        session.client.click_element(
            text="RF",
            exact=True,
            screen="_renforge_editor_launcher",
        ).get("ok")
        is True
    )
    for _ in range(40):
        if session.client.inspect_screen("_renforge_editor_overlay").get("active") is True:
            return
        time.sleep(0.05)
    pytest.fail("editor overlay never became active")


def test_style_color_live_product_path_pass(demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_style_fixture.rpy"

    with launch_with_bridge(
        sdk,
        RenpyProject(demo_copy),
        startup_timeout=120,
        editor=True,
    ) as session:
        _open_editor(session)
        for _ in range(20):
            available = session.client.eval_expr(f'renpy.has_screen("{FIXTURE_SCREEN}")')
            if available is True:
                break
            time.sleep(0.1)
        else:
            pytest.fail("style fixture screen never became available")

        report = run_editor_style_color_live_scenario(
            session.client,
            fixture_path=fixture_path,
        )

    assert report["adapter"] == "text"
    assert report["property"] == "color"
    assert report["resolve_source"]["unlocked"] is True
    assert report["resolve_source"]["color"] == "#e22b2b"
    assert report["locks"]["inherited"]["matches_expected"] is True
    assert report["locks"]["expression"]["matches_expected"] is True
    assert report["runtime_alpha"]["ok"] is True, report["runtime_alpha"]
    assert report["runtime_alpha"]["observation"]["style_color"] == "#33669980"
    assert report["runtime_repeated_lock"]["ok"] is True
    assert report["runtime_repeated_lock"]["instance_discriminator"]["instance_count"] >= 2

    assert report["product_select_unlocked_style"] is True
    assert report["product_preview_available"] is True
    assert report["product_commit_available"] is True
    assert report["product_undo_available"] is True
    assert report["refused_attestation_rollback_available"] is True
    assert report["product_seam_probe"]["measurement_source"] == (
        "editor_task0_status.current_capabilities"
    )

    preview = report["product_preview"]
    assert preview["ok"] is True
    assert preview["source_byte_identical"] is True
    assert preview["pixel"]["dominant"] == "blue"

    preview_reset = report["product_preview_reset"]
    assert preview_reset["ok"] is True
    assert preview_reset["source_byte_identical"] is True
    assert preview_reset["pixel"]["dominant"] == "red"

    refused = report["refused_attestation_rollback"]
    assert refused["ok"] is True
    assert refused["byte_identical"] is True

    patch = report["source_patch"]
    assert patch["changed"] is True
    assert patch["matches_independent_expected"] is True
    assert patch["outside_color_span_identical"] is True
    assert patch["source_color_after"] == "#2457d6"
    assert report["product_commit"]["ok"] is True

    assert report["pixel_before"]["dominant"] == "red", report["pixel_before"]
    assert report["pixel_after"]["dominant"] == "blue", report["pixel_after"]
    assert report["pixel_before"]["bounds_from_scene_tree"] is True
    assert report["pixel_after"]["bounds_from_scene_tree"] is True
    assert report["runtime_color_change_proven"] is True
    assert report["published_source_after_reload"]["ok"] is True
    assert report["rebinding"]["ok"] is True

    undo = report["product_undo"]
    assert undo["ok"] is True
    assert undo["byte_identical"] is True
    assert undo["source_color"] == "#e22b2b"
    assert undo["pixel"]["dominant"] == "red"
    assert undo["note"] == "product_undo_transaction"

    generations = report["generations"]
    assert generations["pre_commit"] >= generations["initial_analysis"]
    assert generations["post_commit"] >= generations["pre_commit"] + 1
    assert generations["pre_undo"] >= generations["post_commit"]
    assert generations["post_undo"] >= generations["pre_undo"] + 1

    assert report["restore"]["byte_identical"] is True
    assert report["verdict"] == "pass"
    assert report["verdict_reason"] is None
    assert TARGET_ID == "style_color_target"
