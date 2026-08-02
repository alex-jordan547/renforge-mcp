# Issue #49 — z-order product-level editing criteria (frozen before implementation)

**Status:** criteria locked before implementation.
**SDK:** Ren'Py **8.5.3** only.
**Surface:** in-game bridge + editor coordinator.
**Operation:** `raise_adjacent_sibling` (structural swap of two adjacent direct sibling buttons).

## Question

Can RenForge:

1. analyze and validate a candidate z-order `raise_adjacent_sibling` swap for direct adjacent button siblings in the same screen body;
2. reject all invalid/unsupported cases fail-closed with stable error codes (different parents, non-adjacent siblings, duplicate/dynamic IDs, explicit zorder, stale SHA, combined intents);
3. process a structural swap transaction in the coordinator without modifying unrelated source bytes;
4. support transactional undo that restores original source bytes octet-for-octet (SHA match) and re-binds stable IDs;
5. expose the capability and control in the production bridge (`src/renforge/bridge/editor.rpy`), re-binding both widgets by stable id after reload;
6. prove the full product path live: capability exposure → swap intent submission → coordinator publication → reload → pixel attestation → product undo → pixel/SHA restoration?

## Accepted source form (unlocked)

Two consecutive direct `button ...:` block statements inside the same `screen` body:

```renpy
screen zorder_test():
    button id "zorder_target":
        xpos 220 ypos 220 xsize 180 ysize 100
        # ...
    button id "zorder_sibling":
        xpos 220 ypos 220 xsize 180 ysize 100
        # ...
```

- Target button is immediately before sibling button (ignoring blank/comment separator lines);
- Same parent screen, same block indentation;
- Distinct literal IDs matching runtime widget IDs;
- No explicit or dynamic `zorder` property on either button;
- Single file, baseline SHA matching current file state.

## Reject cases & fail-closed lock codes

| Code / Reason | Case |
| --- | --- |
| `STATEMENT_KIND_MISMATCH` / `PARENT_MISMATCH` | Different parents or non-button blocks |
| `NON_ADJACENT_SIBLINGS` | Target and sibling are not adjacent |
| `ID_LITERAL_REQUIRED` / `ID_MISMATCH` | Missing, dynamic, or mismatched literal ID |
| `EXPLICIT_ZORDER_UNSUPPORTED` | Header contains explicit/dynamic `zorder` |
| `STALE_SOURCE` | Source SHA changed since analysis |
| `STRUCTURAL_INTENT_COMBINATION_REJECTED` | Transaction combines z-order swap with position/color intents |

## Evidence planes (independent)

| Plane | Must measure | How |
| --- | --- | --- |
| **Source swap** | Byte-for-byte block exchange, zero size delta outside target blocks | `analyze_raise_adjacent_sibling` + `apply_button_sibling_swap` |
| **Coordinator** | Structural intent processing, new lines calculation, combined intent rejection | Unit tests in `test_editor_coordinator.py` |
| **Undo** | Undo committed swap restores exact original source bytes (SHA match) | `_command_undo_commit` unblocked for structural swaps |
| **Product UI / Bridge** | Capability calculation (`zorder_raise_adjacent_sibling`), control submission, post-reload rebinding | `editor.rpy` capability + intent submission |
| **Live proof** | Painted pixel at overlap point changes to target color, undo restores pixel color and SHA | `test_editor_zorder_live.py` running through product bridge |

## Pass / blocked / inconclusive (frozen)

### PASS

1. Bridge publishes `zorder_raise_adjacent_sibling` capability when adjacent eligible sibling exists.
2. Bridge submits structural intent; coordinator validates, stages swap, lint-checks, and publishes atomically.
3. Reload advances script generation; both widgets are rebound by stable ID at their updated source lines.
4. Overlap painted pixel changes from sibling color to target color.
5. Product undo (`undo_commit`) restores baseline bytes octet-for-octet (SHA match) and restores original painted pixel color.

### BLOCKED

Any mandatory product seam fails or is missing.

### INCONCLUSIVE

Pixel sampling is ambiguous or live runtime environment is unavailable.

## Deliverable

1. Coordinator support for structural swap intents and undo.
2. Product bridge exposure in `editor.rpy`.
3. Unit and live tests passing in pytest suite.
4. Result report `docs/superpowers/spikes/2026-08-02-zorder-product-result.md` with `verdict: PASS`.
