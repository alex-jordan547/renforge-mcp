# Issue #50 — style colour source-write result

## Verdict

**`BLOCKED` — `style_color_product_path_missing`**

The dedicated `text` + `color` source-write contract is implemented and proven
(unit + live Ren'Py 8.5.3 pixel evidence). Production style editing remains
**disabled**: plain `text` is outside the focus_list selection path, and no
style-colour preview, coordinator intent, refused-attestation rollback, or
**product** undo surface is enabled.

Manual fixture restore after the live run is **cleanup only**, not product undo.

## Frozen pair

| Field | Value |
| --- | --- |
| Adapter | `text` |
| Property | `color` |
| Unlocked form | single-line pure hex string literal (`#rgb` / `#rrggbb` / `#rrggbbaa`) |
| Style mode | `literal_hex` |

## Source contract

- `analyze_text_color_style` / `apply_text_color_patch` in `renforge.editor.source`
- Dedicated colour-token spans — **not** coordinate token semantics
- Fail-closed locks: inherited, expression, unsupported form, duplicate, non-text, block form, id mismatch
- Unrelated bytes, quote form, and hex family preserved; stale source rejected

Unit suite: `tests/test_editor_style_color_source.py` — **18 passed**

## Live Ren'Py 8.5.3 evidence (observed)

Opt-in: `RENFORGE_STYLE_COLOR_LIVE=1`

```text
RENFORGE_STYLE_COLOR_LIVE=1 PYTHONPATH=src python -m pytest -q tests/test_editor_style_color_live.py
# 1 passed in 8.99s
```

Fixture: large red `text "STYLE" color "#e22b2b"` → dedicated patch to `#2457d6`,
plus inherited and expression lock controls.

| Plane | Observed |
| --- | --- |
| Source unlock | target unlocks (`literal_hex`); inherited → `STYLE_COLOR_NOT_DIRECTLY_AUTHORED`; expression → `STYLE_COLOR_LITERAL_REQUIRED` |
| Source patch | only colour token rewritten; outside-span identity true; independent expected match true |
| Pixel before | independent PNG region sample dominant **red** (not scene-tree style metadata) |
| Pixel after reload | independent PNG region sample dominant **blue** after `reload_script` + re-show; published source reads the requested literal |
| Product select on glyph | does **not** unlock style colour (`product_select_unlocked_style=false`) |
| Product preview/commit/undo | all **false** / unavailable |
| Refused-attestation rollback | **false** / unavailable |
| Fixture restore | byte-identical baseline restore; note = cleanup, not product undo |
| Verdict | `blocked` / `style_color_product_path_missing` |

Live test: `tests/test_editor_style_color_live.py` — **1 passed**

Full suite: **656 passed, 45 skipped**. `compileall` and `git diff --check` passed.

## Why blocked (one stable reason)

`style_color_product_path_missing`

Mandatory product seams for PASS are absent after source + pixel proof:

1. production selection uses `focus_list` — plain `text` is non-focusable;
2. no style-colour preview / intent / coordinator commit path;
3. no refused-attestation rollback or **product** undo for style colour
   (fixture restore is cleanup only).

Widening into Stage 3 non-focusable selection or a second adapter/property is
out of frozen scope. Production style UI/control stays disabled.

## Criteria

`docs/superpowers/spikes/2026-08-02-style-color-criteria.md`
