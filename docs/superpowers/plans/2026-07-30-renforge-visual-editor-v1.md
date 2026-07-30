# RenForge Visual Editor V1 — Implementation Plan

**Goal:** ship a developer-only, in-game editor for moving source-safe `textbutton` widgets, with
observed focus geometry, snap guides, runtime-only preview, undo/redo/reset, and authenticated
all-or-nothing source save.

**Source of truth:** `docs/superpowers/specs/2026-07-30-renforge-visual-editor-v1-scope.md`. The
older design spec remains useful for UX, but any conflict is governed by the four-spike evidence
recorded in the V1 scope.

## Global constraints

1. V1 adapter allowlist is exactly `textbutton`.
2. An editable target is focusable, has a literal screen `id`, is a single-line `textbutton` with one
   literal integer `xpos` and one literal integer `ypos`, and is outside unproven clip containers.
3. Every verdict-bearing coordinate has `measurement_method = "focus_list"`. Computed placement may
   be informational only.
4. Runtime preview uses only `renpy.show_screen(..., _widget_properties={id: {"xpos": x,
   "ypos": y}})`. Never mutate shared style and never inject a wrapper into the rendered tree.
5. The in-game overlay and bridge never open or write user `.rpy` files. Only the host coordinator may.
6. Source changes are token-aware, preserve all text except the two integer token spans, are staged and
   validated before publication, refuse stale SHA-256 baselines, and publish all files or none.
7. A remembered displayable object is invalid after preview and reload. Rebind by stable key.
8. A failed gate is visible with its exact reason; no silent no-op.
9. Nothing is written before Save. A successful Save becomes the new history baseline; Undo does not
   cross it.
10. Editor artifacts are injected only for `editor=True` launches and removed on every teardown path.

## Protocol contract (fixed before parallel work)

One JSON object per UTF-8 line over a dedicated loopback TCP connection. Maximum line: 1 MiB.
Every request is:

```json
{"token":"<per-launch random>","request_id":"<opaque>","command":"<name>","payload":{}}
```

Every reply echoes `request_id` and is either `{"request_id":"...","ok":true,...}` or
`{"request_id":"...","ok":false,"error":{"code":"...","message":"..."}}`.
Authentication failure returns `AUTH_FAILED` and closes the connection. Unknown commands return
`UNKNOWN_COMMAND`. Malformed or oversized frames return a structured error and close.

Commands:

- `analyze_target`: payload `{screen, widget_id, source_location:[path,line], ancestry:[int...],
  ordinal, runtime_position:[x,y], measurement_method}`. Returns `session_id`, `source_key`,
  `original_position`, `capabilities:{move:bool}`, and `lock_reason` when false.
- `commit`: payload `{session_id, intents:[{source_key,x,y}]}`. Returns `transaction_id`,
  `changed_files`, `reload_required:true`. All intents are re-analysed before any publication.
- `commit_status`: payload `{transaction_id}`. Returns state in `staged|published|committed|
  rolled_back|rollback_conflict|failed` and retained diagnostics.
- `reload_handshake`: payload `{transaction_id, script_generation}`. Attests the new runtime, marks the
  transaction committed, deletes backups, and establishes the new baseline.

`source_key` contains only JSON data: `{relative_path,line,screen,widget_id,ancestry,ordinal,
statement_kind,baseline_sha256}`. Paths are project-relative POSIX paths and must remain under `game/`.

The coordinator publishes only one transaction at a time. A published transaction not attested within
30 seconds is conditionally rolled back. Session shutdown also rolls it back. Rollback restores a file
only when its current digest still equals the staged digest; otherwise it records `rollback_conflict`
and never overwrites external work.

## Task 1 — Host source engine and coordinator

**Files:** new `src/renforge/editor/` package and focused tests under `tests/`.

Implement:

- Tokenize one Ren'Py screen-language statement without parsing it as Python syntax. Accept exactly one
  line whose first significant token is `textbutton`, with one literal string `id`, one literal integer
  `xpos`, and one literal integer `ypos`. Reject expressions, duplicates, multi-line statements, and
  paths outside `game/` with stable reason codes.
- Analyse target descriptors and return the fixed `source_key` + capability.
- Apply multiple intents in memory while preserving every byte outside the two integer token spans.
- Stage a shadow project using hard links with copy fallback, replace staged files there, and run the
  supplied Ren'Py SDK lint command with bounded output and timeout. Treat timeout or nonzero return as
  validation failure.
- Durable transaction directory under `.renforge/editor-transactions/<transaction_id>/` containing
  manifest, originals, and staged bytes. Fsync files and directory metadata before publication.
- Per-file immediate stale check, atomic replace, conditional rollback, recovery of non-terminal
  journals at coordinator startup, and the fixed JSON-line server protocol bound to `127.0.0.1`.
- Unit tests must be red before implementation and cover path escape, token preservation, duplicates,
  stale source, validation failure, partial-publication rollback, rollback conflict, auth, oversized
  frames, pending-attestation timeout, and clean attestation.

## Task 2 — In-game overlay and runtime editor session

**Files:** new `src/renforge/bridge/editor.rpy` and focused resource/live tests.

Implement a development-only overlay loaded after the production bridge:

- Floating `RF` button, compact top toolbar, Escape exit, global tool opacity, attached target label.
- In edit mode, select the topmost `focus_list` entry under the pointer while excluding editor nodes.
  Resolve the enclosing `Button` and stable key; call `analyze_target` before enabling manipulation.
- Locked targets remain selected and measured and show the exact `lock_reason`.
- Drag and arrow nudge. One pointer gesture is one command. Arrow = 1 logical pixel, Shift+arrow = 10.
- Snap to game boundaries and other observed focus rect edges/centres. Acquire at 6 logical pixels,
  release at 10. Shift during pointer drag disables snapping.
- Draw red guides and pixel distances. Toolbar/label/guides/handles respect global opacity; the RF exit
  control gets a high-contrast outline below 25%. The label fades near the pointer and stays in bounds.
- Runtime preview/reversal only through `_widget_properties`. Re-resolve and re-measure after every
  recreation; do not retain object identity. All displayed/saved positions are observed focus rects.
- Session-local undo/redo/reset; Save enabled only for a non-empty set of analysed source-safe intents.
  Save calls coordinator `commit`, triggers `renpy.reload_script()`, then performs bounded re-show,
  stable-key rebind, observed measurement, and `reload_handshake`.
- No file I/O to `.rpy`; no bridge source-write RPC.

## Task 3 — Launch and public integration

**Files:** `src/renforge/bridge/launcher.py`, live launch stack, dashboard launch endpoint, MCP server,
client-facing schemas, and focused tests.

Implement:

- `editor: bool = False` from `renforge_launch` and dashboard launch payload through
  `live.launch` to `launch_with_bridge`. Default is false.
- With editor true: start `EditorCoordinator`, inject `editor.rpy` as `zzrenforge_editor.rpy`, and pass
  coordinator host/port/token through environment variables. Injection happens while the project lock
  is held and before Ren'Py starts.
- `BridgeSession` owns and closes the coordinator. All failure, cancellation, normal close, deferred
  reap, and maintenance cleanup paths remove `.rpy/.rpyc/.rpyc.bak` editor artifacts. Pending published
  transactions are rolled back before releasing project ownership.
- Do not add a game-side source-writing handler. Do not duplicate existing `scene_tree`,
  `get_ui_element_bounds`, `measure`, or image-tag positioning APIs.
- Tests must begin red and prove opt-in injection, default absence, environment delivery, coordinator
  ownership/teardown, cleanup after failed launch, and parameter propagation from both MCP and dashboard.

## Task 4 — Integration and live acceptance

Integrate the three isolated task branches, resolve only contract-level conflicts, then run:

1. Focused unit suites for editor source/coordinator, overlay resources, launch, server, and dashboard.
2. The existing project suite.
3. A live Ren'Py 8.5.3 scenario on an isolated demo target containing one editable and four locked
   controls. Exercise: launch with editor enabled; RF activation; editable selection; drag with snap;
   one-pixel nudge; Undo; Redo; Reset; Save; reload; observed pixel agreement ≤1; source persistence;
   external-conflict refusal; locked reason for missing id; locked reason for expression; Escape exit;
   teardown with no injected artifacts.

Acceptance is based on the game frame, focus-list measurements, source bytes, and cleanup state — not
on the overlay's own claimed status.

## Task 5 — Review

Run an adversarial whole-branch review against this plan and
`docs/superpowers/specs/2026-07-30-renforge-visual-editor-v1-scope.md`. Fix every load-bearing
finding, re-run its covering test, then repeat the live acceptance scenario. Do not merge or install;
leave the finished branch for user review.
