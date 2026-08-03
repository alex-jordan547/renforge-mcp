"""Live evidence runner for issue #51 animated-element editing through _widget_properties seam."""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

from renforge.editor.paths import atomic_write_file
from renforge.editor_live_common import wait_bounds
from renforge.editor_task0_runner import _require_ok

FIXTURE_SCREEN = "renforge_editor_animated_fixture"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_animated_fixture.rpy"
)


def inject_editor_animated_resources(project_root: Path) -> Path:
    target = project_root / "game" / "zz_renforge_editor_animated_fixture.rpy"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_RESOURCE, target)
    return target


def _show_fixture(client: Any) -> None:
    last: Any = None
    for _ in range(60):
        last = client.request("editor_task0_start", {"screen": FIXTURE_SCREEN})
        if isinstance(last, dict) and last.get("ok") is True:
            return
        time.sleep(0.1)
    raise AssertionError(f"fixture did not start: {last!r}")
