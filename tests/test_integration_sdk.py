"""Live Ren'Py SDK integration tests.

Opt-in: these download/use a real Ren'Py SDK and invoke it. Enable with::

    RENFORGE_SDK_TESTS=1 pytest tests/test_integration_sdk.py

Optionally pin the version with ``RENFORGE_SDK_VERSION`` (default: the
``DEFAULT_RENPY_VERSION`` RenForge ships with).
Each test runs against a temp copy of the demo so the committed one is never
polluted with compiled ``.rpyc``/cache artifacts.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RENFORGE_SDK_TESTS"),
    reason="set RENFORGE_SDK_TESTS=1 to run live Ren'Py SDK integration tests",
)

_DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo_game"


@pytest.fixture(scope="module")
def sdk():
    from renforge.sdk import DEFAULT_RENPY_VERSION, get_or_install_sdk
    from renforge.editor_live_common import DEMO_COPY_IGNORE

    return get_or_install_sdk(os.environ.get("RENFORGE_SDK_VERSION", DEFAULT_RENPY_VERSION))


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    # Never inherit Ren'Py bytecode/cache from a previous local run: stale
    # compiled scripts can make ``--warp`` skip a fixture label entirely.
    shutil.copytree(_DEMO, destination, ignore=DEMO_COPY_IGNORE)
    return destination


def _add_hover_fixtures(demo_copy: Path) -> None:
    """Add an ImageButton screen and offset idle/hover sprites for SDK E2E."""
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")

    images_dir = demo_copy / "game" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    idle = image_module.new("RGBA", (100, 100), (0, 0, 0, 0))
    hover = image_module.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(12, 36):
        for y in range(12, 36):
            idle.putpixel((x, y), (220, 40, 40, 255))
    for x in range(40, 64):
        for y in range(34, 58):
            hover.putpixel((x, y), (220, 40, 40, 255))
    idle.save(images_dir / "renforge_sdk_idle.png")
    hover.save(images_dir / "renforge_sdk_hover.png")

    fixture = demo_copy / "game" / "renforge_sdk_fixtures.rpy"
    existing = fixture.read_text(encoding="utf-8") if fixture.exists() else ""
    if "renforge_sdk_imagebutton_fixture" not in existing:
        fixture.write_text(
            existing
            + '''

default renforge_sdk_button_clicks = 0

screen renforge_sdk_imagebutton_fixture():
    modal True
    zorder 200
    key "dismiss" action NullAction()
    frame:
        xalign 0.5
        yalign 0.5
        background None
        imagebutton:
            idle "renforge_sdk_idle"
            hover "renforge_sdk_hover"
            action SetVariable("renforge_sdk_button_clicks", renforge_sdk_button_clicks + 1)
''',
            encoding="utf-8",
        )


def _save_capture(project_root: Path, name: str, png: bytes) -> Path:
    import hashlib
    import os
    import tempfile

    capture_dir = project_root / ".renforge" / "captures"
    capture_dir.mkdir(parents=True, exist_ok=True)
    target = (capture_dir / f"{name}.png").resolve()
    target.relative_to(capture_dir.resolve())
    with tempfile.NamedTemporaryFile(dir=capture_dir, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(png)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    assert hashlib.sha256(png).hexdigest()
    return target


def _add_sdk_fixtures(demo_copy: Path) -> dict[str, str]:
    """Add opt-in-only runtime fixtures without changing the public demo.

    The ``renforge_sdk_custom`` screen is driven directly through the bridge
    (``renpy.show_screen``) rather than a ``--warp`` target: warping to a
    standalone label is non-deterministic — Ren'Py intermittently ignores the
    warp and starts at ``start`` instead — so only the input fixture, which
    needs a real ``renpy.input`` interaction that cannot be faked, is reached
    by warp.
    """
    fixture = demo_copy / "game" / "renforge_sdk_fixtures.rpy"
    fixture.write_text(
        '''default renforge_sdk_input_value = ""

screen renforge_sdk_custom(title, amount):
    modal True
    key "dismiss" action NullAction()
    default status = "ready"
    add Solid("#123456", xsize=120, ysize=80) xpos 30 ypos 40
    text title
    text str(amount)
    text status
    textbutton "Click" action NullAction()

label renforge_sdk_input_fixture:
    $ renforge_sdk_input_value = renpy.input("SDK name?", default="")
    pause
    return
''',
        encoding="utf-8",
    )
    return {
        "input": "renforge_sdk_input_fixture",
        "screen": "renforge_sdk_custom",
    }


def test_lint_demo_is_clean(sdk, demo_copy: Path) -> None:
    from renforge.project import RenpyProject
    from renforge.util.subprocess import run_command

    project = RenpyProject(demo_copy)
    result = run_command(project.lint_command(sdk), timeout=180)

    assert "lint report" in result.stdout.lower(), result.stdout + result.stderr


def test_native_dump_returns_authoritative_labels(sdk, demo_copy: Path) -> None:
    from renforge.dump import normalize_definitions, run_native_dump
    from renforge.project import RenpyProject

    raw = run_native_dump(sdk, RenpyProject(demo_copy), timeout=180)
    labels = {d["name"] for d in normalize_definitions(raw) if d["kind"] == "label"}

    assert labels == {
        "main_menu",
        "start",
        "village_gate",
        "stay_home",
        "crossroads",
        "forest_path",
        "hidden_shrine",
        "cave_mouth",
        "cave_depths",
        "ridge_path",
        "wisp_advice",
        "summit",
        "ending_light",
        "ending_ash",
        "ending_home",
        "credits",
    }


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_bridge_ping_state_and_screenshot(sdk, demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        assert session.client.ping().get("pong") is True

        session.client.set_var("renforge_seen", "by-test")
        assert session.client.get_var("renforge_seen") == "by-test"

        state = session.client.get_state()
        assert "variables" in state and "current_label" in state

        png = session.client.screenshot()
        assert png.startswith(b"\x89PNG") and len(png) > 1000

        # Driving: the game starts at label "start" showing its first line;
        # advancing should let us capture dialogue via pushed events.
        says = []
        for _ in range(6):
            for event in session.client.poll_events().get("events", []):
                if event["type"] == "say":
                    says.append(event["what"])
            session.client.advance()
            time.sleep(1.0)
        assert any(
            s == "The village of Emberfall sleeps under a bruised dawn sky."
            for s in says
        ), says


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_reload_script_keeps_bridge_responsive(sdk, demo_copy: Path) -> None:
    """reload_script restores renpy.config from backup, wiping the bridge's
    registered callbacks; the re-run init block must re-register them on the
    surviving listener so the bridge answers again after the reload."""
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        assert session.client.ping().get("pong") is True

        reply = session.client.control("reload_script")
        assert reply.get("ok") is True, reply

        # Requests issued while the engine reloads may time out; the bridge
        # must come back on its own once init blocks have re-run.
        deadline = time.time() + 60.0
        last_error = None
        while time.time() < deadline:
            try:
                if session.client.ping().get("pong") is True:
                    break
            except Exception as exc:
                last_error = exc
            time.sleep(0.5)
        else:
            pytest.fail("bridge never answered after reload_script: %r" % (last_error,))

        # Not just alive: requests drain through the re-registered callbacks.
        state = session.client.get_state()
        assert "current_label" in state and "variables" in state
        png = session.client.screenshot()
        assert png.startswith(b"\x89PNG")


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_screen_introspection_reports_default_say_screen(sdk, demo_copy: Path) -> None:
    """Exercise inspect_screen against Ren'Py's real ScreenDisplayable."""
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        inspected = None
        for _ in range(20):
            inspected = session.client.inspect_screen("say")
            if inspected.get("active"):
                break
            time.sleep(0.25)

        assert inspected is not None
        assert inspected["ok"] is True, inspected
        assert inspected["active"] is True, inspected
        assert inspected["name"] == "say", inspected
        assert inspected["layer"] == "screens", inspected
        assert isinstance(inspected["scope"], dict), inspected
        assert isinstance(inspected["arguments"], dict), inspected
        # Ren'Py's built-in say screen is shown with its resolved parameters as
        # keyword arguments, so the live ScreenDisplayable retains them in both
        # its scope and its ``_kwargs``. Their values (``who`` is None for
        # narration, ``what`` may be empty on the first frame) are transient, so
        # assert on presence, not content.
        assert "what" in inspected["scope"], inspected
        assert "who" in inspected["scope"], inspected
        assert inspected["arguments"]["args"] == [], inspected
        assert set(inspected["arguments"]["kwargs"]) == {"who", "what"}, inspected


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_menu_selection_takes_the_branch(sdk, demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        # Advance until the menu's choices appear on screen.
        focus_elements = []
        for _ in range(6):
            focus_elements = [
                control
                for control in session.client.list_ui_elements()
                if control.get("screen") == "village_gate_choices"
            ]
            if len(focus_elements) >= 2:
                break
            session.client.advance()
            time.sleep(1.0)

        assert any(
            control.get("id") == "demo_lantern_take"
            for control in focus_elements
        ), focus_elements
        assert any(
            control.get("id") == "demo_lantern_decline"
            for control in focus_elements
        ), focus_elements
        for control in focus_elements:
            bounds = control.get("bounds") or {}
            assert (
                isinstance(bounds.get("x"), int)
                and isinstance(bounds.get("y"), int)
                and isinstance(bounds.get("width"), int)
                and isinstance(bounds.get("height"), int)
                and bounds["width"] > 0
                and bounds["height"] > 0
            ), control
            assert control.get("screen") == "village_gate_choices", control

        selected = session.client.select_choice(text="Take the lantern and go.")
        assert selected["ok"] is True, selected
        assert selected["text"] == "Take the lantern and go."
        assert isinstance(selected["x"], int) and isinstance(selected["y"], int)
        assert selected.get("ended_interaction") is True
        time.sleep(1.5)

        assert session.client.get_var("lantern") is True
        assert session.client.get_var("courage") == 1
        assert session.client.eval_expr("renpy.test.testmouse.mouse_pos") is None
        # The branch dialogue is still displayed inside ``village_gate``;
        # the jump to ``crossroads`` follows after that line is dismissed.
        assert session.client.get_state()["current_label"] == "village_gate"

@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_menu_selection_continues_to_next_menu(sdk, demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90, editor=True) as session:
        client = session.client
        for _ in range(40):
            launcher = client.inspect_screen("_renforge_editor_launcher")
            if launcher.get("active") is True:
                break
            time.sleep(0.25)
        assert launcher.get("active") is True, launcher
        assert client.click_element(text="RF", exact=True, screen="_renforge_editor_launcher").get("ok") is True
        for _ in range(40):
            overlay = client.inspect_screen("_renforge_editor_overlay")
            if overlay.get("active") is True:
                break
            time.sleep(0.05)
        assert overlay.get("active") is True, overlay
        assert client.request("editor_task0_key", {"key": "escape", "repeat": 1}).get("ok") is True
        for _ in range(6):
            focus_elements = [
                control
                for control in client.list_ui_elements()
                if control.get("screen") == "village_gate_choices"
            ]
            if focus_elements:
                break
            client.advance()
            time.sleep(0.5)
        selected = client.select_choice(text="Take the lantern and go.")
        assert selected["ok"] is True, selected

        choices = []
        for _ in range(8):
            choices = [choice for choice in client.list_choices() if choice.get("screen") == "choice"]
            if choices:
                break
            client.advance()
            time.sleep(0.5)

        assert any(choice["text"] == "Cut through the deep woods." for choice in choices), choices
        assert any(choice["text"] == "Climb along the ridge." for choice in choices), choices
        selected = client.select_choice(text="Climb along the ridge.")
        assert selected["ok"] is True, selected

        choices = []
        for _ in range(8):
            choices = [choice for choice in client.list_choices() if choice.get("screen") == "choice"]
            if choices:
                break
            client.advance()
            time.sleep(0.5)

        assert any(choice["text"] == "Shield the lantern from the wind." for choice in choices), choices
        assert any(choice["text"] == "Grip the rocks with both hands." for choice in choices), choices


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_demo_control_supports_editor_save(sdk, demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90, editor=True) as session:
        client = session.client
        for _ in range(40):
            launcher = client.inspect_screen("_renforge_editor_launcher")
            if launcher.get("active") is True:
                break
            time.sleep(0.1)
        assert launcher.get("active") is True, launcher
        assert client.click_element(text="RF", exact=True, screen="_renforge_editor_launcher").get("ok") is True
        for _ in range(40):
            overlay = client.inspect_screen("_renforge_editor_overlay")
            if overlay.get("active") is True:
                break
            time.sleep(0.05)
        assert overlay.get("active") is True, overlay

        for _ in range(8):
            controls = [
                control
                for control in client.list_ui_elements()
                if control.get("screen") == "village_gate_choices"
            ]
            if controls:
                break
            client.advance()
            time.sleep(0.4)
        button = next(control for control in controls if control.get("id") == "demo_lantern_take")
        client.request("editor_task0_start", {"screen": "village_gate_choices"})
        bounds = button["bounds"]
        selected = client.request(
            "editor_task0_select",
            {"x": int(bounds["x"]) + 5, "y": int(bounds["y"]) + 5},
        )
        assert selected["ok"] is True, selected

        status = {}
        for _ in range(120):
            status = client.request("editor_task0_status")
            if status.get("current_analysis_id") or status.get("selected_lock_reason") not in (None, "ANALYZING"):
                break
            time.sleep(0.1)
        assert status.get("selected_lock_reason") is None, status
        assert status.get("current_analysis_id"), status

        nudged = client.request("editor_task0_key", {"key": "right", "repeat": 1})
        assert nudged.get("ok") is True, nudged
        for _ in range(40):
            status = client.request("editor_task0_status")
            if status.get("save_enabled") is True:
                break
            time.sleep(0.1)
        assert status.get("save_enabled") is True, status

        saved = client.click_element(id="rf_save", screen="_renforge_editor_overlay")
        assert saved.get("ok") is True, saved
        for _ in range(600):
            status = client.request("editor_task0_status")
            if not status.get("save_in_progress"):
                break
            time.sleep(0.25)
        assert status.get("status_code") == "reload_committed", status

@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_send_input_traverses_real_renpy_input(sdk, demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.navigation import resolve_warp_target
    from renforge.project import RenpyProject

    labels = _add_sdk_fixtures(demo_copy)
    warp = resolve_warp_target(str(demo_copy), labels["input"])
    assert warp["ok"] is True, warp

    # Ren'Py's --warp intermittently ignores a bare ``label`` node and starts at
    # ``start`` instead; warping to the first executable statement inside the
    # label — the ``$ renpy.input(...)`` line, immediately after it — resumes
    # execution there deterministically.
    file_part, _, line_part = warp["target"].rpartition(":")
    warp_target = "%s:%d" % (file_part, int(line_part) + 1)

    with launch_with_bridge(
        sdk,
        RenpyProject(demo_copy),
        warp=warp_target,
        startup_timeout=90,
    ) as session:
        client = session.client
        for _ in range(40):
            if client.eval_expr("renpy.get_screen('input') is not None"):
                break
            time.sleep(0.25)
        else:
            pytest.fail("fixture renpy.input screen never became active")

        sent = client.send_input(text="Alex", submit=True)
        assert sent == {
            "ok": True,
            "mode": "text",
            "characters": 4,
            "submitted": True,
        }

        for _ in range(40):
            if client.get_var("renforge_sdk_input_value") == "Alex":
                break
            time.sleep(0.25)
        assert client.get_var("renforge_sdk_input_value") == "Alex"


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_screen_introspection_reports_custom_fixture(sdk, demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    labels = _add_sdk_fixtures(demo_copy)

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        # Show the custom screen deterministically through the bridge with two
        # positional args and let its interaction restart so it renders.
        session.client.eval_expr(
            'renpy.show_screen("%s", "fixture-title", 7)' % labels["screen"]
        )
        session.client.eval_expr("renpy.restart_interaction()")

        inspected = None
        for _ in range(40):
            inspected = session.client.inspect_screen("renforge_sdk_custom")
            if inspected.get("active"):
                break
            time.sleep(0.25)

        assert inspected is not None
        assert inspected["ok"] is True, inspected
        assert inspected["active"] is True, inspected
        assert inspected["name"] == "renforge_sdk_custom", inspected
        assert inspected["layer"] == "screens", inspected
        assert inspected["scope"]["title"] == "fixture-title", inspected
        assert inspected["scope"]["amount"] == 7, inspected
        assert inspected["scope"]["status"] == "ready", inspected
        assert inspected["arguments"] == {
            "args": ["fixture-title", 7],
            "kwargs": {},
        }, inspected


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_named_save_state_round_trip(sdk, demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        client = session.client
        focus_elements = []
        for _ in range(8):
            focus_elements = [
                control
                for control in client.list_ui_elements()
                if control.get("screen") == "village_gate_choices"
            ]
            if focus_elements:
                break
            client.advance()
            time.sleep(0.5)
        assert any(control.get("id") == "demo_lantern_take" for control in focus_elements), focus_elements
        selected = client.select_choice(text="Take the lantern and go.")
        assert selected["ok"] is True, selected
        time.sleep(1.0)
        assert client.get_var("courage") == 1

        saved = client.save_slot("branch-a", extra_info="before menu")
        assert saved == {
            "ok": True,
            "slot": "branch-a",
            "extra_info": "before menu",
        }

        listed = client.list_slots(regexp="branch")
        assert listed["ok"] is True
        branch = next(slot for slot in listed["slots"] if slot["name"] == "branch-a")
        assert branch["extra_info"] == "before menu"
        assert isinstance(branch["mtime"], (int, float))

        client.set_var("courage", 99)
        loaded = client.load_slot("branch-a")
        assert loaded == {
            "ok": True,
            "slot": "branch-a",
            "restored_label": "village_gate",
        }

        for _ in range(20):
            try:
                if client.get_var("courage") == 1:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        assert client.get_var("courage") == 1

        missing = client.load_slot("missing-slot")
        assert missing == {
            "ok": False,
            "error": "save slot not found: missing-slot",
        }


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="autopilot needs a display (set DISPLAY, or run under xvfb)")
def test_autopilot_covers_all_labels(sdk, demo_copy: Path) -> None:
    from renforge.autopilot import autopilot
    from renforge.project import RenpyProject

    report = autopilot(sdk, RenpyProject(demo_copy), max_runs=8, max_steps=30, settle=0.5)

    assert report["ok"] is True
    assert report["coverage"] == 1.0
    assert report["labels_unreached"] == []
    assert report["crashes"] == []
    assert report["choices_explored"] >= 2  # both branches taken


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_displayable_bounds_and_repositioning(sdk, demo_copy: Path) -> None:
    """Exercise the pixel-perfect tools against a real engine.

    Unit tests use a fake ``renpy``; this proves the real
    ``renpy.get_image_bounds`` and ``renpy.show(at_list=[Transform])`` behave as
    the bridge assumes, and that the image overlays/diff run on real frames.
    """
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.image_ops import annotate_png, diff_images
    from renforge.project import RenpyProject

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        client = session.client

        # Advance until the "wisp" sprite is actually on screen.
        for _ in range(8):
            if "wisp" in client.get_state().get("showing_tags", []):
                break
            client.advance()
            time.sleep(1.0)
        assert "wisp" in client.get_state()["showing_tags"], "wisp sprite never shown"

        # 1) get_displayable_bounds returns a real logical rectangle.
        measured = client.get_displayable_bounds("wisp")
        assert measured["ok"] is True, measured
        assert measured["coordinate_space"] == "logical"
        start = measured["bounds"]
        assert start["width"] > 0 and start["height"] > 0, measured

        # 2) overlay + diff run on genuine PNG frames of the same size.
        before_png = client.screenshot()
        overlaid = annotate_png(before_png, grid=100, rulers=True, crosshair=(start["x"], start["y"]))
        assert overlaid.startswith(b"\x89PNG") and len(overlaid) > 1000

        # 3) position_element moves the sprite; bounds and the frame both change.
        target_x = start["x"] + 200
        moved = client.position_element("wisp", xpos=target_x, xanchor=0.0, ypos=start["y"], yanchor=0.0)
        assert moved["ok"] is True, moved
        assert moved["bounds"]["x"] != start["x"], (start, moved["bounds"])
        assert abs(moved["bounds"]["x"] - target_x) <= 2, moved

        # 4) the reposition is measurable frame-to-frame.
        after_png = client.screenshot()
        diff = diff_images(before_png, after_png, threshold=16)
        assert diff["changed"] is True, diff
        assert diff["bounds"] is not None, diff


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_imagebutton_hover_bounds_and_capture(sdk, demo_copy: Path) -> None:
    """Exercise hover, painted bounds, and named captures on a real Ren'Py runtime.

    Scope note (important): this SDK subprocess is driven by the bridge drain loop,
    not a player-facing ``interact()`` loop. We can therefore prove:

    - ``hover_element`` resolves the control and moves synthetic input without firing
      the ImageButton action (no click).
    - ``get_ui_element_bounds`` reaches ``renpy.render_to_surface`` and returns
      alpha-painted bounds smaller than the focus rectangle.
    - bridge screenshots can be persisted under ``.renforge/captures/``.

    The idle→hover repaint and painted-bounds translation on real frames are covered
    by :func:`test_live_imagebutton_idle_hover_pipeline`.
    """
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.tools import live

    _add_hover_fixtures(demo_copy)

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        client = session.client
        client.eval_expr('renpy.show_screen("renforge_sdk_imagebutton_fixture")')
        client.eval_expr("renpy.restart_interaction()")

        ui_info = None
        for _ in range(40):
            ui_info = client.list_ui_elements_info()
            if ui_info.get("elements"):
                break
            time.sleep(0.25)
        assert ui_info is not None and ui_info.get("elements"), ui_info

        button = ui_info["elements"][0]
        clicks_before = client.get_var("renforge_sdk_button_clicks")

        bounds = client.get_ui_element_bounds(id=button["id"])
        assert bounds["ok"] is True, bounds
        assert bounds["painted_bounds_available"] is True, bounds
        assert bounds["painted_bounds_source"] == "rendered-alpha"
        assert bounds["state"] == "idle"
        focus = bounds["focus_bounds"]
        painted = bounds["painted_bounds"]
        assert painted["width"] <= focus["width"]
        assert painted["height"] <= focus["height"]

        capture_path = _save_capture(demo_copy, "sdk-idle", client.screenshot())
        assert capture_path.is_file()
        assert capture_path.parent == demo_copy / ".renforge" / "captures"

        hovered = client.hover_element(id=button["id"])
        assert hovered["ok"] is True, hovered
        assert hovered.get("hovered") is True, hovered
        assert hovered["method"] in {"renpy", "renpy-test", "pygame"}
        assert client.eval_expr("renpy.test.testmouse.mouse_pos") is None
        assert client.get_var("renforge_sdk_button_clicks") == clicks_before

        errors = live.get_errors(str(demo_copy))
        assert errors.get("ok") is True, errors
        assert not errors.get("events"), errors


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_imagebutton_click_paths_release_testmouse(sdk, demo_copy: Path) -> None:
    """Exercise click_element and click_at against a real Ren'Py ImageButton."""
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject

    _add_hover_fixtures(demo_copy)

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        client = session.client
        client.eval_expr('renpy.show_screen("renforge_sdk_imagebutton_fixture")')
        client.eval_expr("renpy.restart_interaction()")

        elements = []
        for _ in range(40):
            elements = client.list_ui_elements()
            if elements:
                break
            time.sleep(0.25)
        assert elements, elements

        button = elements[0]
        center = button["center"]
        before = client.get_var("renforge_sdk_button_clicks")

        clicked = client.click_element(id=button["id"])
        assert clicked["ok"] is True, clicked
        for _ in range(40):
            if client.get_var("renforge_sdk_button_clicks") == before + 1:
                break
            time.sleep(0.1)
        assert client.get_var("renforge_sdk_button_clicks") == before + 1, clicked
        assert client.eval_expr("renpy.test.testmouse.mouse_pos") is None

        clicked_at = client.click_at(center["x"], center["y"])
        assert clicked_at["ok"] is True, clicked_at
        for _ in range(40):
            if client.get_var("renforge_sdk_button_clicks") == before + 2:
                break
            time.sleep(0.1)
        assert client.get_var("renforge_sdk_button_clicks") == before + 2
        assert client.eval_expr("renpy.test.testmouse.mouse_pos") is None


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_imagebutton_idle_hover_pipeline(sdk, demo_copy: Path) -> None:
    """Run the MCP idle→hover workflow on a real Ren'Py ImageButton.

    Mirrors the agent recipe documented in ``docs/MCP.md``:

    1. list UI + read ``painted_bounds`` while idle
    2. persist an idle capture
    3. ``hover_element`` without clicking
    4. read ``painted_bounds`` again (hover state) and derive the logical shift
    5. persist a hover capture and diff the frames
    6. run ``estimate_translation`` on the fixture art (file-based MCP tool path)

    Ren'Py scales screenshots (logical UI vs physical PNG), so the overlap
    estimator can be ambiguous on live captures even when the bridge reports an
    exact shift through ``painted_bounds``. Agents should prefer the bounds delta
    for UI alignment and reserve ``estimate_translation`` for named PNG captures.
    """
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.image_ops import diff_images, estimate_translation
    from renforge.project import RenpyProject
    from renforge.tools import live

    _add_hover_fixtures(demo_copy)
    images_dir = demo_copy / "game" / "images"

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        client = session.client
        client.eval_expr('renpy.show_screen("renforge_sdk_imagebutton_fixture")')
        client.eval_expr("renpy.restart_interaction()")

        ui_info = None
        for _ in range(40):
            ui_info = client.list_ui_elements_info()
            if ui_info.get("elements"):
                break
            time.sleep(0.25)
        assert ui_info is not None and ui_info.get("elements"), ui_info

        button = ui_info["elements"][0]
        clicks_before = client.get_var("renforge_sdk_button_clicks")

        bounds_idle = client.get_ui_element_bounds(id=button["id"])
        assert bounds_idle["ok"] is True, bounds_idle
        assert bounds_idle["state"] == "idle"

        idle_path = _save_capture(demo_copy, "pipeline-idle", client.screenshot())

        hovered = client.hover_element(id=button["id"])
        assert hovered["ok"] is True, hovered
        assert client.get_var("renforge_sdk_button_clicks") == clicks_before

        bounds_hover = client.get_ui_element_bounds(id=button["id"])
        assert bounds_hover["ok"] is True, bounds_hover
        assert bounds_hover["state"] == "hover"

        painted_idle = bounds_idle["painted_bounds"]
        painted_hover = bounds_hover["painted_bounds"]
        assert painted_idle is not None and painted_hover is not None
        assert (painted_hover["x"] - painted_idle["x"], painted_hover["y"] - painted_idle["y"]) == (28, 22)

        hover_path = _save_capture(demo_copy, "pipeline-hover", client.screenshot())
        diff = diff_images(idle_path, hover_path, threshold=16)
        assert diff["changed"] is True, diff
        assert diff.get("changed_pixels", 0) > 100

        estimate = estimate_translation(
            images_dir / "renforge_sdk_idle.png",
            images_dir / "renforge_sdk_hover.png",
            region=(8, 8, 60, 54),
            threshold=16,
            max_shift=32,
        )
        assert estimate["ok"] is True, estimate
        assert estimate.get("available") is True, estimate
        assert (estimate["dx"], estimate["dy"]) == (28, 22)

        errors = live.get_errors(str(demo_copy))
        assert errors.get("ok") is True, errors
        assert not errors.get("events"), errors


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_scene_tree_perceives_layers_text_and_measures(sdk, demo_copy: Path) -> None:
    """Prove full-scene perception on a real engine: non-focusable layer images
    and dialogue text get real logical bounds, and measure/wireframe run on them.
    """
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.tools import live

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        client = session.client
        what = ""
        for _ in range(8):
            what = client.eval_expr(
                "str((getattr(renpy.get_screen('say'), 'scope', None) or {}).get('what') or '')"
            )
            if what:
                break
            client.advance()
            time.sleep(1.0)
        assert what, "no dialogue text appeared"

        scene = client.scene_tree(include=["style"])
        assert scene["ok"] is True
        assert scene["window"]["width"] > 0
        assert scene["coordinate_space"] == "logical"
        assert "omitted" in scene
        nodes = scene["nodes"]

        # A non-focusable layer image (e.g. bg) with a real rendered rectangle.
        images = [n for n in nodes if n["type"] == "image" and n["bounds_available"]]
        assert images, "no image node with bounds perceived"

        # Dialogue text is NOT a focusable control, yet it is perceived with
        # bounds and its declared style colour.
        texts = [n for n in nodes if n["type"] == "text" and n.get("text")]
        assert texts, "no text node perceived"
        say_text = next((n for n in texts if n.get("screen") == "say"), texts[0])
        assert say_text["bounds"]["width"] > 0
        assert say_text.get("style", {}).get("color")

        # measure: quick-menu buttons live on one row, so their top edges align.
        buttons = [n["id"] for n in nodes if n["type"] == "button"]
        if len(buttons) >= 2:
            aligned = live.measure(str(demo_copy), action="align", targets=buttons[:2], tolerance=2)
            assert aligned["ok"] is True
            assert aligned["result"]["top"] == 0

        # wireframe format renders an ASCII map with a legend.
        wire = live.scene_tree(str(demo_copy), format="wireframe")
        assert "wireframe" in wire and "Legend" in wire["wireframe"]

        errors = live.get_errors(str(demo_copy))
        assert errors.get("ok") is True, errors

@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_scene_tree_ids_are_unique_across_layers(sdk, demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.tools import live

    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        client = session.client
        for layer, color in (("master", "#ff0000"), ("screens", "#0000ff")):
            client.eval_expr(
                'renpy.show("renforge_duplicate", what=Solid("%s", xsize=80, ysize=80), layer="%s")'
                % (color, layer)
            )
        client.eval_expr("renpy.restart_interaction()")
        time.sleep(0.5)

        scene = client.scene_tree(detail="raw")
        duplicates = [
            node for node in scene["nodes"]
            if node.get("tag") == "renforge_duplicate"
        ]
        assert {node["layer"] for node in duplicates} == {"master", "screens"}
        assert len({node["id"] for node in duplicates}) == 2

        measured = live.measure(
            str(demo_copy),
            action="gap",
            targets=[node["id"] for node in duplicates],
        )
        assert measured["ok"] is True, measured


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="live bridge needs a display (set DISPLAY, or run under xvfb)")
def test_live_scene_tree_reports_nested_nodes_custom_layers_and_limits(sdk, demo_copy: Path) -> None:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.tools import live

    fixture = _add_sdk_fixtures(demo_copy)
    with launch_with_bridge(sdk, RenpyProject(demo_copy), startup_timeout=90) as session:
        client = session.client
        client.eval_expr(
            'renpy.show_screen("%s", "X" * 100, 7, _layer="master")' % fixture["screen"]
        )
        client.eval_expr("renpy.restart_interaction()")
        time.sleep(0.5)

        scene = client.scene_tree(detail="raw")
        node_ids = [node["id"] for node in scene["nodes"]]
        assert len(node_ids) == len(set(node_ids))
        screen_nodes = [
            node for node in scene["nodes"]
            if node.get("screen") == fixture["screen"] and node.get("layer") == "master"
        ]
        nested_image = next(node for node in screen_nodes if node["type"] == "image")
        assert nested_image["bounds"]["x"] == 30
        assert nested_image["bounds"]["y"] == 40

        containers = client.scene_tree(types=["container"])

        text_limited = client.scene_tree(detail="raw", max_text_chars=16)
        truncated_text = [
            node["text"]
            for node in text_limited["nodes"]
            if node.get("text", "").endswith("…")
        ]
        assert truncated_text
        assert max(len(text) for text in truncated_text) == 17
        assert any((node.get("screen") or "").endswith("…") for node in text_limited["nodes"])
        for node in text_limited["nodes"]:
            for key in ("text", "screen", "action", "tag", "layer", "type"):
                if node.get(key) is not None:
                    assert len(node[key]) <= 17
            assert len(node["id"]) <= 257
        stable_id_node = next(
            node
            for node in text_limited["nodes"]
            if node["type"] == "image"
            and (node.get("bounds") or {}).get("x") == 30
            and (node.get("bounds") or {}).get("y") == 40
        )
        assert stable_id_node["id"] == nested_image["id"]
        by_stable_id = client.scene_tree(
            detail="raw",
            max_text_chars=16,
            ids=[stable_id_node["id"]],
        )
        assert [node["id"] for node in by_stable_id["nodes"]] == [stable_id_node["id"]]
        assert by_stable_id["nodes"][0]["bounds"] == stable_id_node["bounds"]
        by_full_screen = client.scene_tree(
            detail="raw",
            max_text_chars=16,
            screen=fixture["screen"],
        )
        assert any(
            node["id"] == stable_id_node["id"] and node["bounds"] == stable_id_node["bounds"]
            for node in by_full_screen["nodes"]
        )
        assert any(node["type"] == "button" for node in by_full_screen["nodes"])
        measured = live.measure(
            str(demo_copy),
            action="fit",
            targets=[stable_id_node["id"]],
            within={"x": 0, "y": 0, **scene["window"]},
        )
        assert measured["ok"] is True
        assert any(node.get("screen") == fixture["screen"] for node in containers["nodes"])

        limited = client.scene_tree(detail="raw", max_depth=0)
        assert limited["truncated"] is True
        assert limited["omitted"]["by_reason"]["max_depth"] > 0

        node_limited = client.scene_tree(detail="raw", max_nodes=1)
        assert len(node_limited["nodes"]) == 1
        assert node_limited["truncated"] is True
        assert node_limited["omitted"]["by_reason"]["max_nodes"] > 0
