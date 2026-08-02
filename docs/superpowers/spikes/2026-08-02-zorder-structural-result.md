# Issue #49 — source-safe z-order structural spike result

## Verdict

`BLOCKED — structural_transaction_undo_missing`

The smallest useful source rewrite is proven, but it must not be exposed in the editor UI yet. RenForge's current position transaction only owns coordinate edits and has no committed structural undo record.

## Frozen operation

`raise_adjacent_sibling` swaps exactly two consecutive direct `button ...:` children of the same `screen` while leaving the separator and every byte outside the two button spans untouched.

Rejected before writing:

- non-button or non-block statements;
- different/nested parents;
- non-adjacent siblings;
- dynamic/non-literal or duplicate IDs;
- explicit `zorder` on either button;
- stale source SHA.

## Source evidence

- UTF-8 byte offsets are used, including non-ASCII content before and inside both blocks.
- The source baseline SHA is bound into the analysis plan.
- Publication rejects a changed baseline with `STALE_SOURCE`.
- Post-swap source lines are recomputed for both stable IDs.
- The patch has a zero-byte size delta in the live fixture.
- Unit source suite: `89 passed`.

## Live Ren'Py 8.5.3 evidence

Fixture: two overlapping direct sibling buttons at `(220, 220)`, each `180×100`.

Before swap:

- painted pixel at `(240, 240)`: `[35, 84, 206]` (blue sibling);
- focus selection: `zorder_sibling`.

After source swap + real `reload_script`:

- painted pixel at `(240, 240)`: `[216, 58, 58]` (red target);
- focus selection: `zorder_target`;
- both stable IDs rebound with unchanged bounds;
- target source line: `16`;
- sibling source line: `9`.

After fixture restoration + second real reload:

- painted pixel returns blue;
- focus selection returns to `zorder_sibling`;
- restored SHA equals the original SHA;
- restored bytes are exactly equal to the baseline.

Live test: `1 passed in 8.21s`.

## Why blocked

The runtime result, reload, stable rebind, stale guard, and byte-identical fixture restoration are proven. What is not yet present is a product-level structural transaction that can persist the original spans/bytes and perform conditional rollback plus explicit undo after commit.

Enabling a z-order control before that seam exists would bypass the editor's transactional safety contract. No production UI control or coordinator command is enabled by this spike.
