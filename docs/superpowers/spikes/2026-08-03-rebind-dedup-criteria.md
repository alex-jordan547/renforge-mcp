# Rebind Candidate Deduplication Criteria

**Date:** 2026-08-03  
**Target issue / feature:** Fix `AMBIGUOUS_REBIND` on candidate resolution when widgets surface in both focus and non-focusable text candidate passes.

## Context & Problem Definition
- Commit `87bf1246f1e5bfc550bb1d2dec018e5d8c47be43` added deduplication on identical `runtime_key` dicts (`seen.get("runtime_key") == candidate_key`).
- However, when a widget (such as `pos_container`, `anchor_computed`, `offset_container`, `task0_target`) surfaces in both `_renforge_editor_focus_candidates()` and `_renforge_editor_text_candidates()`:
  - Focus candidate `runtime_key` ends ancestry at `Button` (5 nodes) and uses focus list `ordinal` (e.g. `8`).
  - Text candidate `runtime_key` ends ancestry at `Text` (6 nodes) and uses text pass `ordinal` (e.g. `6`).
- As a result, `runtime_key` dicts are not strictly equal (`==`). Deduplication by raw `runtime_key` fails.
- Rebind falls back to `_renforge_editor_rebind_signature` tuple: `(screen, widget_id, source_location, instance_key)`.
- Both candidates return the identical signature tuple `('renforge_editor_pos_fixture', 'pos_container', ('game/zz_renforge_editor_pos_fixture.rpy', 29), None)`.
- Without deduplication on signature, the fallback loop collects both candidates (`len(candidates) == 2`), triggering `AMBIGUOUS_REBIND` / `RUNTIME_PROBE_FAILED`.

## Implementation Criteria
1. `_renforge_editor_h_observe_target` (editor.rpy ~L3980):
   - Deduplicate candidates matching `_renforge_editor_same_target_key` by keeping the first (focus candidate).
   - In signature fallback loop (`_renforge_editor_rebind_signature`), deduplicate candidates sharing the same signature so that multiple candidate representations of a single statement instance are counted as 1 target.
2. `_renforge_editor_h_attest_targets` (editor.rpy ~L4020):
   - Deduplicate candidates matching `_renforge_editor_same_target_key` by keeping the first.
   - In signature fallback loop, check signature uniqueness against `_renforge_editor_rebind_signature` (not exact `runtime_key ==`) so focus and text views of the same target do not produce `AMBIGUOUS_REBIND`.
3. `_renforge_editor_resolve_selected_candidate` (editor.rpy ~L2625):
   - When matching loose matches by signature, deduplicate candidates that share the same `_renforge_editor_rebind_signature`.

## Verification Criteria
- `RENFORGE_POS_LIVE=1 PYTHONPATH=src uv run pytest -q tests/test_editor_pos_live.py` passes 3 runs in a row.
- `RENFORGE_ANCHOR_LIVE=1 PYTHONPATH=src uv run pytest -q tests/test_editor_anchor_live.py` passes 3 runs in a row.
- `RENFORGE_OFFSET_LIVE=1 PYTHONPATH=src uv run pytest -q tests/test_editor_offset_live.py` passes 3 runs in a row.
- `RENFORGE_TASK0_LIVE=1 PYTHONPATH=src uv run pytest -q tests/test_editor_task0_live.py` passes 3 runs in a row.
- Full test suite passes: `PYTHONPATH=src uv run pytest` (668 passed, 48 skipped).
- `PYTHONPATH=src uv run python -m compileall -q src tests` clean.
- `git diff --check` clean.
- Regression check on previously passing live suites (`style_color`, `zorder`, `button`, `crop`, `rotation`).
