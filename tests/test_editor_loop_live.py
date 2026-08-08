from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from renforge.editor_live_common import DEMO_COPY_IGNORE
from renforge.editor_loop_runner import (
    FIXTURE_SCREEN,
    inject_editor_loop_resources,
    run_editor_loop_live_scenario,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_LOOP_LIVE"),
    reason="set RENFORGE_LOOP_LIVE=1 to run the loop / repeated-use disambiguation proof",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=DEMO_COPY_IGNORE)
    inject_editor_loop_resources(destination)
    return destination


def test_loop_instance_disambiguation_live_proof(demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    sdk = get_or_install_sdk("8.5.3", project_root=demo_copy)
    project = RenpyProject(demo_copy)
    fixture_path = demo_copy / "game" / "zz_renforge_editor_loop_fixture.rpy"

    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
        for _ in range(40):
            launcher = session.client.inspect_screen("_renforge_editor_launcher")
            if launcher.get("active") is True:
                break
            time.sleep(0.25)
        else:
            pytest.fail("editor launcher never became active")

        launcher_click = session.client.click_element(
            text="RF",
            exact=True,
            screen="_renforge_editor_launcher",
        )
        assert launcher_click.get("ok") is True, launcher_click
        for _ in range(40):
            overlay = session.client.inspect_screen("_renforge_editor_overlay")
            if overlay.get("active") is True:
                break
            time.sleep(0.05)
        else:
            pytest.fail("editor overlay never became active")

        for _ in range(20):
            available = session.client.eval_expr(f'renpy.has_screen("{FIXTURE_SCREEN}")')
            if available is True:
                break
            time.sleep(0.1)
        else:
            pytest.fail("loop fixture screen never became available")

        report = run_editor_loop_live_scenario(session.client, fixture_path=fixture_path)

    # Selection is proven: each instance of a repeated statement is reachable and
    # carries its own key, even though they share one source line and one id.
    vbox = report["vbox_loop"]
    assert vbox["selectable_instance_count"] == 3
    assert vbox["distinct_instance_keys"] == 3
    assert {instance["widget_id"] for instance in vbox["instances"]} == {"loop_vbox_target"}
    assert {instance["source_line"] for instance in vbox["instances"]} == {40}

    expression = report["expression_loop"]
    assert expression["distinct_instance_keys"] == 3

    used = report["repeated_use"]
    assert used["selectable_instance_count"] == 2
    assert used["distinct_instance_keys"] == 2
    # Two distinct call sites converge on a single authored line — the reason an
    # instance-specific write is impossible rather than merely unimplemented.
    assert {instance["source_line"] for instance in used["instances"]} == {8}

    # A literal position inside a loop yields coincident instances.
    literal = report["literal_loop"]
    assert literal["instance_count"] == 3
    assert literal["distinct_origins"] == 1
    assert literal["lock_reason"] == "LOOP_INSTANCE_UNSUPPORTED"

    control = report["unique_control"]
    assert control["kind"] == "static"
    assert control["instance_count"] == 1

    # The write gate held throughout.
    assert report["source_unchanged"] is True
    assert report["verdict"]["instance_specific_source_write"] == "blocked"
