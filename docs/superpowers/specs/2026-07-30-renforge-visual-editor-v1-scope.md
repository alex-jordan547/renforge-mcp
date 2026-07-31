# RenForge Visual Editor — V1 Scope

Status: **implementation scope, frozen.** Derived from four feasibility spikes, not from intent.
Anything not proven by a spike is out of V1 by default — see
`2026-07-30-renforge-visual-editor-vfull-roadmap.md`.

## What V1 is

Position focusable widgets in a running Ren'Py game, pixel-precise, with safe write-back to source.

You open the game with edit mode enabled, click a widget, drag it (or nudge it with arrow keys), see
alignment guides and live pixel values, then press Save. Dragging changes only Ren'Py runtime state.
Save sends source intents over a dedicated authenticated loopback connection to RenForge; only the
host coordinator may validate and atomically publish `.rpy` changes.

## What V1 is not

It is **not** VizBug for Ren'Py. The original idea was "move any element". The spikes proved that is
not reachable today, and V1 does not pretend otherwise:

- Non-focusable elements (plain `text`, `add`, decorative `frame`) cannot be **selected** — they are
  absent from Ren'Py's `focus_list`. The workaround (`quad ∩ sentinel mask`) is `untested`.
- There is no general visual coverage oracle. Selection is AABB-level (Spike A: `coverage_oracle: blocked`).
- Per-pixel alpha hit-testing is out of scope.
- No resize, no rotation, no style editing. V1 moves things.

## The four gates

An element is editable in V1 **only if all four hold**. Each gate exists because a proven mechanism
requires it — none is arbitrary.

| Gate | Requirement | Why (mechanism) |
|---|---|---|
| 1 | Displayable is **focusable** | Selection and every save-bearing measurement read `renpy.display.focus.focus_list` |
| 2 | Source statement carries one **literal `id`** matching the runtime widget id | Runtime preview uses `_widget_properties`, keyed by the authored widget id; synthetic observation IDs do not qualify |
| 3 | Single-line statement from the proven adapter allowlist (`textbutton`, `imagebutton`) with one literal integer **`xpos` and `ypos`** | The token-aware patcher replaces only those two integer spans |
| 4 | Exactly one runtime instance with a fully classified, static ancestry outside **`viewport` / `Crop` / `Transform(crop=)`**, loop and repeated `use` cases | Only a unique static instance under proven clipping can be rebound unambiguously |

**A failing gate is a first-class UI state, never a silent no-op.** The overlay must name which gate
failed and why, e.g. *"`text` is not focusable — cannot be selected in V1"* or
*"`xpos` is an expression, not a literal — cannot be written back"*.

Elements that fail gate 2, 3 or 4 but pass gate 1 remain **selectable and measurable** (you can inspect
their box and distances) but are **locked for editing**. Elements failing gate 1 are invisible to V1.

The runtime descriptor is deny-by-default. It carries a typed ancestor chain, screen invocation path,
instance discriminator, source location and editor-ownership marker. The host independently parses the
source statement and compares the literal id. Missing proof, an unknown ancestor/crop state, or more
than one live instance produces a stable lock reason; an ordinal or synthetic scene-tree id is never
treated as source identity.

## Adapter allowlist

V1 ships an explicit allowlist, extended one adapter at a time, each backed by an end-to-end proof.

| Adapter | Selection | Write chain | Status |
|---|---|---|---|
| `textbutton` | Spike C `pass` | Spike D `pass` | **Shipped in V1** |
| `imagebutton` | Spike C `pass` (focusable) | dedicated analyzer + coordinator path | **Implemented; live proof opt-in (`RENFORGE_IMAGEBUTTON_LIVE=1`)** |
| `button` | focusable by construction | not exercised | Blocked until proven |
| `text`, `add`, `frame` | **not selectable** | Spike B proved write on a literal `text`, but by key, not by click | Out of V1 |

`imagebutton` has a dedicated single-line adapter (issue #32) rather than a textbutton allowlist widen.
It ships to the host path with unit/coordinator coverage; the seven-step live proof is opt-in until
exercised in default CI.

## Proven mechanisms (do not redesign)

Every item below is backed by a live measurement, not a reading of the docs.

**Selection** — `renpy.display.focus.focus_list`. The engine already resolves clipping and z-order, so
a clipped `textbutton` reports its true visible rect (Spike C: focus rect `120×260`, not the unclipped
`260`).

**Runtime preview** — the supported screen override seam:

```python
renpy.show_screen(screen, _layer="screens",
                  _widget_properties={widget_id: {"xpos": x, "ypos": y}})
```

`show_screen` documents `_widget_properties` (`display/screen.py:1329`). SL2 merges it into the
statement keywords (`sl2/slast.py:985`); the keyword set then differs from the cache, so `can_reuse`
is false (`sl2/slast.py:1011-1017`) and the widget is **recreated** at the new position.

Consequences that matter: no shared style is mutated, and reversal is symmetric — re-show without
`_widget_properties`. The widget's object identity changes on every preview, so any code holding a
displayable reference across a preview is wrong by construction.

**Rejected alternative, measured not assumed:** injecting a wrapper `Displayable` into the live screen
tree. It stays reachable from `screen.child` yet Ren'Py never traverses it — `0` calls to `render`,
`visit`, `get_placement` — even with `invalidate` + `redraw` + `force_redraw` all succeeding. Ren'Py
renders from the SL2 cache. Do not retry this.

**Source write** — the RenForge host coordinator performs a token-aware, single-statement rewrite,
refuses a stale baseline, validates staged source before publication, then atomically replaces the
file. The bridge and in-game overlay never open user `.rpy` files.

**Rebinding** — after `reload_script()`, the target is re-found by a stable key of
`screen name + source location + ancestry + ordinal`. Never by a remembered object reference: identity
is provably gone across a reload.

**Rollback proof** — the spike restored baseline bytes and verified byte identity by SHA-256. In the
product, this mechanism is reserved for failed publication/reload attestation. User Undo/Redo edits
runtime commands before Save and does not cross a successful-save boundary.

## Measurement rule (non-negotiable)

**Every position that backs a user-visible value or a save decision MUST be an observed focus rect.**

The bridge also has a computed-placement path (`_renforge_scene_place`). It is fine for informational
values and **must never** back a save, a guide, or a reported delta. Positions carry a
`measurement_method` field; anything other than `focus_list` is treated as unmeasured.

This is not pedantry — during the spikes, four separate bugs all reduced to the same root cause:
evidence that validated itself. A computed placement agreeing with a requested position proves nothing.

## Security boundary

- The injected overlay owns interaction and runtime preview only.
- The existing game-side bridge remains host-to-game and is reused only for independent fresh-frame
  observation and post-reload attestation — never to publish source.
- A per-launch `EditorCoordinator` on the RenForge host listens on loopback with a separate random
  token and an auth-first, versioned protocol.
- The overlay may call `analyze_target`, `commit`, `commit_status`, and `reload_handshake` on that
  dedicated connection. It never receives a filesystem write primitive.
- Save stays disabled while analysis is pending, the coordinator/runtime probe is unavailable, or any
  selected target is locked.
- The injected editor uses a random basename absent across `.rpy/.rpyc/.rpyc.bak`; a durable
  ownership manifest and content checks govern cleanup. Uncertain or externally modified artifacts
  are left untouched and reported, never blindly deleted.

## Save semantics

- **Nothing is written until Save.** Dragging only mutates runtime state.
- A V1 edit session may contain multiple target intents but **all must resolve to one source file**.
  This makes publication one atomic file replacement; a second file is rejected up front as
  `MULTI_FILE_UNSUPPORTED`.
- All intents are re-analysed against the same baseline immediately before publication. Any stale,
  ambiguous or unsavable target aborts the complete save.
- On an external modification: write nothing, keep the in-memory work, and offer reload or retry.
- After publication, the coordinator marks the transaction committed only after a new script
  generation, a successful draw barrier, stable-key rebinding and independent `focus_list`
  measurement of every successor within one logical pixel. Failure triggers conditional rollback.
- A successful Save establishes the new baseline and clears Undo/Redo; history does not cross it.

## Known operational hazards

Both were hit during the spikes and are handled; re-introducing either will look like a capability
failure when it is not.

1. **A show lost to the async reload.** `reload_script()` completes asynchronously; a `show_screen`
   landing mid-teardown is silently dropped and the handler still reports success.
2. **The bridge disappears mid-reload.** While init blocks re-execute, the client raises
   (`bridge response was empty`) rather than returning a reply.
3. **A headless game does not redraw on its own.** A focus list can describe a stale frame.

The save path therefore follows one explicit state machine:
`reload_requested → bridge_reconnected → fresh_frame → re_show_observed → all_targets_attested`.
Transient transport failures and the idempotent re-show are retried to a deadline. A screenshot/draw
barrier immediately precedes each focus measurement. Generation, pending transaction and callback
registration live in a reload-surviving module, not `renpy.store`. Exhaustion never certifies success;
it starts conditional rollback.

## Verification standard

The spike brief's rule carries into V1: **never report a pass that was inferred rather than observed.**
A value that was guessed or defaulted makes the result `inconclusive`, not `pass`.

Verdicts are tri-state, and `inconclusive` outranks `blocked`: an unmeasured step cannot certify a
capability boundary.
