# RenForge Visual Editor — Road to V-full

Status: **roadmap, not a commitment.** Every future item is gated on evidence that does not exist yet.
V1's scope is in `2026-07-30-renforge-visual-editor-v1-scope.md`; this document is what stands
between V1 and "move any element".

The ordering is by **evidence risk**, not by user appeal. The cheap, high-confidence work comes
first precisely so the expensive unknown is attacked with a working product already in hand.

---

## Stage 1 — Widen the adapter allowlist (low risk)

V1's adapter allowlist expands one adapter at a time. Each new one is a day of work and an end-to-end proof,
not a day of work and an analogy.

| Adapter | Expected difficulty | Why |
|---|---|---|
| `imagebutton` | Low | Same `Button` mechanics proven by Spikes C and D. Focusable, takes an `id`, literal position. **Implemented** with dedicated analyzer; seven-step live proof green locally via `RENFORGE_IMAGEBUTTON_LIVE=1` (issue #32 / `2026-08-01-imagebutton-adapter-design.md`). |
| `button` (explicit block form) | Low | Focusable by construction; the child block may complicate the source-line contract |
| `bar` (single-line literal position) | Medium | Focusable when adjustable; often style/container-positioned. **Implemented** for the minimal single-line literal form; seven-step live proof green via `RENFORGE_BAR_LIVE=1` on Ren'Py 8.5.3 (issue #34). Runtime class `Bar` is shared with `vbar` — source keyword decides the adapter. |
| `vbar` (single-line literal position) | Medium | Same runtime `Bar` class as `bar`; dedicated `VbarStatement` analyzer/patcher dispatches from the source keyword. **Implemented** for one physical line with one literal `id` and literal integer `xpos`/`ypos`; seven-step live proof green on Ren'Py 8.5.3 via `RENFORGE_VBAR_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_vbar_live.py` (issue #35). |
| `slider` (as `bar` + `style "slider"`) | Medium | **Implemented** (issue #36). Measured: Ren'Py 8.5.3 has no screen-language `slider` keyword; sliders are authored as `bar` with literal `style "slider"`. Dedicated `SliderStatement` path + seven-step live proof green via `RENFORGE_SLIDER_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_slider_live.py`. |
| `vslider` | Medium | May surface as runtime `Bar` / style `"vslider"`; positioning often style/container-driven. **Pending** — no live proof; not claimed. |

The supported vbar source form is exactly one physical line, for example:

```renpy
vbar value VariableValue("some_var", range=100) id "vbar_target" xpos 200 ypos 180 xsize 24 ysize 240
```

Host dispatch uses the exact source keyword rather than runtime `node_type`: both `bar` and `vbar` expose
the runtime displayable class `Bar`, while vbar uses its dedicated `VbarStatement`, analyzer, and patcher.
The proof uses fresh `focus_list` observations for every visual position and verifies the underlying vbar
value is invariant across preview and reload. Vbar inherits #34's exact measured lock boundaries:
computed/non-literal coordinates (`XPOS_LITERAL_REQUIRED` / `YPOS_LITERAL_REQUIRED`), style-driven
position (`BAR_STYLE_POSITION_UNSUPPORTED`), missing direct position (`BAR_POSITION_NOT_DIRECTLY_AUTHORED`),
container ancestry (`CONTAINER_POSITION_UNSUPPORTED`), repeated instances (`REPEATED_USE_UNSUPPORTED`),
unknown ancestry (`UNKNOWN_ANCESTRY_TYPE`), and multi-line statements (`MULTILINE_STATEMENT_REJECTED`).
These remain selectable but locked; all other vbar forms remain pending.

The supported slider source form is a single physical `bar` line with literal `style "slider"`:

```renpy
bar value VariableValue("some_var", range=100) style "slider" id "slider_target" xpos 200 ypos 180 xsize 240 ysize 24
```

`statement_kind` is `"slider"` for that specialized form; the first source keyword remains `bar`. Live
proof command: `RENFORGE_SLIDER_LIVE=1 uv run --extra test python -m pytest -q tests/test_editor_slider_live.py`.
**Exit criterion per adapter:** its own 7-step live proof — resolve → preview → patch → reload →
pixel agreement → rebinding → byte-identical undo — with every position measured from `focus_list`.

**Do not** widen the allowlist by pattern-matching against `textbutton`. The whole point of the gate
system is that "it looks similar" is not evidence.

---

## Stage 2 — Statement forms beyond the single literal line (medium risk)

The `bar`/`vbar` patchers accept one shape: a single physical source line carrying literal integer `xpos`
and `ypos`.
Real screens are messier.

| Form | Problem | Possible approach |
|---|---|---|
| Multi-line statement with a block | Position keywords may sit on any line of the block | **Partially implemented (issue #37):** multi-line `textbutton` with `id`/`xpos`/`ypos` in the child block only; seven-step live proof green via `RENFORGE_MULTILINE_TEXTBUTTON_LIVE=1`. Other adapters remain single-line or header-only (`button`). |
| `pos (x, y)` / `align` / `anchor` / `offset` | Different property, same intent | **`pos` (#38)**, **`align` (#39)**, **`anchor` (#40)**, **`offset` (#41)** implemented for single-line `textbutton`. Write-back preserves form. Lives: `RENFORGE_POS_LIVE=1`, `RENFORGE_ALIGN_LIVE=1`, `RENFORGE_ANCHOR_LIVE=1`, `RENFORGE_OFFSET_LIVE=1`. |
| Position from a **variable or expression** | Cannot be rewritten without changing semantics | **Stays locked.** Offer to show the computed value, never to overwrite the expression |
| Element inside `hbox` / `vbox` / `grid` | Position is computed by the layout, not authored | **Stays locked** for direct move. A layout-aware edit is a different feature |
| Multiple instances from one loop / `use` | One source line, N runtime widgets | **Selection proven, write blocked (issue #42).** Instances are now identified individually; the source write stays locked. See below. |

**Design rule inherited from the agreed spec:** the editor preserves the form the author used. An
element written with `xpos` gets `xpos` back; one written with `offset` gets `offset`. The editor is
not entitled to normalise someone's source.

### Repetition (issue #42) — selection proven, instance-specific write blocked

Measured live on Ren'Py **8.5.3** via `RENFORGE_LOOP_LIVE=1`.

**Selection is proven.** Ren'Py keys each `SLFor` iteration in its SL2 cache by the author's own loop
index (`slast.py`: `newcaches[index] = ctx.new_cache = {}`) and gives every `use` call site its own
cache dict. Walking that cache maps every focus entry to exactly one instance — 12/12 in the fixture,
no collisions — using authored data rather than a synthetic id. `screen.widgets` cannot do this: it
stores one displayable per widget id, so the last iteration overwrites its siblings, which is why
these instances previously locked as `SYNTHETIC_WIDGET_ID` by accident rather than by design.

**The instance-specific source write is blocked**, and this is a property of the source, not a gap in
the implementation. Every repetition shape keeps the authored position in a single location shared by
all N instances:

| Shape | Measured | Why no write is possible |
|---|---|---|
| Literal position in a loop | 3 instances, all at `(200,160)` | One literal serves N; a write moves all of them |
| Loop-derived position | 3 instances at `x = 200 + i·160` | The position is an expression, locked by the expression gate |
| Layout-positioned in a loop | 3 instances in a `vbox` | Locked by the container gate |
| Repeated `use` | 2 call sites, both resolving to line 8 | One authored line backs both sites |

Moving one instance alone would mean synthesising a per-instance expression — which the design rule
above forbids. The gate therefore stays locked, but the lock is now *precise*: `LOOP_INSTANCE_UNSUPPORTED`
and `REPEATED_USE_UNSUPPORTED` replace the incidental `SYNTHETIC_WIDGET_ID`, and the runtime key
carries `kind`, `instance_count` and `instance_key` so a failed-gate UI can explain which instance of
how many is selected (issue #52).

**Identity outranks form.** When a statement is both repeated and expression-positioned, the analysis
reports the repetition lock. Otherwise the reported reason would depend on which gate ran last.

**Known limit:** `instance_key` contains SL2 AST serials, which Ren'Py reassigns on every script
reload. It is stable within one script generation — enough to rebind during a session — and is
deliberately absent from unique statements so it never enters their rebinding equality.

---

## Stage 3 — Non-focusable selection (high risk, the real unknown)

This is the gate between "position widgets" and "move any element".

Plain `text`, `add`, and decorative `frame` never enter `focus_list`, so the engine cannot tell us
where they are on screen. Spike A recorded the state precisely:

```text
coverage_oracle:      blocked     (no general visual coverage; AABB-level only)
hit_test_workaround:  pass        (issue #43 — quad ∩ observed colour-mask; see spike result)
```

**Issue #43 (2026-08-02)** exercised `point ∈ transformed_quad ∧ point ∈ observed_paint_mask` on
Ren'Py **8.5.3** with independent screenshot ground truth. Criteria were locked before measurement
(`docs/superpowers/spikes/2026-08-02-non-focusable-hit-sentinel-criteria.md`). Result:
`docs/superpowers/spikes/2026-08-02-non-focusable-hit-sentinel-result.md`.

Measured:

- AABB alone **false-positives** on a 25° rotated solid corner; COMP does not.
- COMP agreement **1.0** on the critical solid/clip/viewport probe matrix; non-focusables stay out of `focus_list`.
- Cost is screenshot-bound (tens of ms), not a multi-second isolation loop, on the fixture.

Still not V1: no production selection UI, no write adapters for non-focusables, per-pixel alpha still
out of scope, focusable `focus_list` hover regression not fully proven in the harness.

**Note on Spike B:** it proved the write chain on a literal `text` — but that target was designated by
key, not selected by clicking. With #43 the selection *mechanism* for non-focusables is no longer
untested; product selection UX remains Stage 3 work.

---

## Stage 4 — Clipping containers beyond `fixed` (medium risk)

Only `fixed` + `clipping True` was exercised. Untested and common:

- `viewport` — the most common in real UIs, and it adds scroll offsets
- `Crop`
- `Transform(crop=)`

Until proven, elements inside these are locked by gate 4. Note that focus rects may already be
correct inside a viewport, since the engine computes them — this may be cheap to unlock. It needs
measuring, not assuming.

---

## Stage 5 — Beyond moving (scope expansion, low technical risk)

Only once the above is settled:

- **Resize** — needs a size model per adapter (`xsize`/`ysize`/`xysize`/implicit)
- **Rotation** — `Transform(rotate=)`; interacts badly with AABB selection
- **Z-order / reparenting** — changes the source tree structure, not just a literal
- **Style editing** — colours, padding, fonts; a different write contract entirely

**Animated elements** (`at Transform(...)`) are untested with the `_widget_properties` seam. Spike A
found the older override approach non-deterministic on animated variants (3 of 4 runs blocked). The
new seam recreates the widget, which may interact differently — measure before claiming.

---

## Cross-cutting: the UI layer

**None of the four spikes touched the UI.** They answered the engine questions, which were the real
unknowns. The overlay — input capture, drag, guides, snap, undo/redo, Save — is conventional work at
known risk, but it is not small, and it is not yet designed against the gate system.

Specifically unsolved: how the overlay communicates a **failed gate** so it reads as a designed state
rather than a bug. A locked element with a clear reason is a feature; a click that does nothing is a
defect report.

---

## Standing rules

These carry from the spikes into every stage above.

1. **Never report a pass that was inferred rather than observed.** A guessed or defaulted value makes
   a result `inconclusive`, not `pass`.
2. **Verdicts are tri-state.** `inconclusive` (unmeasured) outranks `blocked` (proven limit): an
   unmeasured step cannot certify a capability boundary.
3. **No self-validating evidence.** Do not measure the thing you just injected, do not assign a
   requested value into an observed field, and do not compare the two identities that a preview
   already changed. Four separate spike bugs all reduced to this one root cause.
4. **A negative result is a success.** "This is not reachable, here is what was tried and what Ren'Py
   returned" is a deliverable, not a failure.
