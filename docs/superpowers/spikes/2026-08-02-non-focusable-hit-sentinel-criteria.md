# Spike — Non-focusable selection via quad ∩ sentinel mask (issue #43)

**Status:** criteria locked before measurement.  
**SDK:** Ren'Py **8.5.3** only.  
**Surface:** in-game bridge, opt-in resource injection (no production editor UI).

## Question

Can RenForge select plain `text`, `add`, and decorative `frame` (absent from
`focus_list`) by a falsifiable hit region of the form:

```text
point ∈ transformed_painted_quad  AND  point ∈ sentinel_mask
```

without self-validating injected/requested geometry?

## Mechanisms under test

| Id | Mechanism | Notes |
|---|---|---|
| `GT` | Independent ground truth | Unique paint colours on a dark stage; full-window screenshot; colour→target map at probe points |
| `AABB` | Axis-aligned layout box only | Placement + render width/height; known weak for rotation |
| `QUAD` | Transformed painted quad only | Four corners after placement/transform; no paint mask |
| `MASK` | Isolation sentinel mask | Show only the candidate; screenshot; non-background pixels = mask (observed paint, not Solid AABB fill) |
| `COMP` | `QUAD ∩ MASK` | Candidate Stage-3 selection mechanism |

## Probe matrix (must all run)

1. **Axis-aligned solid `add`** — interior, exterior, near edge  
2. **Plain `text`** — glyph interior (paint), exterior of layout box  
3. **Decorative `frame`** — interior paint  
4. **Rotated solid `add` (25°)** — interior of rotated body, exterior AABB corner that is outside the rotated quad  
5. **Clipped overflow** — child larger than clipping parent; probe in visible region and in clipped-away region  
6. **Viewport** — non-focusable inside a scrolled viewport; probe at on-screen paint vs off-scroll region  
7. **Focusable control** — a `textbutton` present for regression; must still be resolvable via `focus_list` (this spike does not replace focus for focusables)

## Cost measurement

For each candidate that builds a mask, record wall-clock ms for:

- isolation show/hide + screenshot  
- mask construction from pixels  
- per-probe test cost for 20 probes on the rotated target  

Report totals; no hard SLA — document whether cost scales with full-screen isolation only (expected) or requires per-pixel work proportional to candidates × screen.

## Pass / blocked / inconclusive (written before code)

### Pass

All of the following hold on a single Ren'Py 8.5.3 live run, and a second consecutive run produces the same capability verdict string:

1. `GT` classifies every probe point as one of `{target_id, background}` with no ambiguous colour collisions.  
2. For the rotated solid, `AABB` **disagrees** with `GT` on at least one exterior AABB corner (proves AABB alone is insufficient).  
3. `COMP` agrees with `GT` on **every** probe in the matrix (including rotation exterior corner, clipped-away, viewport off-scroll), within the independent screenshot sample (exact pixel).  
4. Sentinel isolation is reachable for `add`, `text`, and `frame` (mask non-empty where `GT` shows paint).  
5. Focusable regression: `focus_list` still names the fixture `textbutton` at its centre.

### Blocked

Any of:

- Sentinel isolation unreachable for a required type (`add` / `text` / `frame`) with empty mask while `GT` shows paint.  
- No transform-quad seam: cannot obtain a non-degenerate quad for the rotated case.  
- `COMP` systematically disagrees with `GT` on rotation or clip after two independent runs (mechanism false).  
- Viewport scroll makes `COMP` disagree with `GT` with no recoverable correction from measured scroll offsets.

### Inconclusive

- Consecutive runs disagree on the capability verdict without code change.  
- Ground-truth colour sampling is ambiguous (anti-aliasing / subpixel) on more than 10% of probes after allowing a 1-pixel neighbour search for the expected colour.  
- Cost measurement fails to complete (hang/crash) so no reproducible report exists.

## Non-goals

- Production editor UI, hover chrome, or write-back for non-focusables.  
- Per-pixel alpha correctness (transparent holes may false-positive; design already out of scope).  
- Promoting Stage 3 into V1 scope on a partial pass.

## Artifacts

- Criteria (this file)  
- Opt-in live runner + fixture  
- Machine JSON report under `.renforge/` (local, not required to commit)  
- Human report: `docs/superpowers/spikes/2026-08-02-non-focusable-hit-sentinel-result.md`  
- Roadmap Stage 3 updated only after a measured verdict
