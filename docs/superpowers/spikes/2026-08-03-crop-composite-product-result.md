# Crop Composite Selection & Move Unblocking Result Report (Issue #46)

Date: 2026-08-03
Issue: #46 (part of tracker #70)
Status: **PASS**

## Summary

`Transform(crop=...)` combined with scaling transforms (`zoom`) is now unblocked and enabled in product (`src/renforge/bridge/editor.rpy`). The in-game editor bridge converts screen displacement deltas into source coordinate deltas using the inverse transform matrix (`reverse`), ensuring 1:1 movement accuracy on screen regardless of zoom scale.

## Implementation & Key Results

1. **Inverse Matrix Delta Conversion**:
   - Implemented `_renforge_editor_compute_next_position(target)`: maps start and end screen points through `_renforge_editor_matrix_map(reverse_fn, ...)` so translation offsets cancel out and $\Delta \text{source}$ scales accurately.
   - For `crop_with_zoom` (`zoom=1.25`): a 20px requested screen drag converts to 16px source update ($20 / 1.25$), producing an exact 20px displacement on screen.

2. **Verification & Live Attestation**:
   - `crop_with_zoom`: Passes live move preview, 1px pixel agreement gate after reload rebind, and byte-identical undo (`RENFORGE_CROP_LIVE=1`).
   - `crop_with_rotate`: Remains locked as `TRANSFORM_CROP_PARTIAL_UNSUPPORTED` because rotation expands the axis-aligned focus bounding box past the crop boundary (partial crop visibility).
   - Untransformed controls (`button`, `pos`, `offset`, `align`, pure `crop`): **Zero regression**, producing byte-identical source changes.

3. **Automated Evidence & Gates**:
   - `tests/test_editor_crop_live.py`: **3 passed** in 19.82s.
   - `tests/test_editor_rotation_live.py`: **1 passed** in 18.98s.
   - Full codebase test suite: **668 passed, 48 skipped**.
   - Repository gates: `compileall` clean, `git diff --check` clean.
