# RenForge editor launcher by default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Always launch RenPy with the in-game RF launcher and add real editable controls to the demo VN without dashboard changes.

**Architecture:** Normalize the launch boundary to an editor-capable session so injection, coordinator, endpoint, and environment are created together. Keep the overlay opt-in inside RenPy: the injected launcher is visible, and its existing activate/exit handlers control the overlay without restarting the game. Add explicit-position `textbutton` controls to the demo's ordinary story path.

**Tech Stack:** Python, pytest, Ren'Py 8.5.3, `.rpy` screens/labels, existing bridge/editor protocol.

## Global Constraints

- No dashboard UI changes.
- `editor=false` legacy launch payloads must not suppress editor injection or produce a session-mode mismatch.
- RF launcher starts visible; full overlay starts inactive.
- Demo controls must use real RenPy actions and remain part of the normal narrative.
- Write failing tests before production changes and run the exact focused checks before broad verification.

---

### Task 1: Canonicalize editor-capable launches

**Files:**
- Modify: `src/renforge/bridge/launcher.py:709-756`
- Modify: `src/renforge/tools/live.py:269-317`
- Modify: `src/renforge/server.py:100-190,350-405`
- Modify: `src/renforge/dashboard_client.py:46-58`
- Test: `tests/test_live_stop.py:258-328` and `tests/test_bridge_launcher.py:93-120`
- Test: `tests/test_dashboard_client.py:58-95`

**Interfaces:**
- `live.launch_game(project_path, ..., editor=...)` returns an editor-capable session for either boolean input.
- `launch_with_bridge(..., editor=...)` initializes `EditorCoordinator`, `BridgeRuntimeProbe`, endpoint, environment, and editor artifact together.

- [ ] **Step 1: Write the failing contract tests in `tests/test_live_stop.py`**

Replace the existing expectation in `test_launch_reuses_owned_session_only_when_editor_mode_matches` with the canonical result for `editor=False` against an `_OwnedRunningSession(editor=True)`:

```python
result = live.launch_game(str(project), editor=False)
assert result == {
    "ok": True,
    "already_running": True,
    "ready": True,
    "current_label": "dashboard_scene",
    "editor": True,
}
assert live._SESSIONS[key] is session
assert session.close_calls == 0
```

Extend `test_new_launch_passes_editor_mode_to_bridge_launcher` with a second call using `editor=False`; assert the captured `launch_kwargs["editor"] is True` and the returned `result["editor"] is True`.

- [ ] **Step 2: Run only the changed launch tests and confirm the expected failure**

Run: `pytest tests/test_live_stop.py::test_launch_reuses_owned_session_only_when_editor_mode_matches tests/test_live_stop.py::test_new_launch_passes_editor_mode_to_bridge_launcher -q`
Expected: FAIL because the current implementation rejects legacy `editor=False` reuse and forwards false to `launch_with_bridge`.

- [ ] **Step 3: Implement the smallest canonicalization**

Normalize `editor` to `True` before session reuse and before calling `launch_with_bridge`. Ensure `launch_with_bridge` creates the coordinator, endpoint, editor environment, and artifact together; never inject a script without its backend. Preserve the in-game launcher inactive/visible behavior.

- [ ] **Step 4: Run focused launch tests**

Run: `pytest tests/test_live_stop.py tests/test_bridge_launcher.py tests/test_dashboard_client.py -q`
Expected: PASS, with all old session-mode mismatch expectations updated to the canonical editor session contract.


### Task 2: Add real positioned controls to the normal demo VN

**Files:**
- Modify: `examples/demo_game/game/script.rpy`
- Modify: `examples/demo_game/game/screens.rpy`
- Test: extend `tests/test_integration_sdk.py::test_live_menu_selection_takes_the_branch`

**Interfaces:**
- New demo screen returns a real choice value through `call screen`.
- Each editable `textbutton` has a literal `id`, literal `xpos`/`ypos`, and a real `action` (`Return`, `SetVariable`, or an equivalent existing RenPy action).

- [ ] **Step 1: Add the failing live assertion to `tests/test_integration_sdk.py::test_live_menu_selection_takes_the_branch`**

After the existing dialogue advances to the village-gate interaction, assert that the focus list contains two demo-owned button IDs with source locations and explicit bounds; then select the lantern control and keep the existing `lantern is True` and `courage == 1` branch assertions.

- [ ] **Step 2: Run the focused live test and confirm it fails**

Run: `pytest tests/test_integration_sdk.py::test_live_menu_selection_takes_the_branch -q`
Expected: FAIL because the current story exposes only the generic choice screen and no dedicated explicit-position demo buttons.

- [ ] **Step 3: Add the real screen to `examples/demo_game/game/screens.rpy` and call it from `examples/demo_game/game/script.rpy`**

Keep the normal narrative labels and consequences. Add a positioned panel with multiple real `textbutton` statements; route their returned values into the existing `lantern`, `courage`, and branch variables. Do not add a sandbox label or editor-only fake action.

- [ ] **Step 4: Run the focused live scenario**

Run: `pytest tests/test_integration_sdk.py::test_live_menu_selection_takes_the_branch -q`
Expected: PASS with the real buttons visible, selectable, and branch-changing.

### Task 3: Verify end to end

**Files:**
- No production edits expected.
- Test: `tests/test_live_stop.py`, `tests/test_bridge_launcher.py`, `tests/test_dashboard_client.py`, and `tests/test_integration_sdk.py::test_live_menu_selection_takes_the_branch`

- [ ] **Step 1: Run targeted Python tests**

Run: `.venv/bin/pytest tests/test_live_stop.py tests/test_bridge_launcher.py tests/test_dashboard_client.py tests/test_editor_coordinator.py tests/test_editor_protocol.py tests/test_editor_source.py tests/test_editor_runtime.py tests/test_editor_task0_runner.py -q`

- [ ] **Step 2: Run the live RenPy scenario**

Run: `PYTHONPATH=src RENFORGE_TASK0_LIVE=1 .venv/bin/pytest tests/test_editor_task0_live.py::test_task0_live_editor_prerequisite tests/test_integration_sdk.py::test_live_menu_selection_takes_the_branch -q`

Confirm from the runtime response and screenshot: RF launcher visible at startup, overlay active after clicking RF, real demo buttons selectable, Save/Reload succeeds, and the normal action still branches the VN.

- [ ] **Step 3: Run the project formatter/type checks only if required by existing repository scripts**

Do not alter dashboard UI or perform unrelated cleanup.
