# Rotation evidence spike — result (issue #48)

**Date:** 2026-08-02
**SDK:** Ren'Py 8.5.3
**Verdict:** **BLOCKED** — `aabb_false_positive`

## Scope

This spike evaluates moving an already-rotated, focusable target. It does not add rotation controls or modify production editor logic. The frozen criteria are in `2026-08-02-rotation-criteria.md`.

## Independent evidence

### Runtime Transform seam

The injected opt-in resource discovered the target's runtime Transform, mapped its child quad through Ren'Py's `forward` seam, then translated that quad through the measured center of the fixture's centered focus parent. No authored-angle trigonometry was used.

- seam: `forward`
- runtime screen quad: `[[256.7042245864868, 252.96609663963318], [333.9782876968384, 232.2605721950531], [343.2957773208618, 267.033903837204], [266.021710395813, 287.73942732810974]]`
- forward/reverse round-trip error: `8.878860171535052e-07`

This plane proves a non-degenerate runtime rotation seam in screen coordinates. It is recorded separately from screen-space paint classification and editor selection evidence.

### Candidate-isolated paint mask

For the rotated target's focus AABB `[220, 220, 160, 80]`:

- painted center: `[300, 260]`
- painted edge derived from the runtime screen quad: `[304, 275]`
- unpainted AABB corner: `[220, 220]`

### Real editor selection probe

The editor was asked to select at the unpainted corner `[220, 220]`. It selected `rotation_target` despite the candidate-isolated paint mask reporting that point as unpainted.

This is a deterministic AABB false positive, so transformed selection safety is not proved and production rotation controls must remain disabled.

## Write, reload, rebind, and undo evidence

The supported single-line `button` source form remained editable while containing a rotated child:

- preview move: `[220, 220] -> [221, 220]`
- product undo: `[221, 220] -> [220, 220]`
- product redo: `[220, 220] -> [221, 220]`
- save status: `Reload committed`
- script generation: `0 -> 1`
- post-reload position: `[221, 220]`
- rebinding lock reason: `null`
- source bytes matched an independently constructed xpos/ypos patch exactly

A separate temporary rotate-literal round trip changed `rotate=15` to `rotate=16`, preserved every byte outside that literal, and restored the temporary fixture. That direct byte restoration is documentation evidence only, not product undo. The original fixture baseline was restored after the scenario.

## Reproducibility

The final scenario produced the same `BLOCKED / aabb_false_positive` classification on consecutive Ren'Py 8.5.3 runs.

```bash
RENFORGE_ROTATION_LIVE=1 PYTHONPATH=src python -m pytest -q tests/test_editor_rotation_live.py -vv
```

Result: `1 passed`.

Focused editor regression set:

```bash
PYTHONPATH=src python -m pytest -q \
  tests/test_editor_source.py \
  tests/test_editor_runtime.py \
  tests/test_editor_coordinator.py
```

Result: `143 passed`.

Full suite:

```bash
PYTHONPATH=src python -m pytest -q
```

Result: `632 passed, 43 skipped`.

## Decision

**Do not unlock rotation editing.** The next prerequisite is a production selection path that gates rotated targets with exact transformed hit evidence rather than the focus AABB alone.
