from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from renforge.editor_hit_sentinel_runner import (
    inject_hit_sentinel_resources,
    run_hit_sentinel_spike,
    run_twice_for_determinism,
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
    injected = inject_hit_sentinel_resources(destination)
    assert injected, "inject_hit_sentinel_resources returned no paths"
    for key, path_str in injected.items():
        resource_path = Path(path_str)
        assert resource_path.exists(), f"missing injected {key}: {resource_path}"
        assert resource_path.suffix == ".rpy", f"injected {key} is not .rpy: {resource_path}"
        assert "game" in resource_path.parts, f"injected {key} not under game/: {resource_path}"
    return destination


def test_hit_sentinel_spike_deterministic_pass(demo_copy: Path) -> None:
    """Locked criteria: same capability on two consecutive runs, and capability is pass."""
    result = run_twice_for_determinism(demo_copy)
    assert result["deterministic"] is True, result
    assert result["run1_capability"] == "pass", result["run1"].get("reason")
    assert result["run2_capability"] == "pass", result["run2"].get("reason")
    for run_key in ("run1", "run2"):
        report = result[run_key]
        assert report.get("nonfocusable_absent_from_focus_list") is True
        assert report.get("isolation_reachable") is True
        assert report.get("rotated_quad_available") is True
        assert report.get("aabb_rotated_false_positive") is True
        assert report["agreement"]["n"] >= 10


def test_hit_sentinel_spike_single_run_structure(demo_copy: Path) -> None:
    report = run_hit_sentinel_spike(demo_copy)
    assert report["capability"] in {"pass", "blocked", "inconclusive"}
    assert "probes" in report
    assert "isolation" in report
    assert report["isolation"].get("pixel_counts") is not None
