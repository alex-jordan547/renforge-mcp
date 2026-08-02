# Rotated Element Selection & Dragging Result Report (Issue #48)

Date: 2026-08-02
Issue: #48 (part of tracker #70)
Status: **PASS**

## Summary

Rotated element selection and dragging (`Transform(..., rotate=...)`) are now unblocked and enabled in product (`src/renforge/bridge/editor.rpy`). The in-game editor bridge extracts runtime matrix seams (`forward`/`reverse`), derives the transformed screen quadrilateral, enforces strict quad-to-AABB alignment invariants, and replaces coarse AABB hit testing with exact point-in-quadrilateral tests.

## Key Verification Results

1. **Selection Accuracy & Corner Rejection**:
   - Probing an unpainted AABB corner (point `[220, 220]` for focus rect `[220, 220, 160, 80]`) is rejected (`NO_FOCUSABLE_TARGET`), eliminating the `aabb_false_positive` flaw.
   - Clicking the painted target center (`[300, 260]`) correctly selects the rotated target.

2. **In-Game Move & Undo Flow**:
   - Preview drag move `[220, 220]` -> `[221, 220]` works seamlessly.
   - Save commit, reload rebind (`Reload committed`), script generation increment (`0` -> `1`), and product undo/redo all succeed.

3. **Fallback & Invariants**:
   - Untransformed displayables fall back to standard AABB rect filtering without regression.
   - Transformed displayables whose shape is uncalculable or whose quad center violates the focus center invariant are safely locked with `TRANSFORM_GEOMETRY_UNPROVEN` rather than falling back to AABB.

4. **Issue #46 (Crop Composite) Distinction**:
   - **Note**: Issue #46 (`Transform(crop=...)` combined with rotation/zoom) remains **BLOCKED**. Fixing selection for rotated elements does not resolve Issue #46 because crop composite issues stem from displacement motion scaling (e.g., 20px requested -> 25px painted delta under zoom, or diagonal drift under rotate), not hit-testing selection.

## Automated Test Evidence

- `tests/test_editor_rotation_live.py`: Upgraded to `test_rotation_product_path_pass`, returning **PASS** across **two consecutive live runs** on Ren'Py 8.5.3 (`RENFORGE_ROTATION_LIVE=1`).
- Codebase test suite: `668 passed, 47 skipped`.
- Targeted editor regressions: `155 passed`.
- Repository gates: `compileall` clean, `git diff --check` clean.
