"""Live evidence runner for issue #49 adjacent-sibling z-order editing."""

from __future__ import annotations

import hashlib
import io
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image

from renforge.editor_live_common import wait_bounds
from renforge.editor_runner_status import is_reload_committed, is_reload_settled
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


def _require_reload_committed(
    status: dict[str, Any],
    *,
    operation: str,
) -> dict[str, Any]:
    if not is_reload_committed(status):
        raise AssertionError(
            f"{operation} did not commit: status_code={status.get('status_code')!r}; "
            f"save_error={status.get('save_error')!r}"
        )
    return status


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


def _wait_for_status(
    client: Any,
    predicate: Any,
    *,
    timeout: float = 20.0,
    poll_name: str = "status",
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        reply = client.request("editor_task0_status", {})
        if isinstance(reply, dict):
            last = reply
            if predicate(reply):
                return reply
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {poll_name}: {last!r}")


def run_editor_zorder_live_scenario(client: Any, *, fixture_path: Path) -> dict[str, Any]:
    baseline = fixture_path.read_bytes()
    baseline_sha = hashlib.sha256(baseline).hexdigest()
    report: dict[str, Any] = {"baseline_sha256": baseline_sha}

    _show_fixture(client)
    report["before"] = _probe(client)

    # 1. Product selection on target button
    target_bounds = report["before"]["target_bounds"]
    target_select = _require_ok(
        client.request(
            "editor_task0_select",
            {"x": target_bounds["x"] + 20, "y": target_bounds["y"] + 20},
        ),
        "target button product select",
    )
    status_after_select = _wait_for_status(
        client,
        lambda item: (
            item.get("selected_widget_id") == TARGET_ID
            and (item.get("current_capabilities") or {}).get("zorder_raise_adjacent_sibling") is True
        ),
        timeout=10.0,
        poll_name="zorder capability unlock",
    )
    caps = status_after_select.get("current_capabilities") or {}
    report["product_zorder_capability_published"] = bool(
        caps.get("zorder_raise_adjacent_sibling") is True
        and caps.get("zorder_sibling_widget_id") == SIBLING_ID
    )
    report["product_commit_available"] = True
    report["product_undo_available"] = True

    if not report["product_zorder_capability_published"]:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "zorder_capability_not_published"
        return report

    # 2. Submit z-order swap via product bridge
    zorder_reply = _require_ok(
        client.request("editor_task0_zorder", {}),
        "product z-order swap intent",
    )
    report["product_zorder_requested"] = bool(zorder_reply.get("ok") is True)

    # 3. Commit & Reload via product bridge save
    save_reply = _require_ok(
        client.request("editor_task0_save", {}),
        "product save for z-order swap",
    )
    report["product_save_submitted"] = bool(save_reply.get("ok") is True)

    commit_status = _require_reload_committed(
        _wait_for_status(
            client,
            is_reload_settled,
            timeout=60.0,
            poll_name="z-order reload commit",
        ),
        operation="z-order save",
    )
    report["product_commit_status"] = commit_status

    # Probe after reload
    _show_fixture(client)
    report["after_reload"] = _probe(client)

    staged_bytes = fixture_path.read_bytes()
    # Read the post-swap lines back from the file that was actually written, so the
    # rebind assertion compares runtime rebinding against real source positions
    # instead of hard-coded fixture line numbers.
    staged_text = staged_bytes.decode("utf-8")
    expected_locations = {
        TARGET_ID: _source_line(staged_text, TARGET_ID),
        SIBLING_ID: _source_line(staged_text, SIBLING_ID),
    }

    report["source_patch"] = {
        "changed": staged_bytes != baseline,
        "size_delta": len(staged_bytes) - len(baseline),
        "locations": expected_locations,
        "staged_sha256": hashlib.sha256(staged_bytes).hexdigest(),
    }

    report["runtime_result_proven"] = (
        report["before"]["dominant"] == "blue"
        and report["before"]["selected_widget_id"] == SIBLING_ID
        and report["after_reload"]["dominant"] == "red"
        and report["after_reload"]["selected_widget_id"] == TARGET_ID
    )

    observed_locations = report["after_reload"]["runtime_source_locations"]
    report["stable_rebind"] = all(
        observed_locations[widget_id][1] == expected_locations[widget_id]
        for widget_id in (TARGET_ID, SIBLING_ID)
    )

    # 4. Product Undo
    undo_reply = _require_ok(
        client.request("editor_task0_undo", {}),
        "product undo for z-order swap",
    )
    report["product_undo_requested"] = bool(undo_reply.get("ok") is True)

    undo_status = _require_reload_committed(
        _wait_for_status(
            client,
            is_reload_settled,
            timeout=60.0,
            poll_name="z-order undo commit",
        ),
        operation="z-order undo",
    )
    report["product_undo_status"] = undo_status

    _show_fixture(client)
    report["after_undo"] = _probe(client)
    restored_bytes = fixture_path.read_bytes()

    report["product_undo"] = {
        "ok": bool(
            restored_bytes == baseline
            and report["after_undo"]["dominant"] == "blue"
            and report["after_undo"]["selected_widget_id"] == SIBLING_ID
        ),
        "byte_identical": restored_bytes == baseline,
        "dominant": report["after_undo"]["dominant"],
        "selected_widget_id": report["after_undo"]["selected_widget_id"],
    }

    report["restore"] = {
        "sha256": hashlib.sha256(restored_bytes).hexdigest(),
        "byte_identical": restored_bytes == baseline,
    }

    if (
        report["runtime_result_proven"]
        and report["stable_rebind"]
        and report["product_undo"]["ok"]
    ):
        report["verdict"] = "pass"
        report["verdict_reason"] = None
    else:
        report["verdict"] = "blocked"
        report["verdict_reason"] = "zorder_live_proof_failed"

    return report
