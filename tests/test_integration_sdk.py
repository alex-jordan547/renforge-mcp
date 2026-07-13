"""Live Ren'Py SDK integration tests.

Opt-in: these download/use a real Ren'Py SDK and invoke it. Enable with::

    RENFORGE_SDK_TESTS=1 pytest tests/test_integration_sdk.py

Optionally pin the version with ``RENFORGE_SDK_VERSION`` (default: 8.3.7).
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
    from renforge.sdk import get_or_install_sdk

    return get_or_install_sdk(os.environ.get("RENFORGE_SDK_VERSION", "8.3.7"))


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo"
    # Never inherit Ren'Py bytecode/cache from a previous local run: stale
    # compiled scripts can make ``--warp`` skip a fixture label entirely.
    shutil.copytree(_DEMO, destination, ignore=shutil.ignore_patterns("*.rpyc", "cache"))
    return destination


def _add_hover_fixtures(demo_copy: Path) -> None:
    """Add an ImageButton screen and offset idle/hover sprites for SDK E2E."""
    image_module = pytest.importorskip("PIL.Image", reason="Pillow not installed")

    images_dir = demo_copy / "game" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    idle = image_module.new("RGBA", (100, 100), (0, 0, 0, 0))
    hover = image_module.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(10, 60):
        for y in range(10, 60):
            idle.putpixel((x, y), (220, 40, 40, 255))
    for x in range(15, 65):
        for y in range(13, 63):
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
    text title
    text str(amount)
    text status

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
        choices = []
        for _ in range(6):
            choices = [
                choice
                for choice in session.client.list_choices()
                if choice.get("screen") == "choice"
            ]
            if choices:
                break
            session.client.advance()
            time.sleep(1.0)
        assert any(c["text"] == "Take the lantern and go." for c in choices), choices

        session.client.select_choice(text="Take the lantern and go.")
        time.sleep(1.5)

        assert session.client.get_var("lantern") is True
        assert session.client.get_var("courage") == 1
        # The branch dialogue is still displayed inside ``village_gate``;
        # the jump to ``crossroads`` follows after that line is dismissed.
        assert session.client.get_state()["current_label"] == "village_gate"


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
        choices = []
        for _ in range(8):
            choices = [
                choice
                for choice in client.list_choices()
                if choice.get("screen") == "choice"
            ]
            if choices:
                break
            client.advance()
            time.sleep(0.5)
        assert any("Take the lantern" in choice["text"] for choice in choices), choices
        client.select_choice(text="Take the lantern and go.")
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
        assert loaded == {"ok": True, "slot": "branch-a"}

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
def test_live_imagebutton_hover_bounds_capture_and_translation(sdk, demo_copy: Path) -> None:
    """Prove hover without click, painted bounds, named captures, and translation."""
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.image_ops import diff_images, estimate_translation
    from renforge.project import RenpyProject

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

        button = next(
            (
                element
                for element in ui_info["elements"]
                if "imagebutton" in str(element.get("type", "")).casefold()
                or "imagebutton" in str(element.get("role", "")).casefold()
            ),
            ui_info["elements"][0],
        )
        frame_id = ui_info["frame_id"]
        clicks_before = client.get_var("renforge_sdk_button_clicks")

        idle_path = _save_capture(demo_copy, "sdk-idle", client.screenshot())

        hovered = client.hover_element(id=button["id"], expected_frame_id=frame_id)
        assert hovered["ok"] is True, hovered
        assert hovered.get("hovered") is True, hovered
        assert client.get_var("renforge_sdk_button_clicks") == clicks_before

        client.eval_expr("renpy.restart_interaction()")
        time.sleep(0.5)

        bounds_idle = client.get_ui_element_bounds(id=button["id"], expected_frame_id=frame_id)
        assert bounds_idle["ok"] is True, bounds_idle
        assert bounds_idle["focus_bounds"]["width"] > 0
        if bounds_idle.get("painted_bounds_available"):
            painted = bounds_idle["painted_bounds"]
            focus = bounds_idle["focus_bounds"]
            assert painted["width"] <= focus["width"]
            assert painted["height"] <= focus["height"]

        hover_path = _save_capture(demo_copy, "sdk-hover", client.screenshot())
        assert idle_path.is_file() and hover_path.is_file()

        diff = diff_images(idle_path, hover_path, threshold=16)
        assert diff["changed"] is True, diff

        region = bounds_idle.get("painted_bounds") or bounds_idle["focus_bounds"]
        estimate = estimate_translation(
            idle_path,
            hover_path,
            region=(
                region["x"],
                region["y"],
                region["width"],
                region["height"],
            ),
            threshold=16,
            max_shift=16,
        )
        assert estimate["ok"] is True, estimate
        if estimate.get("available"):
            assert abs(estimate["dx"]) <= 16
            assert abs(estimate["dy"]) <= 16

        errors = client.get_errors()
        assert errors.get("ok") is True, errors
        assert not errors.get("errors"), errors
