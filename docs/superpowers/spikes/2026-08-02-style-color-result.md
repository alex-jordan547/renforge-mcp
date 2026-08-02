# Issue #50 — style colour source-write result

## Verdict

**`PASS`**

RenForge now proves the frozen `text` + `color` pair through the production in-game bridge on Ren'Py 8.5.3. The product path covers selection of a non-focusable plain text displayable, source-safe analysis, preview without a source write, transactional commit, reload, independent painted-pixel attestation, stable-id rebinding, refused-attestation rollback, and product undo.

This result remains deliberately narrow. It does not enable a generic style panel, additional style properties, or additional adapters.

## Frozen pair

| Field | Value |
| --- | --- |
| Adapter | `text` |
| Property | `color` |
| Unlocked form | single-line pure hex string literal (`#rgb` / `#rrggbb` / `#rrggbbaa`) |
| Style mode | `literal_hex` |
| Runtime measurement | `scene_tree_text` |

## Source contract

- `analyze_text_color_style` / `apply_text_color_patch` in `renforge.editor.source`
- Dedicated colour-token spans — never coordinate-token semantics
- Exactly one literal `id` matching the runtime widget id
- Only the colour token is rewritten; unrelated bytes, quote form, and hex family are preserved
- Stale source is rejected before publication
- Inherited, expression-based, duplicated, multiline, malformed, non-text, ambiguous, or multi-instance targets remain locked

Source/coordinator suite:

```text
PYTHONPATH=src python -m pytest -q tests/test_editor_style_color_source.py tests/test_editor_coordinator.py
77 passed in 12.16s
```

## Production bridge path

The in-game bridge now provides the following bounded path:

1. Discover an identified plain `Text` displayable independently of `focus_list`.
2. Resolve its source location and reject unproven ownership, ancestry, or instance identity.
3. Preview the requested colour through `_widget_properties` without touching source bytes.
4. Submit a dedicated colour intent to the coordinator.
5. Shadow-lint and atomically publish the one-file transaction.
6. Reload Ren'Py and rebind the same source target by stable id.
7. Attest the runtime colour and independently sample the painted pixels.
8. Roll source bytes back when attestation is deliberately refused.
9. Undo a committed colour edit through a second product transaction and re-attest the restored colour.

## Live Ren'Py 8.5.3 evidence

Opt-in command:

```text
RENFORGE_STYLE_COLOR_LIVE=1 PYTHONPATH=src python -m pytest -q tests/test_editor_style_color_live.py
1 passed in 28.31s
```

Fixture: large red `text "STYLE" color "#e22b2b"` changed to blue `#2457d6`, with inherited and expression-based lock controls.

| Plane | Observed evidence |
| --- | --- |
| Product selection | Clicking the painted glyph selects the non-focusable plain text target and unlocks only `style_color` capabilities. |
| Ownership locks | Direct literal unlocks; inherited and expression values return their frozen lock codes. |
| Preview | Painted pixels become blue while fixture source remains byte-identical. |
| Preview reset | The bridge Reset control restores red painted pixels while source remains byte-identical, then the requested preview can be applied again. |
| Source write | Independent expected-byte construction matches; bytes outside the colour token remain identical. |
| Refused attestation | A deliberately refused runtime attestation rolls the published bytes back to the baseline. |
| Commit/reload | Shadow validation, atomic publication, script-generation advance, reload, and committed handshake succeed. |
| Pixel attestation | Screenshot sampling inside independently resolved text bounds changes from dominant red to dominant blue. |
| Rebinding | The post-reload target resolves again using the stable literal id and source location. |
| Product undo | The bridge invokes `undo_commit`; a second validated transaction restores the original bytes and red painted result. |
| Cleanup | Final fixture restore is byte-identical but is recorded only as cleanup, never as undo evidence. |

The live report returns `verdict="pass"` only when every mandatory evidence field above is true. Missing product seams return `blocked`; ambiguous pixel evidence returns `inconclusive`.

## Repository gates

```text
PYTHONPATH=src python -m pytest -q
663 passed, 45 skipped in 35.82s

python -m compileall -q src tests
# exit 0

git diff --check
# exit 0

cd ui && npm run build
# i18n status=GREEN, TypeScript clean, Vite build successful
```

The full suite requires the locked UI dependencies (`npm ci --no-audit --no-fund` in `ui/`) because the i18n contract tests invoke the TypeScript scanner.

## Deliberate limits

- one adapter: `text`
- one property: `color`
- one physical source line
- directly authored pure hex literal only
- one proven static instance
- no fonts, padding, hover/idle/selected colour families, inheritance editing, expressions, or generic property registry

## Criteria

`docs/superpowers/spikes/2026-08-02-style-color-criteria.md`
