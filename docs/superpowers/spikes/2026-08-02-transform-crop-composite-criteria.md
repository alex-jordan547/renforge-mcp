# Spike criteria — `Transform(crop=...)` combined with `rotate` / `zoom` (issue #46)

Written **before** any implementation or measurement, as the issue requires. Issue #45 proved pure
`Transform(crop=)` and locked the composite case as `TRANSFORM_CROP_COMPOSITE_UNSUPPORTED`; this
spike decides whether that lock can be lifted.

**SDK under test:** Ren'Py **8.5.3**. Any other version makes the result inconclusive.

## What the editor needs to be true

The editor's whole write chain rests on one assumption, inherited from every adapter shipped so far:

> A displacement applied in the authored (child) coordinate space produces an **equal** displacement
> in the screen space that `focus_list` reports.

That is why the bridge derives the authored value as
`source + (preview_screen − runtime_baseline_screen)` and the host attests `runtime_rect + Δ` against
a later focus rect. Under pure crop the assumption holds, because a crop only translates and clips.

`zoom` scales that mapping by a factor. `rotate` turns it into a rotation. Neither is a translation,
so the assumption is not merely untested here — it is arithmetically suspect. This spike measures
whether it survives, and whether anything recoverable from observations alone could replace it.

## Pass conditions

All four must hold, measured live, for a shape to be unlocked:

1. **Focus rects describe visible geometry.** For a rotated child, the reported rect must correspond
   to the widget actually painted on screen. An axis-aligned rect that ignores the rotation, or that
   reports the untransformed layout box, fails this condition.
2. **The screen↔child mapping is recoverable from observed quantities only.** The editor may not read
   `rotate`/`zoom` off the Transform and trust it; it must be derivable from focus measurements, or
   the mapping is not attestable. (Reading the property is acceptable *evidence* in the spike, but a
   shape unlocked on that basis must still attest through observed pixels.)
3. **A child-space edit lands within 1 px of prediction.** Preview a known displacement, then verify
   the observed focus rect agrees within one logical pixel — the same bar every other adapter meets.
4. **The full seven steps pass:** resolve → preview → patch → reload → pixel agreement → rebinding →
   byte-identical undo, with the source patch touching only the coordinate spans.

## Blocked conditions

Any one of these means the composite case stays locked, and that is an accepted outcome:

- The reported focus rect is the AABB of a rotated quad (or the unrotated box), so it does not
  identify the visible shape and cannot back a pixel-accurate edit.
- A child-space displacement produces a screen displacement of a different magnitude or direction,
  and the conversion factor cannot be recovered from observations.
- Pixel agreement cannot be met within 1 px because the transform introduces rounding the editor
  cannot predict.
- The source write would have to encode a transform-aware value, i.e. the editor would have to
  rewrite the author's meaning rather than move a literal.

## Inconclusive conditions

- Measurements differ across runs or across frames for an unchanged scene.
- The fixture cannot isolate the composite transform from crop clipping, so a failure cannot be
  attributed to rotation/zoom rather than to the crop.
- The target cannot be selected at all under the composite transform, which would make this a
  selection question (issue #43 territory) rather than a write-chain question.

## Explicitly out of scope

- Rotation **without** crop — that is issue #48, and it must not be unlocked here as a side effect.
- Any change to the pure-crop behaviour proven by issue #45.
- Building a general transform-aware coordinate layer. If the measurements say that is what would be
  required, the finding is recorded and the gate stays locked.

## Decision rule

A negative result with recorded evidence closes this issue. The lock is only lifted for a shape that
meets **all four** pass conditions, and only for exactly that shape.
