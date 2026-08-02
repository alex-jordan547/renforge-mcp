from __future__ import annotations

import math
import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_rotation_runner import (
    FIXTURE_SCREEN,
    inject_editor_rotation_resources,
    run_editor_rotation_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_ROTATION_LIVE"),
    reason="set RENFORGE_ROTATION_LIVE=1 to run issue #48 rotation spike",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_editor_rotation_resources(destination)
    return destination


def _open_editor(session) -> None:
    for _ in range(40):
        if session.client.inspect_screen("_renforge_editor_launcher").get("active") is True:
            break
        time.sleep(0.2)
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
        pytest.fail("rotation fixture screen never became active")


def test_rotation_spike_evidence_leads_to_blocked(demo_copy: Path) -> None:
    """Issue #48 stays blocked when an unpainted AABB corner selects the rotated target."""
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_rotation_fixture.rpy"

    reports = []
    for _ in range(2):
        with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
            _open_editor(session)
            reports.append(
                run_editor_rotation_live_scenario(
                    session.client,
                    fixture_path=fixture_path,
                )
            )

    assert [(item["verdict"], item["verdict_reason"]) for item in reports] == [
        ("blocked", "aabb_false_positive"),
        ("blocked", "aabb_false_positive"),
    ]
    report = reports[-1]
    rotated = report["transform_plane"]["rotated"]
    assert rotated["quad_available"] is True
    assert rotated["quad_source"] in {"forward", "reverse"}
    assert rotated["quad_coordinate_space"] == "screen"
    assert rotated["roundtrip_error"] is not None
    assert rotated["roundtrip_error"] <= 0.5
    quad = rotated["quad"]
    assert isinstance(quad, list) and len(quad) == 4
    points = [(float(point[0]), float(point[1])) for point in quad]
    assert all(math.isfinite(value) for point in points for value in point)
    area = abs(
        sum(
            x1 * y2 - x2 * y1
            for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
        )
    ) / 2.0
    assert area > 1.0
    assert any(
        abs(x2 - x1) > 0.5 and abs(y2 - y1) > 0.5
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
    )

    paint = report["isolation"]["rotated"]["point_samples"]
    assert paint["center"]["painted"] is True
    assert paint["edge"]["painted"] is True
    assert paint["edge"]["source"] == "runtime_transform_quad_inset"
    assert paint["aabb_corner"]["painted"] is False

    corner = report["aabb_corner_probe"]
    assert corner["painted"] is False
    assert corner["selected_rotated"] is True
    assert corner["selected_widget_id"] == "rotation_target"

    assert report["product_undo"]["ok"] is True
    assert report["write_chain"]["ok"] is True
    assert report["write_chain"]["status_text"] == "Reload committed"
    assert report["write_chain"]["generation_delta"] == 1
    assert report["write_chain"]["matches_independent_expected"] is True
    assert report["write_chain"]["post_save_rebind_lock_reason"] is None

    manual = report["manual_rotate_roundtrip"]
    assert manual["outside_bytes_equal"] is True
    assert manual["matches_baseline"] is True
    assert report["fixture_restore"]["matches_baseline"] is True
