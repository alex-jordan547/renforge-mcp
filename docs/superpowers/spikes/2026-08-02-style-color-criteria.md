# Issue #50 — style colour source-write criteria (frozen before implementation)

**Status:** criteria locked before measurement.
**SDK:** Ren'Py **8.5.3** only.
**Surface:** in-game bridge (production style control stays disabled until PASS).
**Frozen pair:** adapter `text`, property `color` (one property, one adapter).

## Question

Can RenForge:

1. resolve ownership of a pure literal `color` on a single-line screen-language `text` statement;
2. fail closed for inherited, expression, ambiguous, multi-instance, stale, malformed, unsupported, or non-text cases;
3. patch only that colour token while preserving every unrelated byte and the supported authored form;
4. prove the full product chain on Ren'Py 8.5.3: select/resolve → preview → source patch → reload → independent pixel colour change → rebind → refused-attestation rollback → **product** undo?

## Explicit non-goals

- fonts, padding, hover/idle/selected colour families;
- style panels or generic property registries;
- a second adapter or a second property;
- routing style writes through coordinate-token / xpos-ypos semantics;
- silently widening scope when this pair is unsafe.

## Evidence planes (independent)

| Plane | Must measure | How |
| --- | --- | --- |
| **Ownership** | Direct authored `color` vs inherited/expression/ambiguous | source analyser lock codes; do not infer from runtime style |
| **Source write** | Only the colour string token changes | independent expected constructor + outside-span identity |
| **Pixel colour** | Painted colour at a fixed probe point | screenshot RGB sample; not editor/runtime colour claims |
| **Product path** | select, preview, commit, rebind, refused attestation, product undo | real bridge + coordinator commands only |

## Supported authored form (unlocked)

Single physical line:

```text
text "LABEL" color "#rrggbb" id "widget_id" …
```

- statement kind is exactly `text` (not `textbutton`, not a block header);
- exactly one pure string-literal `color` whose value is `#rgb`, `#rrggbb`, or `#rrggbbaa`;
- exactly one pure string-literal `id` matching the runtime widget id;
- no expression/operator tokens after the colour literal.

## Fail-closed lock codes (stable)

| Code | Meaning |
| --- | --- |
| `STATEMENT_KIND_MISMATCH` | not a single-line `text` statement |
| `MULTILINE_STATEMENT_REJECTED` | block form (`:`) |
| `ID_LITERAL_REQUIRED` / `ID_MISMATCH` | missing/invalid/mismatched id |
| `STYLE_COLOR_NOT_DIRECTLY_AUTHORED` | no `color` keyword (inherited / style-driven) |
| `STYLE_COLOR_LITERAL_REQUIRED` | colour is not a pure string literal |
| `STYLE_COLOR_UNSUPPORTED_FORM` | string is not a supported hex form |
| `STYLE_COLOR_DUPLICATE` | more than one `color` keyword |
| `STYLE_COLOR_EXPRESSION_UNSUPPORTED` | expression / compound colour value |
| `STYLE_COLOR_HEX_FAMILY_MISMATCH` | requested value changes the authored hex length |
| `STALE_SOURCE` | statement bytes changed after analysis |

Non-text adapters never unlock style colour through this contract.

## Pass / blocked / inconclusive (frozen)

### PASS

All of the following are observed on Ren'Py 8.5.3:

1. Select/resolve unlocks style colour for the target (`statement_kind=text`, `style_mode=literal_hex`, no lock).
2. Preview changes painted colour without writing source (independent screenshot evidence).
3. Source patch rewrites only the colour token; outside bytes identical; form preserved (same quotes / hex family).
4. Save → shadow validate → atomic publish → reload succeeds; script generation advances.
5. Post-reload pixel sample matches the requested colour class (independent of editor claims).
6. Runtime source-location rebinding succeeds for the same stable id.
7. One deliberately refused attestation rolls the staged transaction back (source bytes unchanged).
8. Product undo (editor undo / product transaction) restores baseline bytes — **not** a manual fixture restore.

### BLOCKED

Any mandatory product seam is absent or fails while source ownership is otherwise well-defined, including:

- plain `text` is not selectable via the production focus_list path;
- no style-colour preview / intent / coordinator transaction path is enabled;
- product undo for style colour is missing (fixture restore only);
- any required gate cannot be closed without widening beyond the frozen pair.

Record **one** stable `verdict_reason`. Keep production style UI/control disabled.

### INCONCLUSIVE

- required ids/spans cannot be resolved;
- pixel probe is ambiguous for the fixture (anti-alias / subpixel noise not resolvable);
- identical runs disagree on verdict class.

## Stop conditions

- If the frozen pair is unsafe or unavailable, stop and report BLOCKED — do not switch adapter/property.
- If a mandatory seam is missing, do not invent alternate UI surfaces or a second property.
- Manual fixture restore may appear in reports only as cleanup, never as product-undo evidence.

## Deliverable

One report from `run_editor_style_color_live_scenario` with `verdict` in `{pass, blocked, inconclusive}`, explicit evidence fields, and a single stable `verdict_reason` when not PASS.
