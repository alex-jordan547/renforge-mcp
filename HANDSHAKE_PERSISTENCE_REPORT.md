# #81 Handshake Persistence - Implementation & Status

## Commits Delivered (Branch: cursor/say-what-style-position-bd4b)

1. **1b9c9de** - Initial handshake persistence implementation
2. **6c882d5** - Fix duplicate function declaration syntax error
3. **5e78c0f** - Use project basedir for reliable file I/O
4. **b2b50bb** - Restore editor active state after restart
5. **9cc2043** - Always check persistence file (no flag gating)
6. **4560f41** - Initialize pending_reload_draw_generation after restore

## Implementation Summary

### What Works ✅
- **Persistence file creation**: `.renforge_handshake_state.json` is successfully written to project basedir before restart
- **Persistence file restoration**: File is successfully read and deleted after restart, proving `_renforge_editor_restore_handshake_state()` is called
- **State restoration**: All critical state variables restored:
  - `pending_handshake_generation`
  - `pending_transaction_id`
  - `pending_operation`
  - `editor_session_screen`
  - `state.active = True`
  - `save_in_progress = True`
  - `pending_reload_requested/started = True`
  - `pending_reload_draw_generation = script_generation`

### Current Blocker 🚫

**Status stuck at `commit_queued` forever, even after successful handshake restoration.**

**Evidence:**
- `gui.rpy` is NEVER modified (values remain 268, 50 instead of expected 288, 80)
- Persistence file is consumed (deleted) after restart
- No Ren'Py syntax errors or crashes
- Ren'Py successfully restarts (2 "Interface start" sequences in log)

**Root Cause:**
The coordinator's `_command_commit` is never executing the actual file write, OR the coordinator thread is dead/stuck after restart.

## Technical Architecture

### Persistence Mechanism
```python
# Before restart (line 5836 in editor.rpy)
state.pending_handshake_generation = script_generation + 1
_renforge_editor_save_handshake_state(state)
→ renpy.reload_script()  # FULL RESTART

# After restart (_renforge_editor_state initialization, line 797)
_renforge_editor_restore_handshake_state(state)
→ Reads .renforge_handshake_state.json from basedir
→ Restores all state
→ Deletes persistence file
→ state.active = True (allows _renforge_editor_periodic to run)

# Handshake sending (_renforge_editor_periodic, line 6018)
if not state.pending_handshake_sent:
    state.pending_handshake_sent = True
    _renforge_editor_ensure_coordinator().submit_host("reload_handshake", ...)
```

### Key Insights
1. **`_renforge_runtime_module` persists** across restarts, but attributes are cleared
2. **Flags don't work** - must check filesystem as source of truth
3. **`state.active = True`** required for `_renforge_editor_periodic` to run handshake logic
4. **`pending_reload_draw_generation`** must equal `script_generation` or handshake loop re-triggers

## Remaining Mystery

Despite correct handshake restoration:
- Coordinator status remains `commit_queued`
- `gui.rpy` is never written
- No error messages

**Hypothesis:** Coordinator thread may not survive restart, OR `_command_commit` is failing silently in the coordinator's Python process (which we cannot directly observe from the bridge).

## Next Steps Options

### Option A: Continue Deep Debugging
- Add explicit logging to coordinator `_command_commit`
- Check if coordinator thread is alive after restart
- Investigate why file write never happens

### Option B: Document & Defer
- Mark handshake persistence as "partially implemented"
- Document known limitation: works for quick reloads, fails for full restarts
- Open follow-up issue for coordinator thread lifecycle

### Option C: Alternative Architecture
- Instead of persisting handshake state, make coordinator re-discover pending work after restart
- Coordinator checks for uncommitted transaction IDs on startup

## Recommendation

**Continue with Option A** - The handshake persistence is 90% there. The issue is now isolated to the coordinator's commit execution, which may be a simpler fix than the multi-restart state management we just solved.

**Test Command:**
```bash
cd /workspace && RENFORGE_SAY_WHAT_STYLE_POSITION_LIVE=1 python3 run_say_what_live_test.py
```

**Fixture Path (last run):**
```
/tmp/say_what_live_563mynda/say_what_clean
```

**Evidence File Still Exists:**
- Persistence file deleted: ✅
- `gui.rpy` unchanged: ❌ (268, 50 not 288, 80)
