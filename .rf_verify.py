"""Live proof: top panel + no auto-advance while the editor is open."""

from __future__ import annotations

import io
import shutil
import time
from pathlib import Path

from PIL import Image

from renforge.editor_live_common import DEMO_COPY_IGNORE

ROOT = Path(__file__).resolve().parent
DEMO = ROOT / "examples" / "demo_game"
WORK = ROOT / ".rf_verify_work" / "demo"
OUT = ROOT / ".rf_verify_out"

EDITOR = "_renforge_editor_overlay"


def shot(session, name: str, *, band: bool = True) -> None:
    raw = session.client.screenshot()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}_full.png").write_bytes(raw)
    if band:
        image = Image.open(io.BytesIO(raw))
        image.crop((0, 0, image.width, 70)).save(OUT / f"{name}_band.png")


SCREENS_EXPR = '[str(x) for x in sorted(renpy.get_showing_tags(layer="screens"))]'


def diag(client, moment: str) -> None:
    state = client.get_state()
    print(
        f"DIAG [{moment}]"
        f" label={state.get('current_label')!r}"
        f" dialogue={state.get('dialogue')!r}"
        f" screens={client.eval_expr(SCREENS_EXPR)!r}"
    )


def main() -> int:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(DEMO, WORK, ignore=DEMO_COPY_IGNORE)
    shutil.rmtree(WORK / ".renforge", ignore_errors=True)

    sdk = get_or_install_sdk("8.5.3", project_root=WORK)
    project = RenpyProject(WORK)
    failures: list[str] = []

    def check(label: str, ok: bool, detail: object = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {label}  {detail}")
        if not ok:
            failures.append(label)

    with launch_with_bridge(sdk, project, startup_timeout=120, editor=True) as session:
        client = session.client
        for _ in range(80):
            if client.inspect_screen("_renforge_editor_launcher").get("active"):
                break
            time.sleep(0.25)
        client.click_element(text="RF", exact=True, screen="_renforge_editor_launcher")
        for _ in range(80):
            if client.inspect_screen(EDITOR).get("active"):
                break
            time.sleep(0.05)
        time.sleep(1.0)
        shot(session, "1_idle")
        diag(client, "after activation")

        # ── Story must stay put while the editor is open ─────────────────────
        # The HUD timer used to call Function(status_code) every 0.25s; that
        # returned a string, ended the interaction, and auto-advanced dialogue.
        baseline_label = client.get_state().get("current_label")
        baseline_line = client.get_state().get("dialogue")
        seen_lines: list[str] = []
        hold_deadline = time.time() + 2.5
        while time.time() < hold_deadline:
            line = client.get_state().get("dialogue")
            if line and (not seen_lines or seen_lines[-1] != line):
                seen_lines.append(line)
            time.sleep(0.2)
        hold_label = client.get_state().get("current_label")
        hold_line = client.get_state().get("dialogue")
        check(
            "no auto-advance while editor open",
            hold_label == baseline_label and hold_line == baseline_line and len(seen_lines) <= 1,
            f"label {baseline_label!r}->{hold_label!r} line {baseline_line!r}->{hold_line!r} seen={seen_lines}",
        )

        # ── The chip renders with no selection, carries the caret, and opens ──
        chip = client.eval_expr(
            f"renpy.get_widget('{EDITOR}', 'rf_toolbar_jump') is not None"
        )
        check("chip visible without a selection", chip is True, chip)

        opacity_before = client.eval_expr("_renforge_editor_opacity_label()")
        check("opacity readout present", bool(opacity_before), opacity_before)

        client.click_element(id="rf_toolbar_jump", screen=EDITOR)
        time.sleep(0.6)
        diag(client, "after chip click")
        opened = client.eval_expr("_renforge_editor_jump_open()")
        menu = client.eval_expr(
            f"renpy.get_widget('{EDITOR}', 'rf_toolbar_jump_menu') is not None"
        )
        check("dropdown opens", opened is True, opened)
        check("dropdown menu rendered", menu is True, menu)
        targets = client.eval_expr("_renforge_editor_jump_targets()")
        check("jump targets listed", isinstance(targets, list) and "summit" in targets, targets)
        check(
            "Ren'Py internals filtered out",
            isinstance(targets, list)
            and not {"save_screen", "load_screen", "main_menu_screen"} & set(targets),
            targets,
        )
        shot(session, "2_dropdown")

        # ── Selecting a label really warps the running game ──────────────────
        summit_line = "The beacon tower stands empty, its great bowl cold."
        diag(client, "before summit click")
        clicked = client.click_element(text="summit", exact=True, screen=EDITOR)
        print("DIAG click reply:", {k: v for k, v in clicked.items() if k != "element"})
        seen = []
        deadline = time.time() + 3.0
        while time.time() < deadline and summit_line not in seen:
            line = client.get_state().get("dialogue")
            if line and (not seen or seen[-1] != line):
                seen.append(line)
            time.sleep(0.05)
        check("jump landed on summit", summit_line in seen, seen)
        check(
            "editor survived the jump",
            client.eval_expr("_renforge_editor_is_active()") is True,
        )
        check("menu closed after jump", client.eval_expr("_renforge_editor_jump_open()") is False)
        time.sleep(0.5)
        shot(session, "3_after_jump")

        # After the jump the story must also stay put (no residual advance).
        post_label = client.get_state().get("current_label")
        post_line = client.get_state().get("dialogue")
        time.sleep(1.5)
        check(
            "no auto-advance after jump",
            client.get_state().get("current_label") == post_label
            and client.get_state().get("dialogue") == post_line,
            f"{post_label!r}/{post_line!r} -> "
            f"{client.get_state().get('current_label')!r}/{client.get_state().get('dialogue')!r}",
        )

        # ── Opacity readout tracks the − / + buttons ─────────────────────────
        client.click_element(id="rf_opacity_down", screen=EDITOR)
        time.sleep(0.5)
        opacity_after = client.eval_expr("_renforge_editor_opacity_label()")
        check(
            "opacity readout updates",
            opacity_after != opacity_before,
            f"{opacity_before} -> {opacity_after}",
        )
        client.click_element(id="rf_opacity_up", screen=EDITOR)
        time.sleep(0.4)

        # ── The relabelled Guides pill still toggles canvas decorations ──────
        guides_before = client.eval_expr("_renforge_editor_tools_visible()")
        client.click_element(id="rf_tools", screen=EDITOR)
        time.sleep(0.5)
        guides_after = client.eval_expr("_renforge_editor_tools_visible()")
        check(
            "Guides pill toggles",
            guides_before != guides_after,
            f"{guides_before} -> {guides_after}",
        )
        client.click_element(id="rf_tools", screen=EDITOR)
        time.sleep(0.4)

        errors = client.eval_expr("len(_renforge_editor_state().save_last_error or '')")
        check("no editor error recorded", not errors, errors)
        shot(session, "4_final")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
