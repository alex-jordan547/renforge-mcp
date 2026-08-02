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

- Non-focusable elements (plain `text`, `add`, decorative `frame`) cannot be **selected in V1** — they
  are absent from Ren'Py's `focus_list`. Issue **#43** measured `quad ∩ observed paint mask` as a
  viable Stage-3 selection mechanism (`hit_test_workaround: pass`); it is **not** part of V1 editable
  scope until a product selection UX is proven.
- There is no general visual coverage oracle. V1 selection remains focus_list + AABB-level geometry
  (Spike A: `coverage_oracle: blocked`).
- Per-pixel alpha hit-testing is out of scope.
- No resize, no rotation, no style editing. V1 moves things.

## The four gates

An element is editable in V1 **only if all four hold**. Each gate exists because a proven mechanism
requires it — none is arbitrary.

| Gate | Requirement | Why (mechanism) |
|---|---|---|
| 1 | Displayable is **focusable** | Selection and every save-bearing measurement read `renpy.display.focus.focus_list` |
| 2 | Source statement carries one **literal `id`** matching the runtime widget id | Runtime preview uses `_widget_properties`, keyed by the authored widget id; synthetic observation IDs do not qualify |
| 3 | A proven adapter statement (`textbutton` single-line or multi-line block, `imagebutton`, single-line `bar`/`vbar`, slider-styled `bar`) or an explicit-block `button`, with one literal integer **`xpos` and `ypos`** | The token-aware patcher replaces only the coordinate spans; block forms preserve every non-position byte |
| 4 | Exactly one runtime instance with a fully classified, static ancestry — at most one **`viewport`** (issue #44), at most pure **`Transform(crop=)` / `Crop()` sugar** (issue #45), and outside crop+rotate/zoom, `clipping True`, loop and repeated `use` cases | A repeated statement stores its position once for all N instances, so no write can move one of them alone (issue #42) |

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
| `textbutton` | Spike C `pass` | Spike D + multi-line block form (issue #37) | **Shipped in V1**; multi-line block live via `RENFORGE_MULTILINE_TEXTBUTTON_LIVE=1` |
| `imagebutton` | Spike C `pass` (focusable) | dedicated analyzer + coordinator path + seven-step live proof | **Implemented** (live proof green via `RENFORGE_IMAGEBUTTON_LIVE=1`) |
| `button` | focusable by construction | Seven-step live proof (explicit block) | **Shipped in V1** |
| `bar` | focusable when adjustable (measured on Ren'Py 8.5.3) | dedicated single-line analyzer + seven-step live proof | **Implemented** (live proof green via `RENFORGE_BAR_LIVE=1`) |
| `vbar` | focusable when adjustable (measured on Ren'Py 8.5.3) | dedicated `VbarStatement` analyzer + patcher + seven-step live proof | **Implemented** (live proof green via `RENFORGE_VBAR_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_vbar_live.py`) |
| `slider` (authored as `bar` + `style "slider"`) | focusable when adjustable (measured on Ren'Py 8.5.3) | dedicated `SliderStatement` path + seven-step live proof | **Implemented** (live proof green via `RENFORGE_SLIDER_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_slider_live.py`) |
| `text`, `add`, `frame` | **not selectable** | Spike B proved write on a literal `text`, but by key, not by click | Out of V1 |

`imagebutton` has a dedicated single-line adapter (issue #32) rather than a textbutton allowlist widen.
Host unit/coordinator coverage lands in default CI; the seven-step live proof is opt-in in CI but was
executed green locally against Ren'Py 8.5.3 (`RENFORGE_IMAGEBUTTON_LIVE=1 pytest tests/test_editor_imagebutton_live.py`).

### Multi-line textbutton blocks (issue #37)

In addition to the single-line form, V1 supports one multi-line shape for the already-proven
`textbutton` adapter — position tokens live in the child block, not on the header:

```renpy
textbutton "MOVE ME":
    id "ml_tb_target"
    xpos 200
    ypos 180
    action NullAction()
```

Requirements: header ends with `:`; header carries no `id`/`xpos`/`ypos`; exactly one literal string
`id` and one literal integer `xpos`/`ypos` each among direct children at the block indent; patch
replaces only those two integer tokens (absolute spans in the full file); every other block byte is
preserved. Computed coordinates, container ancestry, repeated `use` instances, and unproven ancestry
remain locked with the measured codes (`XPOS_LITERAL_REQUIRED`, `CONTAINER_POSITION_UNSUPPORTED`,
`REPEATED_USE_UNSUPPORTED`, `UNKNOWN_ANCESTRY_TYPE`, …).

Live proof: `RENFORGE_MULTILINE_TEXTBUTTON_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_multiline_textbutton_live.py`
against Ren'Py **8.5.3**.

### Literal `pos (x, y)` on textbutton (issue #38)

Single-line textbuttons may author position as a pure integer pair:

```renpy
textbutton "MOVE ME" id "pos_target" pos (200, 180) action NullAction()
```

The analyzer records `position_mode == "pos"` and patches only the two integer tokens inside
the tuple, preserving `pos (...)` and never converting the statement to `xpos`/`ypos`. Non-literal
pairs (`pos (base_x, 10)`), mixed forms (`pos` + `xpos`/`ypos`), and duplicate `pos` keywords are
locked with exact codes (`POS_LITERAL_REQUIRED`, `POSITION_FORM_MIXED`, `POS_DUPLICATE`).

Live proof: `RENFORGE_POS_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_pos_live.py`
against Ren'Py **8.5.3**.

### Literal `align (x, y)` on textbutton (issue #39)

```renpy
textbutton "MOVE ME" id "align_target" align (0.2, 0.25) action NullAction()
```

Host analysis records `position_mode == "align"` and stores authored fractions plus the measured
focus baseline and widget size from the **independent** `focus_list` observation. Parent size is
unlocked only when that geometry matches the proven full-screen **1280×720** model. Preview uses
absolute xpos/ypos (with align/anchor/offset neutralized) so focus_list tracks the pixel delta;
write-back applies `authored + Δpixel / (parent − widget)` because Ren'Py `align` also sets the
anchor, and preserves the `align` form. Attestation requires agreement within **1 px**. Zero
placement extent on an axis is locked; concurrent `offset` / axis-split properties are locked.

Live: `RENFORGE_ALIGN_LIVE=1 … tests/test_editor_align_live.py` on Ren'Py **8.5.3**.

### Literal `anchor` with xpos/ypos (issue #40)

```renpy
textbutton "MOVE ME" id "anchor_target" xpos 400 ypos 300 anchor (0.5, 0.5) action NullAction()
```

Pure `anchor (fx, fy)` is required when present (`ANCHOR_LITERAL_REQUIRED` otherwise). Moves patch
only `xpos`/`ypos`; **anchor bytes are preserved**. Live proof measures focus_list independently of
the anchor point.

Live: `RENFORGE_ANCHOR_LIVE=1 … tests/test_editor_anchor_live.py` on Ren'Py **8.5.3**.

### Literal `offset (x, y)` on textbutton (issue #41)

```renpy
textbutton "MOVE ME" id "offset_target" offset (200, 180) action NullAction()
```

Host analysis records `position_mode == "offset"` and stores authored integer offsets plus the
measured focus baseline. Preview uses absolute xpos/ypos with offset/align/anchor neutralized;
write-back applies `authored + Δpixel` (offset is additive) and preserves the `offset` form.
Non-literal pairs, axis-split `xoffset`/`yoffset`, and mixed placement forms are locked
(`OFFSET_LITERAL_REQUIRED`, `POSITION_FORM_MIXED`, `OFFSET_DUPLICATE`). Attestation requires
agreement within **1 px**.

Live: `RENFORGE_OFFSET_LIVE=1 … tests/test_editor_offset_live.py` on Ren'Py **8.5.3**.

The `button` adapter is intentionally limited to `button id "..." xpos N ypos N:` headers. Computed
coordinates, direct child `xpos`/`ypos`, layout-container ancestry, and ambiguous or unproven runtime
instances remain selectable but locked with an exact reason.

The `bar` and `vbar` adapters each accept exactly one **physical source line** with one literal string
`id` and literal integer `xpos`/`ypos` (the #34 form, with #35 adding the dedicated vbar adapter):

```renpy
bar value VariableValue("some_var", range=100) id "bar_target" xpos 200 ypos 180 xsize 240 ysize 24
vbar value VariableValue("some_var", range=100) id "vbar_target" xpos 200 ypos 180 xsize 24 ysize 240
```

Host dispatch uses the exact source keyword, not runtime `node_type`: both `bar` and `vbar` expose the
runtime displayable class `Bar`, but `vbar` is analyzed and patched through dedicated
`VbarStatement`/analyzer/patcher paths. The vbar proof uses fresh `focus_list` observations for every
visual position and preserves the underlying vbar value across preview and reload.

Live proof: `RENFORGE_VBAR_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_vbar_live.py`
against Ren'Py **8.5.3**. Vbar inherits the exact #34 lock boundaries: computed/non-literal `xpos`
and `ypos` (`XPOS_LITERAL_REQUIRED` / `YPOS_LITERAL_REQUIRED`), style-driven position
(`BAR_STYLE_POSITION_UNSUPPORTED`), missing direct position (`BAR_POSITION_NOT_DIRECTLY_AUTHORED`),
container ancestry (`CONTAINER_POSITION_UNSUPPORTED`), repeated instances (`REPEATED_USE_UNSUPPORTED`),
unknown ancestry (`UNKNOWN_ANCESTRY_TYPE`), and multi-line statements
(`MULTILINE_STATEMENT_REJECTED`). These targets remain selectable but locked with their measured reason.

### Slider adapter (issue #36)

**Measured on Ren'Py 8.5.3:** screen language has no `slider` keyword (parse error:
`'slider' is not a keyword argument or valid child of the screen/fixed statement`). Games author
sliders as single-line `bar` statements that select the `slider` style. The dedicated slider adapter
therefore specializes `bar` when the line carries exactly one literal `style "slider"`, returning
`statement_kind == "slider"` while the source keyword remains `bar`:

```renpy
bar value VariableValue("some_var", range=100) style "slider" id "slider_target" xpos 200 ypos 180 xsize 240 ysize 24
```

Live proof: `RENFORGE_SLIDER_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_slider_live.py`
against Ren'Py **8.5.3**. Slider reuses the bar-family lock boundaries for computed/non-literal
coordinates, style-only/missing position (`BAR_STYLE_POSITION_UNSUPPORTED` when `style "slider"` is
present without xpos/ypos), container ancestry, duplicate instances, and unproven ancestry.
`vslider`, style-driven position without inline literals, container-driven position, computed
coordinates, multi-line blocks, and unproven ancestry remain pending or locked; no other forms are
claimed.

### Repeated loop and `use` instances (issue #42)

Instance identity comes from the SL2 cache path, not from `screen.widgets`. Ren'Py keys each `SLFor`
iteration by the author's own loop index and gives every `use` call site its own cache dict, so
walking the cache maps each focus entry to exactly one instance without inventing an id. The widgets
map cannot: it keeps a single displayable per widget id, so siblings of a repeated statement used to
fail id resolution and lock as `SYNTHETIC_WIDGET_ID` by accident.

Selection is therefore proven, and the write stays locked — **not** pending implementation. A repeated
statement holds its authored position in one place shared by all N instances, so a write moves all of
them; moving one alone would require synthesising a per-instance expression, which the editor is not
entitled to do. The runtime key now carries `kind`, `instance_count` and `instance_key`, and the lock
is `LOOP_INSTANCE_UNSUPPORTED` or `REPEATED_USE_UNSUPPORTED`. A repetition lock outranks a source-form
lock, so the reason does not depend on gate ordering.

`instance_key` holds SL2 AST serials, which are reassigned on script reload; it is stable within one
script generation and is omitted for unique statements, so it never enters their rebinding equality.

Live proof: `RENFORGE_LOOP_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_loop_live.py`
against Ren'Py **8.5.3**.

### Targets inside a `viewport` (issue #44)

A single `viewport` ancestor is editable. The engine reports `focus_list` rects in screen space with
the scroll already applied — sampled at scroll 0/40/90/0, the target's `y` falls by exactly the
distance scrolled — so the host's `runtime_rect + Δ` attestation carries the same scroll term on both
sides and needs no viewport-specific arithmetic.

Locked, each with its own reason: nested viewports (`NESTED_VIEWPORT_UNSUPPORTED`), `scrollbars`
(which wraps the viewport in a `Side`, so `UNKNOWN_ANCESTRY_TYPE`), and layout containers inside a
viewport (`CONTAINER_POSITION_UNSUPPORTED`, unchanged).

Committing while the viewport is scrolled fails attestation: Ren'Py drops the scroll on reload, so
the post-reload geometry cannot match a position derived at the old scroll. The editor reports
`TARGET_POSITION_MISMATCH` rather than accepting what it cannot reproduce, and restores the file
before reporting the failure.

Note for anyone touching this path: `preview_position` is screen space and the authored value is
child space. They coincide only when no ancestor offsets the child, which is why earlier adapters
could compare them directly.

Live proof: `RENFORGE_VIEWPORT_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_viewport_live.py`
against Ren'Py **8.5.3**.

### Targets under pure `Transform(crop=)` / `Crop()` (issue #45)

**Identity:** on Ren'Py 8.5.3, `Crop(rect, child)` is a constructor that returns
`Transform(child, crop=rect)` (`renpy/display/layout.py`). Live ancestry reports `type == "Transform"`
and `crop_state == "transform_crop"` — never a class named `Crop`.

**Unlocked:** exactly one pure-crop `Transform` in the ancestry (crop set; rotate/zoom at defaults),
with a plain `fixed` child and a fully-visible single-line `textbutton` (literal id + xpos/ypos).

Measured on Ren'Py **8.5.3** via `RENFORGE_CROP_LIVE=1`: pure crop already **clips focus rects** to the
crop window. A partially clipped sibling reports a shorter focus height than a natural outside control
(e.g. focus height 15 vs natural ~35) while remaining fully inside the crop AABB; a fully clipped
control is **absent** from `list_ui_elements`. Focus therefore tracks visible geometry under pure crop
— the same kind of engine-truth finding as viewport scroll (#44). The seven-step write chain is green
with delta-only source comparison (screen-space `preview_position` ≠ child-space authored values).

**Still locked, each with its own reason:**

| Shape | Reason |
|---|---|
| Partially crop-clipped child | `TRANSFORM_CROP_PARTIAL_UNSUPPORTED` |
| Crop + rotate or crop + zoom | `TRANSFORM_CROP_COMPOSITE_UNSUPPORTED` (issue #46) |
| Layout container inside the crop | `CONTAINER_POSITION_UNSUPPORTED`, unchanged |
| Expression ypos inside the crop | `YPOS_LITERAL_REQUIRED`, unchanged |
| `fixed` + `clipping True` | bridge `CLIPPED_ANCESTRY_UNSUPPORTED` / unproven |

Live proof: `RENFORGE_CROP_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_crop_live.py`
against Ren'Py **8.5.3**.

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
