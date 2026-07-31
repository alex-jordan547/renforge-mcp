# RenForge editor launcher by default

## Goal
Make the in-game `RF` launcher available on every RenPy launch, without requiring dashboard changes, and give the demo VN real editable controls in its normal story cycle.

## Decisions
- Editor injection is unconditional at the RenForge launch boundary.
- The legacy `editor` input is canonicalized to the editor-capable session; `editor=false` must not suppress injection or create a reuse mismatch.
- RenPy starts with the small `RF` launcher visible and the full editor overlay inactive.
- Clicking `RF` activates the existing overlay in-game; Exit/Escape hides it and returns to the launcher without restarting RenPy.
- The dashboard is not changed for this feature. Its current launch payload remains accepted.
- The demo keeps its normal narrative flow and adds real `textbutton` controls with real RenPy actions and explicit source positions; no separate editor sandbox is added.

## Runtime contract
A launch must initialize all editor dependencies together: injected editor script, `EditorCoordinator`, editor endpoint, and editor environment. A second launch request with `editor=false` must reuse the editor-capable session rather than return `SESSION_MODE_MISMATCH`.

The RF launcher is editor-owned and excluded from selectable game targets. The overlay must preserve existing Save, Undo, Redo, drag, and source-commit behavior.

## Demo acceptance path
1. Launch `examples/demo_game`.
2. Reach the normal village-gate story choice.
3. Interact with the real positioned buttons and observe their RenPy actions/branching.
4. Click `RF` in the game window.
5. Select and move a real demo button, use Undo/Redo, and save.
6. Confirm the story controls still execute their intended actions after the editor interaction.

## Verification
- Unit/protocol coverage proves false legacy launch payloads canonicalize to an editor-capable session and repeated launch does not mismatch.
- Existing editor coordinator/source/runtime tests remain green.
- A live RenPy scenario proves the launcher is visible, the overlay can activate, real demo controls are selectable, and Save/Reload still works.
