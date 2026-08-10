"""Contract tests for the editor overlay's UI sources.

Each test here exists because the failure it describes actually happened and no
existing test noticed. They check the seams between the region screens, the
translation catalogues, and the repository itself — the places where a panel can
be perfectly written and still never reach a user.
"""

import ast
import json
import pathlib
import pickle
import re
import subprocess
import sys
import textwrap
import types

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


def _builtin_catalogue(editor_content: str) -> dict[str, str]:
    """Parse the literal _RF_UI_STRINGS fallback dictionary."""
    start = editor_content.find("_RF_UI_STRINGS = {")
    assert start != -1, f"_RF_UI_STRINGS not found in {EDITOR_RPY}"
    depth = 0
    for index in range(start, len(editor_content)):
        if editor_content[index] == "{":
            depth += 1
        elif editor_content[index] == "}":
            depth -= 1
            if depth == 0:
                opening = editor_content.find("{", start)
                catalogue = ast.literal_eval(editor_content[opening : index + 1])
                assert isinstance(catalogue, dict)
                return catalogue
    raise AssertionError("unterminated _RF_UI_STRINGS literal")


def _builtin_keys(editor_content: str) -> set[str]:
    return set(_builtin_catalogue(editor_content))


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


def test_lot1_panels_cover_the_portage_seams():
    """Stable screen/id wiring for the toolbar, panels, HUD, and canvas."""
    toolbar = (SCREENS_DIR / "rf_toolbar.rpy").read_text(encoding="utf-8")
    tree = (SCREENS_DIR / "rf_tree.rpy").read_text(encoding="utf-8")
    inspector = (SCREENS_DIR / "rf_inspector.rpy").read_text(encoding="utf-8")
    style = (SCREENS_DIR / "rf_style.rpy").read_text(encoding="utf-8")
    hud = (SCREENS_DIR / "rf_hud.rpy").read_text(encoding="utf-8")
    decor = (SCREENS_DIR / "rf_canvas_decor.rpy").read_text(encoding="utf-8")
    editor = EDITOR_RPY.read_text(encoding="utf-8")

    # 1.A — toolbar control ids and shared button wiring
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
    assert "screen _rf_icon_btn(" in toolbar

    # 1.B — live tree + viewport + badge chrome
    assert "rf_tree_viewport" in tree
    assert '_renforge_editor_tree_rows()' in tree
    assert '_renforge_editor_select_widget' in tree
    assert '_renforge_editor_select_runtime_key' in tree
    assert 'elif row.get("selectable")' in tree
    assert 'row.get("source_location")' in tree
    assert "_RF_TREE_BADGE" in tree
    assert "substitute False" in tree
    assert "_renforge_editor_tree_snippet" in editor
    overlay = editor.split("screen _renforge_editor_overlay():", 1)[1].split(
        "init 1090 python:", 1
    )[0]
    main_fixed = overlay.split("        fixed:", 1)[1]
    assert "add _renforge_editor_event_catcher()" not in overlay.split(
        "        fixed:", 1
    )[0]
    assert main_fixed.index("add _renforge_editor_event_catcher()") < main_fixed.index(
        "use _rf_editor_tree()"
    )
    assert "def _renforge_editor_toggle_tree_node(node_key):" in editor
    assert "state.tree_collapsed_keys = set()" in editor
    assert '"has_children": has_children' in editor
    assert 'screen _rf_tree_disclosure(row):' in tree
    assert 'use _rf_tree_disclosure(row)' in tree
    assert 'at Transform(rotate=(0 if row.get("expanded") else -90))' in tree
    assert '"panel": "#272729"' in editor
    assert '"panel_head": "#2a2a2c"' in editor
    assert "# Flat section header" in tree
    assert 'background Solid(_renforge_editor_ui_color("panel_head"))' in tree
    assert "_renforge_editor_ui_text_px(18, minimum=13)" in tree

    # 1.C — geometry fields + display-only 3×3 anchor
    assert "rf_inspector_anchor_grid" in inspector
    assert "_renforge_editor_anchor_cell_on" in inspector
    assert 'use _rf_editor_field("xpos"' in inspector
    assert 'use _rf_editor_field("xanchor"' in inspector
    assert "rf_inspector_lock" in inspector

    # 1.D — colour allowlist surface + lock reason + visible colour controls
    assert "rf_style_color_value" in style
    assert 'id "rf_style_color"' in style
    assert 'id "rf_style_cycle"' in style
    assert "rf_style_lock" in style or "style.locked" in style
    assert "_renforge_editor_style_color_capable" in style
    assert "_renforge_editor_cycle_style_color_preview" in style

    # 1.E — session status lives on the band
    assert "rf_hud_band" in hud
    assert "rf_hud_pending" in hud
    assert "rf_hud_selection" in hud
    assert "rf_hud_status" in hud
    assert "_renforge_editor_collect_intents()" in hud
    assert "reload.active" in hud or "reload.reloading" in hud
    # Timer must not call status_code (returns a string → ends interaction
    # and auto-advances dialogue). status_tick returns None on purpose.
    assert "Function(_renforge_editor_status_tick)" in hud
    assert "Function(_renforge_editor_status_code)" not in hud
    assert "def _renforge_editor_status_tick():" in editor

    # 1.F — canvas ids the live suites sample
    for decor_id in ("rf_guide_x", "rf_guide_y", "rf_distance_x", "rf_distance_y", "rf_label"):
        assert f'id "{decor_id}"' in decor, decor_id

    # Step 7/8 seams: clear selection, status codes, effective props, layout, reset
    assert "def _renforge_editor_clear_selection" in editor
    assert "def _renforge_editor_set_status" in editor
    assert "def _renforge_editor_effective_properties" in editor
    assert "def _renforge_editor_layout_metrics" in editor
    assert "editor_task0_layout_snapshot" in editor
    assert "_renforge_editor_live_layout_metrics()" in toolbar
    for inclusion_flag in (
        'show_brand',
        'show_screen',
        'show_lock',
        'show_disabled_tools',
    ):
        assert f'_rf_metrics["{inclusion_flag}"]' in toolbar
    preview_screen = toolbar.split("screen _rf_editor_preview_toolbar():", 1)[1].split(
        "screen _rf_editor_toolbar(tools_visible):", 1
    )[0]
    assert '"rf_toolbar_view_edit"' in preview_screen
    assert '"rf_save"' not in preview_screen
    assert 'use _rf_save_btn()' in preview_screen
    assert '"rf_exit"' in preview_screen
    assert '"rf_undo"' not in preview_screen
    assert "_renforge_editor_can_reset()" in toolbar
    assert "tooltip_text" in toolbar
    assert "GetTooltip()" in toolbar
    assert 'id "rf_toolbar_status"' not in toolbar
    assert "rf_tree_filter" not in tree
    assert "tree.filter" not in editor
    assert "_renforge_editor_effective_properties" in inspector
    assert "inspector.read_only" in inspector
    assert "scrollbars" in tree
    assert 'style_prefix "rf"' in tree
    assert "bar.aft_gutter = bottom_radius" in editor


def test_editor_event_catcher_survives_script_reload_unpickle(monkeypatch):
    """The overlay catcher must not be serialized as a default-store class."""
    editor = EDITOR_RPY.read_text(encoding="utf-8")
    marker = "init 1095 python in _renforge_editor_runtime:\n"
    assert marker in editor
    block = editor.split(marker, 1)[1].split("\ninit 1100 python:\n", 1)[0]
    body = textwrap.dedent(block)
    assert "_constant = True" in body

    runtime_store = types.ModuleType("store._renforge_editor_runtime")
    parent_store = types.ModuleType("store")
    parent_store.__path__ = []
    parent_store._renforge_editor_runtime = runtime_store
    runtime_store.__dict__["renpy"] = types.SimpleNamespace(
        Displayable=object,
        Render=lambda width, height: types.SimpleNamespace(width=width, height=height),
        store=types.SimpleNamespace(
            _renforge_editor_handle_event=lambda event, x, y, st: (event, x, y, st)
        ),
    )
    monkeypatch.setitem(sys.modules, parent_store.__name__, parent_store)
    monkeypatch.setitem(sys.modules, runtime_store.__name__, runtime_store)
    exec(compile(body, "editor.rpy", "exec"), runtime_store.__dict__)

    payload = pickle.dumps(runtime_store.event_catcher)
    restored = pickle.loads(payload)
    assert restored.__class__.__module__ == runtime_store.__name__


def test_editor_side_panels_can_be_collapsed_independently():
    """Each side panel keeps its own hide control and restore tab."""
    tree = (SCREENS_DIR / "rf_tree.rpy").read_text(encoding="utf-8")
    inspector = (SCREENS_DIR / "rf_inspector.rpy").read_text(encoding="utf-8")
    style = (SCREENS_DIR / "rf_style.rpy").read_text(encoding="utf-8")

    assert "def _renforge_editor_panel_visible(panel_name):" in inspector
    assert "def _renforge_editor_toggle_panel(panel_name):" in inspector
    assert "def _renforge_editor_panel_tooltip(panel_name, action):" in inspector
    assert "screen _rf_editor_panel_hide_button(" in inspector
    assert "screen _rf_editor_panel_restore_tab(" in inspector
    assert '"panel-collapse-left" if side == "left" else "panel-collapse-right"' in inspector
    assert '_RF_EDITOR_PANEL_ICONS[panel_name]' in inspector
    assert 'tooltip _renforge_editor_panel_tooltip(panel_name, "hide")' in inspector
    assert 'tooltip _renforge_editor_panel_tooltip(panel_name, "show")' in inspector
    hide_button = inspector.split("screen _rf_editor_panel_hide_button", 1)[1].split(
        "screen _rf_editor_panel_restore_tab", 1
    )[0]
    assert 'background Solid("#00000000")' in hide_button

    icons = BRIDGE_DIR / "editor_assets" / "icons"
    for icon_name in (
        "panel-collapse-left",
        "panel-collapse-right",
        "panel-tree",
        "panel-inspector",
        "panel-style",
    ):
        assert (icons / f"{icon_name}.svg").is_file(), icon_name

    for source, panel_name, side in (
        (tree, "tree", "left"),
        (inspector, "inspector", "right"),
        (style, "style", "right"),
    ):
        assert f'screen _rf_editor_{panel_name}():' in source
        assert f'use _rf_editor_{panel_name}_panel()' in source
        assert f'"{panel_name}", "{side}", "rf_{panel_name}_hide"' in source
        assert f'"{panel_name}", "{side}", "{panel_name}_rect", "rf_{panel_name}_show"' in source


def test_status_catalogue_keys_match_plan():
    """Step 8 status/reload keys exist with identical key sets and English parity."""
    editor = EDITOR_RPY.read_text(encoding="utf-8")
    english = json.loads(EN_JSON.read_text(encoding="utf-8"))
    chinese = json.loads(ZH_CN_JSON.read_text(encoding="utf-8"))
    builtin_catalogue = _builtin_catalogue(editor)
    builtin = set(builtin_catalogue)

    required = {
        "toolbar.brand",
        "tree.items_count",
        "tree.items_more",
        "tree.screen",
        "tree.truncated",
        "inspector.read_only",
        "style.color_value",
        "analysis.pending",
        "reload.active",
        "reload.reloading",
        "reload.failed",
        "status.ready",
        "status.analyzing",
        "status.analyzed",
        "status.undo",
        "status.undo_unavailable",
        "status.undoing",
        "status.redo",
        "status.redo_unavailable",
        "status.reset",
        "status.saving",
        "status.commit_queued",
        "status.undo_queued",
        "status.committed",
        "status.reload_committed",
        "status.analyze_failed",
        "status.commit_failed",
        "status.status_failed",
        "status.reload_failed",
        "status.reload_handshake_failed",
        "status.invalid_result",
        "status.locked",
    }
    missing = sorted(
        key
        for key in required
        if key not in english or key not in chinese or key not in builtin
    )
    assert not missing, f"missing status catalogue keys: {missing}"
    assert "tree.filter" not in english
    assert "tree.filter" not in chinese
    assert "hud.reload" not in english
    assert english["style.color"] == "Cycle colour"
    assert chinese["status.ready"] == "就绪"
    assert chinese["status.reload_committed"] == "重新加载已提交"
    assert "{count}" in english["hud.pending"]
    assert "{value}" in english["hud.selection"]
    # Built-in English equals en.json for every shared key
    assert builtin_catalogue == english
    # Placeholder parity for named keys
    for key in ("hud.pending", "hud.selection", "tree.items_count", "style.color_value"):
        en_ph = set(re.findall(r"\{(\w+)\}", english[key]))
        zh_ph = set(re.findall(r"\{(\w+)\}", chinese[key]))
        assert en_ph == zh_ph, (key, en_ph, zh_ph)


def test_editor_assets_force_included_in_pyproject():
    """Wheels and sdists must ship editor frames, icons, and locales."""
    pyproject = (BASE_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert "editor_assets" in pyproject
    assets = BRIDGE_DIR / "editor_assets"
    assert (assets / "locales" / "en.json").is_file()
    assert (assets / "locales" / "zh-CN.json").is_file()
    assert (assets / "frames").is_dir()
    assert (assets / "icons").is_dir()
