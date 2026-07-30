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
| 1 | Displayable is **focusable** | Selection reads `renpy.display.focus.focus_list`; non-focusables are absent from it |
| 2 | Statement carries an **`id`** | Runtime preview uses `_widget_properties`, which is keyed by widget id |
| 3 | Single-line statement with **literal `xpos` and `ypos`** | The patcher rewrites literal integers on one source line |
| 4 | Not inside a **`viewport`** / `Crop` / `Transform(crop=)` | Only `fixed` + `clipping True` was exercised; viewport clipping is untested |

**A failing gate is a first-class UI state, never a silent no-op.** The overlay must name which gate
failed and why, e.g. *"`text` is not focusable — cannot be selected in V1"* or
*"`xpos` is an expression, not a literal — cannot be written back"*.

Elements that fail gate 2, 3 or 4 but pass gate 1 remain **selectable and measurable** (you can inspect
their box and distances) but are **locked for editing**. Elements failing gate 1 are invisible to V1.

## Adapter allowlist

V1 ships an explicit allowlist, extended one adapter at a time, each backed by an end-to-end proof.

| Adapter | Selection | Write chain | Status |
|---|---|---|---|
| `textbutton` | Spike C `pass` | Spike D `pass` | **Shipped in V1** |
| `imagebutton` | Spike C `pass` (focusable) | **not exercised** | Blocked until proven |
| `button` | focusable by construction | not exercised | Blocked until proven |
| `text`, `add`, `frame` | **not selectable** | Spike B proved write on a literal `text`, but by key, not by click | Out of V1 |

`imagebutton` shares the `Button` mechanics proven in Spikes C and D, so it is the cheapest next
adapter — but it ships only after its own end-to-end proof, not by analogy.

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
- The existing game-side bridge request socket remains host-to-game; it is not reused to publish source.
- A per-launch `EditorCoordinator` on the RenForge host listens on loopback with a random token.
- The overlay may call `analyze_target`, `commit`, `commit_status`, and `reload_handshake` on that
  dedicated connection. It never receives a filesystem write primitive.
- Save stays disabled while analysis is pending, the coordinator is unavailable, or any selected
  target is locked.

## Save semantics

- **Nothing is written until Save.** Dragging only mutates runtime state.
- Save is **all-or-nothing** across the session's changes. A single unsavable element aborts the whole
  write rather than leaving the file half-applied.
- Unsavable elements can never enter the change set in the first place (gates are checked at selection
  time, not at save time), so an abort means a real conflict — not a surprise.
- On external modification during the session: refuse, keep the in-memory work, and offer reload or retry.
- After a successful write: `reload_script()`, re-show the screen, rebind by stable key, and the new
  state becomes the baseline for Undo.

## Known operational hazards

Both were hit during the spikes and are handled; re-introducing either will look like a capability
failure when it is not.

1. **A show lost to the async reload.** `reload_script()` completes asynchronously; a `show_screen`
   landing mid-teardown is silently dropped and the handler still reports success. Post-reload waits
   must re-issue the idempotent show, bounded, and still surface the original error on exhaustion.
2. **The bridge disappears mid-reload.** While init blocks re-execute, the client *raises*
   (`bridge response was empty`) rather than returning a reply. Every poll that can straddle a reload
   must treat a transport error as transient and retry to its deadline — while never accepting an
   observation that was not preceded by a successful frame.

A headless game does not redraw on its own: force a real frame (screenshot goes through the draw path)
before any measurement, or you will read a stale focus list.

## Verification standard

The spike brief's rule carries into V1: **never report a pass that was inferred rather than observed.**
A value that was guessed or defaulted makes the result `inconclusive`, not `pass`.

Verdicts are tri-state, and `inconclusive` outranks `blocked`: an unmeasured step cannot certify a
capability boundary.
