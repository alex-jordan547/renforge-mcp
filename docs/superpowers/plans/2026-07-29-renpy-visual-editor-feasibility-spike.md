# Ren'Py Visual Editor Feasibility Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, on the real Ren'Py 8.5.3 SDK, the runtime traversal, hit-testing, reversible override, and SL2 rebinding seams required by the concrete V1 visual-editor adapter roster, then retain only adapters supported by recorded evidence.

**Architecture:** Add an opt-in, spike-only Ren'Py resource that the existing launcher injects beside the normal bridge. A Python driver launches the demo game through `BridgeSession`, drives correlated spike phases through the existing authenticated request channel, captures JSON and screenshots, and renders adapter behavior sheets. The spike does not build the host coordinator, source patcher, transaction layer, or production editor overlay.

**Tech Stack:** Python 3.11+, Ren'Py 8.5.3 SDK, injected Ren'Py screen language/Python, existing `BridgeClient` JSON RPC, Pillow for bounded screenshot checks.

## Global Constraints

- Run against exactly Ren'Py 8.5.3; a different SDK version makes the result inconclusive.
- Keep the spike opt-in through `launch_with_bridge(..., editor_spike=True)`; normal sessions must not inject or register spike code.
- Do not implement the production coordinator, CST patcher, source transaction, or Save code path.
- Do not mutate shared Ren'Py styles. Runtime experiments must use a reversible outer wrapper or another isolated seam recorded in evidence.
- Use transformed painted quads/bounds intersected with effective clips for V1 hit testing; per-pixel alpha is explicitly out of scope.
- Exclude every spike/RenForge overlay node from hit-test candidates.
- Keep scene image tags measure-only; scene provenance and non-destructive `at_list` handling require a separate spike.
- Every retained adapter requires a live fixture, before/after geometry, object-identity evidence, style-preservation evidence, restart/rebind evidence, and a complete behavior sheet.
- A failed adapter is removed from the V1 roster rather than patched around or reported as successful.
- The live run is the only proof for Ren'Py runtime hooks. Add and run focused unit tests only for the permanent launcher opt-in/cleanup behavior and the pure report-schema/roster reducer; do not mock runtime hook success.
- A process exit code of `0` means the experiment completed and produced a conclusive roster, not that every candidate adapter passed. Exit `2` for missing/inconclusive evidence.

---

## File structure

- Create `src/renforge/bridge/editor_spike.rpy` — opt-in fixture screen, runtime graph probe, hit tester, override experiments, and spike RPC handlers.
- Create `src/renforge/editor_spike.py` — typed report schema, phase driver, evidence validation, roster decision, and Markdown behavior-sheet renderer.
- Create `scripts/run_visual_editor_spike.py` — CLI entry point for the experiment.
- Modify `src/renforge/bridge/launcher.py` — conditionally inject and always clean up the spike resource.
- Modify `tests/test_bridge_launcher.py` — protect normal launch, opt-in injection, and cleanup after success/failure.
- Create `tests/test_editor_spike.py` — validate deterministic report-schema rejection and roster reduction.
- Modify `docs/superpowers/specs/2026-07-29-renforge-visual-editor-design.md` — replace the candidate roster with the measured retained roster and approved behavior sheets after the run.
- Create `docs/superpowers/spikes/2026-07-29-visual-editor-runtime.md` — durable human-readable result and gate decision.
- Generate but do not commit `.renforge/editor-spike/result.json` and `.renforge/editor-spike/screenshots/*.png` — raw machine evidence.

### Shared spike protocol

The implementation tasks use these exact commands and result envelopes:

```text
editor_spike_prepare   -> show fixture, return fixture generation
editor_spike_graph     -> return graph, geometry, clips, and hit-test cases
editor_spike_apply     -> apply one adapter operation and return before/after evidence
editor_spike_history   -> exercise undo, redo, restart, rebind, reset
editor_spike_finish    -> hide fixture and return final runtime state
```

```python
class SpikeReply(TypedDict):
    ok: bool
    phase: str
    generation: int
    evidence: dict[str, object]
    errors: list[str]
```

The final JSON has this exact top-level shape:

```python
class SpikeReport(TypedDict):
    schema_version: int              # always 1
    completed: bool
    sdk_requested: str               # "8.5.3"
    sdk_manifest_version: str        # e.g. "8.5.3.26051504"
    sdk_manifest_triplet: list[int]  # [8, 5, 3]
    sdk_runtime_version: str         # live renpy.version_only
    sdk_runtime_triplet: list[int]   # live list(renpy.version_tuple[:3])
    project: str
    phases: list[SpikeReply]
    adapters: list[dict[str, object]]
    retained_roster: list[str]
    removed_roster: list[str]
    decision: str                    # "proceed", "narrow", "blocked", or "inconclusive"
    inconclusive_reasons: list[str]
```

---

### Task 1: Opt-in injection harness and reproducible driver

**Files:**
- Create: `src/renforge/bridge/editor_spike.rpy`
- Create: `src/renforge/editor_spike.py`
- Create: `scripts/run_visual_editor_spike.py`
- Modify: `src/renforge/bridge/launcher.py:35-38,126-148,316-405,547-585`

**Interfaces:**
- Consumes: `RenpySdk`, `get_or_install_sdk()`, `RenpyProject`, `launch_with_bridge()`, `BridgeSession.client.request()`.
- Produces: `launch_with_bridge(..., editor_spike: bool = False) -> BridgeSession`, spike RPC commands, `run_spike(project_root: Path, output: Path, *, display: str = "auto") -> SpikeReport`.

- [ ] **Step 1: Write failing launcher opt-in and cleanup tests**

Extend `tests/test_bridge_launcher.py` using its existing `_make_project`, `_FakeProcess`, `_FakeClient`, and `_write_bridge_info` helpers:

```python
def test_editor_spike_injection_is_opt_in(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    project, sdk, root = _make_project(tmp_path)
    observed: list[bool] = []

    def fake_popen(command, env=None, stdout=None, stderr=None, start_new_session=False):
        observed.append((root / "game" / "zzrenforge_editor_spike.rpy").exists())
        _write_bridge_info(root, env["RENFORGE_BRIDGE_TOKEN"])
        return _FakeProcess()

    monkeypatch.setattr("renforge.bridge.launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "renforge.bridge.launcher.BridgeClient.from_project",
        lambda _project_root: _FakeClient(),
    )

    with launch_with_bridge(sdk, project, editor_spike=False):
        pass
    with launch_with_bridge(sdk, project, editor_spike=True):
        pass

    assert observed == [False, True]
    assert not (root / "game" / "zzrenforge_editor_spike.rpy").exists()
```

Extend `test_remove_bridge_artifacts_deletes_injected_and_runtime_files` to create and assert deletion of:

```python
game / "zzrenforge_editor_spike.rpy"
game / "zzrenforge_editor_spike.rpyc"
game / "zzrenforge_editor_spike.rpyc.bak"
```

Extend `test_failed_launch_removes_every_generated_bridge_artifact` to call `launch_with_bridge(..., editor_spike=True)` and assert the same three paths are absent after the failure.

Run:

```bash
pytest tests/test_bridge_launcher.py::test_editor_spike_injection_is_opt_in \
  tests/test_bridge_launcher.py::test_remove_bridge_artifacts_deletes_injected_and_runtime_files \
  tests/test_bridge_launcher.py::test_failed_launch_removes_every_generated_bridge_artifact -v
```

Expected: FAIL because `editor_spike` and its cleanup paths do not exist yet.

- [ ] **Step 2: Define the spike resource and cleanup names**

Add beside the existing bridge constants in `launcher.py`:

```python
_EDITOR_SPIKE_RESOURCE = Path(__file__).parent / "editor_spike.rpy"
_EDITOR_SPIKE_NAME = "zzrenforge_editor_spike.rpy"
```

Extend `remove_bridge_artifacts()` with `_EDITOR_SPIKE_NAME`, its `.rpyc`, and `.rpyc.bak`. Add `editor_spike: bool = False` to both `_launch_after_project_lock()` and `launch_with_bridge()`, forward it unchanged, and inject `_EDITOR_SPIKE_RESOURCE` only when true:

```python
if editor_spike:
    spike_path = project.game_dir / _EDITOR_SPIKE_NAME
    spike_path.write_text(_EDITOR_SPIKE_RESOURCE.read_text(encoding="utf-8"), encoding="utf-8")
```

Normal launch behavior and signatures remain backward compatible.

- [ ] **Step 3: Create the minimal injected fixture and command registration**

Start `editor_spike.rpy` with a fixture that is not reachable from the demo game's normal flow:

```renpy
image _renforge_spike_animated = Animation(Solid("#4568aaff", xysize=(160, 100)), 0.4, Solid("#68a8e8ff", xysize=(160, 100)), 0.4)

transform _renforge_spike_guard_motion:
    xoffset 0
    linear 0.5 xoffset 20
    linear 0.5 xoffset 0
    repeat

screen _renforge_editor_spike_fixture():
    layer "overlay"
    zorder 900

    fixed:
        id "spike_root"
        xfill True
        yfill True

        add "_renforge_spike_animated":
            id "spike_add"
            xpos 120
            ypos 110
            at Transform(xoffset=11, yoffset=-7, alpha=0.85)

        imagebutton:
            id "spike_imagebutton"
            xpos 340
            ypos 110
            xsize 160
            ysize 100
            idle Solid("#7048d8ff")
            hover Solid("#8468e8ff")
            action NullAction()

        frame:
            id "spike_frame"
            xpos 120
            ypos 290
            xsize 210
            ysize 120
            text "Frame"

        textbutton "Button":
            id "spike_textbutton"
            xpos 380
            ypos 300
            xsize 180
            ysize 90
            action NullAction()

        text "Non-focusable text":
            id "spike_text"
            xpos 150
            ypos 470

        imagebutton:
            id "spike_guard_imagebutton"
            xpos 760
            ypos 110
            idle Solid("#7048d8ff", xysize=(120, 70))
            hover Solid("#8468e8ff", xysize=(120, 70))
            action NullAction()
            at _renforge_spike_guard_motion

        frame:
            id "spike_guard_frame"
            xpos 760
            ypos 220
            xsize 150
            ysize 80
            at _renforge_spike_guard_motion
            text "Guard frame"

        textbutton "Guard button":
            id "spike_guard_textbutton"
            xpos 760
            ypos 330
            at _renforge_spike_guard_motion
            action NullAction()

        text "Guard text":
            id "spike_guard_text"
            xpos 760
            ypos 440
            at _renforge_spike_guard_motion

        fixed:
            id "spike_clip_parent"
            xpos 600
            ypos 110
            xsize 130
            ysize 100
            clipping True
            add Solid("#e05263ff", xysize=(220, 80)):
                id "spike_clipped_child"
                xpos 40
                ypos 20

screen _renforge_editor_spike_chrome():
    layer "overlay"
    zorder 1000
    frame:
        id "spike_chrome"
        xalign 0.5
        ypos 12
        text "RenForge spike chrome — excluded"
```

Register handlers only after the main bridge has initialized:

```renpy
init 1000 python:
    import sys
    import types

    if "_renforge_runtime" not in sys.modules:
        raise Exception("RenForge bridge must load before editor_spike.rpy")

    _renforge_runtime_module = sys.modules["_renforge_runtime"]
    if not hasattr(_renforge_runtime_module, "editor_spike"):
        _renforge_runtime_module.editor_spike = types.SimpleNamespace(
            generation=0,
            originals={},
            wrappers={},
            history=[],
            redo=[],
        )

    def _renforge_spike_state():
        # Mutable displayables, wrappers, and histories live outside store and rollback.
        return sys.modules["_renforge_runtime"].editor_spike


    def _renforge_spike_reply(phase, evidence=None, errors=None):
        state = _renforge_spike_state()
        return {
            "ok": not errors,
            "phase": phase,
            "generation": int(state.generation),
            "evidence": evidence or {},
            "errors": list(errors or []),
        }

    def _renforge_spike_prepare(payload):
        state = _renforge_spike_state()
        state.originals.clear()
        state.wrappers.clear()
        state.history[:] = []
        state.redo[:] = []
        renpy.show_screen("_renforge_editor_spike_fixture")
        renpy.show_screen("_renforge_editor_spike_chrome")
        state.generation += 1
        renpy.restart_interaction()
        return _renforge_spike_reply(
            "prepare",
            {
                "fixture_active": True,
                "chrome_active": True,
                "literal_add_profile": True,
                "runtime_version": str(renpy.version_only),
                "runtime_triplet": [int(part) for part in renpy.version_tuple[:3]],
            },
        )

    _RENFORGE_HANDLERS["editor_spike_prepare"] = _renforge_spike_prepare
```

Later tasks add the remaining handlers to this same file.

- [ ] **Step 4: Create the typed Python runner skeleton**

Implement `src/renforge/editor_spike.py` with `TypedDict` definitions matching the shared schema and this driver contract:

```python
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TypedDict

from renforge.bridge.launcher import launch_with_bridge
from renforge.project import RenpyProject
from renforge.sdk import _sdk_internal_version, _version_tuple, get_or_install_sdk

EXPECTED_SDK = "8.5.3"
EXPECTED_SDK_TRIPLET = (8, 5, 3)
PHASE_COMMANDS = (
    "editor_spike_prepare",
    "editor_spike_graph",
    "editor_spike_apply",
    "editor_spike_history",
    "editor_spike_finish",
)


def _wait_for_screen(client, name: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reply = client.inspect_screen(name)
        if reply.get("active"):
            return
        time.sleep(0.05)
    raise RuntimeError(f"screen did not become active: {name}")


def run_spike(project_root: Path, output: Path, *, display: str = "auto") -> dict:
    project = RenpyProject(project_root.resolve())
    sdk = get_or_install_sdk(EXPECTED_SDK, project_root=project.root)
    manifest_version = _sdk_internal_version(sdk.root)
    manifest_triplet = _version_tuple(manifest_version or "")
    if manifest_triplet != EXPECTED_SDK_TRIPLET:
        raise RuntimeError(
            f"visual-editor spike requires exact SDK 8.5.3, found {manifest_version!r}"
        )

    phases: list[dict] = []
    with launch_with_bridge(
        sdk,
        project,
        editor_spike=True,
        display=display,
        audio="dummy",
        savedir="temporary",
        persistent="empty",
    ) as session:
        prepare = session.client.request("editor_spike_prepare")
        phases.append(prepare)
        _wait_for_screen(session.client, "_renforge_editor_spike_fixture")
        # Tasks 2–4 append the remaining commands and report reduction.

    report = {
        "schema_version": 1,
        "completed": False,
        "sdk_requested": EXPECTED_SDK,
        "sdk_manifest_version": manifest_version,
        "sdk_manifest_triplet": list(manifest_triplet),
        "sdk_runtime_version": prepare["evidence"]["runtime_version"],
        "sdk_runtime_triplet": prepare["evidence"]["runtime_triplet"],
        "project": str(project.root),
        "phases": phases,
        "adapters": [],
        "retained_roster": [],
        "removed_roster": [],
        "decision": "inconclusive",
        "inconclusive_reasons": ["graph and adapter phases not yet run"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report
```

Make the CLI parse `--project`, `--output`, `--display`, and optional `--phase harness|full`, call `run_spike()`, print the JSON output path and decision, and return `2` only when `completed` is false.

- [ ] **Step 5: Run focused launcher tests, then the harness phase**

First rerun the three focused pytest cases from Step 1.

Expected: PASS, proving default launch remains unchanged and every spike artifact is removed after normal teardown and failed launch.

Run:

```bash
python scripts/run_visual_editor_spike.py \
  --project examples/demo_game \
  --output .renforge/editor-spike/harness.json \
  --phase harness
```

Expected evidence:

```text
sdk_requested=8.5.3 sdk_manifest_version=8.5.3.* sdk_runtime_triplet=[8,5,3]
phase=prepare ok=true runtime_version=8.5.3.*
screen=_renforge_editor_spike_fixture active=true
completed=false decision=inconclusive
```

The command intentionally exits `2` because later evidence is absent. After teardown, verify through the runner's cleanup report that `game/renforge_bridge.rpy` and `game/zzrenforge_editor_spike.rpy` no longer exist.

- [ ] **Step 6: Commit the reproducible harness**

```bash
git add src/renforge/bridge/editor_spike.rpy src/renforge/bridge/launcher.py src/renforge/editor_spike.py scripts/run_visual_editor_spike.py tests/test_bridge_launcher.py
git commit -m "spike: add Ren'Py editor runtime harness"
```

---

### Task 2: Runtime graph, transformed-bounds hit testing, and chrome exclusion

**Files:**
- Modify: `src/renforge/bridge/editor_spike.rpy`
- Modify: `src/renforge/editor_spike.py`

**Interfaces:**
- Consumes: active fixture from Task 1 and existing bridge helpers `_renforge_scene_place()` and `_renforge_jsonable()`.
- Produces: `editor_spike_graph` evidence containing `nodes`, `hit_cases`, `geometry_seam`, `clip_seam`, and `graph_pass`.

- [ ] **Step 1: Add stable fixture keys and graph records**

In `editor_spike.rpy`, define:

```python
_SPIKE_WIDGET_IDS = (
    "spike_root",
    "spike_add",
    "spike_imagebutton",
    "spike_frame",
    "spike_textbutton",
    "spike_text",
    "spike_guard_imagebutton",
    "spike_guard_frame",
    "spike_guard_textbutton",
    "spike_guard_text",
    "spike_clip_parent",
    "spike_clipped_child",
    "spike_chrome",
)


_SPIKE_WIDGET_SCREENS = {
    "spike_chrome": "_renforge_editor_spike_chrome",
}


def _renforge_spike_widget(widget_id):
    screen_name = _SPIKE_WIDGET_SCREENS.get(
        widget_id,
        "_renforge_editor_spike_fixture",
    )
    return renpy.get_widget(screen_name, widget_id)


def _renforge_spike_location(displayable):
    location = getattr(displayable, "_location", None)
    if isinstance(location, (list, tuple)) and len(location) >= 2:
        return [str(location[0]), int(location[1])]
    return None
```

Build one graph by traversing both active screen roots, `_renforge_editor_spike_fixture` and `_renforge_editor_spike_chrome`. Child discovery must record the exact seam used (`children`, `child`, or `visit()`) and avoid duplicates by object identity. Each node record contains:

```python
{
    "key": "fixture:<widget-id-or-object-index>",
    "widget_id": widget_id_or_none,
    "runtime_object_id": id(displayable),
    "parent_key": parent_key_or_none,
    "paint_index": integer,
    "depth": integer,
    "type": displayable.__class__.__name__,
    "location": [file, line] or None,
    "quad": [[x0, y0], [x1, y1], [x2, y2], [x3, y3]],
    "aabb": {"x": x, "y": y, "width": width, "height": height},
    "effective_clip": {"x": x, "y": y, "width": width, "height": height} or None,
    "child_seam": "children" | "child" | "visit",
    "overlay": bool,
}
```

Do not report a pass when transform or clip data is guessed. If no exact runtime seam exists, set the corresponding value to `None` and add an explicit error.

- [ ] **Step 2: Implement deterministic bounds hit testing**

Add pure geometry helpers inside the spike resource:

```python
def _renforge_spike_point_in_quad(point, quad):
    px, py = point
    sign = None
    for index in range(4):
        ax, ay = quad[index]
        bx, by = quad[(index + 1) % 4]
        cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        current = cross >= 0
        if sign is None:
            sign = current
        elif current != sign:
            return False
    return True


def _renforge_spike_hit(nodes, x, y):
    candidates = []
    for node in nodes:
        if node["overlay"]:
            continue
        clip = node["effective_clip"]
        if clip and not (
            clip["x"] <= x < clip["x"] + clip["width"]
            and clip["y"] <= y < clip["y"] + clip["height"]
        ):
            continue
        quad = node["quad"]
        if quad and _renforge_spike_point_in_quad((x, y), quad):
            candidates.append(node)
    candidates.sort(key=lambda item: (item["paint_index"], item["depth"]), reverse=True)
    return [item["key"] for item in candidates]
```

This function deliberately does not sample alpha.

- [ ] **Step 3: Add graph handler and fixed hit cases**

`editor_spike_graph` must evaluate these cases from measured fixture bounds rather than hard-coded window pixels:

```text
non_focusable_add_center       -> first hit is spike_add
non_focusable_text_center      -> first hit is spike_text
clipped_child_inside_clip      -> first hit is spike_clipped_child
clipped_child_outside_clip     -> spike_clipped_child absent
chrome_center                  -> spike_chrome exists in nodes with overlay=true and is absent from hit candidates
```

The phase passes only when all fixture widgets, including `spike_chrome`, have parent and paint-order records; transformed quads are non-null; effective clipping excludes the clipped child's overflow; the chrome node is explicitly present with `overlay is True`; and the five hit cases match. Absence of the chrome node is a graph failure, not evidence of exclusion.

- [ ] **Step 4: Capture graph evidence with a real rendered frame**

Extend the driver to call `editor_spike_graph` after prepare, save `session.client.screenshot()` to `.renforge/editor-spike/screenshots/graph.png`, and validate:

```python
required = {
    "non_focusable_add_center": "spike_add",
    "non_focusable_text_center": "spike_text",
    "clipped_child_inside_clip": "spike_clipped_child",
}
for case, expected in required.items():
    hits = graph_reply["evidence"]["hit_cases"][case]
    if not hits or not hits[0].endswith(expected):
        inconclusive.append(f"hit case failed: {case}")
```

Run:

```bash
python scripts/run_visual_editor_spike.py \
  --project examples/demo_game \
  --output .renforge/editor-spike/graph.json \
  --phase graph
```

Expected: `phase=graph ok=true`, every fixed hit case passes, `graph.png` is non-empty, and teardown removes both injected files. A missing matrix/clip seam is a conclusive adapter-blocking result, not permission to substitute public `scene_tree` bounds.

- [ ] **Step 5: Commit graph and hit-test probe**

```bash
git add src/renforge/bridge/editor_spike.rpy src/renforge/editor_spike.py
git commit -m "spike: probe Ren'Py editor graph seams"
```

---

### Task 3: Reversible runtime adapters, history, and SL2 rebinding

**Files:**
- Modify: `src/renforge/bridge/editor_spike.rpy`
- Modify: `src/renforge/editor_spike.py`

**Interfaces:**
- Consumes: graph node identity and parent seams from Task 2.
- Produces: `editor_spike_apply` and `editor_spike_history` evidence for `screen_add_image`, `screen_imagebutton`, `screen_frame`, `screen_textbutton`, and `screen_text`.

- [ ] **Step 1: Define the candidate adapter behavior sheets in code**

Use this exact candidate configuration; measured results may remove entries but may not silently alter their semantics:

```python
SPIKE_ADAPTERS = {
    "screen_add_image": {
        "widget_id": "spike_add",
        "operations": ["move", "scale_resize", "rotate"],
        "move_delta": [37, 23],
        "resize_scale": [1.25, 1.25],
        "aspect_ratio": "locked",
        "pivot": "visual_center",
        "rotate": 17.0,
        "rotate_pad": False,
        "transform_anchor": True,
        "minimum_size": [16, 16],
        "requires_animation_continuity": True,
        "existing_transform": {"xoffset": 11, "yoffset": -7, "alpha": 0.85},
        "requires_existing_transform_preservation": True,
        "capability_guard": None,
    },
    "screen_imagebutton": {
        "widget_id": "spike_imagebutton",
        "operations": ["move", "allocation_resize"],
        "move_delta": [37, 23],
        "resize_delta": [40, 24],
        "fixed_edge": "top_left",
        "minimum_size": [44, 44],
        "requires_animation_continuity": False,
        "requires_existing_transform_preservation": False,
        "capability_guard": "spike_guard_imagebutton",
    },
    "screen_frame": {
        "widget_id": "spike_frame",
        "operations": ["move", "allocation_resize"],
        "move_delta": [37, 23],
        "resize_delta": [40, 24],
        "fixed_edge": "top_left",
        "minimum_size": [32, 32],
        "requires_animation_continuity": False,
        "requires_existing_transform_preservation": False,
        "capability_guard": "spike_guard_frame",
    },
    "screen_textbutton": {
        "widget_id": "spike_textbutton",
        "operations": ["move", "allocation_resize"],
        "move_delta": [37, 23],
        "resize_delta": [40, 24],
        "fixed_edge": "top_left",
        "minimum_size": [44, 44],
        "requires_animation_continuity": False,
        "requires_existing_transform_preservation": False,
        "capability_guard": "spike_guard_textbutton",
    },
    "screen_text": {
        "widget_id": "spike_text",
        "operations": ["move"],
        "move_delta": [37, 23],
        "requires_animation_continuity": False,
        "requires_existing_transform_preservation": False,
        "capability_guard": "spike_guard_text",
    },
}
```

The `screen_add_image` behavior sheet fixes corner resize to a locked aspect ratio around the visual center. Container/button resize fixes the top-left edge and changes allocation; rotation is unavailable for those adapters.

`SPIKE_ADAPTERS` and a pure `expected_behavior_sheet(name)` builder in `src/renforge/editor_spike.py` are the only semantic source of truth. The driver sends the chosen config with each bridge request. The bridge reports measured evidence but may not rewrite operations, pivots, fixed edges, units, minimum sizes, aspect policies, transform order, or unsupported combinations.

The four `spike_guard_*` fixtures carry an active ATL transform and are not adapter candidates. Before a corresponding static adapter can pass, its measured capability guard must classify the transformed variant as measure-only. The spike must not attempt to wrap or manipulate a guard fixture.

- [ ] **Step 2: Implement an evidence-first child replacement seam**

Add a replacement helper that returns a seam name and refuses unknown parent shapes:

```python
def _renforge_spike_replace_child(parent, old, new):
    if getattr(parent, "child", None) is old:
        parent.child = new
        return "child"
    children = getattr(parent, "children", None)
    if isinstance(children, list):
        for index, entry in enumerate(children):
            if entry is old:
                children[index] = new
                return "children"
            if isinstance(entry, tuple) and entry and entry[0] is old:
                children[index] = (new,) + entry[1:]
                return "children_tuple"
    return None
```

Wrap the original child with `Transform` only when replacement returns a known seam. Record the original object, parent, exact list entry, style object ID, style-prefix fields, and baseline geometry before applying the wrapper. Never mutate `displayable.style`.

Apply working transforms through an outer wrapper:

```python
wrapper = Transform(
    child=original,
    xoffset=move_x,
    yoffset=move_y,
    xzoom=scale_x,
    yzoom=scale_y,
    rotate=rotation,
    rotate_pad=False,
    transform_anchor=True,
)
```

For allocation-resize adapters, use the runtime property seam proven by inspection. If no isolated allocation seam exists without a style mutation, return a failed adapter record; do not fall back to `style.xsize` or `style.ysize`.

- [ ] **Step 3: Record before/after invariants for every operation**

`editor_spike_apply` accepts `{"adapter": name, "operation": operation}` and returns:

```python
{
    "adapter": name,
    "operation": operation,
    "passed": bool,
    "replacement_seam": seam_or_none,
    "before": {
        "runtime_object_id": int,
        "style_object_id": int,
        "geometry": geometry,
        "location": location,
        "animation": {"st": float, "at": float, "sample_time": float} or None,
    },
    "after": {
        "runtime_object_id": int,
        "wrapped_child_id": int,
        "style_object_id": int,
        "geometry": geometry,
        "location": location,
        "animation": {"st": float, "at": float, "sample_time": float} or None,
    },
    "existing_transform": {
        "runtime_object_id": int,
        "xoffset": 11,
        "yoffset": -7,
        "alpha": 0.85,
    } or None,
    "expected_geometry": geometry,
    "style_preserved": bool,
    "animation_continuous": bool,
    "visual_delta_within_one_pixel": bool,
    "existing_transform_preserved": bool,
    "capability_guard_passed": bool,
    "guard_evidence": {
        "target_widget_id": str,
        "reason": "dynamic_at",
        "capabilities": {"move": False, "resize": False, "rotate": False},
        "wrapper_present": False,
        "operation_command_count": 0,
        "history_command_count": 0,
        "screenshot": screenshot_descriptor,
        "passed": True,
    } or None,
    "screenshot_refs": [baseline_path, result_path],
    "animation_probe": {
        "seam": exact_runtime_seam_or_none,
        "samples": list[dict],
        "rendered_phase_samples": list[dict],
        "control_recreation_samples": list[dict],
    } or None,
    "errors": list[str],
}
```

A pass requires unchanged style identity, expected geometry within one logical pixel, unchanged source location on the wrapped child, a named reversible replacement seam, and existing screenshot files for baseline and result. `screen_add_image` is the literal source profile `add "_renforge_spike_animated"` with an inline literal `Transform`. Its editor wrapper must compose outside that real pre-existing transform without changing the current transform object's identity or its `xoffset=11`, `yoffset=-7`, and `alpha=0.85` during a live interaction. Animation continuity must be measured from Ren'Py's actual `Animation` displayable through an exact runtime timing seam plus rendered color-phase samples; the fixture must not use a custom displayable, cached factory, or editor-owned animation clock. If no exact timing seam can be established, the adapter fails rather than inferring continuity from object identity.

For `screen_imagebutton`, `screen_frame`, `screen_textbutton`, and `screen_text`, a pass additionally requires a complete `guard_evidence` record proving the configured active-transform target was classified measure-only, all edit capabilities were false for reason `dynamic_at`, no wrapper/operation/history command touched it, and a screenshot captured the guard. `capability_guard_passed` is derived from that record, never accepted as standalone evidence.

- [ ] **Step 4: Exercise undo, redo, interaction restart, and rebind**

`editor_spike_history` runs two explicit sequences for each candidate adapter:

```text
continuity: baseline -> apply -> undo -> redo -> renpy.restart_interaction()
            -> reapply working transform -> reset
recreation: capture RuntimeInstanceKey -> hide fixture screen -> restart interaction
            -> show fixture screen -> restart interaction -> resolve without widget id
            -> reapply working transform -> reset
```

The continuity sequence must prove that editor operations and a normal interaction restart do not restart the literal image animation or replace the current inline source transform unnecessarily.

The recreation sequence must force a new screen root and a changed target owner-chain object identity; a reused leaf image object does not waive this requirement. Rebinding receives only the pre-recreation `RuntimeInstanceKey` (screen name, source location, statement kind, ancestry/source path, invocation ordinal, and generation), not `widget_id`. Fixture IDs may be used only afterward as a test oracle. A rebind passes only when:

- the screen root and at least one target owner-chain object ID differ after forced hide/show;
- `rebind_evidence["lookup_fields"]` contains no widget ID and `rebind_evidence["used_widget_id"] is False`;
- the target is rediscovered from screen ownership, source location, statement kind, ancestry/source path, and generation rather than a stale object reference;
- the working transform reapplies to expected geometry;
- reset restores baseline geometry and removes the outer wrapper;
- the original style identity remains unchanged throughout;
- for `screen_add_image`, the current inline transform retains its identity and parameters during the continuity sequence; after forced recreation, the new source transform has the same parameters and the editor wrapper composes in the same order;
- for `screen_add_image`, the forced-recreation animation samples match an unedited control recreation's phase policy, while the continuity sequence itself never restarts the animation;
- for every non-add adapter, the corresponding `spike_guard_*` target remains unwrapped, receives no operation or history command, reports measure-only throughout both sequences, and yields the same structured `guard_evidence` at adapter, operation, and history levels.

- [ ] **Step 5: Run the complete adapter experiment**

Extend the driver to invoke every declared operation, then the history phase, taking screenshots after baseline, apply, restart/rebind, and reset:
Each RPC reply is correlated by adapter and operation and stored in an in-memory matrix. After all calls complete, the driver emits exactly one aggregate `apply` phase and one aggregate `history` phase; raw per-RPC envelopes never become top-level phases.


```bash
python scripts/run_visual_editor_spike.py \
  --project examples/demo_game \
  --output .renforge/editor-spike/adapters.json \
  --phase adapters
```

Expected conclusive output:

```text
phase=graph ok=true|false with explicit evidence
adapter=screen_add_image pass=true|false errors=[...]
adapter=screen_imagebutton pass=true|false errors=[...]
adapter=screen_frame pass=true|false errors=[...]
adapter=screen_textbutton pass=true|false errors=[...]
adapter=screen_text pass=true|false errors=[...]
completed=true decision=proceed|narrow|blocked
```

Any missing phase, screenshot, geometry, style invariant, animation-continuity record, or rebind record makes the run inconclusive and exits `2`.

- [ ] **Step 6: Commit adapter probe**

```bash
git add src/renforge/bridge/editor_spike.rpy src/renforge/editor_spike.py
git commit -m "spike: verify reversible Ren'Py editor adapters"
```

---

### Task 4: Run the gate, publish behavior sheets, and narrow the V1 roster

**Files:**
- Modify: `src/renforge/editor_spike.py`
- Create: `docs/superpowers/spikes/2026-07-29-visual-editor-runtime.md`
- Modify: `docs/superpowers/specs/2026-07-29-renforge-visual-editor-design.md`
- Create: `tests/test_editor_spike.py`

**Interfaces:**
- Consumes: conclusive `SpikeReport` from Tasks 1–3.
- Produces: human-readable spike report, final retained V1 roster, complete behavior sheets, and a documented proceed/narrow/blocked gate decision.

- [ ] **Step 1: Write failing report-schema and roster-decision tests**

Create `tests/test_editor_spike.py` with a complete adapter factory and negative contracts for every promotion invariant:

```python
import pytest

from renforge.editor_spike import decide_roster, validate_report


PHASES = ("prepare", "graph", "apply", "history", "finish")
GEOMETRY = {"quad": [[0, 0], [10, 0], [10, 10], [0, 10]], "aabb": [0, 0, 10, 10]}
SCREENSHOT = {"path": "screenshots/probe.png", "sha256": "a" * 64, "width": 1280, "height": 720}
CANDIDATES = {
    "screen_add_image": {
        "operations": ["move", "scale_resize", "rotate"],
        "minimum_size": [16, 16],
        "fixed_edge_or_pivot": "visual_center",
        "aspect_ratio_policy": "locked",
        "guard": None,
    },
    "screen_imagebutton": {
        "operations": ["move", "allocation_resize"],
        "minimum_size": [44, 44],
        "fixed_edge_or_pivot": "top_left",
        "aspect_ratio_policy": "free",
        "guard": "spike_guard_imagebutton",
    },
    "screen_frame": {
        "operations": ["move", "allocation_resize"],
        "minimum_size": [32, 32],
        "fixed_edge_or_pivot": "top_left",
        "aspect_ratio_policy": "free",
        "guard": "spike_guard_frame",
    },
    "screen_textbutton": {
        "operations": ["move", "allocation_resize"],
        "minimum_size": [44, 44],
        "fixed_edge_or_pivot": "top_left",
        "aspect_ratio_policy": "free",
        "guard": "spike_guard_textbutton",
    },
    "screen_text": {
        "operations": ["move"],
        "minimum_size": None,
        "fixed_edge_or_pivot": "not_applicable",
        "aspect_ratio_policy": "not_applicable",
        "guard": "spike_guard_text",
    },
}
EXPECTED_WIDGETS = (
    "spike_root",
    "spike_add",
    "spike_imagebutton",
    "spike_frame",
    "spike_textbutton",
    "spike_text",
    "spike_guard_imagebutton",
    "spike_guard_frame",
    "spike_guard_textbutton",
    "spike_guard_text",
    "spike_clip_parent",
    "spike_clipped_child",
    "spike_chrome",
)


def _node(widget_id: str, index: int) -> dict:
    return {
        "key": f"fixture:{widget_id}",
        "widget_id": widget_id,
        "runtime_object_id": index + 100,
        "parent_key": None if widget_id == "spike_root" else "fixture:spike_root",
        "paint_index": index,
        "depth": 0 if widget_id == "spike_root" else 1,
        "type": "FixtureDisplayable",
        "location": ["game/renforge_editor_spike.rpy", index + 1],
        "quad": GEOMETRY["quad"],
        "aabb": {"x": 0, "y": 0, "width": 10, "height": 10},
        "effective_clip": None,
        "child_seam": "children",
        "overlay": widget_id == "spike_chrome",
    }


def _behavior(name: str) -> dict:
    config = CANDIDATES[name]
    is_add = name == "screen_add_image"
    is_allocation = "allocation_resize" in config["operations"]
    return {
        "editable_properties": (
            ["xpos", "ypos", "zoom", "rotate"]
            if is_add
            else ["xpos", "ypos", "xsize", "ysize"]
            if is_allocation
            else ["xpos", "ypos"]
        ),
        "units": {
            "position": "logical_pixels",
            "resize": "scale" if is_add else "logical_pixels" if is_allocation else "not_applicable",
            "rotation": "degrees" if is_add else "not_applicable",
        },
        "drag_handle_meaning": config["operations"],
        "fixed_edge_or_pivot": config["fixed_edge_or_pivot"],
        "anchor_compensation": [0, 0],
        "aspect_ratio_policy": config["aspect_ratio_policy"],
        "minimum_size": config["minimum_size"],
        "rotation_pivot": "visual_center" if is_add else None,
        "rotate_pad": False if is_add else None,
        "transform_anchor": True if is_add else None,
        "transform_composition_order": "outer_after_existing" if is_add else "outer_runtime_wrapper",
        "expected_hit_geometry": "transformed_quad_intersect_clip",
        "unsupported_combinations": (
            ["dynamic_at", "non_literal_displayable"]
            if is_add
            else ["active_transform", "active_animation"]
        ),
    }


def _guard(name: str) -> dict | None:
    target = CANDIDATES[name]["guard"]
    if target is None:
        return None
    return {
        "target_widget_id": target,
        "reason": "dynamic_at",
        "capabilities": {"move": False, "resize": False, "rotate": False},
        "wrapper_present": False,
        "operation_command_count": 0,
        "history_command_count": 0,
        "screenshot": SCREENSHOT,
        "passed": True,
    }


def _operation(name: str, operation: str, passed: bool, guard: dict | None) -> dict:
    state = {
        "runtime_object_id": 11,
        "style_object_id": 12,
        "geometry": GEOMETRY,
        "location": ["game/renforge_editor_spike.rpy", 18],
        "animation": {"seam": "renpy.animation.timebase", "value": 0.25},
    }
    is_add = name == "screen_add_image"
    return {
        "passed": passed,
        "adapter": name,
        "operation": operation,
        "before": state,
        "after": {**state, "runtime_object_id": 13, "wrapped_child_id": 11},
        "existing_transform": {
            "runtime_object_id": 11,
            "xoffset": 11,
            "yoffset": -7,
            "alpha": 0.85,
        } if is_add else None,
        "expected_geometry": GEOMETRY,
        "replacement_seam": "children",
        "style_preserved": passed,
        "animation_continuous": passed if is_add else False,
        "visual_delta_within_one_pixel": passed,
        "existing_transform_preserved": passed if is_add else False,
        "capability_guard_passed": guard is None or guard["passed"],
        "guard_evidence": guard,
        "screenshot_refs": [SCREENSHOT, SCREENSHOT],
        "animation_probe": {
            "seam": "renpy.animation.timebase",
            "samples": [{"before": 0.25, "after": 0.30}],
            "rendered_phase_samples": [{"color": "#4568aaff"}],
            "control_recreation_samples": [{"color": "#4568aaff"}],
        } if is_add else None,
        "errors": [] if passed else ["measured operation failure"],
    }


def _adapter(name: str, passed: bool) -> dict:
    config = CANDIDATES[name]
    guard = _guard(name)
    operations = list(config["operations"])
    operation_evidence = {
        operation: _operation(name, operation, passed, guard)
        for operation in operations
    }
    is_add = name == "screen_add_image"
    return {
        "name": name,
        "pass": passed,
        "operations": operations,
        "operation_evidence": operation_evidence,
        "replacement_seam": "children",
        "geometry_evidence": {"within_one_pixel": passed, "samples": [GEOMETRY]},
        "style_preserved": passed,
        "animation_continuous": passed if is_add else False,
        "existing_transform_preserved": passed if is_add else False,
        "capability_guard_passed": guard is None or guard["passed"],
        "guard_evidence": guard,
        "rebind_evidence": {
            "passed": passed,
            "continuity": {"passed": passed, "animation_restarted": False},
            "recreation": {
                "passed": passed,
                "root_id_changed": passed,
                "owner_chain_changed": passed,
                "lookup_fields": [
                    "screen_name",
                    "location",
                    "statement_kind",
                    "source_path",
                    "invocation_ordinal",
                    "generation",
                ],
                "used_widget_id": False,
                "geometry_within_one_pixel": passed,
            },
            "guard_evidence": guard,
            "errors": [] if passed else ["measured rebind failure"],
        },
        "behavior_sheet": _behavior(name),
        "errors": [] if passed else ["measured failure"],
    }


def _phase_evidence(phase: str, adapters: list[dict]) -> dict:
    if phase == "prepare":
        return {
            "fixture_active": True,
            "chrome_active": True,
            "literal_add_profile": True,
            "runtime_version": "8.5.3.26051504",
            "runtime_triplet": [8, 5, 3],
        }
    if phase == "graph":
        return {
            "nodes": [_node(widget_id, index) for index, widget_id in enumerate(EXPECTED_WIDGETS)],
            "hit_cases": {
                "non_focusable_add_center": ["fixture:spike_add"],
                "non_focusable_text_center": ["fixture:spike_text"],
                "clipped_child_inside_clip": ["fixture:spike_clipped_child"],
                "clipped_child_outside_clip": [],
                "chrome_center": [],
            },
            "geometry_seam": "render-tree",
            "clip_seam": "render-tree",
            "evidence_complete": True,
            "capability_pass": True,
            "capability_errors": [],
            "screenshot": SCREENSHOT,
        }
    if phase == "apply":
        return {
            "operations": {
                adapter["name"]: adapter["operation_evidence"]
                for adapter in adapters
            },
            "screenshot_manifest": [SCREENSHOT],
        }
    if phase == "history":
        return {
            "adapters": {
                adapter["name"]: adapter["rebind_evidence"]
                for adapter in adapters
            },
            "screenshot_manifest": [SCREENSHOT],
        }
    return {"fixture_active": False, "chrome_active": False, "cleanup_requested": True}


def _report(adapter: dict) -> dict:
    adapters_by_name = {
        name: _adapter(name, True)
        for name in CANDIDATES
    }
    adapters_by_name[adapter["name"]] = adapter
    adapters = [adapters_by_name[name] for name in CANDIDATES]
    return {
        "schema_version": 1,
        "completed": False,
        "sdk_requested": "8.5.3",
        "sdk_manifest_version": "8.5.3.26051504",
        "sdk_manifest_triplet": [8, 5, 3],
        "sdk_runtime_version": "8.5.3.26051504",
        "sdk_runtime_triplet": [8, 5, 3],
        "project": "/tmp/demo_game",
        "phases": [
            {
                "phase": name,
                "ok": True,
                "generation": 1,
                "evidence": _phase_evidence(name, adapters),
                "errors": [],
            }
            for name in PHASES
        ],
        "adapters": adapters,
        "retained_roster": [],
        "removed_roster": [],
        "decision": "inconclusive",
        "inconclusive_reasons": [],
    }

def test_validate_report_rejects_failed_phase() -> None:
    report = _report(_adapter("screen_add_image", True))
    report["phases"][1]["ok"] = False
    report["phases"][1]["errors"] = ["graph probe incomplete"]
    assert any("graph phase is not ok" in error for error in validate_report(report))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("sdk_manifest_triplet", [8, 5, 4]),
        ("sdk_runtime_triplet", [8, 5, 4]),
    ),
)
def test_validate_report_rejects_sdk_mismatch(field: str, value: list[int]) -> None:
    report = _report(_adapter("screen_add_image", True))
    report[field] = value
    assert any(field in error for error in validate_report(report))


def test_validate_report_rejects_incomplete_raw_operation() -> None:
    report = _report(_adapter("screen_add_image", True))
    report["adapters"][0]["operation_evidence"]["move"] = {"passed": True}
    assert any("operation_evidence.move.before" in error for error in validate_report(report))


def test_validate_report_rejects_absent_chrome_node() -> None:
    report = _report(_adapter("screen_add_image", True))
    graph = report["phases"][1]["evidence"]
    graph["nodes"] = [node for node in graph["nodes"] if node["widget_id"] != "spike_chrome"]
    assert any("spike_chrome" in error for error in validate_report(report))


def test_validate_report_rejects_incomplete_apply_matrix() -> None:
    report = _report(_adapter("screen_add_image", True))
    report["phases"][2]["evidence"]["operations"] = {}
    assert any("apply.operations" in error for error in validate_report(report))


def test_validate_report_rejects_missing_candidate_adapter() -> None:
    report = _report(_adapter("screen_add_image", True))
    report["adapters"].pop()
    assert any("adapter names" in error for error in validate_report(report))


def test_validate_report_rejects_missing_behavior_field() -> None:
    report = _report(_adapter("screen_add_image", True))
    del report["adapters"][0]["behavior_sheet"]["rotation_pivot"]
    assert "screen_add_image.behavior_sheet.rotation_pivot is required" in validate_report(report)


@pytest.mark.parametrize(
    "field",
    ("style_preserved", "animation_continuous", "existing_transform_preserved"),
)
def test_validate_report_rejects_false_boolean_invariant(field: str) -> None:
    report = _report(_adapter("screen_add_image", True))
    report["adapters"][0][field] = False
    assert any(field in error for error in validate_report(report))

def test_validate_report_rejects_false_capability_guard() -> None:
    report = _report(_adapter("screen_frame", True))
    frame = next(item for item in report["adapters"] if item["name"] == "screen_frame")
    frame["capability_guard_passed"] = False
    assert any("capability_guard_passed" in error for error in validate_report(report))


def test_validate_report_rejects_unsafe_guard_evidence() -> None:
    report = _report(_adapter("screen_frame", True))
    frame = next(item for item in report["adapters"] if item["name"] == "screen_frame")
    frame["guard_evidence"]["wrapper_present"] = True
    assert any("guard_evidence.wrapper_present" in error for error in validate_report(report))


def test_validate_report_rejects_changed_behavior_semantics() -> None:
    report = _report(_adapter("screen_frame", True))
    frame = next(item for item in report["adapters"] if item["name"] == "screen_frame")
    frame["behavior_sheet"]["fixed_edge_or_pivot"] = "visual_center"
    assert any("behavior_sheet.fixed_edge_or_pivot" in error for error in validate_report(report))


def test_validate_report_rejects_false_geometry_invariant() -> None:
    report = _report(_adapter("screen_add_image", True))
    report["adapters"][0]["geometry_evidence"]["within_one_pixel"] = False
    assert any("within_one_pixel" in error for error in validate_report(report))


def test_validate_report_rejects_false_rebind_invariant() -> None:
    report = _report(_adapter("screen_add_image", True))
    report["adapters"][0]["rebind_evidence"]["passed"] = False
    assert any("rebind_evidence.passed" in error for error in validate_report(report))


def test_validate_report_rejects_declared_operation_without_evidence() -> None:
    report = _report(_adapter("screen_add_image", True))
    del report["adapters"][0]["operation_evidence"]["move"]
    assert any("operation_evidence.move" in error for error in validate_report(report))


def test_decide_roster_refuses_invalid_passing_adapter() -> None:
    adapter = _adapter("screen_add_image", True)
    adapter["geometry_evidence"]["within_one_pixel"] = False
    with pytest.raises(ValueError, match="within_one_pixel"):
        decide_roster([adapter])


def test_decide_roster_returns_narrow_for_valid_mixed_results() -> None:
    retained, removed, decision = decide_roster(
        [_adapter("screen_add_image", True), _adapter("screen_frame", False)]
    )
    assert (retained, removed, decision) == (
        ["screen_add_image"],
        ["screen_frame"],
        "narrow",
    )


def test_decide_roster_returns_blocked_when_none_pass() -> None:
    assert decide_roster([_adapter("screen_text", False)]) == (
        [],
        ["screen_text"],
        "blocked",
    )
```

Run:

```bash
pytest tests/test_editor_spike.py -v
```

Expected: FAIL because the reducer functions do not exist.

- [ ] **Step 2: Implement strict report reduction**

Add these exact interfaces:

```python
def validate_report(report: dict) -> list[str]: ...
def decide_roster(adapters: list[dict]) -> tuple[list[str], list[str], str]: ...
def render_markdown(report: dict) -> str: ...
```

Validation must require:

```python
required_top_level_fields = {
    "schema_version",
    "completed",
    "sdk_requested",
    "sdk_manifest_version",
    "sdk_manifest_triplet",
    "sdk_runtime_version",
    "sdk_runtime_triplet",
    "project",
    "phases",
    "adapters",
    "retained_roster",
    "removed_roster",
    "decision",
    "inconclusive_reasons",
}
required_phase_names = {"prepare", "graph", "apply", "history", "finish"}
required_phase_fields = {"phase", "ok", "generation", "evidence", "errors"}
required_phase_evidence_fields = {
    "prepare": {
        "fixture_active",
        "chrome_active",
        "literal_add_profile",
        "runtime_version",
        "runtime_triplet",
    },
    "graph": {
        "nodes",
        "hit_cases",
        "geometry_seam",
        "clip_seam",
        "evidence_complete",
        "capability_pass",
        "capability_errors",
        "screenshot",
    },
    "apply": {"operations", "screenshot_manifest"},
    "history": {"adapters", "screenshot_manifest"},
    "finish": {"fixture_active", "chrome_active", "cleanup_requested"},
}
required_adapter_fields = {
    "name",
    "pass",
    "operations",
    "operation_evidence",
    "replacement_seam",
    "geometry_evidence",
    "style_preserved",
    "animation_continuous",
    "existing_transform_preserved",
    "capability_guard_passed",
    "guard_evidence",
    "rebind_evidence",
    "behavior_sheet",
    "errors",
}
required_operation_fields = {
    "passed",
    "adapter",
    "operation",
    "before",
    "after",
    "existing_transform",
    "expected_geometry",
    "replacement_seam",
    "style_preserved",
    "animation_continuous",
    "visual_delta_within_one_pixel",
    "existing_transform_preserved",
    "capability_guard_passed",
    "guard_evidence",
    "screenshot_refs",
    "animation_probe",
    "errors",
}
required_runtime_state_fields = {
    "runtime_object_id",
    "style_object_id",
    "geometry",
    "location",
    "animation",
}
required_screenshot_fields = {"path", "sha256", "width", "height"}
required_guard_fields = {
    "target_widget_id",
    "reason",
    "capabilities",
    "wrapper_present",
    "operation_command_count",
    "history_command_count",
    "screenshot",
    "passed",
}
expected_adapter_names = {
    "screen_add_image",
    "screen_imagebutton",
    "screen_frame",
    "screen_textbutton",
    "screen_text",
}
required_behavior_fields = {
    "editable_properties",
    "units",
    "drag_handle_meaning",
    "fixed_edge_or_pivot",
    "anchor_compensation",
    "aspect_ratio_policy",
    "minimum_size",
    "rotation_pivot",
    "rotate_pad",
    "transform_anchor",
    "transform_composition_order",
    "expected_hit_geometry",
    "unsupported_combinations",
}
```

Require every top-level field, `schema_version == 1`, a non-empty project path, `sdk_requested == "8.5.3"`, and both manifest/live version triplets exactly equal to `[8, 5, 3]`. `sdk_manifest_version` must come from `<sdk.root>/renpy/vc_version.py`; `sdk_runtime_version` and `sdk_runtime_triplet` must come from the live bridge's `renpy.version_only` and `renpy.version_tuple`, and must equal the corresponding prepare evidence. Never derive an actual-version field from `RenpySdk.version`, which is only the requested resolver label. Before reduction, the runner must verify every screenshot path exists under the output directory, recompute its SHA-256, decode its dimensions, and compare those values with the descriptor.

Require exactly one aggregate envelope for every required phase, every `required_phase_fields` key, the phase-specific evidence fields above, `phase["ok"] is True`, and an empty phase-level `errors` list. Adapter failures belong inside conclusive apply/history evidence and do not make those phase envelopes fail. The phase invariants are:

- `prepare`: fixture and chrome active, literal add profile true, and live runtime version evidence matching the top level;
- `graph`: `evidence_complete is True`, all fixture nodes present, `spike_chrome` present with `overlay is True`, all node records structurally complete, all five hit cases present, and `capability_pass` a measured boolean;
- `apply`: one `operations[adapter][operation]` matrix covering the fixed five adapters and their immutable configured operations exactly once;
- `history`: one `adapters[adapter]` matrix covering the fixed five adapters exactly once with continuity and forced-recreation evidence;
- `finish`: fixture and chrome inactive with cleanup requested.

Require the report's adapter-name set to equal `expected_adapter_names` exactly before reading any per-adapter declarations. Coverage is never derived from the report itself. A missing or extra adapter makes the report inconclusive.

`graph["evidence_complete"]` means the probe ran every documented introspection path and captured a result; it does not mean the capability exists. When `capability_pass is False`, require structured `capability_errors` naming the missing seam and every affected adapter, then require each affected adapter to fail. That remains a conclusive `narrow` or `blocked` result. Missing fields, unexecuted probes, handler failures, or unverifiable observations set `evidence_complete` false and make the report inconclusive.

The runner calls `editor_spike_apply` and `editor_spike_history` as many times as needed but must aggregate their correlated raw replies into those single `apply` and `history` phase envelopes. It must not append one top-level phase per RPC.

For each raw operation, require every `required_operation_fields` key, complete `before`/`after` runtime states, non-null source locations, `wrapped_child_id` after replacement, complete expected/observed geometry, a non-null replacement seam, at least two complete screenshot descriptors, and deterministic error lists. The apply matrix must equal each adapter's `operation_evidence` exactly; the history matrix must equal each adapter's `rebind_evidence` exactly. Reject missing, duplicate, extra, or mismatched adapter/operation keys.

For each rebind record, require complete `continuity` and `recreation` records. A passing recreation requires `root_id_changed`, `owner_chain_changed`, and `geometry_within_one_pixel` true; `used_widget_id` false; and non-empty lookup fields containing screen name, source location, statement kind, source ancestry/path, invocation ordinal, and generation.

Iterate required field names in sorted order so errors are deterministic. For every declared operation, require `operation_evidence[operation]`. For an adapter with `pass is True`, require all of the following:

```python
item["errors"] == []
item["replacement_seam"] is not None
item["geometry_evidence"]["within_one_pixel"] is True
item["style_preserved"] is True
item["rebind_evidence"]["passed"] is True
item["rebind_evidence"]["continuity"]["passed"] is True
item["rebind_evidence"]["recreation"]["passed"] is True
all(
    raw["passed"] is True
    and raw["errors"] == []
    and raw["replacement_seam"] is not None
    and raw["style_preserved"] is True
    and raw["visual_delta_within_one_pixel"] is True
    and len(raw["screenshot_refs"]) >= 2
    for raw in item["operation_evidence"].values()
)
```

Additionally require `animation_continuous is True`, `existing_transform_preserved is True`, and a complete non-null `animation_probe` with an exact seam and runtime/rendered/control samples for every `screen_add_image` operation. A failed adapter must contain at least one measured error and at least one false raw operation or rebind invariant; a summary error string alone cannot manufacture a failure.

For every adapter, require `operations == SPIKE_ADAPTERS[name]["operations"]` and `behavior_sheet == expected_behavior_sheet(name)` by deep value comparison. Emit a field-specific error for the first mismatch; field presence alone is insufficient.

For every non-add candidate adapter, require `capability_guard_passed is True` and the same complete `guard_evidence` object at adapter, raw-operation, and rebind/history levels. Validate every `required_guard_fields` key, the exact configured guard target, reason `dynamic_at`, all three capabilities false, `wrapper_present is False`, both command counts zero, a verified screenshot descriptor, and `passed is True`. The static fixture may set animation and existing-transform invariants to not-applicable values, but the transformed guard fixture must have no wrapper, operation evidence, or history command.

When `completed is False`, require `decision == "inconclusive"`. When `completed is True`, require empty `inconclusive_reasons`, exact retained/removed arrays derived from the adapter results, and the matching `proceed`/`narrow`/`blocked` decision.

`decide_roster(adapters)` must call the same adapter validator defensively and raise `ValueError` on any schema or invariant error. Only validated adapters reach the mechanical reduction:

```python
errors = validate_adapters(adapters)
if errors:
    raise ValueError("; ".join(errors))
retained = sorted(item["name"] for item in adapters if item["pass"] is True)
removed = sorted(item["name"] for item in adapters if item["pass"] is False)
decision = "proceed" if not removed else "narrow" if retained else "blocked"
```

The runner validates the evidence report before roster reduction, fills the final status, then validates the completed report a second time:

```python
errors = validate_report(report)
if errors:
    report["completed"] = False
    report["decision"] = "inconclusive"
    report["inconclusive_reasons"] = errors
else:
    retained, removed, decision = decide_roster(report["adapters"])
    report["retained_roster"] = retained
    report["removed_roster"] = removed
    report["decision"] = decision
    report["completed"] = True
    final_errors = validate_report(report)
    if final_errors:
        report["completed"] = False
        report["retained_roster"] = []
        report["removed_roster"] = []
        report["decision"] = "inconclusive"
        report["inconclusive_reasons"] = final_errors
```

No code path may call `decide_roster` after the first `validate_report` returns an error.

- [ ] **Step 3: Run focused reducer tests, then the final spike**

First run:

```bash
pytest tests/test_editor_spike.py -v
```

Expected: PASS.

Run:

```bash
python scripts/run_visual_editor_spike.py \
  --project examples/demo_game \
  --output .renforge/editor-spike/result.json \
  --display auto \
  --phase full
```

Expected:

- `sdk_actual` is exactly `8.5.3`;
- `completed` is `true`;
- every required phase and behavior-sheet field is present;
- every retained adapter has passing geometry/style/rebind evidence;
- every removed adapter has an explicit failure and screenshot reference;
- the command exits `0` for a conclusive `proceed`, `narrow`, or `blocked` decision;
- both injected `.rpy` resources are removed on teardown.

If the command exits `2`, fix only the missing instrumentation and rerun. Do not reinterpret missing evidence as an adapter failure or pass.

- [ ] **Step 4: Inspect the visual evidence**

Open the four screenshot states for each retained adapter and compare:

```text
baseline -> applied -> rebound -> reset
```

Confirm the selected displayable moves or resizes as reported, no neighboring fixture shifts unexpectedly, chrome remains outside hit candidates, clipped overflow stays unselectable, and reset is visually identical to baseline. Record any mismatch in `result.json`, rerun the affected phase, and retain the adapter only after the corrected evidence agrees.

- [ ] **Step 5: Generate the durable spike report**

Render `docs/superpowers/spikes/2026-07-29-visual-editor-runtime.md` with these sections:

```markdown
# Ren'Py 8.5.3 Visual Editor Runtime Spike

## Verdict
## Environment
## Runtime seams observed
## Hit-test evidence
## Adapter results
### screen_add_image
### screen_imagebutton
### screen_frame
### screen_textbutton
### screen_text
## Retained V1 roster
## Removed adapters and reasons
## Behavior sheets
## Remaining risks
## Gate decision
```

Each adapter section links to its raw screenshot path, records the child-replacement seam and object IDs, and contains every mandatory behavior-sheet field. Do not embed or commit raw project screenshots if they contain user content; this spike uses only the repository demo fixture.

- [ ] **Step 6: Apply the measured roster to the design spec**

Update `docs/superpowers/specs/2026-07-29-renforge-visual-editor-design.md` mechanically:

- remove failed adapters from the concrete V1 roster or mark their unsupported operations measure-only;
- copy the approved behavior-sheet semantics for retained adapters;
- set the status to `Feasibility spike passed — ready for implementation plan` for `proceed` or `narrow`;
- set the status to `Feasibility spike blocked — runtime seam not proven` for `blocked`;
- keep scene images measure-only in every outcome.

Do not broaden the roster beyond the candidates measured by this spike.

- [ ] **Step 7: Request one focused expert gate review**

Provide the updated spec, Markdown spike report, and raw JSON to a reviewer. The review question is limited to:

```text
Does every retained adapter have sufficient live Ren'Py 8.5.3 evidence for
runtime extraction, hit testing, isolated override, rebinding, history, and
fixed behavior semantics? Are any spec claims broader than the evidence?
```

Fix documentation/evidence mismatches. Do not add an adapter to satisfy the reviewer; only a new live run can change the roster.

- [ ] **Step 8: Commit the conclusive gate result**

For `proceed` or `narrow`:

```bash
git add src/renforge/editor_spike.py tests/test_editor_spike.py docs/superpowers/spikes/2026-07-29-visual-editor-runtime.md docs/superpowers/specs/2026-07-29-renforge-visual-editor-design.md
git commit -m "docs: record Ren'Py visual editor feasibility gate"
```

For `blocked`, use the same paths and commit:

```bash
git commit -m "docs: record blocked Ren'Py editor runtime spike"
```

The spike is complete after this commit. A full visual-editor implementation plan is allowed only when the final status is `Feasibility spike passed — ready for implementation plan` and the user has approved the retained roster and behavior sheets.
