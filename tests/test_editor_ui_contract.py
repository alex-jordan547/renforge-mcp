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
    assert "_RF_TREE_BADGE" in tree
    assert "substitute False" in tree
    assert "_renforge_editor_tree_snippet" in editor

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
