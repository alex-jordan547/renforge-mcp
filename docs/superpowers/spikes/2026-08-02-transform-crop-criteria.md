# Spike — Source-safe editing under `Transform(crop=)` / `Crop` (issue #45)

**Status:** criteria locked before measurement.  
**SDK:** Ren'Py **8.5.3** only.  
**Surface:** in-game bridge + host coordinator; opt-in live fixture.

## Identity (measured, not assumed)

In Ren'Py 8.5.3, `Crop(rect, child)` is a constructor that returns
`Transform(child, crop=rect, **properties)`
(`renpy/display/layout.py`). There is no runtime class named `Crop`.

Therefore:

- Ancestry `type` is **`Transform`**, never `Crop`.
- Bridge `crop_state` for a pure crop is expected to be **`transform_crop`**
  (via `getattr(node, "crop", None)`), not `crop_displayable`.
- Branches that key on `class_name` containing `"Crop"`, host
  `ancestor_type == "Crop"`, or `crop_state == "crop"` are suspected dead code
  until a live dump proves otherwise. Do not delete them until the dump is on
  record; after the dump, correct locks so they name the shape that actually
  fires.

**Scope of this issue:** a `Transform` carrying **only** `crop` (and defaults).
Crop combined with `rotate` / non-default `zoom` / `xzoom` / `yzoom` is issue
**#46** (and overlaps #48). Those shapes stay locked with a distinct reason.

## Question

Can RenForge select, preview, patch, reload, rebind, and undo a single-line
`textbutton` whose ancestry includes exactly one pure-`crop` `Transform`
(authored as `fixed at Transform(crop=...)` or equivalent `Crop` sugar), with
**visible geometry** proven independently of the focus rectangle?

## Central risk

Unlike a viewport (issue #44), a crop can leave the **focus rect intact while
the widget is partially or fully invisible**. Gate: do not treat the focus
rectangle as sufficient evidence of on-screen paint. Measure painted pixels
(or an equivalent independent sample) before and after movement.

## Mechanisms under test

| Id | Mechanism | Notes |
|---|---|---|
| `FOCUS` | `focus_list` / select bounds | Engine-reported rect used by the editor today |
| `VISIBLE` | Independent visible geometry | Screenshot colour sample / crop-window ∩ focus AABB (or isolation paint) — must not be derived from the same focus rect alone |
| `WRITE` | Seven-step write chain | resolve → preview → patch → reload → pixel ≤1 → rebind → byte-identical undo |
| `CLASS` | Ancestry classification | Runtime type, `crop_state`, pure vs composite crop |

## Fixture shapes (must all run)

1. **Editable candidate** — single-line `textbutton` with literal `id` and
   integer `xpos`/`ypos`, fully inside a pure `Transform(crop=)` window whose
   child is a plain `fixed`.
2. **Partially clipped** — same statement shape; focus may extend past the crop
   edge; decide unlock only if `VISIBLE` and `FOCUS` stay consistent enough for
   safe select + attestation.
3. **Fully clipped** — entirely outside the crop rect; if the editor can still
   report a movable position as if it were on-screen, that is **`blocked`** for
   unlock of that case (may remain locked while a fully-visible sibling unlocks).
4. **Outside control** — same statement shape outside any crop (proves the crop
   is what changes, not the adapter).
5. **Computed ypos** — expression position → `YPOS_LITERAL_REQUIRED`.
6. **Layout container** — `vbox` child → `CONTAINER_POSITION_UNSUPPORTED`.
7. **Crop + rotate or crop + zoom** — must stay locked with a **distinct** reason
   (not the pure-crop code), scope #46.

## Coordinate space (known trap from #44)

`preview_position` is **screen** space; authored `xpos`/`ypos` are **child**
space. Under a crop transform they need not coincide. Compare **deltas**, never
absolute screen vs source values.

## Pass / blocked / inconclusive (written before code)

### Pass

All of the following hold on Ren'Py 8.5.3 live runs (two consecutive runs agree
on the capability verdict string):

1. **Classification:** live ancestry for `Crop(...)` / `fixed at Transform(crop=...)`
   shows `type == "Transform"` and `crop_state == "transform_crop"` (or the
   refined pure-crop label chosen after measurement). No live node reports
   `type == "Crop"` unless measured otherwise.
2. **Visible geometry:** for the partially clipped target, an independent
   `VISIBLE` sample disagrees with naive “full focus rect = fully painted”
   *or* agrees in a documented way — either way the report records numbers.
   Movement of the fully-visible editable target keeps `VISIBLE` and `FOCUS`
   deltas within ≤1 px of each other for the attested axes.
3. **Fully clipped:** either (a) selection/edit is refused with a stable lock /
   failed select, or (b) if focus still finds it, the proof documents that
   unlock is **not** granted for fully-clipped targets and only the fully
   visible shape is unlocked.
4. **Seven-step write** for the fully-visible pure-crop child: resolve unlocked,
   preview delta match ≤1, patch only xpos/ypos spans, reload committed,
   post-reload focus delta ≤1 vs post-preview, rebind ok, undo byte-identical.
5. **Locks:** computed / container / crop+rotate or crop+zoom each return their
   exact expected codes; pure crop is the only newly unlocked crop shape.
6. **Outside control** remains editable (or at least not crop-locked).

### Blocked

Any of:

- Focus reports a usable rect for a fully painted-invisible (fully clipped)
  control and the only path to “success” would be trusting that rect without a
  visible-geometry check — unlock is refused; document as negative result.
- Preview or post-reload attestation systematically disagrees (>1 px) with
  requested deltas after two runs.
- Pure `transform_crop` cannot be classified distinctly from crop+rotate/zoom.
- Observation never reaches the host (`CLIPPED_ANCESTRY_UNSUPPORTED` or
  equivalent) and cannot be opened without also opening unmeasured shapes.

A documented **blocked** verdict that keeps the lock is an accepted issue
deliverable.

### Inconclusive

- Consecutive runs disagree on the capability verdict without code change.
- Anti-aliasing / subpixel makes `VISIBLE` sampling ambiguous on >10% of probes
  after a 1-pixel neighbour allowance.
- Fixture cannot place a stable partial clip without Ren'Py auto-scrolling or
  recentering the focused control (must be measured; if unavoidable, report it).

## Non-goals

- Nested crop transforms.
- Crop combined with rotate/zoom (issue #46).
- Viewport (already #44).
- Production selection UX for non-focusables (#43 product follow-up).
- Deleting unrelated dead code outside the crop lock path.

## Artifacts

- Criteria (this file)
- Fixture `tests/live_fixtures/renforge_editor_crop_fixture.rpy`
- Runner `src/renforge/editor_crop_runner.py`
- Live test `tests/test_editor_crop_live.py` (`RENFORGE_CROP_LIVE=1`)
- Unit tests in `tests/test_editor_coordinator.py` for any lock change
- Result notes in roadmap Stage 4 + V1 scope gate 4 (measured values only)
- PR `Closes #45`
