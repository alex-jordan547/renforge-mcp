"""Contract tests for the editor overlay's UI sources.

Each test here exists because the failure it describes actually happened and no
existing test noticed. They check the seams between the region screens, the
translation catalogues, and the repository itself — the places where a panel can
be perfectly written and still never reach a user.
"""

import json
import pathlib
import re
import subprocess

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
BRIDGE_DIR = BASE_DIR / "src" / "renforge" / "bridge"
SCREENS_DIR = BRIDGE_DIR / "screens"
EDITOR_RPY = BRIDGE_DIR / "editor.rpy"
LOCALES_DIR = BRIDGE_DIR / "editor_assets" / "locales"
EN_JSON = LOCALES_DIR / "en.json"
ZH_CN_JSON = LOCALES_DIR / "zh-CN.json"

# Built by the screens as _renforge_editor_t("lock.%s" % level), so no literal
# scan will ever see them.
DYNAMIC_KEYS = {"lock.locked", "lock.blocked", "lock.refused"}

_USED_KEY = re.compile(r'_renforge_editor_t\(\s*["\']([^"\']+)["\']\s*\)')


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=BASE_DIR, capture_output=True, text=True
    )


def _builtin_keys(editor_content: str) -> set[str]:
    """Keys of the _RF_UI_STRINGS fallback dict living inside editor.rpy."""
    start = editor_content.find("_RF_UI_STRINGS = {")
    assert start != -1, f"_RF_UI_STRINGS not found in {EDITOR_RPY}"
    depth = 0
    for index in range(start, len(editor_content)):
        if editor_content[index] == "{":
            depth += 1
        elif editor_content[index] == "}":
            depth -= 1
            if depth == 0:
                body = editor_content[start : index + 1]
                return set(re.findall(r'["\']([^"\']+)["\']\s*:', body))
    raise AssertionError("unterminated _RF_UI_STRINGS literal")


def test_screens_directory_is_not_ignored():
    """A bare `screens` rule in .gitignore once hid every panel from the repository.

    The panels existed on one disk and nowhere else; a fresh clone would have
    built its payload from editor.rpy alone and crashed on the first `use`.

    The probe path matters: git never applies ignore rules to files it already
    tracks, so asking about the committed panels would answer "not ignored"
    even with the bad rule restored. Only an untracked path tells the truth.
    """
    assert SCREENS_DIR.is_dir(), f"{SCREENS_DIR} does not exist"

    probe = SCREENS_DIR / "__ignore_probe__.rpy"
    assert not probe.exists(), "probe path unexpectedly exists on disk"
    ignored = _git("check-ignore", "-v", str(probe))
    assert ignored.returncode != 0, (
        "new .rpy files under src/renforge/bridge/screens/ would be ignored by git: "
        f"{ignored.stdout.strip()}"
    )


def test_every_panel_file_is_tracked():
    """A panel that is not committed ships to nobody."""
    panels = sorted(SCREENS_DIR.glob("*.rpy"))
    assert panels, f"no .rpy files found in {SCREENS_DIR}"

    untracked = [
        path.name
        for path in panels
        if not _git("ls-files", "--error-unmatch", str(path)).returncode == 0
    ]
    assert not untracked, f"panel files not tracked by git: {untracked}"


def test_every_used_translation_key_exists_everywhere():
    """A missing key renders as the key itself, which reads fine in English only.

    _renforge_editor_t falls back to the key, so an absent entry is invisible in
    the language we develop in and shows raw identifiers in every other one.
    """
    editor_content = EDITOR_RPY.read_text(encoding="utf-8")

    used = set(DYNAMIC_KEYS)
    used |= set(_USED_KEY.findall(editor_content))
    for path in SCREENS_DIR.glob("*.rpy"):
        used |= set(_USED_KEY.findall(path.read_text(encoding="utf-8")))

    catalogues = {
        EN_JSON.name: json.loads(EN_JSON.read_text(encoding="utf-8")),
        ZH_CN_JSON.name: json.loads(ZH_CN_JSON.read_text(encoding="utf-8")),
        "_RF_UI_STRINGS": _builtin_keys(editor_content),
    }

    missing = [
        f"{key!r} missing from {where}"
        for key in sorted(used)
        for where, catalogue in catalogues.items()
        if key not in catalogue
    ]
    assert not missing, "translation keys missing:\n" + "\n".join(missing)


def test_every_region_screen_is_used():
    """A screen nobody invokes is a file that renders nothing."""
    panels = sorted(SCREENS_DIR.glob("*.rpy"))
    assert panels, f"no .rpy files found in {SCREENS_DIR}"

    sources = [EDITOR_RPY.read_text(encoding="utf-8")]
    defined: list[tuple[str, str]] = []
    for path in panels:
        content = path.read_text(encoding="utf-8")
        sources.append(content)
        defined += [
            (name, path.name)
            for name in re.findall(r"^screen\s+(_rf_\w+)\s*\(", content, re.M)
        ]
    assert defined, f"no `screen _rf_...` definitions found in {SCREENS_DIR}"

    combined = "\n".join(sources)
    unwired = [
        f"{name!r} defined in {file_name} is never reached by a `use`"
        for name, file_name in defined
        if not re.search(r"\buse\s+" + re.escape(name) + r"\b", combined)
    ]
    assert not unwired, "unwired screens:\n" + "\n".join(unwired)


def test_catalogues_agree_on_their_keys():
    """Parity is what stops one language quietly drifting behind the other."""
    english = json.loads(EN_JSON.read_text(encoding="utf-8"))
    chinese = json.loads(ZH_CN_JSON.read_text(encoding="utf-8"))

    problems = []
    only_english = sorted(set(english) - set(chinese))
    only_chinese = sorted(set(chinese) - set(english))
    if only_english:
        problems.append(f"in {EN_JSON.name} but not {ZH_CN_JSON.name}: {only_english}")
    if only_chinese:
        problems.append(f"in {ZH_CN_JSON.name} but not {EN_JSON.name}: {only_chinese}")

    for name, catalogue in ((EN_JSON.name, english), (ZH_CN_JSON.name, chinese)):
        for key, value in catalogue.items():
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{key!r} in {name} is empty: {value!r}")

    assert not problems, "catalogue mismatch:\n" + "\n".join(problems)


def test_inspector_reads_the_selected_widget_preview_map():
    """The preview builder requires placement arguments and cannot be a getter."""
    inspector = (SCREENS_DIR / "rf_inspector.rpy").read_text(encoding="utf-8")

    assert "_renforge_editor_preview_properties()" not in inspector
    assert '_renforge_editor_widget_properties(_rf_facts["screen"])' in inspector


def test_docked_canvas_and_preview_keep_their_visual_boundaries():
    """Docking must crop the logical canvas; preview must release editor chrome."""
    editor = EDITOR_RPY.read_text(encoding="utf-8")
    tree = (SCREENS_DIR / "rf_tree.rpy").read_text(encoding="utf-8")

    assert "crop=(0, 0, config.screen_width, config.screen_height)" in editor
    assert "def _renforge_editor_chrome_docked():" in editor
    assert "def _renforge_editor_layout_chrome_snapshot():" in editor
    assert "def _renforge_editor_dock_canvas_screen_rect():" in editor
    assert '"stage": "#000000"' in editor
    assert "if _renforge_editor_chrome_docked():" in editor
    assert "def _renforge_editor_dock_stage_bands():" in editor
    assert "_renforge_editor_dock_stage_bands()" in editor
    assert '_renforge_editor_ui_color("stage")' in editor
    assert (
        '$ _rf_canvas_tools_visible = _rf_tools_visible and '
        '_renforge_editor_view_mode() == "edit"'
    ) in editor
    assert (
        'if not state.active or _renforge_editor_view_mode() == "preview":'
        in editor
    )
    assert "config.screen_height - _renforge_editor_ui_px(_RF_DOCK_TREE_Y)" in tree


def test_chrome_docked_is_the_only_dock_flag_in_region_screens():
    """Rails and the scaled canvas share one predicate: docked layout + edit view."""
    panels = sorted(SCREENS_DIR.glob("*.rpy"))
    assert panels, f"no .rpy files found in {SCREENS_DIR}"

    # Maquette panel geometry (xpos/ypos/xsize/ysize), not content max-widths.
    layout_literals = re.compile(
        r"(?:xpos|ypos|xsize|ysize|xoffset|yoffset)\s+[^\n]*"
        r"_renforge_editor_ui_px\(\s*(?:"
        r"550|372|124|540|968|624|692|576|1408|480|952|940|800|1180|28|148"
        r")\s*\)"
    )

    problems = []
    for path in panels:
        text = path.read_text(encoding="utf-8")
        if "_rf_docked" not in text:
            continue
        if "_renforge_editor_chrome_docked()" not in text:
            problems.append(f"{path.name}: _rf_docked without chrome_docked()")
        if re.search(
            r'\$\s*_rf_docked\s*=\s*[^\n]*layout_mode',
            text,
        ):
            problems.append(
                f"{path.name}: _rf_docked assigned from layout_mode instead of chrome_docked()"
            )
        for match in layout_literals.finditer(text):
            problems.append(
                f"{path.name}: layout literal {match.group(0)} — use _RF_* constants"
            )

    assert not problems, "layout chrome contract broken:\n" + "\n".join(problems)


def test_layout_constants_match_maquette_geometry():
    """Maquette numbers live once in editor.rpy; drift here is a visual regression."""
    editor = EDITOR_RPY.read_text(encoding="utf-8")

    expected = {
        "_RF_DOCK_SCALE = 0.57": True,
        "_RF_DOCK_CANVAS_X = 550": True,
        "_RF_DOCK_CANVAS_Y = 372": True,
        "_RF_DOCK_TOOLBAR_H = 124": True,
        "_RF_DOCK_RAIL_W = 540": True,
        "_RF_DOCK_TREE_H = 968": True,
        "_RF_DOCK_INSPECTOR_H = 624": True,
        "_RF_DOCK_STYLE_H = 692": True,
        "_RF_DOCK_HUD_X = 576": True,
        "_RF_DOCK_HUD_W = 1408": True,
        "_RF_OVERLAY_TREE_H = 952": True,
        "_RF_OVERLAY_TREE_W = 480": True,
        "_RF_OVERLAY_TOOLBAR_H = 96": True,
        "_RF_OVERLAY_TOOLBAR_W = 2496": True,
        "_RF_OVERLAY_HUD_X = 536": True,
        "_RF_OVERLAY_HUD_H = 56": True,
        "_RF_ICON_BTN = 60": True,
        "_RF_S4 = 24": True,
        "_RF_T_XS = 21": True,
    }
    missing = [name for name in expected if name not in editor]
    assert not missing, f"layout constants missing or changed: {missing}"


def test_editor_ships_maquette_toolbar_icons():
    """Lot 0.A icons must travel with assets and be reachable from chrome helpers."""
    icons_dir = BRIDGE_DIR / "editor_assets" / "icons"
    frames_dir = BRIDGE_DIR / "editor_assets" / "frames"
    assert icons_dir.is_dir(), icons_dir
    required = {
        "select", "move", "measure", "picker", "text", "hand",
        "undo", "redo", "reset", "exit", "minus", "plus", "save", "eye", "lock", "search",
    }
    present = {path.stem for path in icons_dir.glob("*.svg")}
    missing = sorted(required - present)
    assert not missing, f"missing toolbar icons: {missing}"
    for frame in ("panel.png", "pill_accent.png", "chip.png", "tools.png", "brand.png"):
        assert (frames_dir / frame).is_file(), frame

    editor = EDITOR_RPY.read_text(encoding="utf-8")
    toolbar = (SCREENS_DIR / "rf_toolbar.rpy").read_text(encoding="utf-8")
    assert "def _renforge_editor_ui_icon(name):" in editor
    assert "_renforge_editor_ui_icon(" in toolbar
    assert 'use _rf_icon_btn("rf_toolbar_tool_select"' in toolbar
    assert 'use _rf_icon_btn("rf_exit"' in toolbar
    # Overlay bar shape follows maquette floating geometry.
    assert "_RF_OVERLAY_TOOLBAR_W" in toolbar
    assert "_RF_OVERLAY_TOOLBAR_H" in toolbar
    assert 'Frame(_renforge_editor_ui_frame("pill_accent")' in toolbar


def test_lot2c_docked_layout_contract():
    """Lot 2.C: dedicated editor layer, cropped scale, chrome only in edit, stage bands."""
    editor = EDITOR_RPY.read_text(encoding="utf-8")
    toolbar = (SCREENS_DIR / "rf_toolbar.rpy").read_text(encoding="utf-8")

    assert '_EDITOR_LAYER = "renforge_editor"' in editor
    assert "def _renforge_editor_chrome_docked():" in editor
    assert "def _renforge_editor_dock_stage_bands():" in editor
    assert "def _renforge_editor_layout_chrome_snapshot():" in editor
    assert "crop=(0, 0, config.screen_width, config.screen_height)" in editor
    assert "zoom=_RF_DOCK_SCALE" in editor
    assert "_renforge_editor_screen_to_canvas_point" in editor
    assert "_renforge_editor_canvas_to_screen_point" in editor
    assert '_renforge_editor_set_layout_mode, "docked"' in toolbar
    assert '_renforge_editor_set_view_mode, "preview"' in toolbar
    # Preview must release chrome even when layout_mode remains docked.
    assert "bool(state.active) and _renforge_editor_chrome_docked()" in editor or (
        "enabled = bool(state.active) and _renforge_editor_chrome_docked()" in editor
    )


def test_lot1_panels_cover_the_portage_seams():
    """Lot 1 is done when each panel file exposes its plan seam and frozen tools stay inert."""
    toolbar = (SCREENS_DIR / "rf_toolbar.rpy").read_text(encoding="utf-8")
    tree = (SCREENS_DIR / "rf_tree.rpy").read_text(encoding="utf-8")
    inspector = (SCREENS_DIR / "rf_inspector.rpy").read_text(encoding="utf-8")
    style = (SCREENS_DIR / "rf_style.rpy").read_text(encoding="utf-8")
    hud = (SCREENS_DIR / "rf_hud.rpy").read_text(encoding="utf-8")
    decor = (SCREENS_DIR / "rf_canvas_decor.rpy").read_text(encoding="utf-8")
    editor = EDITOR_RPY.read_text(encoding="utf-8")

    # 1.A — real tools + Lot 3 tools grayed (enabled=False via icon buttons)
    for tool_id in (
        "rf_toolbar_tool_select",
        "rf_toolbar_tool_move",
        "rf_toolbar_tool_measure",
        "rf_toolbar_tool_picker",
        "rf_toolbar_tool_text",
        "rf_toolbar_tool_hand",
        "rf_exit",
        "rf_undo",
        "rf_redo",
        "rf_reset",
        "rf_tools",
        "rf_opacity_down",
        "rf_opacity_up",
        "rf_save",
    ):
        assert tool_id in toolbar, tool_id
    assert "enabled=False" in toolbar
    assert "def _renforge_editor_tool_allows_drag():" in editor
    assert "_renforge_editor_tool_allows_drag()" in editor
    assert "screen _rf_icon_btn(" in toolbar

    # 1.B — live tree + viewport + badge chrome
    assert "rf_tree_viewport" in tree
    assert '_renforge_editor_tree_rows()' in tree
    assert '_renforge_editor_select_widget' in tree
    assert "_RF_TREE_BADGE" in tree

    # 1.C — geometry fields + display-only 3×3 anchor
    assert "rf_inspector_anchor_grid" in inspector
    assert "_renforge_editor_anchor_cell_on" in inspector
    assert 'use _rf_editor_field("xpos"' in inspector
    assert 'use _rf_editor_field("xanchor"' in inspector
    assert "rf_inspector_lock" in inspector

    # 1.D — colour allowlist surface + lock reason
    assert "rf_style_color_value" in style
    assert "rf_style_lock" in style or "style.locked" in style
    assert "_renforge_editor_style_color_capable" in style

    # 1.E — session status lives on the band
    assert "rf_hud_band" in hud
    assert "rf_hud_pending" in hud
    assert "rf_hud_selection" in hud

    # 1.F — canvas ids the live suites sample
    for decor_id in ("rf_guide_x", "rf_guide_y", "rf_distance_x", "rf_distance_y", "rf_label"):
        assert f'id "{decor_id}"' in decor, decor_id
    assert "_renforge_editor_handle_points" in decor or "handle" in decor
