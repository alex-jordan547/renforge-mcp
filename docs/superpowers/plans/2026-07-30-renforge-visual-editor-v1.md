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
   validated before publication, refuse stale SHA-256 baselines, and atomically replace one source file.
7. A remembered displayable object is invalid after preview and reload. Rebind by stable key.
8. A failed gate is visible with its exact reason; no silent no-op.
9. Nothing is written before Save. A successful Save becomes the new history baseline; Undo does not
   cross it.
10. Editor artifacts are injected only for `editor=True` launches and removed on every teardown path.

## Contract prerequisite (fixed before implementation)

Tasks may not invent their own schemas. The contracts below are exact.

### Runtime identity and observation

`runtime_key` is JSON data:

```text
{screen, invocation_path, widget_id, source_location:[path,line],
 instance_discriminator, ancestry:[{index,type,source_location,screen_owner,
 crop_state,editor_owned}]}
```

`observation` is:

```text
{runtime_key, rect:[x,y,w,h], measurement_method:\"focus_list\",
 frame_id, script_generation, object_id}
```

The editor bridge derives this descriptor by walking from the focus entry through the live screen
tree. It counts all live matches. Unknown ancestor type/crop state, missing invocation identity,
synthetic widget id, editor-owned ancestry, or more than one matching instance is a locked result.
The coordinator then parses the source independently, proves a literal `id` matching `widget_id`, and
compares a second observation obtained through the host-to-game bridge. Neither side alone can declare
a target editable.

Every editor displayable belongs to reserved screen `_renforge_editor_overlay` and carries/verifiably
inherits owner namespace `renforge.editor.v1`. Selection rejects any ancestry with that screen or
owner marker before hit testing.

`source_key` is:

```text
{relative_path,line,screen,widget_id,invocation_path,instance_discriminator,
 ancestry,statement_kind:\"textbutton\",baseline_sha256}
```

Paths are canonical project-relative POSIX paths. A single resolver rejects NUL, backslash, absolute,
empty, `.`, `..`, symlinks and non-regular files, and proves containment under the resolved `game/`
root before every read, stage, replace and recovery action.

### Coordinator protocol

Dedicated TCP on `127.0.0.1`, protocol name `renforge-editor`, version `1`. The server validates the
loopback peer. Reads are bounded before JSON parsing: auth frame 4 KiB, later frame 1 MiB; individual
string fields 4 KiB, path 1 KiB, at most 256 intents, and diagnostics 64 KiB with `truncated: true`.

The first frame on every connection is:

```json
{\"protocol\":\"renforge-editor\",\"version\":1,\"token\":\"<editor token>\",\"client_nonce\":\"<opaque>\"}
```

Success returns `{protocol,version,ok:true,connection_id,session_id,server_nonce}`. Bad peer/token,
version or frame returns a structured error and closes before accepting a command. Bridge and editor
tokens are distinct.

Subsequent frames are `{protocol,version,connection_id,request_id,command,payload}`. Replies echo
`request_id`. Request IDs are unique within the launch session: an exact duplicate returns the cached
reply (so `commit` is idempotent); the same ID with different canonical bytes returns
`DUPLICATE_REQUEST_ID` and closes. Truncated/oversized/malformed frames return `request_id:null` when it
cannot be recovered, then close. Reconnect repeats auth with the same launch token/client nonce.

Commands:

- `analyze_target`: payload `{observation}`. Coordinator asks its `RuntimeProbe` for a fresh independent
  observation, requires exact runtime key, `focus_list`, a fresh frame and current generation, then
  returns `analysis_id`, `source_key`, `original_position`, `capabilities:{move}`, `lock_reason`.
- `commit`: payload `{session_id,intents:[{analysis_id,source_key,x,y}]}`. Every analysis must belong to
  this session and current generation. All intents must resolve to the same file. Re-analysis, staging
  and lint precede the one atomic publication. Returns `transaction_id`, state `published`, and
  `reload_required:true`.
- `commit_status`: payload `{transaction_id}`. State is `staged|published|committed|rolled_back|
  rollback_conflict|failed`, with bounded diagnostics and exact uncertain paths.
- `reload_handshake`: payload `{transaction_id,script_generation}`. It only begins attestation. The
  coordinator requires generation `previous + 1`, then independently drives
  `bridge_reconnected → fresh_frame → re_show_observed → all_targets_attested`. It queries every
  successor through `RuntimeProbe` and requires stable-key rebind, `focus_list`, fresh frame and
  position agreement within one logical pixel before marking `committed`.

A published transaction not attested within 30 seconds is conditionally rolled back. Session shutdown
does the same. Because V1 publishes one file, publication itself is one atomic replace. Rollback
restores only when the current digest still equals RenForge's staged digest; otherwise state is
`rollback_conflict` and the exact path remains untouched.

### Python and launch API

```text
EditorCoordinator(project: RenpyProject, sdk: RenpySdk, *,
                  token: str|None = None, attestation_timeout: float = 30.0)
.start() -> EditorEndpoint(host, port, token, protocol_version)
.attach_runtime_probe(probe: RuntimeProbe) -> None
.close(timeout: float = 10.0) -> dict
```

`RuntimeProbe.observe(runtime_key, *, deadline)` forces a screenshot/draw, passes that frame id as a
guard to `editor_observe_target`, retries transient bridge transport failures, and returns an
`observation`. `RuntimeProbe.attest(...)` implements the explicit reload state machine and bounded
idempotent re-show.

Environment names are exactly `RENFORGE_EDITOR_HOST`, `RENFORGE_EDITOR_PORT`,
`RENFORGE_EDITOR_TOKEN`, and `RENFORGE_EDITOR_PROTOCOL=1`. Editor injection chooses a random safe
basename `zzrenforge_editor_<launch_nonce>` for which `.rpy`, `.rpyc`, and `.rpyc.bak` are all absent.
The durable session manifest records that three-file sibling set, the injected `.rpy` SHA-256, and
`absent_before=true` for each generated sibling. Cleanup may remove the source only when its bytes still
match the injected digest; it may remove compiled siblings only when the source still matches, they
were absent before injection, are regular non-symlink files, and retain the exact random basename.
Otherwise it reports `EDITOR_ARTIFACT_CONFLICT`, leaves every uncertain path untouched, and does not
release ownership as if cleanup succeeded. Crash recovery applies the same checks. It never overwrites
or blindly deletes a user file.

`live.launch_game` records editor mode and coordinator ownership. Reuse requires the requested mode to
match; otherwise return `SESSION_MODE_MISMATCH`. Launch status reports the actual `editor` boolean.

## Execution order

Two independent slices begin in parallel: Task 0 proves the production event/render seam; Task 1
implements the host engine against the frozen protocol. Task 2 depends on both. Task 3 depends on
Tasks 1 and 2. Parallelism never crosses those real dependencies.

## Task 0 — Production interaction prerequisite

**Files:** production `src/renforge/bridge/editor.rpy` skeleton plus a dedicated live fixture and
focused tests. This is the first increment of the product, not a disposable spike.

Write failing acceptance checks first, then prove on Ren'Py 8.5.3:

- custom `Displayable.event` (or measured equivalent) captures drag continuously without blocking the
  main thread, Escape/arrow repeat work, and normal game input resumes on exit;
- `focus_list` selection excludes every focusable editor control through the owner marker;
- `_widget_properties` drag preview, reversal, 1/10-pixel nudge, snap acquire 6/release 10, red guides,
  opacity and in-bounds attached label render in live frames;
- coordinator I/O runs off the interaction thread and only applies results on the main thread;
- each accepted observation follows a successful screenshot/draw barrier.

If this prerequisite cannot pass, stop; do not replace it with source-text or mock assertions and do
not claim the drag UI is feasible.

## Task 1 — Host source engine and coordinator

**Files:** new `src/renforge/editor/` package and focused tests.

Implement the exact data/protocol/API contracts above. The source lexer accepts exactly one
single-line `textbutton` with one literal string `id`, one literal integer `xpos`, and one literal
integer `ypos`; expressions, duplicates, dynamic/unknown ancestry and multi-instance descriptors lock.

Apply same-file intents in memory while preserving every byte outside the two integer spans. Build a
copy-on-write shadow by copying project files and excluding `.renforge`, saves, cache, `.rpyc/.rpymc/
.rpyb`; never hard-link writable SDK inputs and reject source symlinks. Run Ren'Py lint with bounded
stdout/stderr and timeout. A lint-generated artifact in the shadow must leave every live inode/byte
unchanged.

Persist transaction manifest, original and staged bytes under
`.renforge/editor-transactions/<id>/`, fsync files/directories, perform one immediate stale check and
one atomic replacement, recover non-terminal journals at startup, and conditionally roll back.

Tests begin red and cover typed proof denial, repeated `use`/loop descriptors, viewport/Crop/
Transform-crop denial, path aliases/escape/symlink, token preservation, duplicates, stale source,
shadow isolation, validation failure, rollback conflict, auth-first framing, duplicate IDs, truncation,
oversize, reconnect, timeout and independent clean attestation.

## Task 2 — Complete in-game overlay and runtime session

**Depends on Tasks 0 and 1. Files:** finish `src/renforge/bridge/editor.rpy`; add only focused tests.

Add floating RF button, compact top toolbar, Escape exit, tool opacity and attached target label.
Locked targets stay selected/measured and show the exact reason. One drag is one history command;
arrows nudge 1 pixel and Shift+arrows 10. Shift during drag disables snap. Draw edge/centre guides and
pixel distances. Below 25% opacity the RF exit control keeps a high-contrast outline.

Runtime preview/reversal uses only `_widget_properties`. Re-resolve and independently re-measure after
every recreation. Maintain reload-safe generation/pending-transaction state in a `sys.modules` object
and reinstall callbacks after reload. Save is enabled only for non-empty, same-file, analysed intents.
It commits, requests reload, reconnects, performs bounded re-show, and asks the coordinator to attest.
No `.rpy` file I/O and no game-side source-write RPC.

## Task 3 — Launch and public integration

**Depends on Tasks 1 and 2.** Propagate `editor: bool = False` through MCP, dashboard and
`live.launch_game`. Start/attach `EditorCoordinator`, inject the randomized owned resource, pass the
four exact environment variables, record mode in the registry, and reject mismatched reuse.

`BridgeSession` owns coordinator and injection manifest. Failure, cancellation, normal close, deferred
reap and maintenance recovery await transaction recovery before unlocking, and remove only
manifest-matching artifacts. Add `BridgeClient.editor_observe_target` and the runtime probe; do not add
a source-writing handler or duplicate scene/bounds/measure/image-position APIs.

Tests begin red and prove opt-in/default launch, mode mismatch, status, dashboard/MCP propagation,
environment delivery, coordinator ownership, reload transport retry, random-name collision retry,
source-digest mismatch, symlinked/generated sibling refusal, manifest-safe `.rpy/.rpyc/.rpyc.bak`
cleanup, crash recovery and every failed-launch/deferred-reap path.

## Task 4 — Integration and live acceptance

Integrate each dependency-ordered task commit onto the V1 branch, resolve only contract-level
conflicts, then run:

1. Focused unit suites for editor source/coordinator, overlay resources, launch, server and dashboard.
2. The existing project suite.
3. A live Ren'Py 8.5.3 scenario on an isolated demo target containing one editable target plus locked
   controls for missing id, expression position, viewport/crop ancestry and repeated instance.
   Exercise: launch editor; RF activation; self-excluding selection; drag with snap; one-pixel nudge;
   Undo; Redo; Reset; Save; reload state machine; independently observed pixel agreement ≤1; source
   persistence; stale-source refusal; multi-file refusal; each exact lock reason; Escape exit; teardown
   with no manifest-owned injected artifacts.

Acceptance is based on the game frame, focus-list measurements, source bytes, and cleanup state — not
on the overlay's own claimed status.

## Task 5 — Review

Run an adversarial whole-branch review against this plan and
`docs/superpowers/specs/2026-07-30-renforge-visual-editor-v1-scope.md`. Fix every load-bearing
finding, re-run its covering test, then repeat the live acceptance scenario. Do not merge or install;
leave the finished branch for user review.
