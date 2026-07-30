# RenForge In-Game Visual Editor Design

**Date:** 2026-07-29  
**Status:** Expert-reviewed design; pending mandatory Ren'Py 8.5.3 feasibility spike and user approval

## Summary

RenForge will provide a development-only visual editor inside the running Ren'Py game. A developer can select an eligible displayable, move it, resize it, or rotate it while seeing alignment guides and pixel measurements. These interactions affect only the running game until the developer presses **Save code**. RenForge then applies a guarded source patch to the relevant `.rpy` files and reloads the script.

The editor prioritizes source safety over universal manipulation. RenForge determines editability before a gesture begins. Displayables whose source cannot be rewritten unambiguously remain selectable and measurable but are locked against transformations.

## Goals

- Activate visual editing from a floating RenForge button in a development session.
- Select visible Ren'Py displayables directly in the game window.
- Move, resize, and rotate eligible displayables with immediate runtime feedback.
- Show pixel dimensions, spacing, centers, edges, anchors, and alignment guides.
- Provide snapping, keyboard nudging, and a complete per-session Undo/Redo history.
- Let the developer adjust the opacity of all editor chrome so the game remains visible.
- Keep element information attached to the selection, make it hideable, and fade it while hovered.
- Write source only when **Save code** is pressed.
- Reject ambiguous or stale source edits before changing any file.
- Reload the Ren'Py script after a successful save.

## Non-goals

- Rewriting automatic layouts such as `hbox`, `vbox`, `grid`, or dynamically calculated positions.
- Converting dynamic layout children into absolute positioning.
- Editing arbitrary Python expressions that produce position, size, or rotation values.
- Manipulating multiple runtime instances that share one source statement without a stable identity.
- Shipping the editor in a game distribution.
- Providing a general-purpose `.rpy` code editor inside the game.

## User experience

### Activation

The floating **RF** button exists only when the game is launched with the RenForge development bridge. Pressing it enters edit mode and reveals a compact toolbar at the top of the game window. Pressing it again, or pressing Escape, exits edit mode without changing source files.

While edit mode is active, the overlay owns pointer and editor keyboard input. Outside edit mode, the game receives input normally.

### Selection

The editor uses an internal runtime graph rather than the public `scene_tree` IDs or the focus list. Hovering chooses the topmost painted inspectable node under the pointer, then the deepest child when paint order is equal. V1 hit testing uses each node's transformed painted quad or bounds intersected with its effective clip; it does not perform per-pixel alpha testing. Selection excludes every RenForge overlay node.

Clicking selects the current candidate. Repeated clicks within a four-logical-pixel radius cycle deterministically through the painted stack and then its parents. Moving outside that radius, changing the screen tree, or leaving edit mode resets the cycle.

A selected displayable shows:

- its painted bounds and axis-aligned bounds when they differ;
- its origin, anchor, and available transform handles;
- width, height, source location, and current rotation;
- edge and center guides;
- text baselines only for untransformed `Text` nodes with reliable font metrics;
- pixel distances to relevant nearby bounds;
- a capability state indicating which transformations can be saved.

Each operation has an independent capability. A displayable may be movable but not resizable or rotatable. Unsupported handles are hidden or disabled with a short explanation.

### Manipulation

The editor supports three modes:

- **Move:** pointer drag, one-pixel arrow-key nudging, and ten-pixel nudging with Shift.
- **Resize:** edge and corner handles for statement kinds with an explicit resize adapter.
- **Rotate:** a rotation handle for statement kinds with an explicit transform adapter.

Snapping uses visible edges, centers, reliable text baselines, and configured stage boundaries. The default snap thresholds are six logical pixels to acquire a guide and ten logical pixels to release it, preventing jitter near a match. Holding Shift temporarily disables snapping during a pointer gesture.

A continuous pointer gesture becomes one Undo/Redo command rather than one command per rendered frame. **Reset** restores every edited displayable to the state established by the last successful save or by session start. A successful save establishes a new history baseline; Undo does not cross a save boundary.

### Visibility of editor tools

The toolbar contains a global **Tools opacity** control. It changes the opacity of the toolbar, guides, handles, measurements, floating RF button, and attached information without changing the game or selected displayable. The RF exit button gains a high-contrast outline below 25% opacity so it remains discoverable.

Selection information is rendered in a compact label attached to the displayable rather than in a permanent side panel. The label:

- can be hidden or shown from the toolbar;
- fades to near-transparent when the pointer approaches it;
- repositions within the game bounds when its preferred location would be clipped;
- never changes the layout of the game being edited.

Guides, measurements, handles outside their drag areas, and attached labels are pointer-transparent. Only explicit toolbar controls and active handle hit areas capture input. Escape exits edit mode regardless of current focus.

The selected displayable remains fully visible at every editor-opacity setting.

### Saving

**Save code** is enabled only when the session contains at least one source-safe transformation. The toolbar exposes a compact, non-blocking summary of changed elements and properties; pressing Save commits immediately without a second dashboard or modal confirmation. The complete valid edit session is committed, or no source file changes.

## Architecture

The feature is split into six independently testable components. The public MCP scene-perception API remains observational and is not used as the editor's identity layer.

### 1. In-game editor overlay

The bridge injects a development-only Ren'Py screen above normal game content. It renders guides, measurements, handles, toolbar controls, capability results, and save status. It translates pointer and keyboard input into editor gestures but never opens or writes `.rpy` files.

### 2. Editor runtime graph

`EditorRuntimeGraph` is a main-thread-only graph distinct from `scene_tree`. It stores internal weak references to runtime displayables together with:

- parent and child relationships;
- paint order and depth;
- layer, screen owner, clipping region, and transform geometry;
- painted bounds and axis-aligned bounds;
- SL2 owner metadata and runtime-instance identity;
- an explicit flag excluding RenForge overlay nodes.

The graph is rebuilt or invalidated when screens or displayables change. Ordinal IDs exposed by `scene_tree` remain observation-only and never appear in an edit command.

### 3. Runtime override adapters

`RuntimeOverrideAdapter` applies reversible, editor-owned transforms without mutating shared styles. Adapters are allowlisted by statement kind and operation. They:

- wrap or replace only the selected runtime instance;
- preserve existing transforms, ATL, clipping, layer, z-order, and animation state;
- rebind through `RuntimeInstanceKey` when SL2 recreates a displayable;
- invalidate rendering after a working-state change;
- freeze the command and revoke edit capability if the target disappears or becomes ambiguous.

Screen displayables and scene images use separate adapters.

### 4. Edit session

A bridge-side session owns original state, working state, and command history. Its records are:

```text
RuntimeInstanceKey
  runtime_kind
  screen_invocation_path or scene_identity
  owner_chain
  instance_discriminator

SourceKey
  canonical_file
  logical_cst_span
  statement_kind
  owning_screen
  statement_digest

EditTarget
  runtime_instance_key
  source_key
  full_file_digest
  capabilities: move | resize | rotate
  capability_reasons

EditCommand
  target_key
  operation
  before_transform
  after_transform

SourceIntent
  source_key
  changed_properties
  expected_file_digest

CommitResult
  old_source_key
  committed_source_key
  staged_file_digest
  expected_runtime_geometry
```

Ren'Py 8.5.3 `_location = (filename, line)` is only the first lookup hint. `SourceKey` is reconstructed from a token/CST-aware logical statement span and is never assumed to survive reload by object identity. `RuntimeInstanceKey` and `SourceKey` remain separate because several runtime instances may share one source statement.

Children of a loop and multiply instantiated `use` screens are read-only in V1 whenever changing their shared statement would affect more than the selected instance.

### 5. Host editor coordinator and transport

`EditorCoordinator` runs outside the Ren'Py process and owns source analysis, transaction locks, patching, validation, and reload attestation. The injected bridge establishes a dedicated loopback connection to it using a random per-launch session token.

The protocol is bidirectional and request-correlated:

```text
analyze_target(request_id, runtime_metadata)
analyze_result(request_id, source_key, capabilities)
commit(request_id, source_intents)
commit_status(request_id, phase, result)
reload_handshake(request_id, generation, resolved_targets)
```

The existing MCP request socket and dashboard event polling are not reused for commits. Transform handles remain disabled until `analyze_result` succeeds. Save remains disabled whenever the coordinator connection is absent.

### 6. Source patcher and shadow validator

The coordinator's patcher performs token/CST-aware edits of logical SL2 statements while preserving untouched bytes. The current regex scanner is not used for screen-body rewriting.

Validation occurs in a temporary shadow project with the same relative source paths and staged replacements. It never places duplicate `.rpy` files beside live sources. The shadow project is parsed and linted with the project's Ren'Py 8.5.3 runtime before publication, and the exit code, bounded output, and timeout are retained in the commit result.

## Mandatory pre-plan feasibility gate

Before a full implementation plan is written, a bounded technical spike must run against the real Ren'Py 8.5.3 SDK and demo game. The spike writes no project-source patcher and proves the runtime seams needed by the first adapter roster.

The spike must demonstrate:

1. extraction of parentage, paint order, transformed geometry, and clipping for the selected screen-displayable roster;
2. hit testing against transformed quads/bounds and effective clips, including non-focusable nodes and excluding the RenForge overlay, with per-pixel alpha explicitly out of scope;
3. a reversible outer runtime override that does not mutate shared styles;
4. target rebinding after SL2 recreates a displayable;
5. preservation of existing transforms and animation state for every adapter variant that permits them, plus a measured capability guard that makes variants with unsupported transform or animation state measure-only;
6. stable Undo/Redo application across screen interaction restarts;
7. measured geometry matching the rendered result within one logical pixel.

The spike passes only when each claimed adapter has a live fixture and a documented extraction, override, invalidation, and rebinding seam. Any adapter that fails is removed from the V1 roster and becomes measure-only before implementation planning continues. Scene-image editing remains measure-only unless a separate provenance and non-destructive-transform spike passes.

For every adapter that passes, the spike produces a behavior sheet fixing the source and interaction semantics before planning: editable properties and units, drag-handle meaning, fixed edge or pivot, anchor compensation, aspect-ratio policy, minimum size, rotation pivot, `rotate_pad`, `transform_anchor`, transform-composition order, expected hit geometry, and unsupported combinations. The user must approve the retained roster and these behavior sheets before the full implementation plan is written.

## Target eligibility

Eligibility is calculated before any transform handle is enabled. The default is deny: an operation is available only when a tested capability adapter matches the statement, source provenance, units, and runtime state.

### Property provenance

The analyzer resolves the effective positioning value and records whether it comes from:

- a direct statement property;
- `pos`, `align`, or `area` aliases;
- an inline `Transform`;
- `properties` or `widget_properties`;
- a local or inherited style;
- a dynamic expression.

Conflicting aliases, relevant property bags that cannot be resolved, inherited shared-style edits, and unsupported expressions are read-only in V1.

Integer literals and `absolute(...)` values are pixel coordinates. Fractional floats retain their proportional meaning; movement is represented by integer `xoffset` or `yoffset` deltas rather than rewriting the float as a pixel value.

### V1 capability matrix

The initial V1 roster is intentionally narrow. These are the only adapters the feasibility spike may graduate into the implementation plan:

| Concrete adapter | Move | Resize | Rotate | Persistent source semantics |
| --- | --- | --- | --- | --- |
| Screen `add` with a literal image-like displayable, direct/literal placement, no dynamic `at`, and the spike-approved existing-transform/intrinsic-animation profile | Yes | Yes: image scale-resize | Yes | Direct position/offset plus an editor-owned inline literal `Transform` |
| Screen `imagebutton` with literal static states, direct/literal placement, and no active transform or animation | Yes | Yes: allocation resize | No | Direct position/offset and literal `xsize`/`ysize` |
| Screen `frame`, `button`, or `textbutton` with static content, direct/literal placement, and no active transform or animation | Yes | Yes: allocation resize | No | Direct position/offset and literal `xsize`/`ysize` |
| Screen `text` with static literal content, direct/literal placement, and no active transform or animation | Yes | No | No | Direct position/offset only |
| Scene image tag | No | No | No | Measure-only until the separate scene provenance spike passes |

For each rostered adapter, the general source rules are:

| Source/runtime case | Move | Resize | Rotate |
| --- | --- | --- | --- |
| Literal integer/`absolute` position with no conflicting provenance | Rewrite the same position family | Use the adapter's declared resize semantics | Use the adapter's declared transform semantics |
| Fractional `xpos`/`ypos`, `xalign`/`yalign`, or resolved local style base | Preserve the base and add/update integer offsets | Disabled | Disabled |
| Existing inline literal `Transform` | Patch supported position fields without replacing unrelated fields | Patch only adapter-approved literal scale/size fields | Patch literal rotation, pivot, `rotate_pad`, and anchor fields |
| Dynamic `at`, ATL, expression, unresolved property bag, shared inherited style, automatic-layout child, adapter absent from the roster, or transform/animation state outside that adapter's proven profile | Measure-only | Measure-only | Measure-only |

Every concrete adapter specifies:

- supported statement and displayable kinds;
- source property and accepted literal units;
- runtime override and rebinding method proven by the spike;
- pivot and anchor behavior;
- aspect-ratio policy;
- `rotate_pad` and `transform_anchor` behavior;
- offset compensation needed to keep the opposite edge or chosen pivot fixed.

Resize means allocation resize for container-like widgets and scale-resize for the image-like `add` adapter. The toolbar labels the active semantics. Any statement/operation pair absent from the roster is measure-only.

### Scene images

The existing `position_element` implementation is not an editor adapter because it replaces the active `at_list`. V1 scene-image editing requires a provenance tracker installed at launch for `show`, `scene`, and `hide`, plus a non-destructive outer override that preserves image name, attributes, layer, z-order, original transforms/ATL, and animation time.

A scene image is editable only when the tracker resolves one active image instance to one patchable source statement and a compatible transform adapter. All other scene images remain measure-only.

### Other read-only targets

Generated displayables without a source location, targets in non-writable files, ambiguous loop/use instances, and targets that disappear during analysis remain inspectable but cannot enter edit history. The UI displays the exact lock reason before a gesture begins.

## Runtime data flow

1. RenForge launches the game with the coordinator endpoint and a random session token.
2. The bridge connects to the coordinator and enables the floating RF button.
3. The user enters edit mode; the bridge builds `EditorRuntimeGraph` on the Ren'Py main thread.
4. Hover and click resolve a `RuntimeInstanceKey` without using public `scene_tree` IDs.
5. The bridge sends `analyze_target` to the coordinator.
6. The coordinator resolves `SourceKey`, full-file digest, provenance, and capability adapters.
7. The matching handles become active only after `analyze_result`.
8. A gesture applies a reversible runtime override and becomes one `EditCommand`.
9. Guides and attached values update from the resulting runtime geometry.
10. Undo, Redo, or Reset reapplies working overrides without touching source.
11. Save sends final `SourceIntent` records over the correlated coordinator channel.
12. The coordinator validates, commits, requests reload, and waits for post-reload attestation.
13. The bridge increments `reload_generation`, reconnects, resolves each `CommitResult.committed_source_key`, and reports measured bounds.
14. Only an attested match establishes the new session baseline.

Dashboard polling may display progress but is not part of commit control or approval.

## Source transaction

A minimal durable transaction journal is part of V1. It is separate from the optional V2 journal for recovering unsaved editor gestures.

Before the first live-file replacement, the coordinator creates `.renforge/transactions/<transaction_id>/` containing:

- a versioned manifest with project identity, ordered file list, transaction phase, old and staged SHA-256 values, and every old-to-committed `SourceKey` mapping;
- flushed original-file backups;
- flushed staged replacements;
- the expected post-reload geometry for each committed target.

The manifest, backups, staged files, and transaction directory are flushed before the phase becomes `prepared`.

### Commit sequence

1. Acquire a per-project RenForge transaction lock.
2. Resolve every original `SourceKey` against the current token/CST representation.
3. Compare each complete file SHA-256 with the digest captured during analysis.
4. Reject the complete save if any file is stale, target is ambiguous, or capability no longer matches.
5. Apply every source transformation in memory while preserving all untargeted bytes.
6. Produce `CommitResult` mappings from each original key to its postimage `committed_source_key`, including the new span and statement digest.
7. Build a shadow project with staged replacements at the original relative paths.
8. Parse and lint the shadow project with the project's Ren'Py 8.5.3 runtime under a bounded timeout.
9. Persist and flush the transaction journal in phase `prepared`.
10. Recalculate every live SHA-256 immediately before publication.
11. Perform a platform-appropriate atomic replace per file, rechecking that file's digest immediately before replacement and durably recording publication progress.
12. Flush affected directories where the platform provides that guarantee.
13. Request script reload and wait for `reload_generation = previous + 1`.
14. Resolve every `committed_source_key` on the new generation and compare measured bounds with expected working bounds.
15. Mark the journal `committed`, flush it, and report success only after every target is present and within one logical pixel.
16. Remove committed transaction artifacts only after the committed phase is durable.

There is no absolute multi-file atomicity against a non-cooperating external writer. RenForge minimizes the window with a project lock, durable journal, and repeated hashes, but never claims a global filesystem transaction.

### Conditional rollback and startup recovery

If publication or attestation fails, or RenForge starts with a non-committed transaction journal:

1. Validate the manifest and every stored backup/staged digest before acting.
2. For each file still matching its original digest, leave it unchanged.
3. Restore a published file only if its current SHA-256 still equals the staged digest written by RenForge.
4. If a file matches neither digest, leave it untouched and record `rollback_conflict`.
5. Persist recovery progress after each file so recovery itself is restartable.
6. After complete restoration, request one reload and wait for a second generation handshake.
7. Mark `rolled_back` only when restored source and runtime are attested.

If any rollback conflict exists, RenForge stops further writes and keeps the journal and backups for manual recovery. It never silently saves a subset and never claims restoration without a durable terminal phase.

## Failure behavior

Failures leave the overlay open and preserve the edit session in memory whenever the bridge remains connected.

Error messages identify:

- the affected displayable;
- the source file and logical span when known;
- the failed phase: analysis, conflict check, shadow validation, publication, reload handshake, attestation, or rollback;
- the next safe action: retry, reload source, reset the target, or exit without saving.

Specific behavior:

- **Coordinator unavailable:** disable all transform handles and Save; measurement remains available.
- **Incomplete transaction at startup:** keep editing disabled until conditional recovery reaches `rolled_back`, `committed`, or an explicit `rollback_conflict`.
- **Stale source:** write nothing and ask the user to reload the target or discard the conflicting edit.
- **Shadow validation failure:** write nothing and show the rejected property change plus bounded Ren'Py output.
- **Target recreation or ambiguity:** freeze that target's commands and revoke its capabilities until reanalysis succeeds.
- **Publication failure:** conditionally restore only files still carrying RenForge's staged digest.
- **Reload timeout or bounds mismatch:** conditionally roll back, reload the restored generation, and preserve the unsaved commands.
- **Rollback conflict:** stop writes, list every uncertain path and digest, and never claim that the project was restored.
- **Bridge disconnect:** freeze editor input, keep the last visible state, and disable Save until the authenticated connection returns or the session is discarded.

## Verification strategy

### Unit tests

- token/CST logical-span resolution and byte-preserving round trips;
- `SourceKey` reconstruction and `RuntimeInstanceKey` separation;
- old-to-committed `SourceKey` mapping after statement length and digest changes;
- property provenance across direct properties, aliases, inline transforms, property bags, and styles;
- float/int/`absolute` unit handling and offset compensation;
- every statement/operation adapter in the V1 capability matrix;
- pivot, anchor, aspect ratio, `rotate_pad`, and `transform_anchor` behavior;
- gesture coalescing and Undo/Redo/Reset transitions;
- deterministic selection-cycle and snap acquire/release thresholds;
- stale digest checks before staging and before each publication;
- conditional rollback, including external edits during publication or rollback;
- coordinator protocol correlation, authentication, timeout, and reconnect behavior;
- reload-generation attestation and bounds comparison.

### Integration fixtures

Add focused Ren'Py screens covering:

- explicitly positioned images, text, frames, buttons, and image buttons;
- integer, `absolute`, fractional, alignment, offset, alias, and property-bag positions;
- local and inherited styles with documented precedence;
- existing literal transforms, dynamic transforms, and ATL;
- non-zero anchors and pivots during resize and rotation;
- loops and repeated `use` instances;
- `hbox`, `vbox`, `grid`, clipping, viewport, and transformed parents;
- SL2 target recreation during and between gestures;
- scene image tags with preserved `at_list`, ATL, layer, z-order, attributes, and animation time;
- the RenForge overlay excluding its own chrome from selection.

### Transaction integration

- shadow-project parse and lint under Ren'Py 8.5.3;
- byte comparison proving all untargeted source content is unchanged;
- external writes between initial check, staging, and each atomic replace;
- process failure during a multi-file publication;
- crash and restart after each journal phase and after each individual file replacement;
- manifest corruption, missing backup, and digest mismatch during startup recovery;
- conflict during conditional rollback;
- reload timeout, missing target, and post-reload bounds mismatch.

### End-to-end scenario

Using the demo game:

1. Launch through RenForge and verify authenticated coordinator connection.
2. Enter edit mode and select an eligible displayable through `EditorRuntimeGraph`.
3. Move, resize, and rotate it with allowlisted adapters.
4. Verify guides, snapping, opacity, attached-info hiding, hover fading, and chrome exclusion.
5. Exercise Undo, Redo, Reset, target recreation, and remapping.
6. Reapply transformations and press Save code.
7. Observe shadow validation, publication, reload-generation handshake, target re-resolution, and bounds attestation.
8. Confirm that source contains only intended edits and runtime bounds match within one logical pixel.
9. Modify source externally during a second session and confirm that Save writes nothing.
10. Inject a post-publication conflict and confirm that rollback does not overwrite the external change.

## Acceptance criteria

The feature is complete when:

- the mandatory Ren'Py 8.5.3 feasibility spike passes for every adapter retained in the concrete V1 roster;
- the visual editor is available only through an authenticated RenForge development session;
- the dedicated coordinator channel, runtime graph, and source/runtime identities work independently of public MCP scene IDs;
- locked targets remain selectable and measurable while only eligible operations expose handles;
- eligible elements can be manipulated only through tested capability adapters;
- runtime overrides preserve styles, transforms, clipping, and animation state for adapter variants that admit them, while measured capability guards lock every unsupported transform/animation variant;
- all requested guides, measurements, snapping, opacity, and attached-information behaviors work without the overlay selecting or blocking itself;
- no source changes occur before Save code;
- shadow-project validation passes before publication;
- every transaction is durably journaled before its first live-file replacement;
- startup recovery and conditional rollback never overwrite a detected external change;
- the patcher maps original keys to committed successor keys;
- Save succeeds only after reload-generation and successor-key bounds attestation;
- source conflicts and validation failures modify no project file;
- Undo/Redo/Reset operate for the complete unsaved session and reset at a successful save boundary;
- the focused tests and demo-game end-to-end scenario pass.

## V2 roadmap

### Indispensable extensions

1. **Dynamic layouts:** edit `hbox`, `vbox`, `grid`, `fixed`, and viewport intent through parent adapters for spacing, padding, alignment, fill, rows/columns, and order.
2. **Multi-selection:** align, distribute, move, and resize temporary groups through composite commands and multi-file intents.
3. **Persistent constraints:** represent edge, center, gap, aspect, and min/max relationships, then compile them to native Ren'Py layout properties.
4. **Style impact graph:** index style inheritance, prefixes/groups, property bags, and interaction states so the user can choose a local override or a shared edit with full impact preview.
5. **Responsive variants:** preview multiple resolutions, aspect ratios, safe areas, and Ren'Py variants in isolated sessions with overflow assertions.
6. **Unsaved edit recovery journal:** recover uncommitted runtime gestures after a crash and provide source plus visual diffs before large saves. This extends, but does not replace, the mandatory V1 source-transaction journal.

### Powerful but achievable

1. **Guided conversion:** offer previewed recipes that convert a locked expression or layout into a local offset, screen parameter, parent constraint, or inline transform.
2. **`screen`/`use` component overrides:** introduce stable parameters or IDs and patch the selected callsite without changing every instance.
3. **Restricted ATL timeline:** edit literal position, scale, and rotation keyframes while preserving unknown expressions opaquely.
4. **Constraint presets:** provide versioned safe-area, thirds, stack, ratio, and project-spacing presets that compile to native properties.
5. **Pragmatic collaboration:** add presence, `SourceKey` soft locks, leases, shared previews, and digest-based conflict detection without CRDT complexity.
6. **Supervised MCP agents:** let agents measure, propose, preview, and discard `SourceIntent` batches while keeping final commit under explicit user control.

### Experimental

1. **Global responsive solver:** optimize weighted constraints across resolutions and content, presenting several previewed solutions without auto-commit.
2. **Intent-level CRDT collaboration:** synchronize `SourceIntent` operations rather than raw text and compile them centrally through the CST patcher.
3. **Semi-autonomous MCP agents:** run bounded plan → intent → shadow lint → isolated preview → measurement loops with final human approval.
4. **Layout inference:** detect geometric patterns and propose conversion from absolute placement to native layouts or constraints with confidence and pixel/source diffs.
5. **Localization adaptation:** generate long-text and RTL/CJK scenarios, detect overflow, and propose variant-specific constraints or style changes.
