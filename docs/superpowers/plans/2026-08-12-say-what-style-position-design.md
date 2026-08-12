# Issue #81 — say.what style-position adapter design

**Status:** In progress  
**Target:** Ren'Py 8.5.3  
**Surface:** In-game bridge  
**Scope:** ONE bounded adapter only (say.what via gui.dialogue_xpos/ypos)

## Context

The live editor can select the standard Ren'Py dialogue text (`screen say`, `widget_id=what`), but Move is locked. Internal reason is often `XPOS_DUPLICATE` / "text statement must contain exactly one xpos", although the statement has no authored xpos. Position comes from:

```renpy
style say_dialogue:
    xpos gui.dialogue_xpos
    ypos gui.dialogue_ypos
    xsize gui.dialogue_width

define gui.dialogue_xpos = gui.scale(268)
define gui.dialogue_ypos = gui.scale(50)
```

## Critical Findings (from remote investigation)

### 1. Two-file architecture
- **Identity/rebind**: `screens.rpy` (`text what id "what"`)
- **Write target**: `gui.rpy` (`define gui.dialogue_xpos/ypos = gui.scale(<int>)`)
- Current coordinator staging is single-`relative_path`
- Must design explicit multi-file write-path

### 2. XPOS_DUPLICATE conflation (root cause)
- `_analyze_positioned_kind_statement` raises `XPOS_DUPLICATE` when count=0
- New adapter must NOT go through that path
- Map zero-authored xpos to style-position lock codes

### 3. Variant overrides
- Demo `gui.rpy` has `@gui.variant small()` reassigning `gui.dialogue_xpos` (~446)
- Desktop unlock requires exactly ONE top-level `define … = gui.scale(<int>)`
- Fail closed with `STYLE_POSITION_VARIANT_UNSUPPORTED` when variants exist

### 4. Preview hazard
- Bridge `_widget_properties` / `renpy.show_screen(screen, …)` rebuild unsafe
- Can yield `TypeError: missing a required argument: 'who'`
- Must NOT rebuild `say` with empty scope
- Prefer mutating live gui values while keeping who/what intact

### 5. Product undo gap
- `_command_undo_commit` currently errors `UNDO_STYLE_COLOR_ONLY`
- Must extend allowlist/reverse-target model (store previous/new GUI literals)

### 6. Coordinator analyze flow
- Text branch tries `analyze_text_position_statement` then style color
- After direct position fails, attempt new ownership resolver
- Set new `position_mode` (e.g. `style_gui_dialogue`)
- Keep out of `RUNTIME_DELTA_POSITION_MODES` unless semantics match

### 7. PR staging
- PR A: Source ownership + unit tests (fail-closed, no Move unlock) ✓
- PR B: Coordinator + bridge + undo + live suite
- Do NOT close #81 without live Ren'Py 8.5.3 proof

### 8. #50 precedent
- Follow `TextColorStyleStatement` contract shape
- Not a generic resolver

## Phase 1: Source ownership (✓ COMPLETE)

**Deliverables:**
- ✅ `SayWhatStylePositionStatement` dataclass
- ✅ `analyze_say_what_style_position()` parser
- ✅ `apply_say_what_style_position_patch()` rewriter
- ✅ 16 unit tests covering all lock codes
- ✅ Variant detection and rejection
- ✅ UTF-8 safety, byte-span correctness

**Lock codes:**
- `STYLE_POSITION_SOURCE_UNRESOLVED` — missing or malformed
- `STYLE_POSITION_SOURCE_AMBIGUOUS` — multiple definitions
- `STYLE_POSITION_EXPRESSION_UNSUPPORTED` — expressions, arithmetic, non-gui.scale
- `STYLE_POSITION_VARIANT_UNSUPPORTED` — phone/small overrides present

**Status:** Ready for PR A

## Phase 2: Coordinator integration (✓ PARTIAL — 2A complete, 2B pending)

### 2A: Two-file write-path (✓ COMPLETE)

**Deliverables:**
- ✅ Analyze flow detects say.what when direct xpos fails
- ✅ Load and analyze gui.rpy for style-backed ownership
- ✅ New `position_mode = SAY_WHAT_STYLE_POSITION_MODE`
- ✅ Two-file transaction: patch gui.rpy, identity screens.rpy
- ✅ Never surface `XPOS_DUPLICATE` for missing inherited position
- ✅ Use `STYLE_POSITION_*` lock codes with human reasons
- ✅ Apply logical-pixel deltas to authored gui.scale ints
- ✅ Extend `_command_undo_commit` for style position
- ✅ Store previous/new GUI literals for undo/redo
- ✅ Revalidate ownership before publish
- ✅ Fail-closed: reject multi-file + other screen changes in V1

**Implementation notes:**
- `_apply_same_file_intents` skips say style position intents (continue)
- gui.rpy transaction created separately when all intents are style position
- screens.rpy stays unchanged (identity-only path for rebind)
- Undo reverses say_style_position_previous/new values

**Status:** Committed to branch `cursor/say-what-style-position-bd4b`

### 2B: Coordinator unit tests (TODO)

Test scenarios needed:
- Capabilities unlock for say.what with valid gui.rpy
- Lock codes propagated when gui.rpy has variants/expressions
- Two-file write succeeds for pure say.what move
- Multi-file write rejected when combined with other changes
- Undo/redo cycle restores correct GUI literals
- Stale gui.rpy baseline detection

**Blocked by:** Bridge preview implementation (need end-to-end flow)

### Multi-file write-path design

```python
# Coordinator must track TWO files:
identity_file = "game/screens.rpy"  # text what id "what" — rebind target
write_file = "game/gui.rpy"         # define gui.dialogue_xpos/ypos — patch target

# Staging model:
# 1. Analyze identity statement in screens.rpy
# 2. Load gui.rpy and analyze ownership
# 3. Stage write to gui.rpy
# 4. Reload + rebind via screens.rpy identity
```

### Analyze flow extension

After `analyze_text_position_statement` fails for say.what:

```python
# In coordinator text branch:
if statement_kind == "text" and widget_id == "what" and screen_name == "say":
    # Attempt style-backed ownership resolution
    gui_path = _resolve_gui_rpy_path(project_root)
    gui_source = read_file(gui_path)
    style_stmt = analyze_say_what_style_position(
        gui_source,
        xpos_var="gui.dialogue_xpos",
        ypos_var="gui.dialogue_ypos",
    )
    if style_stmt.position_mode:
        # Unlock with new position_mode
        return {
            "position_mode": "style_gui_dialogue",
            "can_move": True,
            "write_target_file": gui_path,
            "identity_file": screens_path,
            # Store parsed ownership for commit
            "style_position_statement": style_stmt,
        }
```

### Commit flow (two-file transaction)

```python
def _commit_say_what_style_position(intent):
    # 1. Read current gui.rpy
    gui_bytes = read_file(intent["write_target_file"])
    
    # 2. Revalidate ownership (fail if gui.rpy changed)
    reanalyzed = analyze_say_what_style_position(...)
    if reanalyzed.baseline_sha256 != intent["style_position_statement"].baseline_sha256:
        raise EditorSourceError("STALE_SOURCE", "gui.rpy changed")
    
    # 3. Apply patch
    patched = apply_say_what_style_position_patch(
        gui_bytes,
        intent["style_position_statement"],
        x=intent["x"],
        y=intent["y"],
    )
    
    # 4. Atomic multi-file publish
    staged_files = {
        intent["write_target_file"]: patched,
        # identity_file unchanged
    }
    
    # 5. Reload Ren'Py
    # 6. Rebind via screens.rpy identity (text what id "what")
    # 7. Attest runtime geometry
    # 8. Store undo payload (previous + new gui literals)
```

## Phase 3: Bridge preview (TODO — CRITICAL for unlock)

### Safe preview without say rebuild

⚠️ **Critical finding #4**: DO NOT call `renpy.show_screen("say", ...)` with empty who/what.

**Required implementation:**
```python
# In _renforge_editor_show_target_overrides:
def _renforge_editor_show_target_overrides(screen):
    state = _renforge_editor_state()
    target_key = state.selected_target_key
    target = state.targets.get(target_key)
    source_key = target.get("source_key") if target else None
    position_mode = source_key.get("position_mode") if source_key else None
    
    # Critical: say.what style position uses live GUI mutation, not _widget_properties
    if position_mode == "style_gui_dialogue":  # SAY_WHAT_STYLE_POSITION_MODE
        # Mutate live style properties without rebuilding say screen
        position = target.get("position")
        if position and len(position) == 2:
            renpy.style.say_dialogue.xpos = int(position[0])
            renpy.style.say_dialogue.ypos = int(position[1])
        renpy.restart_interaction()
        return
    
    # Standard path: _widget_properties for other widgets
    _renforge_editor_prepare_anonymous_target_overrides(screen)
    properties = _renforge_editor_widget_properties(screen)
    if properties:
        renpy.show_screen(screen, _layer="screens", _widget_properties=properties)
    else:
        renpy.show_screen(screen, _layer="screens")
```

**Reset path:**
```python
# In _renforge_editor_restore_preview:
if position_mode == "style_gui_dialogue":
    # Restore original GUI values from runtime_baseline
    baseline = target.get("runtime_baseline") or []
    if len(baseline) == 2:
        renpy.style.say_dialogue.xpos = int(baseline[0])
        renpy.style.say_dialogue.ypos = int(baseline[1])
    renpy.restart_interaction()
    return {"ok": True, "restored": True, "method": "style_mutation"}
```

**Must preserve:**
- Current `who` and `what` dialogue text
- Dialogue state (must NOT advance or dismiss)
- All other say.what style properties

**Status:** Not started — BLOCKS product unlock

## Phase 4: Product undo (✓ COORDINATOR IMPL, tests pending)

### Extend undo allowlist (✓ IMPLEMENTED)

```python
# In _command_undo_commit (IMPLEMENTED):
is_say_style_position_tx = bool(prior.expected_targets) and all(
    target.get("say_style_position_previous_x") is not None
    and target.get("say_style_position_previous_y") is not None
    and target.get("say_style_position_new_x") is not None
    and target.get("say_style_position_new_y") is not None
    for target in prior.expected_targets
)

# Reversal logic:
elif is_say_style_position_tx:
    prev_x = target.get("say_style_position_previous_x")
    prev_y = target.get("say_style_position_previous_y")
    new_x = target.get("say_style_position_new_x")
    new_y = target.get("say_style_position_new_y")
    reversed_target["say_style_position_previous_x"] = new_x
    reversed_target["say_style_position_previous_y"] = new_y
    reversed_target["say_style_position_new_x"] = prev_x
    reversed_target["say_style_position_new_y"] = prev_y
```

### Undo payload structure (✓ IMPLEMENTED)

```python
{
    "say_style_position_previous_x": 268,
    "say_style_position_previous_y": 50,
    "say_style_position_new_x": 300,
    "say_style_position_new_y": 100,
}
```

**Status:** Coordinator implementation complete, needs unit + live tests

## Phase 5: Bridge UI + i18n (TODO)

### Capabilities update

```json
{
  "move": true,
  "resize": false,
  "position_mode": "style_gui_dialogue",
  "ownership_chain": "say.what → style say_dialogue → gui.dialogue_xpos/ypos",
  "scope_notice": "This change affects all standard dialogue lines"
}
```

### Lock reason i18n

```json
{
  "en": {
    "STYLE_POSITION_SOURCE_UNRESOLVED": "Dialogue position source could not be resolved",
    "STYLE_POSITION_SOURCE_AMBIGUOUS": "Dialogue position has multiple definitions",
    "STYLE_POSITION_EXPRESSION_UNSUPPORTED": "This dialogue position expression is not editable yet",
    "STYLE_POSITION_VARIANT_UNSUPPORTED": "Phone/small variant overrides prevent desktop editing"
  }
}
```

### Inspector UI

- Show ownership chain
- Display persistent global-scope notice
- Locked targets use designed locked treatment (not editable purple)

## Phase 6: Live test harness (TODO)

### Fixture requirements

```python
# tests/live_fixtures/say_what_style_position/
# - gui.rpy: standard gui.dialogue_xpos/ypos
# - screens.rpy: standard say screen
# - script.rpy: dialogue lines for testing
```

### Test scenario

```python
@pytest.mark.live
def test_say_what_style_position_live():
    # 1. Select say.what
    # 2. Verify unlock (move: true)
    # 3. Preview drag (no TypeError, no dialogue advance)
    # 4. Save (gui.rpy patched, screens.rpy unchanged)
    # 5. Reload + rebind
    # 6. Verify geometry ≤1px agreement
    # 7. Show second dialogue line (global scope proof)
    # 8. Undo (byte-identical restore + geometry agreement)
    # 9. Redo (reapply + geometry agreement)
```

### Environment variable gate

```bash
RENFORGE_SAY_WHAT_STYLE_POSITION_LIVE=1 pytest tests/test_editor_say_what_style_position_live.py
```

## Acceptance criteria mapping

### Phase 1 (✓ COMPLETE)
- [x] Source analyzer for gui.dialogue_xpos/ypos
- [x] Source patcher preserving gui.scale() wrapper
- [x] Variant detection and lock code
- [x] 16 unit tests, UTF-8 safe, byte-span correct

### Phase 2A Coordinator (✓ COMPLETE)
- [x] Two-file write-path: gui.rpy patched, screens.rpy rebind
- [x] No `XPOS_DUPLICATE` for missing inherited position
- [x] Variant detection prevents unsafe unlock
- [x] Product undo/redo with GUI literal storage (implemented, not tested)
- [x] Fail-closed: reject multi-file + other changes

### Phase 2B+ (TODO — BLOCKS issue close)
- [ ] Bridge preview without say rebuild / TypeError
- [ ] Coordinator unit tests for new position_mode
- [ ] Bridge capabilities + ownership chain UI
- [ ] Global-scope notice displayed
- [ ] Live test: select, drag, save, reload, rebind, undo, redo
- [ ] Live test: geometry agreement ≤1px
- [ ] Live test: second dialogue line at new position
- [ ] Live test: byte-identical undo

**Critical path:** Bridge preview (Phase 3) blocks all remaining validation

## Out of scope

- Generic style resolver
- NVL dialogue, bubbles, nameboxes
- Font, color, padding, resize
- Phone/small variant editing
- Moving only current sentence
- Auto-normalizing expressions

## References

- Issue: #81
- Precedent: #50 (style color), PRs #71, #72
- Demo: `examples/demo_game/game/gui.rpy` ~134–136, ~446–447
