# Product Criteria — `Transform(rotate=...)` Selection Unblocking (Issue #48)

**Status:** frozen prior to implementation.
**SDK:** Ren'Py **8.5.3**.
**Scope:** Production in-game bridge (`src/renforge/bridge/editor.rpy`).

## 1. Objective

Unblock selection for rotated displayables (`Transform(..., rotate=...)`) by replacing AABB-only hit-testing (`aabb_false_positive`) with exact runtime quad hit-testing.
- **In-Scope**: Accurate transformed selection (hit-testing following the painted shape) and drag/move capability.
- **Out-of-Scope**: Direct editing of the rotation angle itself.

## 2. Mandatory Geometry Invariant

For any target with a runtime `Transform` matrix from which a transformed quad is derived:
- The calculated quad center MUST align with the focus rectangle center (within ~1.0 px tolerance).
- The calculated quad MUST fit entirely inside the focus rectangle bounds.

If this invariant fails (e.g. unexpected nested transforms, custom anchor offsets), selection MUST be locked with a dedicated lock reason code (`TRANSFORM_GEOMETRY_UNPROVEN`) rather than falling back silently to AABB.

## 3. Mandatory Fallback Matrix

| Situation | Selection Behavior | Lock / Error Code |
|---|---|---|
| **No Transform found** | Pure AABB rectangle check (untransformed displayable, zero regression) | None |
| **Transform found, quad calculable, invariant holds** | Exact point-in-quadrilateral test (vector cross-product sign check on 4 edges) | None |
| **Transform found, quad uncalculable OR invariant violated** | Strict lock out (no selection, no silent AABB fallback) | `TRANSFORM_GEOMETRY_UNPROVEN` |

*Critical Rule*: AABB fallback is NEVER applied to a displayable known to be transformed.

## 4. Verification & Gate Conditions (PASS)

A run is PASS if and only if:
1. Clicking an unpainted AABB corner (e.g., point `[220, 220]` for focus rect `[220, 220, 160, 80]`) selects NOTHING.
2. Clicking the painted center (`[300, 260]`) selects the rotated target displayable.
3. Move preview, save commit, reload rebind, and product undo all succeed on rotated targets.
4. `tests/test_editor_rotation_live.py` passes with verdict `pass` across **two consecutive runs**.
5. Full codebase test suite remains 100% green (`668 passed`).
6. Report explicitly notes that Issue #46 (crop composite) remains locked.
