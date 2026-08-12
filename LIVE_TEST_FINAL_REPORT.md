# #81 Final Live Test Report

## Date: 2026-08-12 21:18 UTC
## Branch: `cursor/say-what-style-position-bd4b`
## Latest Commits:
- `6d3b89f`: Cache clearing + commit error reporting
- `b874126`: Handle BridgeProtocolError during reload

---

## TEST RESULTS SUMMARY

### ✅ WORKING (Steps 1-4)
1. **Move Unlock**: `position_mode='style_gui_dialogue'`, `capabilities.move=True`
2. **Preview**: OK (`x=288, y=80`, `method='style_mutation'`)  
3. **Preview Source Unchanged**: ✅ (no premature gui.rpy write)
4. **Save Click**: ✅ (button interaction successful)

### ❌ HARD BLOCKER (Step 5)
**Commit Stuck at `commit_queued`** - Ren'Py reloaded but handshake never received.

---

## ROOT CAUSE: Handshake State Not Persisted

**Problem**: When `gui.rpy` changes, Ren'Py performs a FULL restart (not just script reload). This wipes all non-persistent Python state, including:
- `pending_handshake_generation`
- `pending_transaction_id`  
- Related commit tracking state

These are stored in `_renforge_runtime_module.editor_v1`, which is lost on restart.

**Evidence**:
```
# Ren'Py log shows TWO Interface starts:
Interface start took 195 ms  ← Initial launch
Resetting cache.
Interface start took 147 ms  ← After reload
```

Ren'Py reloaded successfully, but the bridge lost track of the pending commit, so no handshake was sent.

**Status Timeline**:
```
status_code=saving          (0s)
status_code=commit_queued   (0.5s - 60s)  ← STUCK
```

---

## TECHNICAL DETAILS

### Bridge Handshake Logic
File: `src/renforge/bridge/editor.rpy` lines 5920-5931

```python
if state.pending_handshake_generation is None:
    return  # ← After restart, this is None!
if int(state.script_generation) != int(state.pending_handshake_generation):
    return
if not state.pending_handshake_sent:
    state.pending_handshake_sent = True
    # Send handshake...
```

After gui.rpy restart:
- `pending_handshake_generation` = `None` (lost)
- Bridge skips handshake send
- Coordinator waits forever at `commit_queued`

### Attempted Fixes
1. ✅ Cache clearing (removed stale .rpyc files)
2. ✅ `BridgeProtocolError` handling (allows runner to survive reload)
3. ❌ Handshake state persistence (REQUIRED but not implemented)

---

## SOLUTION REQUIRED

The bridge needs to persist handshake state across full restarts. Options:

### Option A: Use Ren'Py Persistent Storage
```python
# In _renforge_editor_state():
if not hasattr(persistent, 'editor_pending_handshake'):
    persistent.editor_pending_handshake = None
    persistent.editor_pending_transaction_id = None

# Before restart:
persistent.editor_pending_handshake = state.pending_handshake_generation
persistent.editor_pending_transaction_id = state.pending_transaction_id

# After restart:
if persistent.editor_pending_handshake is not None:
    state.pending_handshake_generation = persistent.editor_pending_handshake
    state.pending_transaction_id = persistent.editor_pending_transaction_id
```

### Option B: Coordinator Timeout + Retry
Let coordinator detect `commit_queued` timeout and trigger a manual handshake request.

### Option C: Avoid gui.rpy Live Editing
Lock `say.what` style position in V1, defer to V2 with proper multi-file state management.

---

## WHAT WE PROVED

### Core Functionality ✅
1. **2 Critical Bugs Fixed**:
   - `move_lock_reason` cleared for say.what
   - `position_mode` exposed in status response

2. **Ownership Recognition**: Coordinator correctly identifies style-backed position

3. **Preview Safe**: Style mutation without dialogue advance/TypeError

4. **Source Analysis**: Delta math correct (authored + delta, not absolute coords)

5. **Ren'Py Reload**: gui.rpy changes trigger successful restart

### Remaining Work ❌
**Handshake persistence** - requires bridge architecture change for gui.rpy commits.

---

## COMMITS PUSHED

1. **`6d3b89f`**: Cache clearing + commit error reporting
2. **`b874126`**: Handle BridgeProtocolError during reload

**Branch**: `cursor/say-what-style-position-bd4b`  
**PR**: #83

---

## RECOMMENDATION

**KEEP #81 OPEN** - Hard blocker prevents completion.

**Next Steps**:
1. Design handshake persistence mechanism (Option A recommended)
2. Implement + test with gui.rpy changes
3. Re-run full scenario until green
4. Close #81 with live evidence

**Status**: 4/10 steps passing, blocker identified with evidence.
