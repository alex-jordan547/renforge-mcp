# Z-Order Product Integration Result Report

Date: 2026-08-02
Issue: #49 (part of tracker #70)
Status: **PASS**

## Summary

Structural z-order editing (`raise_adjacent_sibling`) is now fully unblocked and delivered in product. The editor coordinator and Ren'Py in-game bridge (`editor.rpy`) natively expose the structural swap capability for eligible adjacent button displayables, execute safe source file rewriting, perform exact line-remapping attestation across live engine reloads, and support atomic transactional undo/redo.

## Live evidence (Ren'Py 8.5.3)

Observed values from a recorded run of `run_editor_zorder_live_scenario` against the
two-overlapping-buttons fixture. Probe point `(280, 240)` lies inside the overlap of
`zorder_target` (`220,220 180×100`) and `zorder_sibling` (`260,220 180×100`).

| Plane | Before | After swap + reload | After product undo |
| --- | --- | --- | --- |
| Painted pixel at `(280, 240)` | `[27, 65, 159]` → blue | `[216, 58, 58]` → red | blue |
| Editor selection at that point | `zorder_sibling` | `zorder_target` | `zorder_sibling` |
| `zorder_target` source line | 9 | 16 | 9 |
| `zorder_sibling` source line | 16 | 9 | 16 |

The blue channel varies slightly between runs (render transition), so the assertion is on
the dominant channel, not on exact RGB. The red sample is stable at `[216, 58, 58]`,
matching the authored `#d83a3a`.

### Source bytes

| Field | Value |
| --- | --- |
| Baseline SHA-256 | `bddfafad4016dab1723347a5f7e7833bc1f97844bc43d6c6c17ef4281072313d` |
| Post-swap SHA-256 | `7897e73aa1b9d51b89508a357ba4a73f3b137e0b806ed77083e1100a87b1f3c8` |
| Size delta | `0` bytes |
| After product undo | `byte_identical: true` |
| After fixture restore | SHA-256 equals baseline |

`stable_rebind: true` — post-reload runtime source locations match the lines read back
from the file that was actually written, so runtime rebinding is cross-checked against
real source positions rather than predicted ones.

Final report: `verdict: "pass"`, `verdict_reason: null`.

## Implementation & Verification Highlights

1. **Criteria & Intent**:
   - Criteria document `docs/superpowers/spikes/2026-08-02-zorder-product-criteria.md` frozen prior to coding.
   - Enforced strict intent isolation: structural swap intents reject combination with position or style color fields (`STRUCTURAL_INTENT_COMBINATION_REJECTED`).

2. **Coordinator & Engine Attestation**:
   - Coordinator parses `zorder_raise_adjacent_sibling` capability for eligible adjacent buttons.
   - Host `commit` and `undo_commit` calculate exact post-swap line positions (`target_line`, `sibling_line`) and update `expected_targets` runtime keys.
   - Post-reload attestation verifies that both buttons re-bind cleanly at their new line positions without identity loss or orphan states.

3. **In-Game Product Bridge (`editor.rpy`)**:
   - State model registers `pending_commit_is_zorder` to retain `last_committed_transaction_id` across reloads.
   - `editor_task0_zorder` handler exposed in bridge.
   - Product `editor_task0_undo` executes atomic transaction reversal.

4. **Evidence & Automated Testing**:
   - `tests/test_editor_coordinator.py`: Added `test_zorder_structural_swap_commit_and_undo` and `test_zorder_structural_swap_rejections`.
   - `src/renforge/editor_zorder_runner.py`: Upgraded to drive the complete product bridge path (`editor_task0_zorder` -> `editor_task0_save` -> `editor_task0_status` -> `editor_task0_undo`).
   - `tests/test_editor_zorder_live.py`: Verified against live Ren'Py 8.5.3 engine (`RENFORGE_ZORDER_LIVE=1`).
   - Full test suite passing: `668 passed, 47 skipped`.
