# RenForge In-Game Visual Editor Design

**Date:** 2026-07-29  
**Status:** Approved design, pending implementation plan

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

Hovering outlines the deepest inspectable displayable under the pointer. Clicking selects it. Repeated clicks at the same point cycle through overlapping displayables and their parents.

A selected displayable shows:

- its bounds and transform handles;
- its origin and anchor;
- width, height, source location, and current rotation;
- edge, center, and baseline guides where applicable;
- pixel distances to relevant nearby bounds;
- a capability state indicating which transformations can be saved.

Each operation has an independent capability. A displayable may be movable but not resizable or rotatable. Unsupported handles are hidden or disabled with a short explanation.

### Manipulation

The editor supports three modes:

- **Move:** pointer drag, one-pixel arrow-key nudging, and ten-pixel nudging with Shift.
- **Resize:** edge and corner handles for supported displayables.
- **Rotate:** a rotation handle with the angle displayed beside the selection.

Snapping uses visible edges, centers, baselines, and configured stage boundaries. Holding Shift temporarily disables snapping during a pointer gesture.

A continuous pointer gesture becomes one Undo/Redo command rather than one command per rendered frame. **Reset** restores every edited displayable to the state established by the last successful save or by session start. A successful save establishes a new history baseline; Undo does not cross a save boundary.

### Visibility of editor tools

The toolbar contains a global **Tools opacity** control. It changes the opacity of the toolbar, guides, handles, measurements, floating RF button, and attached information without changing the game or selected displayable.

Selection information is rendered in a compact label attached to the displayable rather than in a permanent side panel. The label:

- can be hidden or shown from the toolbar;
- fades to near-transparent while the pointer is over it;
- repositions within the game bounds when its preferred location would be clipped;
- never changes the layout of the game being edited.

The selected displayable remains fully visible at every editor-opacity setting.

### Saving

**Save code** is enabled only when the session contains at least one source-safe transformation. The toolbar exposes a compact, non-blocking summary of changed elements and properties; pressing Save commits immediately without a second dashboard or modal confirmation. The complete valid edit session is committed, or no source file changes.

## Architecture

The feature is split into three independently testable components.

### 1. In-game editor overlay

The bridge injects a development-only Ren'Py screen above normal game content. It is responsible for:

- hit testing and selection;
- rendering guides, measurements, handles, and toolbar controls;
- pointer and keyboard gesture handling;
- applying temporary runtime transforms;
- presenting capability and save results.

The overlay never opens or writes `.rpy` files.

### 2. Edit session

A bridge-side session owns the original state, working state, and command history. Its core records are:

```text
EditTarget
  runtime_id
  kind: screen_displayable | scene_image
  source_file
  source_line
  file_digest
  screen_name or image_tag
  statement_kind
  instance_identity
  capabilities: move | resize | rotate

EditCommand
  target_id
  operation
  before_transform
  after_transform

SourceIntent
  target_id
  source_identity
  changed_properties
  expected_file_digest
```

`runtime_id` is valid only for the current interaction frame. Source identity is based on the Ren'Py AST location and a stable statement identity, not on an ordinal scene-tree ID. `file_digest` is the digest of the complete source file captured when the target becomes editable; V1 deliberately rejects Save after any external change to that file, even outside the target statement.

Ren'Py 8.5.3 attaches `(filename, line)` metadata to runtime SL2 displayables through `_location`. RenForge uses that metadata as the initial source mapping, then verifies the statement kind, screen context, instance identity, and file digest before granting edit capabilities.

If several runtime instances resolve to the same source statement and cannot be distinguished deterministically, each instance is read-only in V1.

### 3. RenForge source patcher

The patcher is the only component allowed to mutate project files. It accepts `SourceIntent` records and performs capability checks, stale-source checks, source transformation, staged validation, commit, and rollback.

The patcher edits only the targeted properties and preserves surrounding formatting and unrelated source text. It does not reformat a complete `.rpy` file.

## Target eligibility

Eligibility is calculated when an element is selected, before any transform handle is enabled.

### Screen displayables

A screen displayable is eligible for an operation when:

- its runtime object maps to one unambiguous SL2 source statement;
- the operation can be represented by supported Ren'Py properties on that statement;
- the relevant source values are literals or another explicitly supported form;
- editing the property does not require restructuring a parent layout.

For movement, RenForge preserves the existing positioning family. Examples:

- numeric `xpos` and `ypos` remain `xpos` and `ypos`;
- alignment-based placement retains `xalign` and `yalign`, with explicit offsets carrying the pixel delta;
- existing transform position properties remain transform position properties.

Resize and rotation capabilities are granted separately. RenForge may add supported size or rotation properties to an otherwise eligible statement only when doing so has a deterministic Ren'Py meaning for that statement kind.

### Scene images

Scene image tags may be manipulated only when RenForge can resolve the active image to a unique source show/transform statement and patch its transform safely. Otherwise the existing runtime positioning capability remains available to MCP tools, but the visual editor exposes the image as measure-only.

### Read-only targets

The following remain inspectable but cannot enter the edit history:

- children positioned exclusively by automatic parent layout;
- positions, sizes, or rotations produced by unsupported expressions;
- repeated instances with a shared ambiguous source statement;
- generated or synthetic displayables without a source location;
- targets whose source file is not writable.

The UI displays the exact reason for the lock before the user attempts a gesture.

## Runtime data flow

1. The user enables edit mode.
2. The overlay obtains the current scene tree and hit-test data from the bridge.
3. Selection resolves a runtime displayable to an `EditTarget`.
4. The bridge calculates operation capabilities from runtime and source metadata.
5. A gesture updates the target only in the running game.
6. The edit session coalesces the gesture into one `EditCommand`.
7. Guides and attached values update from the resulting runtime bounds.
8. Undo, Redo, or Reset reapplies working transforms without touching source.
9. Save converts the final working state into `SourceIntent` records.
10. The patcher validates and commits the source transaction.
11. RenForge reloads the script and refreshes the scene tree.
12. The reloaded values become the session baseline.

The existing bridge event buffer and dashboard polling channel may transport edit-session events, but the dashboard is not part of the save approval path.

## Source transaction

A save follows this sequence:

1. Resolve every intent again against the current source.
2. Compare each current full-file digest with the digest captured for the edit target.
3. Reject the complete save if any source is stale, ambiguous, or no longer eligible.
4. Apply every source transformation in memory.
5. Parse and lint all staged `.rpy` contents before replacing originals.
6. Write staged files beside their originals.
7. Replace each affected file with a per-file atomic rename only after every staged file passes validation.
8. Request a Ren'Py script reload.
9. If replacement or reload fails, restore the captured originals and reload the restored source.
10. Report success only after the running game reflects the saved values.

A transaction spanning several files is prevalidated as a unit. Per-file replacement is atomic; RenForge also retains the original contents until the complete multi-file commit and reload succeeds so it can roll back a partially completed commit.

RenForge never silently saves a subset. Because non-patchable targets are locked before manipulation, a normal session contains only valid intents. A save failure therefore represents a real conflict or environmental error, not an unsupported element discovered late.

## Failure behavior

Failures leave the overlay open and preserve the edit session in memory.

Error messages identify:

- the affected displayable;
- the source file and line when known;
- the failed phase: resolution, conflict check, validation, write, reload, or rollback;
- the next safe action: retry, reload source, reset the target, or exit without saving.

Specific behavior:

- **Stale source:** write nothing and ask the user to reload the target or discard the conflicting edit.
- **Validation failure:** write nothing and show the generated property change that failed.
- **Write failure:** restore any already replaced file before reporting failure.
- **Reload failure:** restore all original files and attempt one reload of the restored source.
- **Rollback failure:** stop further writes and report a critical error with every affected path; never claim that the project was restored.
- **Bridge disconnect:** freeze editor input, keep the last visible state, and disable Save until the connection returns or the session is discarded.

## Verification strategy

### Unit tests

- source-location and statement-identity resolution;
- capability calculation for move, resize, and rotate;
- preservation of `xpos`/`ypos`, alignment-plus-offset, and existing transform families;
- addition of deterministic size or rotation properties only for supported statement kinds;
- rejection of automatic layouts, unsupported expressions, generated targets, and ambiguous instances;
- gesture coalescing and Undo/Redo/Reset transitions;
- stale digest detection;
- multi-intent staging, validation, rollback, and failure reporting.

### Integration fixtures

Add focused Ren'Py screens covering:

- explicitly positioned images, text, frames, buttons, and image buttons;
- alignment with explicit offsets;
- existing transforms;
- resizable and non-resizable targets;
- rotatable and non-rotatable targets;
- loops and repeated `use` instances;
- `hbox`, `vbox`, and `grid` children;
- expression-driven properties;
- scene image tags with resolvable and unresolvable source statements.

### End-to-end scenario

Using the demo game:

1. Launch through RenForge and enter edit mode from the floating RF button.
2. Select an eligible displayable.
3. Move, resize, and rotate it.
4. Verify guides, pixel values, snapping, opacity, attached-info hiding, and hover fading.
5. Exercise Undo, Redo, and Reset.
6. Reapply the transformations and press Save code.
7. Observe a successful script reload.
8. Confirm that runtime bounds after reload match the pre-save working bounds within one logical pixel and that the `.rpy` contains only the intended property edits.
9. Modify a source statement externally during a second session and confirm that Save writes nothing and preserves the runtime edit session.

## Acceptance criteria

The feature is complete when:

- the visual editor is available only in RenForge development sessions;
- eligible elements can be moved, resized, and rotated in the running game;
- unsupported elements are identified and locked before manipulation;
- all requested guides, measurements, snapping, opacity, and attached-information behaviors work in the game window;
- no source changes occur before Save code;
- Save performs guarded, all-or-nothing source updates and reloads Ren'Py;
- source conflicts and validation failures modify no project file;
- successful saves persist visually identical bounds after reload;
- Undo/Redo/Reset operate for the complete unsaved session and reset at a successful save boundary;
- the focused tests and demo-game end-to-end scenario pass.
