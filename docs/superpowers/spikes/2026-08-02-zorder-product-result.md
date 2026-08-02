# Z-Order Product Integration Result Report

Date: 2026-08-02
Issue: #49 (part of tracker #70)
Status: **PASS**

## Summary

Structural z-order editing (`raise_adjacent_sibling`) is now fully unblocked and delivered in product. The editor coordinator and Ren'Py in-game bridge (`editor.rpy`) natively expose the structural swap capability for eligible adjacent button displayables, execute safe source file rewriting, perform exact line-remapping attestation across live engine reloads, and support atomic transactional undo/redo.

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
