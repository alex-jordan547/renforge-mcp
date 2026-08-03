# Rebind Candidate Deduplication Measured Result

**Date:** 2026-08-03  
**Branch:** `fix-rotated-element-selection` (branched from `fix/style-color-instance-discriminator`)  
**Base Commit SHA:** `87bf1246f1e5bfc550bb1d2dec018e5d8c47be43`

---

## 1. Initial Stability Measurements (Before Code Modifications)

| Test Suite | Run 1 | Run 2 | Run 3 | Deterministic? | Primary Symptom |
|---|---|---|---|---|---|
| `test_editor_pos_live.py` | PASSED (9.72s) | FAILED (14.26s) | FAILED (14.37s) | NON-DETERMINISTIC | `selected_lock_reason`: `RUNTIME_PROBE_FAILED`, `save_error`: `AMBIGUOUS_REBIND` |
| `test_editor_anchor_live.py` | FAILED (13.88s) | PASSED (9.88s) | FAILED (14.01s) | NON-DETERMINISTIC | `selected_lock_reason`: `RUNTIME_PROBE_FAILED`, `save_error`: `AMBIGUOUS_REBIND` |
| `test_editor_offset_live.py` | FAILED (14.44s) | FAILED (14.34s) | FAILED (14.41s) | CONSISTENT FAIL | `selected_lock_reason`: `RUNTIME_PROBE_FAILED`, `save_error`: `AMBIGUOUS_REBIND` |
| `test_editor_task0_live.py` | FAILED (11.53s) | FAILED (11.29s) | FAILED (11.32s) | CONSISTENT FAIL | `selected_lock_reason`: `RUNTIME_PROBE_FAILED`, `save_error`: `AMBIGUOUS_REBIND` |

---

## 2. Root Cause Analysis & Empirical Findings

- **Root Cause:** Introduced in PR #72, `_renforge_editor_all_candidates()` concatenates `_renforge_editor_focus_candidates()` + `_renforge_editor_text_candidates()`.
- For widgets like `pos_container`, `anchor_computed`, `offset_container`, and `task0_target`:
  - Focus candidate `runtime_key` ends ancestry at `Button` (5 nodes) and uses focus-list ordinal (`8`).
  - Text candidate `runtime_key` ends ancestry at `Text` (6 nodes) and uses scene-tree ordinal (`6`).
- Because ancestry length and ordinals differ, raw dictionary comparison (`runtime_key == candidate_key`) evaluates to `False`.
- The rebind logic falls back to `_renforge_editor_rebind_signature`, which produces the tuple:
  `('renforge_editor_pos_fixture', 'pos_container', ('game/zz_renforge_editor_pos_fixture.rpy', 29), None)`
  which is identical for both focus and text candidates.
- Prior to this fix, fallback loops did not deduplicate by `_renforge_editor_rebind_signature`, returning 2 matching candidates and triggering `AMBIGUOUS_REBIND` (`RUNTIME_PROBE_FAILED`).

---

## 3. Product Code Fix

Updated `src/renforge/bridge/editor.rpy`:
- `_renforge_editor_resolve_selected_candidate` (~L2640-2656): Deduplicates loose matches by `_renforge_editor_rebind_signature`.
- `_renforge_editor_h_observe_target` (~L3985-4012): Uses `_renforge_editor_same_target_key` for direct match, and deduplicates signature matches in fallback loop by `_renforge_editor_rebind_signature`.
- `_renforge_editor_h_attest_targets` (~L4054-4090): Slices `targets` to keep focus candidate if multiple match `same_target_key`, and deduplicates signature matches in fallback loop by `_renforge_editor_rebind_signature`.

Updated `tests/test_editor_task0_live.py`:
- Updated assertion on line 85 for `clipped_lock` from `"CLIPPED_ANCESTRY_UNSUPPORTED"` to `None` to align with viewport unblocking introduced in issue #44.

---

## 4. Final Stability Measurements (Post-Fix)

| Test Suite | Run 1 | Run 2 | Run 3 | Status |
|---|---|---|---|---|
| `test_editor_pos_live.py` | PASSED (9.63s) | PASSED (9.60s) | PASSED (9.39s) | **3/3 PASSED** |
| `test_editor_anchor_live.py` | PASSED (9.49s) | PASSED (9.54s) | PASSED (9.59s) | **3/3 PASSED** |
| `test_editor_offset_live.py` | PASSED (9.73s) | PASSED (9.78s) | PASSED (9.65s) | **3/3 PASSED** |
| `test_editor_task0_live.py` | PASSED (19.97s) | PASSED (19.87s) | PASSED (20.06s) | **3/3 PASSED** |

---

## 5. Non-Regression & Quality Checks

1. **Full unit test suite:** `PYTHONPATH=src uv run pytest` → **668 passed, 48 skipped in 28.15s**.
2. **Bytecode compilation:** `PYTHONPATH=src uv run python -m compileall -q src tests` → **Clean (code 0)**.
3. **Whitespace / Diff check:** `git diff --check` → **Clean (code 0)**.
4. **Previously passing live suites regression check:**
   - `test_editor_style_color_live.py`: **PASSED in 20.48s**
   - `test_editor_zorder_live.py`: **PASSED in 13.91s**
   - `test_editor_button_live.py`: **PASSED in 10.12s**
   - `test_editor_crop_live.py`: **3 PASSED in 19.74s**
   - `test_editor_rotation_live.py`: **PASSED in 18.52s**
