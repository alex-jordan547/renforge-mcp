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

## Phase 2: Coordinator integration (TODO)

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

## Phase 3: Bridge preview (TODO)

### Safe preview without say rebuild

DO NOT call `renpy.show_screen("say", ...)` with empty who/what.

**Option A: Direct gui mutation (recommended)**
```python
# In bridge preview command:
import gui
gui.dialogue_xpos = new_x  # Mutate live
gui.dialogue_ypos = new_y
renpy.restart_interaction()  # Redraw without advancing
```

**Option B: Style property override**
```python
# Override style properties directly
renpy.style.say_dialogue.xpos = new_x
renpy.style.say_dialogue.ypos = new_y
renpy.restart_interaction()
```

Must preserve current `who` and `what` values. Must NOT advance/dismiss dialogue.

### Reset path

```python
# Reset restores original gui values
gui.dialogue_xpos = original_x
gui.dialogue_ypos = original_y
renpy.restart_interaction()
```

## Phase 4: Product undo (TODO)

### Extend undo allowlist

```python
# In _command_undo_commit:
if original_intent["position_mode"] == "style_gui_dialogue":
    # Reverse gui.rpy write
    gui_path = original_intent["write_target_file"]
    # Stored undo payload has previous literals
    undo_patch = apply_say_what_style_position_patch(
        current_gui_bytes,
        ...,
        x=undo_payload["previous_xpos"],
        y=undo_payload["previous_ypos"],
    )
    # Atomic publish + reload + rebind
    ...
```

### Undo payload structure

```python
{
    "position_mode": "style_gui_dialogue",
    "write_target_file": "game/gui.rpy",
    "identity_file": "game/screens.rpy",
    "previous_xpos": 268,
    "previous_ypos": 50,
    "new_xpos": 300,
    "new_ypos": 100,
    "style_position_statement": <original parsed>,
}
```

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

- [ ] Two-file write-path: gui.rpy patched, screens.rpy rebind
- [ ] No `XPOS_DUPLICATE` for missing inherited position
- [ ] Variant detection prevents unsafe unlock
- [ ] Preview without say rebuild / TypeError
- [ ] Product undo/redo with GUI literal storage
- [ ] Coordinator tests for new position_mode
- [ ] Bridge capabilities + ownership chain UI
- [ ] Global-scope notice displayed
- [ ] Live test: select, drag, save, reload, rebind, undo, redo
- [ ] Live test: geometry agreement ≤1px
- [ ] Live test: second dialogue line at new position
- [ ] Live test: byte-identical undo

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
