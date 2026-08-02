"""Live proof for repeated loop and `use` instance disambiguation (issue #42).

The spike answers two separate questions and reaches two different verdicts.

**Selection — proven.** Ren'Py keys each `SLFor` iteration in its SL2 cache by
the author's own loop index and gives every `use` call site its own cache dict.
That path identifies one runtime instance of a repeated statement without any
synthetic identity, which `screen.widgets` cannot do because it keeps a single
displayable per widget id.

**Instance-specific source write — blocked.** Every repetition shape stores the
authored position in exactly one place shared by all N instances, so no write
can move one instance alone:

- literal position in a loop  → N coincident instances, one literal
- loop-derived position       → an expression, locked by the expression gate
- layout-positioned in a loop → locked by the container gate
- repeated `use`              → N call sites, one authored line

Moving one instance would require synthesising a per-instance expression, which
the roadmap forbids: the editor does not normalise the author's source. The gate
therefore stays locked, and this runner proves the lock holds and is precise.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

from renforge.editor_task0_runner import _require_ok, _wait_for_status

FIXTURE_SCREEN = "renforge_editor_loop_fixture"
EDITOR_RESOURCE = Path(__file__).resolve().parent / "bridge" / "editor.rpy"
FIXTURE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "live_fixtures"
    / "renforge_editor_loop_fixture.rpy"
)

# Fixture lines that back each repetition case, asserted so the proof fails loudly
# if the fixture is edited without revisiting the verdict.
LITERAL_LOOP_LINE = 24
EXPRESSION_LOOP_LINE = 29
VBOX_LOOP_LINE = 40
REPEATED_USE_LINE = 8
UNIQUE_LINE = 60


def inject_editor_loop_resources(project_root: Path) -> dict[str, str]:
    game_dir = project_root / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    editor_target = game_dir / "zz_renforge_editor_loop.rpy"
    fixture_target = game_dir / "zz_renforge_editor_loop_fixture.rpy"
    shutil.copyfile(EDITOR_RESOURCE, editor_target)
    shutil.copyfile(FIXTURE_RESOURCE, fixture_target)
    return {"editor": str(editor_target), "fixture": str(fixture_target)}


def _elements(client: Any) -> list[dict[str, Any]]:
    info = client.list_ui_elements_info(screen=FIXTURE_SCREEN)
    if not isinstance(info, dict):
        raise AssertionError(f"list_ui_elements_info returned non-dict: {info!r}")
    elements = info.get("elements")
    return elements if isinstance(elements, list) else []


def _bounds_in_band(client: Any, band: dict[str, int], *, timeout: float = 6.0) -> list[dict[str, int]]:
    """Focus rects in one region of the fixture.

    Instances are located geometrically rather than by widget id on purpose:
    `list_ui_elements` names only the last instance of a repeated statement and
    invents synthetic ids for its siblings, which is the very lossiness this
    proof exists to characterise.
    """
    axis, value = ("x", band["x"]) if "x" in band else ("y", band["y"])
    deadline = time.monotonic() + timeout
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last = _elements(client)
        found = []
        for element in last:
            bounds = element.get("bounds")
            if not isinstance(bounds, dict) or int(bounds[axis]) != value:
                continue
            found.append(
                {
                    "x": int(bounds["x"]),
                    "y": int(bounds["y"]),
                    "width": int(bounds["width"]),
                    "height": int(bounds["height"]),
                }
            )
        if found:
            return sorted(found, key=lambda item: (item["y"], item["x"]))
        time.sleep(0.05)
    raise AssertionError(f"no bounds in band {band!r}: {last!r}")


def _select_at(client: Any, bounds: dict[str, int], expected_lock: str | None) -> dict[str, Any]:
    """Select the instance under a rect and return its settled status."""
    reply = client.request(
        "editor_task0_select",
        {
            "x": int(bounds["x"]) + max(2, int(bounds["width"]) // 4),
            "y": int(bounds["y"]) + int(bounds["height"]) // 2,
        },
    )
    if not isinstance(reply, dict):
        raise AssertionError(f"select returned non-dict: {reply!r}")
    if expected_lock is None:
        return _wait_for_status(
            client,
            lambda current: current.get("selected_runtime_key") is not None,
            timeout=10.0,
            poll_name="loop select",
        )
    return _wait_for_status(
        client,
        lambda current: current.get("selected_lock_reason") == expected_lock,
        timeout=10.0,
        poll_name=f"loop {expected_lock}",
    )


def _instance_facts(status: dict[str, Any]) -> dict[str, Any]:
    runtime_key = status.get("selected_runtime_key")
    if not isinstance(runtime_key, dict):
        raise AssertionError(f"selection carries no runtime key: {status!r}")
    discriminator = runtime_key.get("instance_discriminator")
    if not isinstance(discriminator, dict):
        raise AssertionError(f"selection carries no discriminator: {runtime_key!r}")
    source_location = runtime_key.get("source_location") or [None, None]
    return {
        "widget_id": runtime_key.get("widget_id"),
        "source_line": source_location[1],
        "kind": discriminator.get("kind"),
        "instance_count": discriminator.get("instance_count"),
        "instance_key": discriminator.get("instance_key"),
        "lock_reason": status.get("selected_lock_reason"),
    }


def _prove_repetition(
    client: Any,
    *,
    widget_id: str,
    band: dict[str, int],
    expected_kind: str,
    expected_lock: str,
    expected_count: int,
    expected_line: int,
) -> dict[str, Any]:
    """Select every instance of one repeated statement and record its identity."""
    rects = _bounds_in_band(client, band)
    observed = []
    for bounds in rects:
        facts = _instance_facts(_select_at(client, bounds, expected_lock))
        if facts["kind"] != expected_kind:
            raise AssertionError(f"{widget_id} kind was {facts['kind']!r}, want {expected_kind!r}")
        if facts["instance_count"] != expected_count:
            raise AssertionError(
                f"{widget_id} instance_count was {facts['instance_count']!r}, want {expected_count}"
            )
        if facts["source_line"] != expected_line:
            raise AssertionError(
                f"{widget_id} source line was {facts['source_line']!r}, want {expected_line}"
            )
        observed.append({"rect": bounds, **facts})
    return {
        "widget_id": widget_id,
        "instances": observed,
        "selectable_instance_count": len(observed),
        "distinct_instance_keys": len({str(item["instance_key"]) for item in observed}),
    }


def run_editor_loop_live_scenario(client: Any, *, fixture_path: Path) -> dict[str, Any]:
    """Prove instances are distinguishable and that the write gate holds."""
    baseline_sha = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    _require_ok(client.request("editor_task0_start", {"screen": FIXTURE_SCREEN}), "loop start")

    report: dict[str, Any] = {"baseline_sha256": baseline_sha}

    # A vertically stacked loop is the only repetition whose instances can each
    # be clicked, so it carries the "select the intended instance" evidence.
    report["vbox_loop"] = _prove_repetition(
        client,
        widget_id="loop_vbox_target",
        band={"x": 900},
        expected_kind="loop",
        expected_lock="LOOP_INSTANCE_UNSUPPORTED",
        expected_count=3,
        expected_line=VBOX_LOOP_LINE,
    )
    report["expression_loop"] = _prove_repetition(
        client,
        widget_id="loop_expr_target",
        band={"y": 300},
        expected_kind="loop",
        expected_lock="LOOP_INSTANCE_UNSUPPORTED",
        expected_count=3,
        expected_line=EXPRESSION_LOOP_LINE,
    )
    report["repeated_use"] = _prove_repetition(
        client,
        widget_id="loop_used_target",
        band={"y": 500},
        expected_kind="use",
        expected_lock="REPEATED_USE_UNSUPPORTED",
        expected_count=2,
        expected_line=REPEATED_USE_LINE,
    )

    # A literal position inside a loop cannot depend on the loop variable, so the
    # three instances coincide. Only the topmost is clickable; the discriminator
    # still reports all three, which is exactly why the write stays blocked.
    literal_rects = _bounds_in_band(client, {"y": 160})
    literal_facts = _instance_facts(
        _select_at(client, literal_rects[0], "LOOP_INSTANCE_UNSUPPORTED")
    )
    if literal_facts["source_line"] != LITERAL_LOOP_LINE:
        raise AssertionError(f"literal loop line was {literal_facts['source_line']!r}")
    report["literal_loop"] = {
        "coincident_rects": literal_rects,
        "distinct_origins": len({(r["x"], r["y"]) for r in literal_rects}),
        **literal_facts,
    }

    # Control: a non-repeated statement on the same screen stays static and is
    # not caught by the repetition gate.
    unique_facts = _instance_facts(_select_at(client, _bounds_in_band(client, {"y": 600})[0], None))
    if unique_facts["kind"] != "static" or unique_facts["instance_count"] != 1:
        raise AssertionError(f"unique control was not static: {unique_facts!r}")
    if unique_facts["lock_reason"] in ("LOOP_INSTANCE_UNSUPPORTED", "REPEATED_USE_UNSUPPORTED"):
        raise AssertionError(f"unique control hit a repetition lock: {unique_facts!r}")
    if unique_facts["source_line"] != UNIQUE_LINE:
        raise AssertionError(f"unique control line was {unique_facts['source_line']!r}")
    report["unique_control"] = unique_facts

    # The gate held: nothing in this scenario was allowed to touch the source.
    report["source_unchanged"] = (
        hashlib.sha256(fixture_path.read_bytes()).hexdigest() == baseline_sha
    )
    report["verdict"] = {
        "selection": "proven",
        "instance_specific_source_write": "blocked",
    }
    return report
