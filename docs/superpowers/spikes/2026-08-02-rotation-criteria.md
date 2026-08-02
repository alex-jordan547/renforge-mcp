# Spike criteria — `Transform(rotate=...)` support for selection/write safety (issue #48)

**Status:** criteria locked before measurement.
**SDK:** Ren'Py **8.5.3** only.
**Surface:** in-game bridge + opt-in resources (no production editor logic changes).

## Question

Can RenForge:

1. derive a runtime-transformed painted quad from Ren'Py `Transform` forward/reverse seams for a rotated, focusable control;
2. validate screen-space selection against an independently captured candidate paint mask instead of accepting AABB alone;
3. safely write, reload, rebind and genuinely product-undo a rotated target in this live spike contract;
4. prove any fallback path is a direct fixture byte restore and not product undo?

## Evidence planes (must be measured independently)

| Plane | What must be measured | How it must be measured |
|---|---|---|
| **Transform seam** | Rotated quad from runtime Transform seam | compute via `_matrix_transform.forward`/`_matrix_transform.reverse` (or equivalent) from a temporary `rotation_spike.rpy`; **do not** derive from authored rotate degrees or AABB math |
| **Paint-mask** | Candidate-isolated mask with probes | isolate one candidate at a time, sample at center, painted edge, and AABB corner; mask comes from full screenshot classification (`rgb > bg_luma`) |
| **AABB/focus behavior** | Focus-based rect and whether probe points hit via AABB | read focus/list_ui bounds separately from seam+mask evidence; do not treat focus AABB as ground-truth geometry |

## Required fixtures and controls

- one focusable **rotated** control using `Transform(..., rotate=15)` with integer rotate literal
- one same-shape unrotated control (same declared size)
- one additional focusable candidate for non-rotated control-path sanity
- one dark background (no crop/layout/repetition ambiguity)
- deterministic integer initial `xpos`/`ypos` in fixture source

## Pass / blocked / inconclusive (frozen before implementation)

### Pass

A run is PASS only if **all** are true:

1. Transform seam extraction succeeds and returns a non-degenerate rotated quad for the rotated control.
2. Candidate mask probes run on isolated screenshots and report:
   - rotated center painted,
   - at least one AABB corner for rotated target outside paint.
3. The seam-derived rotated quad and mask are stored as the live evidence plane; focus AABB is recorded separately and not used as the sole geometry gate.
4. Rotated target is not lock- or seam-blocked, preview delta can be written, `editor_task0_save` commit/reload succeeds, rebinding succeeds, and generation delta is coherent with a committed edit.
5. Undo evidence is **product undo** (not byte restore): a full lockable write + reload + rebind trail is observed.

### Blocked

BLOCKED if any one occurs:

- seam missing (`forward` and `reverse` unavailable, non-degenerate quad not produced);
- mask probes cannot be produced on isolated paint (or cannot recover three required points);
- rotated target is genuinely locked (`selected_lock_reason` not empty), **or** any required plane fails (lock + seam/paint mismatch);
- an unpainted point inside the focus AABB selects the rotated target through the real editor selection path;
- write path reaches preview but fails at save/reload/rebind or generation, even with valid edit intent;
- manual restoration path is only byte-level restore of rotate span (explicitly not product undo).

### Inconclusive

- identical-run reproducibility for the same scene is unstable;
- required points are ambiguous for >10% of repeated probes (anti-alias/viewport noise not resolvable);
- fixture/control cannot be resolved (missing ids/spans) and independent evidence cannot be reconstructed.

## Stop conditions

- If required IDs or spans are unresolved, stop as `inconclusive`.
- If seam and mask disagree on rotated center/edge, stop and mark `blocked`.
- If the same scene measured twice gives different verdict classification, stop as `inconclusive`.
- If fixture line span patching touches non-target bytes, stop as `inconclusive`.

## Controlled write proof (must be included in report)

- patch only the rotated `rotate=<int>` literal in the temporary fixture,
- prove bytes outside that span are unchanged,
- restore the fixture from baseline bytes and prove byte-identical restoration.

`restore` in this proof is documentation-only evidence and must not be presented as product undo.

## Deliverable

One report dictionary from `run_editor_rotation_live_scenario` with `verdict` in
`{pass, blocked, inconclusive}` and explicit evidence fields for seam, mask, focus, lock, write/reload/rebind, and manual rotate-literal restore.
