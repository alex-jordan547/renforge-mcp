from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from renforge.editor_hit_sentinel_runner import (
    inject_hit_sentinel_resources,
    run_hit_sentinel_spike,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_HIT_SENTINEL_LIVE"),
    reason="set RENFORGE_HIT_SENTINEL_LIVE=1 to run issue #43 hit-sentinel spike",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    inject_hit_sentinel_resources(destination)
    return destination


def test_hit_sentinel_spike_produces_capability_verdict(demo_copy: Path) -> None:
    report = run_hit_sentinel_spike(demo_copy)
    assert report["capability"] in {"pass", "blocked", "inconclusive"}
    assert report["agreement"]["n"] >= 10
    assert "probes" in report
    # Non-focusables must not appear in focus_list.
    assert report.get("nonfocusable_absent_from_focus_list") is True
