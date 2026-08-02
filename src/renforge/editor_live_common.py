"""Shared helpers for Stage-2 visual-editor seven-step live proofs.

Keeps selection, bounds polling, lock checks, and resource injection consistent
across pos/align/anchor/offset runners without coupling fixture-specific
source-patch expectations.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from renforge.editor_task0_runner import _require_ok, _wait_for_status

EDITOR_RESOURCE = Path(__file__).resolve().parent / "bridge" / "editor.rpy"
LIVE_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "live_fixtures"


def inject_editor_live_resources(
    project_root: Path,
    *,
    editor_basename: str,
    fixture_filename: str,
    fixture_resource: Path | None = None,
) -> dict[str, str]:
    """Copy editor.rpy + fixture into ``game/`` under zz_renforge_* names."""
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    editor_target = game_dir / f"zz_renforge_{editor_basename}.rpy"
    fixture_target = game_dir / f"zz_renforge_{editor_basename}_fixture.rpy"
    fixture_src = fixture_resource or (LIVE_FIXTURES_DIR / fixture_filename)
    shutil.copyfile(EDITOR_RESOURCE, editor_target)
    shutil.copyfile(fixture_src, fixture_target)
    return {
        "editor": str(editor_target),
        "fixture": str(fixture_target),
    }


def find_element(
    elements: list[dict[str, Any]],
    wanted_id: str,
    *,
    wanted_text: str | None = None,
) -> dict[str, Any]:
    for element in elements:
        element_id = str(element.get("id") or "")
        if wanted_text is not None:
            if element_id == wanted_id and str(element.get("text") or "") == wanted_text:
                return element
            continue
        if element_id == wanted_id:
            return element
    raise AssertionError(
        f"missing expected element id {wanted_id!r} text {wanted_text!r}: {elements!r}"
    )


def center(bounds: dict[str, Any]) -> tuple[int, int]:
    return (
        int(bounds["x"]) + int(bounds["width"]) // 2,
        int(bounds["y"]) + int(bounds["height"]) // 2,
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def list_ui_info(client: Any, fixture_screen: str) -> dict[str, Any]:
    info = client.list_ui_elements_info(screen=fixture_screen)
    if not isinstance(info, dict):
        raise AssertionError(f"list_ui_elements_info returned non-dict: {info!r}")
    return info


def wait_bounds(
    client: Any,
    widget_id: str,
    *,
    fixture_screen: str,
    timeout: float = 6.0,
) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        info = list_ui_info(client, fixture_screen)
        last = info.get("elements") if isinstance(info.get("elements"), list) else []
        try:
            element = find_element(last, widget_id)
        except AssertionError:
            time.sleep(0.05)
            continue
        bounds = element.get("bounds")
        if isinstance(bounds, dict):
            return {
                "x": int(bounds["x"]),
                "y": int(bounds["y"]),
                "width": int(bounds["width"]),
                "height": int(bounds["height"]),
            }
        time.sleep(0.05)
    raise AssertionError(f"bounds for {widget_id!r} unavailable: {last!r}")


def select_lock(
    client: Any,
    widget_id: str,
    expected_code: str,
    *,
    fixture_screen: str,
) -> str:
    bounds = wait_bounds(client, widget_id, fixture_screen=fixture_screen)
    selection = client.request(
        "editor_task0_select",
        {"x": center(bounds)[0], "y": center(bounds)[1]},
    )
    immediate = selection.get("lock_reason") if isinstance(selection, dict) else None
    if immediate == expected_code:
        return expected_code
    status = _wait_for_status(
        client,
        lambda current: current.get("selected_widget_id") == widget_id
        and current.get("selected_lock_reason") == expected_code,
        timeout=10.0,
        poll_name=f"{widget_id} lock",
    )
    lock_reason = status.get("selected_lock_reason")
    if lock_reason != expected_code:
        raise AssertionError(f"unexpected lock for {widget_id!r}: {status!r}")
    return str(lock_reason)


def observe_selected(client: Any) -> dict[str, Any]:
    reply = client.request("editor_task0_observe_selected", {})
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise AssertionError(f"observe_selected failed: {reply!r}")
    observation = reply.get("observation")
    if not isinstance(observation, dict):
        raise AssertionError(f"observe_selected missing observation: {reply!r}")
    return observation


def find_target_line(
    source_text: str,
    *,
    target_id: str,
    form_token: str,
    analyze: Callable[[str], Any],
) -> tuple[str, int]:
    """Return (line, byte_offset) for the single-line textbutton target."""
    offset = 0
    needle = f" {form_token} "
    for line in source_text.splitlines(keepends=True):
        if (
            f'id "{target_id}"' in line
            and line.lstrip().startswith("textbutton ")
            and needle in f" {line}"
        ):
            analyze(line)
            return line, offset
        offset += len(line)
    raise AssertionError(
        f"source missing {form_token} textbutton line for {target_id!r}"
    )
