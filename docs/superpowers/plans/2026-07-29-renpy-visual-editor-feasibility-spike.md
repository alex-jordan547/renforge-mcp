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
    sdk_expected: str                # "8.5.3"
    sdk_actual: str
    project: str
    phases: list[SpikeReply]
    adapters: list[dict[str, object]]
    retained_roster: list[str]
    removed_roster: list[str]
    decision: str                    # "proceed", "narrow", or "blocked"
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
screen _renforge_editor_spike_fixture():
    layer "overlay"
    zorder 900

    fixed:
        id "spike_root"
        xfill True
        yfill True

        add _renforge_spike_animated_displayable():
            id "spike_add"
            xpos 120
            ypos 110

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
    import time
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
            animation_samples=[],
            animated_child=None,
            existing_transform=None,
        )

    def _renforge_spike_state():
        # Mutable displayables, wrappers, and histories live outside store and rollback.
        return sys.modules["_renforge_runtime"].editor_spike

    class _RenforgeSpikeAnimated(renpy.Displayable):
        def __init__(self):
            super(_RenforgeSpikeAnimated, self).__init__()

        def render(self, width, height, st, at):
            state = _renforge_spike_state()
            state.animation_samples.append({
                "runtime_object_id": id(self),
                "st": float(st),
                "at": float(at),
                "sample_time": float(time.monotonic()),
            })
            color = "#4568aaff" if int(st * 4.0) % 2 == 0 else "#68a8e8ff"
            rendered = renpy.render(Solid(color, xysize=(160, 100)), width, height, st, at)
            renpy.redraw(self, 0.05)
            return rendered

    def _renforge_spike_animated_displayable():
        state = _renforge_spike_state()
        if state.animated_child is None:
            state.animated_child = _RenforgeSpikeAnimated()
        if state.existing_transform is None:
            state.existing_transform = Transform(
                child=state.animated_child,
                xoffset=11,
                yoffset=-7,
                alpha=0.85,
            )
        return state.existing_transform

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
        state.animation_samples[:] = []
        renpy.show_screen("_renforge_editor_spike_fixture")
        renpy.show_screen("_renforge_editor_spike_chrome")
        state.generation += 1
        renpy.restart_interaction()
        return _renforge_spike_reply("prepare", {"requested": True})

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
from renforge.sdk import get_or_install_sdk

EXPECTED_SDK = "8.5.3"
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
        phases.append(session.client.request("editor_spike_prepare"))
        _wait_for_screen(session.client, "_renforge_editor_spike_fixture")
        # Tasks 2–4 append the remaining commands and report reduction.
    report = {
        "schema_version": 1,
        "completed": False,
        "sdk_expected": EXPECTED_SDK,
        "sdk_actual": sdk.version,
        "project": str(project.root),
        "phases": phases,
        "adapters": [],
        "retained_roster": [],
        "removed_roster": [],
        "decision": "blocked",
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
sdk_expected=8.5.3 sdk_actual=8.5.3
phase=prepare ok=true
screen=_renforge_editor_spike_fixture active=true
completed=false decision=blocked
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
    "spike_clip_parent",
    "spike_clipped_child",
)


def _renforge_spike_widget(widget_id):
    return renpy.get_widget("_renforge_editor_spike_fixture", widget_id)


def _renforge_spike_location(displayable):
    location = getattr(displayable, "_location", None)
    if isinstance(location, (list, tuple)) and len(location) >= 2:
        return [str(location[0]), int(location[1])]
    return None
```

Build a graph from the active screen's displayable tree. Child discovery must record the exact seam used (`children`, `child`, or `visit()`) and avoid duplicates by object identity. Each node record contains:

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
chrome_center                  -> spike_chrome absent from candidates
```

The phase passes only when all fixture widgets have parent and paint-order records, transformed quads are non-null, effective clipping excludes the clipped child's overflow, and the five hit cases match.

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
_SPIKE_ADAPTERS = {
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
    },
    "screen_imagebutton": {
        "widget_id": "spike_imagebutton",
        "operations": ["move", "allocation_resize"],
        "move_delta": [37, 23],
        "resize_delta": [40, 24],
        "fixed_edge": "top_left",
        "minimum_size": [44, 44],
    },
    "screen_frame": {
        "widget_id": "spike_frame",
        "operations": ["move", "allocation_resize"],
        "move_delta": [37, 23],
        "resize_delta": [40, 24],
        "fixed_edge": "top_left",
        "minimum_size": [32, 32],
    },
    "screen_textbutton": {
        "widget_id": "spike_textbutton",
        "operations": ["move", "allocation_resize"],
        "move_delta": [37, 23],
        "resize_delta": [40, 24],
        "fixed_edge": "top_left",
        "minimum_size": [44, 44],
    },
    "screen_text": {
        "widget_id": "spike_text",
        "operations": ["move"],
        "move_delta": [37, 23],
    },
}
```

The `screen_add_image` behavior sheet fixes corner resize to a locked aspect ratio around the visual center. Container/button resize fixes the top-left edge and changes allocation; rotation is unavailable for those adapters.

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
    "pass": bool,
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
    "expected_geometry": geometry,
    "style_preserved": bool,
    "animation_continuous": bool,
    "visual_delta_within_one_pixel": bool,
    "existing_transform_preserved": bool,
    "errors": list[str],
}
```

A pass requires unchanged style identity, expected geometry within one logical pixel, unchanged source location on the wrapped child, and a named reversible replacement seam. `screen_add_image` additionally requires the editor wrapper to compose outside the stable pre-existing transform without changing its identity or `xoffset=11`, `yoffset=-7`, and `alpha=0.85`. Its instrumented child must keep monotonically advancing `st`/`at` samples across apply, undo, redo, restart/rebind, and reset; neither screen reevaluation nor the editor wrapper may create a replacement child or restart its animation clock.

- [ ] **Step 4: Exercise undo, redo, interaction restart, and rebind**

`editor_spike_history` runs this exact state sequence for each candidate adapter:

```text
baseline -> apply -> undo -> redo -> renpy.restart_interaction()
-> resolve new widget instance -> reapply working transform -> reset
```

Record object IDs before and after restart. A rebind passes only when:

- the post-restart widget is a different runtime object or the report explicitly proves Ren'Py reused it;
- the target is rediscovered from fixture ownership and source-location metadata rather than the stale object reference;
- the working transform reapplies to expected geometry;
- reset restores baseline geometry and removes the outer wrapper;
- the original style identity remains unchanged throughout.
- for `screen_add_image`, the stable animated child and pre-existing transform retain their object identities and parameters while the editor wrapper is applied and removed;
- for `screen_add_image`, animation samples continue monotonically across restart/rebind or the report records an adapter failure; a new child clock silently starting at zero is not preservation.

- [ ] **Step 5: Run the complete adapter experiment**

Extend the driver to invoke every declared operation, then the history phase, taking screenshots after baseline, apply, restart/rebind, and reset:

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

Create `tests/test_editor_spike.py` with one complete adapter factory and four observable contracts:

```python
from renforge.editor_spike import decide_roster, validate_report


def _behavior() -> dict:
    return {
        "editable_properties": ["xpos", "ypos"],
        "units": "logical_pixels",
        "drag_handle_meaning": "move",
        "fixed_edge_or_pivot": "visual_center",
        "anchor_compensation": [0, 0],
        "aspect_ratio_policy": "locked",
        "minimum_size": [16, 16],
        "rotation_pivot": "visual_center",
        "rotate_pad": False,
        "transform_anchor": True,
        "transform_composition_order": "outer_after_existing",
        "expected_hit_geometry": "transformed_quad_intersect_clip",
        "unsupported_combinations": ["dynamic_at"],
    }


def _adapter(name: str, passed: bool) -> dict:
    return {
        "name": name,
        "pass": passed,
        "operations": ["move"],
        "replacement_seam": "children",
        "geometry_evidence": {"within_one_pixel": True},
        "style_preserved": True,
        "animation_continuous": True,
        "existing_transform_preserved": True,
        "rebind_evidence": {"passed": True},
        "behavior_sheet": _behavior(),
        "errors": [] if passed else ["measured failure"],
    }


def test_validate_report_rejects_missing_behavior_field() -> None:
    report = {"phases": [{"phase": name} for name in ("prepare", "graph", "apply", "history", "finish")], "adapters": [_adapter("screen_add_image", True)]}
    del report["adapters"][0]["behavior_sheet"]["rotation_pivot"]
    assert validate_report(report) == ["screen_add_image.behavior_sheet.rotation_pivot is required"]


def test_validate_report_rejects_missing_animation_evidence() -> None:
    report = {"phases": [{"phase": name} for name in ("prepare", "graph", "apply", "history", "finish")], "adapters": [_adapter("screen_add_image", True)]}
    del report["adapters"][0]["animation_continuous"]
    assert validate_report(report) == ["screen_add_image.animation_continuous is required"]


def test_decide_roster_returns_narrow_for_mixed_results() -> None:
    retained, removed, decision = decide_roster([_adapter("a", True), _adapter("b", False)])
    assert (retained, removed, decision) == (["a"], ["b"], "narrow")


def test_decide_roster_returns_blocked_when_none_pass() -> None:
    assert decide_roster([_adapter("a", False)]) == ([], ["a"], "blocked")
```

Run:

```bash
pytest tests/test_editor_spike.py -v
```

Expected: FAIL because the reducer functions do not exist.

- [ ] **Step 2: Implement strict report reduction**

Add `validate_report(report) -> list[str]`, `decide_roster(report) -> tuple[list[str], list[str], str]`, and `render_markdown(report) -> str`.

Validation must reject as inconclusive:

```python
required_phase_names = {"prepare", "graph", "apply", "history", "finish"}
required_adapter_fields = {
    "name",
    "pass",
    "operations",
    "replacement_seam",
    "geometry_evidence",
    "style_preserved",
    "animation_continuous",
    "rebind_evidence",
    "behavior_sheet",
    "existing_transform_preserved",
    "errors",
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

Roster reduction is mechanical:

```python
retained = sorted(item["name"] for item in adapters if item["pass"])
removed = sorted(item["name"] for item in adapters if not item["pass"])
decision = "proceed" if not removed else "narrow" if retained else "blocked"
```

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
