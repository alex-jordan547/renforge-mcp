"""Live evidence runner for issue #49 adjacent-sibling z-order editing."""

from __future__ import annotations

import hashlib
import io
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from renforge.editor.paths import atomic_write_file
from renforge.editor.source import analyze_raise_adjacent_sibling, apply_button_sibling_swap
from renforge.editor_live_common import wait_bounds
from renforge.editor_task0_runner import _require_ok

FIXTURE_SCREEN = "renforge_editor_zorder_fixture"
TARGET_ID = "zorder_target"
SIBLING_ID = "zorder_sibling"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_zorder_fixture.rpy"
)


def inject_editor_zorder_resources(project_root: Path) -> Path:
    target = project_root / "game" / "zz_renforge_editor_zorder_fixture.rpy"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_RESOURCE, target)
    return target


def _source_line(source: str, widget_id: str) -> int:
    matches = [
        index
        for index, line in enumerate(source.splitlines(), start=1)
        if line.lstrip().startswith("button ") and f'id "{widget_id}"' in line
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one source owner for {widget_id!r}, got {matches!r}")
    return matches[0]


def _sample_rgb(client: Any, point: tuple[int, int]) -> tuple[int, int, int]:
    image = Image.open(io.BytesIO(client.screenshot())).convert("RGB")
    pixel = image.getpixel(point)
    if isinstance(pixel, int):
        return pixel, pixel, pixel
    return int(pixel[0]), int(pixel[1]), int(pixel[2])


def _dominant(rgb: tuple[int, int, int]) -> str:
    red, green, blue = rgb
    if red > blue + 35 and red > green + 35:
        return "red"
    if blue > red + 35 and blue > green + 35:
        return "blue"
    return "unknown"


def _show_fixture(client: Any) -> None:
    last: Any = None
    for _ in range(60):
        last = client.request("editor_task0_start", {"screen": FIXTURE_SCREEN})
        if isinstance(last, dict) and last.get("ok") is True:
            return
        time.sleep(0.1)
    raise AssertionError(f"fixture did not start: {last!r}")


def _probe(client: Any) -> dict[str, Any]:
    target = wait_bounds(client, TARGET_ID, fixture_screen=FIXTURE_SCREEN)
    sibling = wait_bounds(client, SIBLING_ID, fixture_screen=FIXTURE_SCREEN)
    intersection = (
        max(target["x"], sibling["x"]) + 20,
        max(target["y"], sibling["y"]) + 20,
    )
    pixel = _sample_rgb(client, intersection)
    selection = _require_ok(
        client.request("editor_task0_select", {"x": intersection[0], "y": intersection[1]}),
        "z-order overlap selection",
    )
    target_selection = _require_ok(
        client.request(
            "editor_task0_select",
            {"x": target["x"] + 20, "y": target["y"] + 20},
        ),
        "z-order target-only selection",
    )
    sibling_selection = _require_ok(
        client.request(
            "editor_task0_select",
            {
                "x": sibling["x"] + sibling["width"] - 20,
                "y": sibling["y"] + 20,
            },
        ),
        "z-order sibling-only selection",
    )

    def source_location(reply: dict[str, Any]) -> list[Any]:
        observation = reply.get("observation")
        runtime_key = observation.get("runtime_key") if isinstance(observation, dict) else None
        location = runtime_key.get("source_location") if isinstance(runtime_key, dict) else None
        if not isinstance(location, list) or len(location) != 2:
            raise AssertionError(f"selection carries no runtime source location: {reply!r}")
        return [str(location[0]), int(location[1])]

    return {
        "point": list(intersection),
        "pixel": list(pixel),
        "dominant": _dominant(pixel),
        "selected_widget_id": (selection.get("selected") or {}).get("widget_id"),
        "runtime_source_locations": {
            TARGET_ID: source_location(target_selection),
            SIBLING_ID: source_location(sibling_selection),
        },
        "target_bounds": target,
        "sibling_bounds": sibling,
    }


def run_editor_zorder_live_scenario(client: Any, *, fixture_path: Path) -> dict[str, Any]:
    baseline = fixture_path.read_bytes()
    baseline_sha = hashlib.sha256(baseline).hexdigest()
    source = baseline.decode("utf-8")
    report: dict[str, Any] = {"baseline_sha256": baseline_sha}

    _show_fixture(client)
    report["before"] = _probe(client)

    plan = analyze_raise_adjacent_sibling(
        source,
        target_source_line=_source_line(source, TARGET_ID),
        sibling_source_line=_source_line(source, SIBLING_ID),
        target_widget_id=TARGET_ID,
        sibling_widget_id=SIBLING_ID,
    )
    staged, locations = apply_button_sibling_swap(baseline, plan)
    report["source_patch"] = {
        "changed": staged != baseline,
        "size_delta": len(staged) - len(baseline),
        "locations": dict(locations),
        "staged_sha256": hashlib.sha256(staged).hexdigest(),
    }

    try:
        atomic_write_file(fixture_path, staged)
        _require_ok(client.control("reload_script"), "z-order reload")
        _show_fixture(client)
        report["after_reload"] = _probe(client)
    finally:
        atomic_write_file(fixture_path, baseline)
        _require_ok(client.control("reload_script"), "z-order restore reload")
        _show_fixture(client)

    restored = fixture_path.read_bytes()
    report["after_restore"] = _probe(client)
    report["restore"] = {
        "sha256": hashlib.sha256(restored).hexdigest(),
        "byte_identical": restored == baseline,
    }
    report["runtime_result_proven"] = (
        report["before"]["dominant"] == "blue"
        and report["before"]["selected_widget_id"] == SIBLING_ID
        and report["after_reload"]["dominant"] == "red"
        and report["after_reload"]["selected_widget_id"] == TARGET_ID
    )
    observed_locations = report["after_reload"]["runtime_source_locations"]
    expected_locations = report["source_patch"]["locations"]
    report["stable_rebind"] = all(
        observed_locations[widget_id][1] == expected_locations[widget_id]
        for widget_id in (TARGET_ID, SIBLING_ID)
    )
    report["verdict"] = "blocked"
    report["verdict_reason"] = "structural_transaction_undo_missing"
    return report
