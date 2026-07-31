# RenForge Visual Editor — Road to V-full

Status: **roadmap, not a commitment.** Every item is gated on evidence that does not exist yet.
V1's scope is in `2026-07-30-renforge-visual-editor-v1-scope.md`; this document is what stands
between V1 and "move any element".

The ordering is by **evidence risk**, not by user appeal. The cheap, high-confidence work comes
first precisely so the expensive unknown is attacked with a working product already in hand.

---

## Stage 1 — Widen the adapter allowlist (low risk)

V1 ships with one adapter. Each new one is a day of work and an end-to-end proof, not a day of work
and an analogy.

| Adapter | Expected difficulty | Why |
|---|---|---|
| `imagebutton` | Low | Same `Button` mechanics proven by Spikes C and D. Focusable, takes an `id`, literal position. **Implemented** with dedicated analyzer + opt-in seven-step live harness (`RENFORGE_IMAGEBUTTON_LIVE=1`); see issue #32 / `2026-08-01-imagebutton-adapter-design.md`. |
| `button` (explicit block form) | Low | Focusable by construction; the child block may complicate the source-line contract |
| `bar` / `vbar` / `slider` | Medium | Focusable, but positioning often comes from style or a container |

**Exit criterion per adapter:** its own 7-step live proof — resolve → preview → patch → reload →
pixel agreement → rebinding → byte-identical undo — with every position measured from `focus_list`.

**Do not** widen the allowlist by pattern-matching against `textbutton`. The whole point of the gate
system is that "it looks similar" is not evidence.

---

## Stage 2 — Statement forms beyond the single literal line (medium risk)

V1's patcher accepts one shape: a single-line statement carrying literal integer `xpos` and `ypos`.
Real screens are messier.

| Form | Problem | Possible approach |
|---|---|---|
| Multi-line statement with a block | Position keywords may sit on any line of the block | Parse the statement span, not one line |
| `pos (x, y)` / `align` / `anchor` / `offset` | Different property, same intent | Normalise to a position model; write back in the form the author used |
| Position from a **variable or expression** | Cannot be rewritten without changing semantics | **Stays locked.** Offer to show the computed value, never to overwrite the expression |
| Element inside `hbox` / `vbox` / `grid` | Position is computed by the layout, not authored | **Stays locked** for direct move. A layout-aware edit is a different feature |
| Multiple instances from one loop / `use` | One source line, N runtime widgets | Needs a disambiguation UI; the stable key already carries `ordinal` |

**Design rule inherited from the agreed spec:** the editor preserves the form the author used. An
element written with `xpos` gets `xpos` back; one written with `offset` gets `offset`. The editor is
not entitled to normalise someone's source.

---

## Stage 3 — Non-focusable selection (high risk, the real unknown)

This is the gate between "position widgets" and "move any element".

Plain `text`, `add`, and decorative `frame` never enter `focus_list`, so the engine cannot tell us
where they are on screen. Spike A recorded the state precisely:

```text
coverage_oracle:      blocked     (no general visual coverage; AABB-level only)
hit_test_workaround:  untested    (quad ∩ sentinel mask — viable in principle, never exercised)
```

**The candidate mechanism** — render the displayable to an offscreen surface with a sentinel colour,
intersect the transformed quad with that mask, and use the intersection as the hit region. Never
exercised. Unknowns worth naming before anyone starts:

- Cost per frame with many candidates, and whether it can be restricted to the hovered region
- Whether the transform chain (rotation, zoom, nested `Transform`) can be reproduced faithfully
- Interaction with `viewport` scroll offsets
- Whether a sentinel render is even reachable for every displayable type

**This deserves its own spike with falsifiability rules written before any code**, and an explicit
"blocked" outcome is a perfectly good result. Do not start it by building a UI.

**Note on Spike B:** it proved the write chain on a literal `text` — but that target was designated by
key, not selected by clicking. Spike B and Spike C do not compose into "text elements work". The write
side for `text` is proven; the selection side is not.

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
