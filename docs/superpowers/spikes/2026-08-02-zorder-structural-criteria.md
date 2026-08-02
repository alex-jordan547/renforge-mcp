# Issue #49 — frozen structural edit criteria

## Verdict scope

The only supported structural operation is `raise_adjacent_sibling`.

It swaps exactly two consecutive, direct `button ...:` children of the same `screen` body in one UTF-8 source file. The target must be immediately followed by the sibling (comments/blank separator bytes may remain between them). Moving the target later raises it one source-order step.

Out of scope: general reparenting, nested containers, non-adjacent moves, arbitrary tree rewrites, non-button statements, UI controls, explicit/dynamic `zorder`, loops/conditionals/`use`, multiple files, and more than one runtime instance per source statement.

## Source contract

The analyzer must prove before staging that:

- operation is exactly `raise_adjacent_sibling`;
- both statements are block-form buttons with distinct literal IDs;
- both are direct children of the same screen and have the same indentation;
- the target is immediately before the sibling, ignoring only blank/comment separator lines;
- each ID has unique source ownership and exactly one static runtime instance;
- neither button header contains explicit or dynamic `zorder`;
- spans are ordered, non-overlapping, UTF-8-safe, and still match the analyzed baseline digest.

The staged bytes must equal the independent construction:

`prefix + sibling_block + separator + target_block + suffix`

Each raw block is moved byte-for-byte. Every byte outside the two block spans is unchanged. Post-swap source locations for both IDs are computed from the staged bytes rather than copied from the baseline.

## Transaction contract

Forward publication must use a dedicated structural coordinator command, not a position intent:

1. independently reobserve both runtime keys;
2. re-read and revalidate the structural source contract;
3. stage the raw block exchange;
4. run Ren'Py lint in the existing shadow project;
5. recheck the source digest;
6. atomically publish;
7. reload exactly once;
8. rebind both IDs at their computed post-swap lines;
9. attest that the target is above the sibling at their overlap;
10. commit only after attestation.

Any validation, stale-source, ambiguous-ownership, multi-instance, rebind, or attestation failure must fail closed before publication or use the existing conditional rollback. A rollback conflict must preserve external bytes and report uncertainty.

`structural_undo` is allowed only for one committed structural transaction whose staged digest still matches the file. It creates a validated atomic reverse transaction, reloads, rebinds both original source locations, and marks the forward transaction undone only after successful attestation. A second undo is rejected.

## Independent live evidence

The opt-in Ren'Py fixture contains two opaque, completely overlapping direct sibling buttons with distinct literal IDs and colors.

The live proof must record:

- baseline topmost hit and screenshot center color belong to the sibling;
- forward swap source bytes match the independent construction;
- shadow validation and atomic publication succeed;
- generation increments by one;
- both IDs rebind uniquely at their new source lines;
- runtime hit order reports the target above the sibling;
- an independent screenshot center pixel changes to the target color;
- one deliberately refused attestation rolls the published source back to the exact baseline SHA;
- a successful forward transaction followed by `structural_undo` reloads and restores byte-identical baseline source and baseline visual order.

Screenshot color and runtime focus/hit order are separate evidence planes; neither may validate the other.

## PASS

All source, transaction, live-reload, unique-rebind, hit-order, screenshot-color, rollback, and byte-identical undo checks pass. Relevant targeted/full tests pass and the fixture is restored.

## BLOCKED

Return `BLOCKED` with one stable reason if runtime paint order, post-swap rebinding, rollback, or byte-identical transactional undo cannot be proven. Do not expose production UI.

## INCONCLUSIVE

Return `INCONCLUSIVE` for missing seams, malformed reports, unavailable live runtime, or incomplete independent evidence. Do not expose production UI.
