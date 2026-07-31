# Imagebutton Adapter — Design

Status: **approved for implementation** (issue #32).  
Roadmap gate: Stage 1 — Widen the adapter allowlist.  
Canonical roadmap: `2026-07-30-renforge-visual-editor-vfull-roadmap.md`.

## Problem

V1 proves source-safe visual movement only for `textbutton`. `imagebutton` is focusable and structurally similar, but the roadmap forbids treating similarity as evidence. Shipping requires a dedicated adapter path and its own seven-step live proof.

## Decisions locked before design

| Decision | Choice | Why |
|---|---|---|
| Statement form | **Single-line only** | Matches V1 textbutton gate 3. Multi-line blocks are Stage 2. |
| Live proof delivery | **Dedicated opt-in live test + fixture** | Independent of the full task0 UI suite; report maps 1:1 to the seven steps. |
| Architecture | **Dedicated analyzer twin (not parameterized allowlist)** | Roadmap: do not widen by pattern-matching against `textbutton`. |

## Scope

### In

1. Dedicated single-line `imagebutton` source analyzer and patcher.
2. Coordinator analyze/commit dispatch by source keyword (`textbutton` \| `imagebutton`).
3. `source_key.statement_kind` set to the kind that actually parsed.
4. Dedicated live fixture with a literal authored single-line `imagebutton`.
5. Opt-in seven-step live proof; every verdict-bearing position measured from `focus_list`.
6. Unit/coordinator tests for accept and lock paths.
7. Doc touch: V1 scope adapter table + gate 3 wording once proof exists.

### Out

- Multi-line `imagebutton:` blocks with properties on child lines (Stage 2).
- Resize, idle/hover rewrite, style editing.
- `button`, `bar`, `vbar`, `slider` adapters.
- Overlay UI redesign for lock messaging (existing lock_reason surface is enough).
- Marking the adapter complete from unit tests alone.

## Architecture

### Source layer — `src/renforge/editor/source.py`

Keep the existing textbutton path unchanged:

- `TextbuttonStatement`
- `analyze_textbutton_statement`
- `apply_textbutton_patch`

Add a dedicated twin:

- `ImagebuttonStatement` — same field shape: `widget_id`, `xpos`, `ypos`, `xpos_span`, `ypos_span`
- `analyze_imagebutton_statement(line: str, *, expected_widget_id: str) -> ImagebuttonStatement`
- `apply_imagebutton_patch(source_bytes: bytes, statement: ImagebuttonStatement, *, x: int, y: int) -> bytes`

Rules for the imagebutton analyzer:

1. Reject multi-line input (`MULTILINE_STATEMENT_REJECTED`).
2. First top-level `WORD` token must be exactly `imagebutton` (`STATEMENT_KIND_MISMATCH` otherwise).
3. Exactly one top-level literal string `id` matching `expected_widget_id`.
4. Exactly one top-level literal integer `xpos` and one `ypos`.
5. Ignore keyword-looking tokens inside strings, comments, and nested call depths (reuse `_lex_single_line`).
6. Error messages name `imagebutton`, not `textbutton`.

Shared lexer helpers may be reused. **Kind acceptance must not be a shared allowlist** such as `kind in {"textbutton", "imagebutton"}` inside one analyzer. Each adapter owns its kind check.

A tiny private helper that rewrites two integer spans is allowed (pure byte surgery, kind-agnostic).

### Coordinator — `src/renforge/editor/coordinator.py`

Today analyze and commit hardcode `analyze_textbutton_statement` and `statement_kind: "textbutton"`.

New analyze flow:

1. Resolve source path/line from `runtime_key.source_location`.
2. Read the line; determine kind from the first top-level word (via a small kind peek or by trying dedicated analyzers without collapsing them into one).
3. Dispatch:
   - `textbutton` → `analyze_textbutton_statement`
   - `imagebutton` → `analyze_imagebutton_statement`
   - anything else → lock `STATEMENT_KIND_MISMATCH` with an exact reason
4. Set `source_key.statement_kind` to the kind that parsed successfully.
5. Keep all existing runtime locks (ancestry, multi-instance, measurement_method, fresh frame, id match).

Commit / `_apply_same_file_intents`:

1. Re-read the target line under the same baseline rules.
2. Dispatch patch by `source_key.statement_kind` (or re-detected line keyword — must agree).
3. Never fall back to the textbutton analyzer for an imagebutton line.

Preferred internal shape (keeps call sites thin without merging analyzers):

```python
def _analyze_statement(line: str, *, expected_widget_id: str) -> tuple[str, Any]:
    # peek kind, call dedicated analyzer, return (kind, statement)
    ...

def _apply_statement_patch(source_bytes: bytes, kind: str, statement: Any, *, x: int, y: int) -> bytes:
    ...
```

These helpers live next to the coordinator or as thin wrappers in `source.py`. They **dispatch**; they do not implement a generic “button” grammar.

### Runtime / overlay

No new selection or preview seam:

- Selection remains `focus_list` (imagebutton is focusable; Spike C already recorded focusability).
- Ancestry allowlist already includes `ImageButton`.
- Preview remains `_widget_properties` keyed by authored widget id.
- Overlay continues to honor host `capabilities.move` / `lock_reason` without kind-specific UI branches.

### Fixture & playground

**Live fixture** (dedicated file under `tests/live_fixtures/`):

- Screen with one editable single-line imagebutton, e.g.

```renpy
imagebutton id "imgbtn_target" idle Solid("#4c6ef5", xysize=(80, 48)) xpos 200 ypos 180 action NullAction()
```

- Optional second control for snap/anchor if needed by the scenario.
- At least one locked negative case in unit/coordinator tests (not necessarily in the live happy path): expression `xpos`, multi-line block, or wrong kind.

**Playground** (`examples/demo_game/game/editor_playground.rpy`):

- Existing `pg_imgbtn` is already single-line with literal id/xpos/ypos — keep it as the manual editable target after unlock.
- Do not convert it to a multi-line block.

## Seven-step live proof

Opt-in test (env flag, same pattern as `RENFORGE_TASK0_LIVE`), dedicated runner, dedicated report keys. Every position used as evidence has `measurement_method == "focus_list"`.

| Step | Name | Observed evidence |
|---|---|---|
| 1 | **resolve** | Select `imgbtn_target`; host `analyze_target` returns `lock_reason is None`, `capabilities.move is True`, `source_key.statement_kind == "imagebutton"`. |
| 2 | **preview** | Runtime preview moves the widget; independent `focus_list` observation shows new position (not computed placement). |
| 3 | **patch** | Save/commit publishes source; file bytes change only at the two integer spans for xpos/ypos. |
| 4 | **reload** | Post-publish reload handshake reaches a committed/attested state with new script generation. |
| 5 | **pixel agreement** | Post-reload `focus_list` position agrees with expected within **one logical pixel**. |
| 6 | **rebinding** | Target re-found by stable key (screen + source location + ancestry + ordinal), not object identity. |
| 7 | **byte-identical undo** | Conditional rollback / restore of baseline bytes verifies SHA-256 identity with the pre-patch file (product path: failed attestation rollback **or** explicit baseline restore in the proof harness after capturing staged bytes — must exercise real byte restore, not a mocked equality). |

Standing rules from the roadmap apply:

- Never report a pass that was inferred rather than observed.
- No self-validating evidence (do not compare a value to the request just injected).
- `inconclusive` outranks `blocked` for unmeasured steps.

### Proof harness shape

- `tests/live_fixtures/renforge_editor_imagebutton_fixture.rpy`
- `src/renforge/editor_imagebutton_runner.py` (or equivalent under `src/renforge/`) driving the seven steps and returning a structured report
- `tests/test_editor_imagebutton_live.py` gated by `RENFORGE_IMAGEBUTTON_LIVE=1` (or a shared live flag if the repo already standardizes one — prefer a dedicated flag to keep cost explicit)

The harness may reuse editor_task0 bridge handlers (`editor_task0_start/select/status/...`) and the normal editor launch path; it must not piggy-back assertions onto the full task0 UI suite.

## Automated non-live tests

### `tests/test_editor_source.py`

- Accept single-line imagebutton with nested noise in strings/calls.
- Patch preserves all bytes outside xpos/ypos integer tokens (including non-ASCII idle labels if present).
- Reject: wrong kind, expressions, duplicate id/xpos/ypos, mismatched id, multi-line, nested-only coordinates.

### `tests/test_editor_coordinator.py`

- Analyze + commit path for an imagebutton source line unlocks move and rewrites only coordinate spans.
- `statement_kind` in `source_key` is `"imagebutton"`.
- textbutton fixtures keep passing unchanged.
- Non-allowlisted kinds still lock with `STATEMENT_KIND_MISMATCH`.

## Documentation updates (after proof, same PR)

- `2026-07-30-renforge-visual-editor-v1-scope.md`
  - Adapter table: `imagebutton` → Selection pass / Write chain pass (this issue) / **Shipped** only if live proof ran green in CI or is recorded as an explicit local live gate with instructions.
  - Gate 3 wording: allow proven single-line adapters (`textbutton`, `imagebutton`) with literal integer xpos/ypos — not “textbutton only”.
- Roadmap Stage 1 row for `imagebutton` can note “implemented; evidence in live proof test” without claiming V-full.

**Honesty rule:** if the live proof is opt-in and not run in default CI, the scope doc must say **“implemented; live proof opt-in”** rather than quietly claiming spike-level shipment. Default unit/coordinator tests still land in CI.

## Error / lock catalogue (imagebutton-relevant)

| Code | When |
|---|---|
| `STATEMENT_KIND_MISMATCH` | Line is not a supported single-line adapter kind |
| `MULTILINE_STATEMENT_REJECTED` | Statement spans multiple lines |
| `ID_LITERAL_REQUIRED` | Missing/non-literal/duplicate id |
| `ID_MISMATCH` | Literal id ≠ runtime widget id |
| `XPOS_LITERAL_REQUIRED` / `YPOS_LITERAL_REQUIRED` | Non-integer or missing |
| `XPOS_DUPLICATE` / `YPOS_DUPLICATE` | Count ≠ 1 |
| Existing runtime locks | viewport/crop/multi-instance/measurement/etc. unchanged |

## Non-goals / anti-patterns

- Do not add `imagebutton` by changing one string in the textbutton analyzer.
- Do not unlock multi-line blocks “because the header has xpos”.
- Do not use `_renforge_scene_place` or any non-`focus_list` measurement for proof steps.
- Do not mark complete from green unit tests alone.

## Success criteria (issue #32)

1. Dedicated analyzer/patch path exists and is wired through analyze + commit.
2. Single-line literal imagebutton is editable end-to-end when live proof runs.
3. Seven-step live proof report is produced with focus_list measurements.
4. Ambiguous/unproven forms remain locked with exact reasons.
5. textbutton behavior unchanged.
6. PR links issue #32.
