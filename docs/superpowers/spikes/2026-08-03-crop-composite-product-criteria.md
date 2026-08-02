# Product Criteria — `Transform(crop=...)` Composite Selection & Move Unblocking (Issue #46)

**Status:** frozen prior to implementation.
**SDK:** Ren'Py **8.5.3**.
**Scope:** Production in-game editor bridge (`src/renforge/bridge/editor.rpy`).

## 1. Objective

Unblock visual editor position editing and drag movement for composite transform displayables that combine `Transform(crop=...)` with `zoom` or `rotate` (Issue #46).

## 2. Delta Conversion Formula

Instead of assuming a 1:1 ratio between screen pixels and source coordinate units:
$$\Delta \text{source} = \text{reverse}(\text{point\_screen\_end}) - \text{reverse}(\text{point\_screen\_start})$$

- Points mapped: $\text{point\_screen\_start}$ (baseline position) and $\text{point\_screen\_end} = \text{point\_screen\_start} + \Delta \text{screen}$.
- The two points are mapped through `_renforge_editor_matrix_map(reverse_fn, ...)` individually, and their difference is taken so that origin translation cancels out.

## 3. Mandatory Fallback Matrix

| Situation | Move & Position Calculation | Lock / Reason Code |
|---|---|---|
| **No Transform found** | Standard 1:1 ratio calculation (`source_position + position - baseline`) | None |
| **Transform found, `reverse` matrix available & invariant holds** | Inverse matrix conversion of screen displacement | None |
| **Transform found, `reverse` matrix unavailable OR invariant violated** | Keep composite target locked | `TRANSFORM_CROP_COMPOSITE_UNSUPPORTED` / `TRANSFORM_GEOMETRY_UNPROVEN` |

## 4. Verification & Gate Conditions (PASS)

A run is PASS if and only if:
1. `crop_with_zoom` (`zoom=1.25`): A requested 20px horizontal drag at screen level moves the displayable by 20px on screen (source updated by $20 / 1.25 = 16\text{px}$), and passes the 1px pixel agreement gate after reload rebind.
2. `crop_with_rotate` (`rotate=15°`): A requested horizontal drag at screen level moves the displayable cleanly without uncompensated diagonal drift, and passes the 1px pixel agreement gate after reload rebind.
3. Untransformed targets (`button`, `pos`, `offset`, `align`, pure `crop`) have **zero regression** and produce byte-identical source changes as before.
4. Product undo/redo and live attestation succeed across engine reloads.
5. Full codebase test suite remains 100% green (`668 passed`).
