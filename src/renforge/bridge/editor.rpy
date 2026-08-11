screen _renforge_editor_launcher():
    layer "renforge_editor"
    zorder 11999

    if _renforge_editor_is_editor_injected() and not _renforge_editor_is_active():
        textbutton "RF":
            id "rf_launcher"
            xalign 0.985
            yalign 0.025
            action Function(_renforge_editor_consume, _renforge_editor_activate)
            background Solid("#7c3aed")
            hover_background Solid("#8b5cf6")
            text_color "#ffffff"
            text_size 16
            xpadding 12
            ypadding 7
            at Transform(alpha=0.92)


screen _renforge_editor_overlay():
    layer "renforge_editor"
    zorder 12000

    if _renforge_editor_is_active():
        $ _rf_selection = _renforge_editor_selection_snapshot()
        $ _rf_label = _renforge_editor_label_snapshot()
        $ _rf_distance = _renforge_editor_distance_snapshot()
        $ _rf_measure = _renforge_editor_measure_snapshot()
        $ _rf_guide = _renforge_editor_guide_snapshot()
        $ _rf_tools_visible = _renforge_editor_tools_visible()
        $ _rf_canvas_tools_visible = _rf_tools_visible and _renforge_editor_view_mode() == "edit"

        fixed:
            xfill True
            yfill True
            at Transform(alpha=_renforge_editor_opacity())

            # Docked edit leaves empty window around the scaled game. Without a
            # stage fill Ren'Py shows its checkerboard there — not the maquette.
            # Paint only the void bands: a full-screen solid would cover the game
            # (editor layer is above the transformed game layers).
            if _renforge_editor_chrome_docked():
                $ _rf_stage = _renforge_editor_dock_stage_bands()
                for _rf_band in _rf_stage:
                    add Solid(
                        _renforge_editor_ui_color("stage"),
                        xysize=(_rf_band[2], _rf_band[3]),
                    ):
                        xpos _rf_band[0]
                        ypos _rf_band[1]

            # Keep canvas input in this container so events that pass through
            # the chrome reach it before Ren'Py continues to the game layer.
            # Panels and the toolbar are declared later and retain priority.
            add _renforge_editor_event_catcher()

            # Canvas decorations belong to the game surface. Paint them before
            # the chrome so selections near an edge never cover tree rows or
            # inspector controls in overlay mode.
            fixed:
                xfill True
                yfill True
                at _renforge_editor_canvas_transform
                use _rf_editor_canvas_decor(
                    _rf_canvas_tools_visible,
                    _rf_selection,
                    _rf_label,
                    _rf_distance,
                    _rf_measure,
                    _rf_guide,
                )

            if _renforge_editor_panels_visible():
                use _rf_editor_tree()
                use _rf_editor_inspector()
                use _rf_editor_style()
                use _rf_editor_hud()

            use _rf_editor_toolbar(_rf_tools_visible)

        if _renforge_editor_opacity() < 0.25:
            $ _rf_exit_rect = _renforge_editor_control_rect("rf_exit")
            if _rf_exit_rect is not None:
                $ _rf_exit_x = int(_rf_exit_rect[0])
                $ _rf_exit_y = int(_rf_exit_rect[1])
                $ _rf_exit_w = int(_rf_exit_rect[2])
                $ _rf_exit_h = int(_rf_exit_rect[3])
                add Solid("#a78bfa", xysize=(_rf_exit_w, 2)):
                    xpos _rf_exit_x
                    ypos _rf_exit_y
                add Solid("#a78bfa", xysize=(_rf_exit_w, 2)):
                    xpos _rf_exit_x
                    ypos _rf_exit_y + _rf_exit_h - 2
                add Solid("#a78bfa", xysize=(2, _rf_exit_h)):
                    xpos _rf_exit_x
                    ypos _rf_exit_y
                add Solid("#a78bfa", xysize=(2, _rf_exit_h)):
                    xpos _rf_exit_x + _rf_exit_w - 2
                    ypos _rf_exit_y


init 1090 python:
    # ── Design tokens (Lot 0.B) ─────────────────────────────────────────────
    # The editor chrome is designed against a 2560-wide canvas. Games run at
    # whatever width they please, so every measurement is authored in that
    # space and converted here. Nothing below may be hardcoded in a screen:
    # a literal pixel in screen language is a bug at 1280 and at 3840 alike.

    _RF_UI_BASE_WIDTH = 2560.0

    # The editor chrome stays deliberately opaque: it must remain readable over
    # any game scene without borrowing a glass/material effect from the host.
    _RF_UI_COLORS = {
        "panel": "#272729",
        "panel_head": "#2a2a2c",
        "sunken": "#00000057",      # black @ 0.34
        "hairline": "#ffffff1a",    # white @ 0.10
        "row_hover": "#ffffff0d",   # white @ 0.05
        "seg_on": "#ffffff21",      # white @ 0.13 (segment pressed)
        "surface": "#f5f5f7",
        "meta": "#a1a1a6",
        "border": "#d2d2d7",
        "fg": "#1d1d1f",
        "accent": "#0071e3",
        "accent_bright": "#2997ff",
        "accent_on": "#ffffff",
        "warn": "#eab308",
        # Canvas overlays sit on the game, not in the chrome, so they keep
        # the maquette's darker scrim rather than the panel fill.
        "scrim": "#111116e6",
        # Docked stage under the scaled canvas (maquette --stage-black).
        "stage": "#000000",
        # One colour per refusal level, so the severity reads before the words.
        "lock_locked": "#eab308",
        "lock_blocked": "#e8913c",
        "lock_refused": "#dc2626",
        "tree_screen": "#a855f7",
        "tree_layout": "#38bdf8",
        "tree_interactive": "#34d399",
        "tree_content": "#fbbf24",
        "tree_container": "#f472b6",
        "tree_transform": "#c084fc",
        "tree_guide": "#ffffff1c",
    }

    # Maquette type scale at 2560 (Apple base × k=1.5).
    _RF_T_MICRO = 18
    _RF_T_XS = 21
    _RF_T_SM = 26
    # Spacing scale at 2560 (space-N × k=1.5).
    _RF_S2 = 12
    _RF_S3 = 18
    _RF_S4 = 24
    _RF_S6 = 36

    def _renforge_editor_ui_scale(screen_w=None, screen_h=None):
        """Chrome scale for this game, derived from width and height.

        Clamped because the editor must stay usable on a 640-wide toy project
        without becoming a magnifying glass on a 5K one.
        """
        try:
            width = float(config.screen_width if screen_w is None else screen_w)
            height = float(config.screen_height if screen_h is None else screen_h)
        except Exception:
            return 1.0
        if width <= 0 or height <= 0:
            return 1.0
        return max(0.45, min(1.35, min(width / _RF_UI_BASE_WIDTH, height / 1440.0)))

    def _renforge_editor_ui_px(value, minimum=0):
        """Convert a 2560-space measurement into this game's pixels.

        ``minimum`` is a floor in *device* pixels. Icons use it so glyphs stay
        legible on 1280-wide games where pure scale would crush them to ~12 px.
        """
        px = int(round(float(value) * _renforge_editor_ui_scale()))
        if minimum:
            return max(int(minimum), px)
        return px

    def _renforge_editor_ui_text_px(base, minimum=12):
        """Chrome text size with a readable device-pixel floor."""
        return _renforge_editor_ui_px(base, minimum=minimum)

    def _renforge_editor_ui_frame(name):
        """Path of a shipped nine-patch, or a flat colour when assets are absent.

        Ren'Py builds rounded corners from an image, so a launch without the
        asset directory has to degrade to a square panel rather than crash.
        """
        import os
        assets = (os.environ.get("RENFORGE_EDITOR_ASSETS") or "").strip()
        if not assets:
            return Solid(_RF_UI_COLORS.get("panel", "#272729"))
        return "%s/frames/%s.png" % (assets, name)

    def _renforge_editor_ui_icon(name):
        """Path of a shipped toolbar icon (SVG), or a light square fallback.

        Icons travel under ``RENFORGE_EDITOR_ASSETS/icons/<name>.svg``. Without
        assets the chrome still lays out; it just loses the glyph.
        """
        import os
        assets = (os.environ.get("RENFORGE_EDITOR_ASSETS") or "").strip()
        if not assets:
            return Solid(_RF_UI_COLORS.get("surface", "#f5f5f7"))
        return "%s/icons/%s.svg" % (assets, name)

    def _renforge_editor_ui_color(name):
        """Look up a design token. Unknown names shout in magenta on purpose."""
        return _RF_UI_COLORS.get(name, "#ff00ff")


    def _renforge_editor_install_scrollbar_styles():
        """Apple-thin overlay scrollbars for every ``style_prefix "rf"`` viewport.

        Default game scrollbars are chunky and square — they read as a foreign
        control and poke past the panel's rounded corners. Scope the fix to the
        ``rf_`` prefix so the host game's own chrome is untouched.

        The thumb travel is shortened with gutters (not just widget margins):
        when scrolled fully to either end the capsule still has to sit inside
        the panel radius, otherwise it clips the rounded corner.
        """
        try:
            clear = Solid("#00000000")
            border = int(_RF_FRAME_SCROLL)
            thumb = Frame(_renforge_editor_ui_frame("scroll_thumb"), border, border)
            hover = Frame(_renforge_editor_ui_frame("scroll_thumb_hover"), border, border)
            width = max(4, int(_renforge_editor_ui_px(10, minimum=5)))
            top_edge = max(10, int(_renforge_editor_ui_px(_RF_FRAME_PANEL, minimum=14)))
            bottom_edge = max(14, int(_renforge_editor_ui_px(_RF_FRAME_PANEL + 4, minimum=18)))
            # Panel frame corners keep their source-pixel radius when the rest
            # of the chrome scales, so this gutter must use that fixed radius.
            bottom_radius = int(_RF_FRAME_PANEL)
            # Pull off the right edge a bit (reads as floating, not flush).
            inset = max(6, int(_renforge_editor_ui_px(12, minimum=8)))

            for style_name in ("rf_vscrollbar", "rf_scrollbar"):
                try:
                    bar = getattr(style, style_name)
                except Exception:
                    continue
                bar.base_bar = clear
                bar.thumb = thumb
                bar.hover_thumb = hover
                bar.unscrollable = "hide"
                bar.thumb_offset = 0
                bar.bar_resizing = True
                if style_name == "rf_vscrollbar":
                    bar.xsize = width
                    bar.xmaximum = width
                    bar.top_margin = top_edge
                    bar.bottom_margin = bottom_edge
                    bar.right_margin = inset
                    bar.left_margin = 0
                    # Runtime style assignment uses the orientation-neutral
                    # names consumed by Bar.render; top_gutter/bottom_gutter
                    # are only translated by screen/style-language parsing.
                    bar.fore_gutter = 0
                    bar.aft_gutter = bottom_radius
                    bar.left_gutter = 0
                    bar.right_gutter = 0
                else:
                    bar.ysize = width
                    bar.ymaximum = width
                    bar.left_margin = top_edge
                    bar.right_margin = bottom_edge
                    bar.bottom_margin = inset
                    bar.top_margin = 0
                    bar.left_gutter = 0
                    bar.right_gutter = 0
                    bar.fore_gutter = 0
                    bar.aft_gutter = 0
        except Exception:
            pass

    # ── Layout geometry (maquette 2560-space) ────────────────────────────────
    # Overlay = floating chrome on a full-screen game.
    # Docked  = solid edge rails + scaled canvas between them.
    # Preview always releases dock chrome and the canvas scale.
    # Numbers are authored once here; screens and transforms only read them.

    _RF_DOCK_SCALE = 0.57
    _RF_DOCK_CANVAS_X = 550
    _RF_DOCK_CANVAS_Y = 372
    _RF_DOCK_TOOLBAR_H = 124
    _RF_DOCK_RAIL_W = 540
    _RF_DOCK_TREE_Y = 124
    _RF_DOCK_TREE_H = 968
    _RF_DOCK_INSPECTOR_Y = 124
    _RF_DOCK_INSPECTOR_H = 624
    _RF_DOCK_STYLE_Y = 748
    _RF_DOCK_STYLE_H = 692
    _RF_DOCK_HUD_X = 576
    _RF_DOCK_HUD_Y = 148
    _RF_DOCK_HUD_W = 1408

    # Overlay floating toolbar (maquette: left 32, top 28, w 2496, h 96).
    _RF_OVERLAY_INSET = 32
    _RF_OVERLAY_TOOLBAR_Y = 28
    _RF_OVERLAY_TOOLBAR_H = 96
    _RF_OVERLAY_TOOLBAR_W = 2496
    # Screen-name/status labels may shrink, but trailing actions must always fit.
    _RF_BADGE_MAX_W = 240
    _RF_BADGE_TEXT_MAX_W = 200
    _RF_STATUS_MAX_W = 120
    _RF_OVERLAY_TREE_X = 32
    _RF_OVERLAY_TREE_Y = 148
    _RF_OVERLAY_TREE_W = 480
    _RF_OVERLAY_TREE_H = 952
    _RF_OVERLAY_INSPECTOR_X = 1988
    _RF_OVERLAY_INSPECTOR_Y = 148
    _RF_OVERLAY_INSPECTOR_H = 600
    _RF_OVERLAY_STYLE_X = 1988
    _RF_OVERLAY_STYLE_Y = 772
    _RF_OVERLAY_STYLE_H = 636
    _RF_OVERLAY_PANEL_W = 540
    # HUD overlay: left 536, top 148, w 1180, h 56.
    _RF_OVERLAY_HUD_X = 536
    _RF_OVERLAY_HUD_Y = 148
    _RF_OVERLAY_HUD_W = 1180
    _RF_OVERLAY_HUD_H = 56
    _RF_HANDLE_PX = 18
    _RF_TREE_BADGE = 34
    _RF_ANCHOR_CELL = 28
    # Maquette tool cells 60×60; iconbtn actions 44×44; glyphs 28 / 24.
    # Floors keep hits/glyphs readable when the host game is 1280-wide (scale 0.5).
    _RF_ICON_BTN = 60
    _RF_ICON_ACTION = 44
    _RF_ICON_GLYPH = 28
    _RF_ICON_GLYPH_SM = 24
    _RF_ICON_BTN_MIN = 28
    _RF_ICON_ACTION_MIN = 28
    _RF_ICON_GLYPH_MIN = 18
    _RF_ICON_GLYPH_SM_MIN = 18
    _RF_BRAND_MARK = 44
    _RF_SAVE_H = 56
    _RF_SEG_H = 42
    _RF_VRULE_H = 44
    # Frame() borders for generated nine-patches (see scripts/gen_editor_frames.py).
    _RF_FRAME_PANEL = 28
    _RF_FRAME_CHIP = 13
    _RF_FRAME_TOOLS = 19
    _RF_FRAME_PILL = 29
    _RF_FRAME_BRAND = 13
    # Capsule scroll thumb is 20×40 → Frame border = half width.
    _RF_FRAME_SCROLL = 10

    # ── Interface catalogue (Lot 0.D) ───────────────────────────────────────
    # The overlay is a guest in someone else's game, so it never touches
    # renpy.change_language(): that would replay the host's translation blocks
    # and rebuild its styles. It carries its own catalogue instead, shipped
    # beside the .rpy and selected by the launcher's RENFORGE_EDITOR_LANG.

    # English lives in the .rpy, not only in a shipped file. A catalogue that
    # fails to load must degrade to readable English, never to raw keys: an
    # editor whose button reads "toolbar.exit" is worse than one that was never
    # translated. The JSON files add languages and may override, nothing more.
    _RF_UI_STRINGS = {
        "toolbar.exit": "Exit",
        "toolbar.undo": "Undo",
        "toolbar.redo": "Redo",
        "toolbar.reset": "Reset",
        "toolbar.tools_on": "Tools On",
        "toolbar.tools_off": "Tools Off",
        "toolbar.opacity_down": "-",
        "toolbar.opacity_up": "+",
        "toolbar.brand": "RENFORGE · LIVE",
        "launcher.activate": "RF",
        "save.idle": "Save",
        "save.saving": "Saving / Reloading...",
        "save.saved": "Saved",
        "lock.locked": "Locked",
        "lock.blocked": "Blocked here",
        "lock.refused": "Refused",
        "lock.reason.xpos_literal_required": "Position must use a literal xpos value.",
        "lock.reason.ypos_literal_required": "Position must use a literal ypos value.",
        "lock.reason.literal_required": "This property must use a literal value in source.",
        "lock.reason.unsupported": "This source form is not editable yet.",
        "lock.reason.mismatch": "The live element no longer matches its source.",
        "lock.reason.ambiguous": "This selection does not resolve to one unique source element.",
        "lock.reason.default": "This selection cannot be edited safely.",
        "tree.title": "SCENE TREE",
        "tree.items_count": "{count} items",
        "tree.items_more": "{count}+ items",
        "tree.screen": "SCREEN",
        "tree.truncated": "More items not shown",
        "panel.tree": "Scene Tree",
        "panel.inspector": "Inspector",
        "panel.style": "Style",
        "panel.hide": "Hide {panel}",
        "panel.show": "Show {panel}",
        "style.title": "STYLE",
        "inspector.offset": "OFFSET",
        "inspector.anchor": "ANCHOR",
        "inspector.fill": "FILL",
        "inspector.read_only": "Read only",
        "style.color": "Cycle colour",
        "style.color_value": "Colour {value}",
        "style.locked": "Outside the allowlist",
        "analysis.pending": "Analyzing selection…",
        "reload.active": "Hot-reload active",
        "reload.reloading": "Reloading…",
        "reload.failed": "Reload failed",
        "hud.pending": "{count} unsaved change(s)",
        "hud.selection": "Selection: {value}",
        "hud.none": "no selection",
        "toolbar.screen": "screen",
        "toolbar.jump": "Jump to a label",
        "toolbar.jump_none": "—",
        "toolbar.jump_empty": "No labels to jump to",
        "toolbar.opacity_value": "{value} %",
        "toolbar.select": "Select",
        "toolbar.move": "Move",
        "toolbar.measure": "Measure",
        "toolbar.picker": "Picker",
        "toolbar.text": "Text",
        "toolbar.hand": "Hand",
        "toolbar.preview": "Preview",
        "toolbar.edit": "Edit",
        "toolbar.overlay": "Overlay",
        "toolbar.docked": "Edges",
        "toolbar.guides": "Guides",
        "inspector.position": "POSITION",
        "inspector.size": "SIZE",
        "inspector.no_geometry": "No measured geometry for this selection.",
        "inspector.none": "No selection",
        "style.none": "No selection",
        "style.color_label": "Text colour",
        "status.ready": "Ready",
        "status.analyzing": "Analyzing",
        "status.analyzed": "Analyzed",
        "status.undo": "Undo",
        "status.undo_unavailable": "Undo unavailable",
        "status.undoing": "Undoing",
        "status.redo": "Redo",
        "status.redo_unavailable": "Redo unavailable",
        "status.reset": "Reset",
        "status.saving": "Saving",
        "status.commit_queued": "Commit queued",
        "status.undo_queued": "Undo queued",
        "status.committed": "Committed",
        "status.reload_committed": "Reload committed",
        "status.analyze_failed": "Analyze failed",
        "status.commit_failed": "Commit failed",
        "status.status_failed": "Status failed",
        "status.reload_failed": "Reload failed",
        "status.reload_handshake_failed": "Reload handshake failed",
        "status.invalid_result": "Invalid result",
        "status.locked": "Locked",
    }
    _RF_UI_STRINGS_READY = []

    def _renforge_editor_language():
        import os
        return (os.environ.get("RENFORGE_EDITOR_LANG") or "").strip() or "en"

    def _renforge_editor_ui_font():
        """Interface font: the borrowed CJK face, else the game's own.

        Never None. Ren'Py does not read a None font as "inherit" — it passes
        it to load_face as a filename and dies on `"@" in fn`. So when no font
        was borrowed, hand back the font the game is already drawing with.
        """
        import os
        assets = (os.environ.get("RENFORGE_EDITOR_ASSETS") or "").strip()
        font = (os.environ.get("RENFORGE_EDITOR_FONT") or "").strip()
        if assets and font:
            return "%s/%s" % (assets, font)
        try:
            inherited = style.default.font
        except Exception:
            inherited = None
        return inherited or "DejaVuSans.ttf"

    def _renforge_editor_load_strings():
        """Overlay the requested locale on the built-in English, key by key.

        The requested language overrides; the English catalogue only fills keys
        neither it nor the built-ins provide. A half-translated catalogue
        therefore degrades one label at a time instead of wholesale.
        """
        import json as _json
        import os

        assets = (os.environ.get("RENFORGE_EDITOR_ASSETS") or "").strip()
        if not assets:
            return
        for candidate, overrides in ((_renforge_editor_language(), True), ("en", False)):
            try:
                handle = renpy.open_file("%s/locales/%s.json" % (assets, candidate))
            except Exception:
                continue
            try:
                payload = handle.read()
                if not isinstance(payload, str):
                    payload = payload.decode("utf-8")
                for key, value in _json.loads(payload).items():
                    if not value:
                        continue
                    if overrides or key not in _RF_UI_STRINGS:
                        _RF_UI_STRINGS[key] = value
            except Exception:
                pass
            finally:
                try:
                    handle.close()
                except Exception:
                    pass

    def _renforge_editor_t(key):
        """Translate an interface string.

        A missing key renders as the key itself. A blank label is a bug report
        the user cannot describe; a visible ``toolbar.exit`` is one we can.
        """
        if not _RF_UI_STRINGS_READY:
            _RF_UI_STRINGS_READY.append(True)
            try:
                _renforge_editor_load_strings()
            except Exception:
                pass
        return _RF_UI_STRINGS.get(key, key)


init 1095 python in _renforge_editor_runtime:
    # This store is rebuilt by script reloads and excluded from save data.
    # Keeping the custom displayable here prevents reload slots from referring
    # to a class in the default store before that store has been restored.
    _constant = True

    class EventCatcher(renpy.Displayable):
        def render(self, width, height, st, at):
            return renpy.Render(int(max(1, width)), int(max(1, height)))

        def event(self, event, x, y, st):
            return renpy.store._renforge_editor_handle_event(event, x, y, st)

    event_catcher = EventCatcher()
    event_catcher._renforge_editor_owner = "renforge.editor.v1"


init 1100 python:
    import builtins
    import hashlib
    import json
    import os
    import socket
    import sys
    import threading
    import time
    import types
    import uuid

    try:
        import pygame_sdl2 as pygame
    except Exception:
        pygame = None

    if "_renforge_runtime" not in sys.modules:
        raise Exception("RenForge bridge must load before editor.rpy")
    _renforge_runtime_module = sys.modules["_renforge_runtime"]
    if not hasattr(_renforge_runtime_module, "editor_v1"):
        _renforge_runtime_module.editor_v1 = types.SimpleNamespace()

    _EDITOR_SCREEN = "_renforge_editor_overlay"
    _EDITOR_LAUNCHER_SCREEN = "_renforge_editor_launcher"
    _EDITOR_LAYER = "renforge_editor"
    _EDITOR_SCREENS = set([_EDITOR_SCREEN, _EDITOR_LAUNCHER_SCREEN])
    _EDITOR_OWNER = "renforge.editor.v1"
    _EDITOR_VIOLET = "#a78bfa"
    _EDITOR_AMBER = "#f59e0b"
    _EDITOR_LOCKED_TEXT = "LOCKED"
    # Protocol value mirrored by renforge.editor.source.BAR_SIZE_MODE_XSIZE_YSIZE.
    _BAR_SIZE_MODE_XSIZE_YSIZE = "xsize_ysize"
    _SNAP_ACQUIRE = 6
    _SNAP_RELEASE = 10
    _CACHE_WALK_MAX_DEPTH = 32

    if not hasattr(renpy.config, "top_layers"):
        renpy.config.top_layers = []
    if _EDITOR_LAYER not in renpy.config.top_layers:
        renpy.config.top_layers.append(_EDITOR_LAYER)
    if not hasattr(renpy.config, "layer_transforms"):
        renpy.config.layer_transforms = {}
    _ALLOWED_ANCESTRY_TYPES = set(
        [
            "ScreenDisplayable",
            "Fixed",
            "MultiBox",
            "Button",
            "ImageButton",
            "Bar",
            "Text",
            "Frame",
            "Window",
            "Transform",
            "Viewport",
            "Container",
            "Grid",
            "HBox",
            "VBox",
            "Drag",
            "Null",
        ]
    )

    # Ancestry types that are locked for a known, specific reason rather than
    # simply being unrecognised. `at <atl transform>` resolves to ATLTransform,
    # which subclasses Transform but reports its own class name (issue #51):
    # measured on 8.5.3, an active ATL resets show time on every
    # `_widget_properties` preview and overwrites position overrides, so these
    # targets stay locked — but the editor should say why.
    _ATL_ANCESTRY_TYPES = set(["ATLTransform"])

    def _renforge_editor_ancestry_lock_code(class_name):
        """Lock code for an ancestry type outside the allowlist."""
        if class_name in _ATL_ANCESTRY_TYPES:
            return "ATL_ANIMATION_UNSUPPORTED"
        return "UNKNOWN_ANCESTRY_TYPE"

    def _renforge_editor_state():
        state = _renforge_runtime_module.editor_v1
        if not hasattr(state, "initialized"):
            state.initialized = True
            state.script_generation = 0
            state.active = False
            state.screen = None
            state.editor_injected = _renforge_editor_host_config() is not None
            state.editor_session_screen = None
            state.selected_runtime_key = None
            state.selected_widget_id = None
            state.selected_screen = None
            state.selected_target_key = None
            state.selected_lock_reason = None
            state.selected_original_position = None
            state.selected_source_position = None
            state.selected_original_size = None
            state.selected_source_size = None
            state.selected_rect = None
            state.preview_position = None
            state.preview_size = None
            state.style_color_input = ""
            state.pointer = [0, 0]
            state.drag_active = False
            state.drag_offset = [0, 0]
            state.drag_start_position = None
            state.resize_active = False
            state.resize_handle = None
            state.resize_start_pointer = None
            state.resize_start_position = None
            state.resize_start_size = None
            state.resize_start_rect = None
            state.snap_anchor_x = None
            state.snap_anchor_y = None
            state.snap_offset_x = None
            state.snap_offset_y = None
            state.snap_candidates_x = None
            state.snap_candidates_y = None
            state.snap_target_x_rect = None
            state.snap_target_y_rect = None
            state.guide_x = None
            state.guide_y = None
            state.guide_x_span = None
            state.guide_y_span = None
            state.opacity = 0.86
            state.tools_visible = True
            state.tree_collapsed_keys = set()
            state.jump_open = False
            state.jump_context_last = None
            state.layout_mode = "overlay"
            state.label_rect = [20, 20, 260, 32]
            state.label_alpha = 1.0
            state.label_text = "No selection"
            state.status_code = "ready"
            state.status_expires_at = None
            state.status_text = "Ready"
            state.save_button_state = "idle"
            state.save_enabled = False
            state.save_in_progress = False
            state.save_requested = False
            state.save_last_error = None
            state.save_error = None
            _renforge_editor_clear_current_analysis(state)
            state.selected_analysis_pending = False
            state.history = []
            state.history_index = -1
            state.history_entries = []
            state.targets = {}
            state.pending_analysis_key = None
            state.pending_transaction_id = None
            state.pending_transaction_state = None
            state.pending_operation = None
            state.last_committed_transaction_id = None
            state.pending_commit_is_style_color = False
            state.pending_commit_is_zorder = False
            state.refuse_next_style_attestation = False
            state.pending_commit_request_id = None
            state.pending_status_request_id = None
            state.pending_attest_request_id = None
            state.pending_handshake_sent = False
            state.pending_handshake_generation = None
            state.pending_reload_draw_generation = None
            state.pending_reload_started = False
            state.pending_reload_requested = False
            state.last_commit_status = None
            state.pending_coordinator_request = None
            state.barrier_counter = 0
            state.runtime_targets = {}
            state.accepted_observations = []
            state.last_event_trace = []
            state.coordinator = None
            state.coordinator_applied = []
            state.last_coordinator_apply = None
            state.last_restore_method = None
            state.last_preview_method = None
            state.main_thread_id = None
        if not hasattr(state, "snap_offset_x"):
            state.snap_offset_x = None
        if not hasattr(state, "snap_offset_y"):
            state.snap_offset_y = None
        if not hasattr(state, "snap_candidates_x"):
            state.snap_candidates_x = None
        if not hasattr(state, "snap_candidates_y"):
            state.snap_candidates_y = None
        if not hasattr(state, "snap_target_x_rect"):
            state.snap_target_x_rect = None
        if not hasattr(state, "snap_target_y_rect"):
            state.snap_target_y_rect = None
        if not hasattr(state, "guide_x_span"):
            state.guide_x_span = None
        if not hasattr(state, "guide_y_span"):
            state.guide_y_span = None
        if not hasattr(state, "tools_visible"):
            state.tools_visible = True
        if not hasattr(state, "tree_collapsed_keys"):
            state.tree_collapsed_keys = set()
        if not hasattr(state, "jump_open"):
            state.jump_open = False
        if not hasattr(state, "jump_context_last"):
            state.jump_context_last = None
        if not hasattr(state, "layout_mode"):
            state.layout_mode = "overlay"
        if not hasattr(state, "selected_source_position"):
            state.selected_source_position = None
        if not hasattr(state, "selected_target_key"):
            state.selected_target_key = None
        if not hasattr(state, "save_button_state"):
            state.save_button_state = "idle"
        if not hasattr(state, "pending_reload_draw_generation"):
            state.pending_reload_draw_generation = None
        if not hasattr(state, "pending_reload_started"):
            state.pending_reload_started = False
        if not hasattr(state, "current_capabilities"):
            state.current_capabilities = {}
        if not hasattr(state, "selected_original_size"):
            state.selected_original_size = None
        if not hasattr(state, "selected_source_size"):
            state.selected_source_size = None
        if not hasattr(state, "preview_size"):
            state.preview_size = None
        if not hasattr(state, "style_color_input"):
            state.style_color_input = ""
        if not hasattr(state, "pending_operation"):
            state.pending_operation = None
        if not hasattr(state, "last_committed_transaction_id"):
            state.last_committed_transaction_id = None
        if not hasattr(state, "pending_commit_is_style_color"):
            state.pending_commit_is_style_color = False
        if not hasattr(state, "pending_commit_is_zorder"):
            state.pending_commit_is_zorder = False
        if not hasattr(state, "refuse_next_style_attestation"):
            state.refuse_next_style_attestation = False
        if not hasattr(state, "resize_active"):
            state.resize_active = False
        if not hasattr(state, "resize_handle"):
            state.resize_handle = None
        if not hasattr(state, "resize_start_pointer"):
            state.resize_start_pointer = None
        if not hasattr(state, "resize_start_position"):
            state.resize_start_position = None
        if not hasattr(state, "resize_start_size"):
            state.resize_start_size = None
        if not hasattr(state, "resize_start_rect"):
            state.resize_start_rect = None
        if not hasattr(state, "status_code"):
            state.status_code = "ready"
        if not hasattr(state, "status_expires_at"):
            state.status_expires_at = None
        return state


    def _renforge_editor_is_editor_injected():
        return bool(_renforge_editor_state().editor_injected)


    def _renforge_editor_consume(callback, *args):
        """Run an editor control's action and report nothing back to Ren'Py.

        `Function` ends the interaction as soon as its callable returns a
        non-None value (behavior.py, Button.handle_click), and every editor
        callback returns a status dict. That ended the interaction and
        dismissed the dialogue underneath, so pressing Exit or Tools also
        advanced the story. Returning None lets Ren'Py consume the click.
        """
        callback(*args)


    def _renforge_editor_activate():
        state = _renforge_editor_state()
        if not _renforge_editor_is_editor_injected():
            return {"ok": False, "error": "editor session unavailable"}
        state.active = True
        state.selected_lock_reason = None
        try:
            _renforge_editor_install_scrollbar_styles()
        except NameError:
            pass
        _renforge_editor_apply_layout_transform()
        renpy.show_screen(_EDITOR_SCREEN, _layer=_EDITOR_LAYER)
        renpy.restart_interaction()
        return {"ok": True}


    def _renforge_editor_selection_snapshot():
        state = _renforge_editor_state()
        if state.selected_rect is None:
            return None
        rect = list(state.selected_rect)
        if len(rect) != 4:
            return None
        if state.selected_lock_reason is None:
            color = _EDITOR_VIOLET
        else:
            color = _EDITOR_AMBER
        return {"x": int(rect[0]), "y": int(rect[1]), "w": int(rect[2]), "h": int(rect[3]), "color": color}


    _RF_STATUS_TRANSIENT = frozenset({
        "analyzed",
        "undo",
        "undo_unavailable",
        "redo",
        "redo_unavailable",
        "reset",
        "locked",
    })
    _RF_STATUS_ONGOING = frozenset({
        "analyzing",
        "undoing",
        "saving",
        "commit_queued",
        "undo_queued",
    })
    _RF_STATUS_TERMINAL = frozenset({
        "committed",
        "reload_committed",
    })
    _RF_STATUS_FAILURE = frozenset({
        "analyze_failed",
        "commit_failed",
        "status_failed",
        "reload_failed",
        "reload_handshake_failed",
        "invalid_result",
    })

    def _renforge_editor_status_label(code):
        """Translate a status code; fall back when the catalogue init is absent."""
        key = "status." + str(code or "ready")
        try:
            return _renforge_editor_t(key)
        except Exception:
            return str(code or "ready")

    def _renforge_editor_set_status(code, *, transient=None):
        """Publish a stable status code; UI text is always translated from it."""
        state = _renforge_editor_state()
        code = str(code or "ready")
        state.status_code = code
        if transient is None:
            transient = code in _RF_STATUS_TRANSIENT
        if transient:
            state.status_expires_at = time.monotonic() + 2.0
        else:
            state.status_expires_at = None
        # Keep legacy field in sync for any residual screen probes.
        state.status_text = _renforge_editor_status_label(code)
        return code

    def _renforge_editor_status_code():
        state = _renforge_editor_state()
        expires = getattr(state, "status_expires_at", None)
        if expires is not None and time.monotonic() >= float(expires):
            _renforge_editor_set_status("ready", transient=False)
        return str(getattr(state, "status_code", None) or "ready")

    def _renforge_editor_status_tick():
        """Expire a transient HUD status without advancing the story.

        Ren'Py's Function action ends the current interaction when the
        callable returns non-None (behavior.py). The HUD used to fire
        `Function(_renforge_editor_status_code)` every 0.25s, and that
        always returns a string — so dialogue dismissed and the game
        rolled forward on its own. Tick returns None; it only restarts
        the interaction when a transient notice actually expires.
        """
        state = _renforge_editor_state()
        expires = getattr(state, "status_expires_at", None)
        if expires is not None and time.monotonic() >= float(expires):
            _renforge_editor_set_status("ready", transient=False)
            renpy.restart_interaction()

    def _renforge_editor_status_text():
        return _renforge_editor_status_label(_renforge_editor_status_code())


    def _renforge_editor_save_enabled():
        state = _renforge_editor_state()
        return (
            bool(state.save_enabled)
            and not bool(state.save_in_progress)
            and not bool(state.selected_lock_reason)
            and state.current_analysis_id is not None
        )


    def _renforge_editor_save_label():
        state = _renforge_editor_state()
        if state.save_button_state == "saving":
            return _renforge_editor_t("save.saving")
        if state.save_button_state == "saved":
            return _renforge_editor_t("save.saved")
        return _renforge_editor_t("save.idle")


    def _renforge_editor_tools_visible():
        return bool(_renforge_editor_state().tools_visible)


    def _renforge_editor_lock_code(lock_reason):
        if isinstance(lock_reason, builtins.dict):
            return str(lock_reason.get("code") or "")
        if lock_reason is None:
            return None
        return str(lock_reason)


    # ── Scene tree (Lot 1.B) ────────────────────────────────────────────────
    # Built from the live displayable tree, not from a description of it. Ren'Py
    # wraps generously, so a raw walk buries the author's structure under
    # engine scaffolding: only nodes the author would recognise are listed,
    # while the walk still descends through the ones it hides.

    _RF_TREE_KINDS = {
        "Frame": ("F", "frame", "container"),
        "Window": ("W", "window", "container"),
        "Text": ("T", "text", "content"),
        "Button": ("B", "button", "interactive"),
        "TextButton": ("B", "textbutton", "interactive"),
        "ImageButton": ("B", "imagebutton", "interactive"),
        "Viewport": ("P", "viewport", "container"),
        "Bar": ("R", "bar", "interactive"),
        "Grid": ("G", "grid", "layout"),
        "Image": ("I", "image", "content"),
        "ImageReference": ("I", "image", "content"),
        "Transform": ("X", "transform", "transform"),
    }
    _RF_TREE_MAX_ROWS = 1000
    _RF_TREE_MAX_DEPTH = 64

    def _renforge_editor_tree_kind(displayable):
        """(badge, label, category) for a displayable, or None when it is scaffolding."""
        name = type(displayable).__name__
        if name == "MultiBox":
            layout = getattr(displayable, "layout", None)
            if layout == "vertical":
                return ("V", "vbox", "layout")
            if layout == "horizontal":
                return ("H", "hbox", "layout")
            return ("X", "fixed", "layout")
        return _RF_TREE_KINDS.get(name)

    def _renforge_editor_tree_badge_color(tag):
        """Return hex color for a tree row badge based on its tag/type."""
        if tag == "S":
            return _renforge_editor_ui_color("tree_screen")
        elif tag in ("V", "H", "G", "X"):
            return _renforge_editor_ui_color("tree_layout")
        elif tag in ("B", "R"):
            return _renforge_editor_ui_color("tree_interactive")
        elif tag in ("T", "I"):
            return _renforge_editor_ui_color("tree_content")
        elif tag in ("F", "P", "W"):
            return _renforge_editor_ui_color("tree_container")
        return _renforge_editor_ui_color("tree_transform")

    def _renforge_editor_tree_flatten_text(value):
        """Pull plain text out of Ren'Py Text / segment containers."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (builtins.list, builtins.tuple, builtins.set)):
            parts = []
            for item in value:
                flat = _renforge_editor_tree_flatten_text(item)
                if flat:
                    parts.append(flat)
            return " ".join(parts)
        # Segment-like objects expose nested .text; never str() the container
        # (str(list) produces "['…']" which blows up Ren'Py substitution).
        nested = getattr(value, "text", None)
        if nested is not None and nested is not value:
            return _renforge_editor_tree_flatten_text(nested)
        return ""

    def _renforge_editor_tree_escape(value):
        """Make a string safe for Ren'Py ``text`` (interpolation + text tags)."""
        text_str = " ".join(str(value or "").split())
        # Double so Ren'Py shows a single character even if substitute is on.
        text_str = text_str.replace("{", "{{").replace("[", "[[")
        return text_str

    def _renforge_editor_tree_snippet(displayable, name):
        """Extract a short, human-readable preview snippet for tree rows."""
        try:
            if name in ("Text", "TextButton"):
                text_str = _renforge_editor_tree_escape(
                    _renforge_editor_tree_flatten_text(getattr(displayable, "text", None))
                )
                if text_str:
                    if len(text_str) > 20:
                        return text_str[:18] + "…"
                    return text_str
            elif name in ("Image", "ImageReference"):
                img_name = getattr(displayable, "name", None)
                val = _renforge_editor_tree_escape(
                    _renforge_editor_tree_flatten_text(img_name)
                )
                if val:
                    if len(val) > 22:
                        return val[:20] + "…"
                    return val
            elif name in ("MultiBox", "Grid"):
                children = _renforge_editor_children(displayable)
                if children:
                    return "%d items" % len(children)
        except Exception:
            pass
        return ""

    def _renforge_editor_tree_walk(
        displayable,
        depth,
        path,
        by_id,
        runtime_by_object,
        rows,
        selected_id,
        selected_runtime_key,
        selected_screen,
        screen_name,
        visited,
        counters,
        visible=True,
    ):
        try:
            node_key = (screen_name, builtins.id(displayable))
        except Exception:
            node_key = (screen_name, None)
        if node_key in visited:
            return
        visited.add(node_key)
        if depth > _RF_TREE_MAX_DEPTH:
            counters["depth_truncated"] = True
            return
        kind = _renforge_editor_tree_kind(displayable)
        widget_id = by_id.get(builtins.id(displayable), "")
        runtime_key = runtime_by_object.get(builtins.id(displayable))
        # A wrapper with no id and no recognised kind is engine plumbing: step
        # through it without spending a row or a level of indentation on it.
        if kind is None and not widget_id:
            for child_index, child in enumerate(_renforge_editor_children(displayable) or []):
                _renforge_editor_tree_walk(
                    child, depth, path + (child_index,), by_id, runtime_by_object, rows,
                    selected_id, selected_runtime_key, selected_screen,
                    screen_name, visited, counters, visible,
                )
            return
        counters["total"] += 1
        if counters["total"] > _RF_TREE_MAX_ROWS:
            counters["count_truncated"] = True
        if kind is not None:
            badge, label, kind_cat = kind
        else:
            badge, label, kind_cat = ("?", type(displayable).__name__, "default")
        snippet = _renforge_editor_tree_snippet(displayable, type(displayable).__name__)
        badge_color = _renforge_editor_tree_badge_color(badge)
        children = _renforge_editor_children(displayable) or []
        tree_key = (str(screen_name), tuple(path))
        has_children = bool(children) and depth < _RF_TREE_MAX_DEPTH
        expanded = has_children and tree_key not in _renforge_editor_state().tree_collapsed_keys
        if visible and len(rows) < _RF_TREE_MAX_ROWS:
            rows.append({
                "depth": depth,
                "tag": badge,
                "label": label,
                "id": widget_id,
                "runtime_key": runtime_key,
                "selectable": bool(widget_id or runtime_key),
                "source_location": (
                    list(runtime_key.get("source_location") or [])
                    if isinstance(runtime_key, builtins.dict)
                    else None
                ),
                "snippet": snippet,
                "kind_cat": kind_cat,
                "badge_color": badge_color,
                "screen_name": screen_name,
                "node_key": tree_key,
                "toggle_id": "rf_tree_toggle_%s_%s" % (
                    screen_name,
                    "_".join(str(part) for part in path),
                ),
                "has_children": has_children,
                "expanded": expanded,
                "selected": bool(
                    (
                        isinstance(runtime_key, builtins.dict)
                        and isinstance(selected_runtime_key, builtins.dict)
                        and runtime_key == selected_runtime_key
                    )
                    or (
                        not isinstance(selected_runtime_key, builtins.dict)
                        and widget_id
                        and widget_id == selected_id
                        and screen_name == selected_screen
                    )
                ),
            })
        elif visible:
            counters["count_truncated"] = True
        if depth >= _RF_TREE_MAX_DEPTH:
            # Count this node but do not descend past the depth cap.
            if children:
                counters["depth_truncated"] = True
            return
        for child_index, child in enumerate(children):
            _renforge_editor_tree_walk(
                child, depth + 1, path + (child_index,), by_id, runtime_by_object, rows,
                selected_id, selected_runtime_key, selected_screen,
                screen_name, visited, counters, visible and expanded,
            )

    def _renforge_editor_toggle_tree_node(node_key):
        """Toggle one disclosure node without touching game/save state."""
        state = _renforge_editor_state()
        if node_key in state.tree_collapsed_keys:
            state.tree_collapsed_keys.remove(node_key)
            expanded = True
        else:
            state.tree_collapsed_keys.add(node_key)
            expanded = False
        renpy.restart_interaction()
        return {"ok": True, "expanded": expanded}

    def _renforge_editor_tree_rows():
        """Flatten every active game screen into displayable rows.

        Returns ``{rows, total, count_truncated, depth_truncated}``. At most 1000
        real rows are kept; when either truncation flag is set, one terminal
        translated row is appended.
        """
        rows = []
        counters = {"total": 0, "count_truncated": False, "depth_truncated": False}
        visited = set()
        selected_id = ""
        selected_runtime_key = None
        selected_screen = ""
        try:
            state = _renforge_editor_state()
            selected_id = str(state.selected_widget_id or "")
            selected_runtime_key = state.selected_runtime_key
            selected_screen = str(state.selected_screen or "")
        except Exception:
            selected_id = ""
            selected_runtime_key = None
            selected_screen = ""
        runtime_by_object = {}
        for candidate in _renforge_editor_all_candidates():
            widget = candidate.get("focused_widget")
            runtime_key = candidate.get("runtime_key")
            if widget is not None and isinstance(runtime_key, builtins.dict):
                runtime_by_object[builtins.id(widget)] = runtime_key
        for screen_name in _renforge_editor_active_game_screens():
            screen, widgets = _renforge_editor_widget_map(screen_name)
            if screen is None:
                continue
            by_id = {}
            for wid, widget in widgets.items():
                try:
                    by_id[builtins.id(widget)] = str(wid)
                except Exception:
                    pass
            counters["total"] += 1
            if counters["total"] > _RF_TREE_MAX_ROWS:
                counters["count_truncated"] = True
            screen_key = ("screen", str(screen_name))
            screen_expanded = screen_key not in _renforge_editor_state().tree_collapsed_keys
            if len(rows) < _RF_TREE_MAX_ROWS:
                rows.append({
                    "depth": 0, "tag": "S", "label": "screen",
                    "id": str(screen_name), "snippet": "",
                    "kind_cat": "screen",
                    "badge_color": _renforge_editor_ui_color("tree_screen"),
                    "screen_name": str(screen_name),
                    "node_key": screen_key,
                    "toggle_id": "rf_tree_toggle_screen_" + str(screen_name),
                    "has_children": getattr(screen, "child", None) is not None,
                    "expanded": screen_expanded,
                    "selected": False,
                })
            else:
                counters["count_truncated"] = True
            child = getattr(screen, "child", None)
            if child is not None:
                _renforge_editor_tree_walk(
                    child, 1, (0,), by_id, runtime_by_object, rows,
                    selected_id, selected_runtime_key, selected_screen,
                    str(screen_name), visited, counters, screen_expanded,
                )
        count_truncated = bool(counters["count_truncated"])
        depth_truncated = bool(counters["depth_truncated"])
        if count_truncated or depth_truncated:
            rows.append({
                "depth": 0,
                "tag": "…",
                "label": _renforge_editor_t("tree.truncated"),
                "id": "",
                "snippet": "",
                "kind_cat": "truncated",
                "badge_color": _renforge_editor_ui_color("meta"),
                "screen_name": "",
                "node_key": None,
                "toggle_id": "",
                "has_children": False,
                "expanded": False,
                "selected": False,
                "truncated": True,
            })
        return {
            "rows": rows,
            "total": int(counters["total"]),
            "count_truncated": count_truncated,
            "depth_truncated": depth_truncated,
        }

    def _renforge_editor_duplicate_widget_screens():
        widget_screens = {}
        for screen_name in _renforge_editor_active_game_screens():
            _mapped_screen, widgets = _renforge_editor_widget_map(screen_name)
            for widget_id in widgets:
                widget_screens.setdefault(str(widget_id), set()).add(str(screen_name))
        return {
            widget_id: sorted(screen_names)
            for widget_id, screen_names in widget_screens.items()
            if len(screen_names) > 1
        }


    def _renforge_editor_tree_summary():
        duplicate_widget_screens = _renforge_editor_duplicate_widget_screens()
        tree = _renforge_editor_tree_rows()
        terminal_row_count = 0
        for row in tree.get("rows", []):
            if row.get("truncated"):
                terminal_row_count += 1
        return {
            "total": tree.get("total"),
            "count_truncated": tree.get("count_truncated"),
            "depth_truncated": tree.get("depth_truncated"),
            "terminal_row_count": terminal_row_count,
            "duplicate_widget_screens": duplicate_widget_screens,
        }

    def _renforge_editor_tree_indent(depth):
        return _renforge_editor_ui_px(int(depth) * 26)



    # ── View mode and tool mode (Lot 2.B groundwork) ────────────────────────
    # Both live on the editor state rather than in a screen, so every region
    # reads the same answer and a hot reload does not desynchronise them.

    _RF_TOOL_MODES = ("select", "move", "measure")

    def _renforge_editor_view_mode():
        """Either "edit" or "preview". Preview hides the panels, not the state."""
        mode = getattr(_renforge_editor_state(), "view_mode", None)
        return mode if mode in ("edit", "preview") else "edit"

    def _renforge_editor_set_view_mode(mode):
        if mode in ("edit", "preview"):
            _renforge_editor_state().view_mode = mode
            _renforge_editor_apply_layout_transform()
            renpy.restart_interaction()
        return {"ok": True, "view_mode": _renforge_editor_view_mode()}

    def _renforge_editor_panels_visible():
        """Panels show only while editing; tools_visible only controls canvas guides."""
        return _renforge_editor_view_mode() == "edit"

    def _renforge_editor_tool_mode():
        mode = getattr(_renforge_editor_state(), "tool_mode", None)
        return mode if mode in _RF_TOOL_MODES else "select"

    def _renforge_editor_set_tool_mode(mode):
        if mode in _RF_TOOL_MODES:
            _renforge_editor_state().tool_mode = mode
            renpy.restart_interaction()
        return {"ok": True, "tool_mode": _renforge_editor_tool_mode()}

    def _renforge_editor_tool_allows_drag():
        """Measure selects but does not move; select and move both drag."""
        return _renforge_editor_tool_mode() != "measure"

    def _renforge_editor_anchor_grid_cell(xanchor, yanchor):
        """Map property anchors to a 3×3 cell (col, row) in {0,1,2}."""

        def axis(value):
            try:
                v = float(value)
            except Exception:
                v = 0.0
            if v <= 0.25:
                return 0
            if v >= 0.75:
                return 2
            return 1

        return (axis(xanchor), axis(yanchor))

    def _renforge_editor_anchor_cell_on(xanchor, yanchor, col, row):
        cell = _renforge_editor_anchor_grid_cell(xanchor, yanchor)
        return cell == (int(col), int(row))

    def _renforge_editor_layout_mode():
        mode = getattr(_renforge_editor_state(), "layout_mode", None)
        return mode if mode in ("overlay", "docked") else "overlay"

    def _renforge_editor_chrome_docked():
        """True only when edge rails and the scaled canvas should be active."""
        return (
            _renforge_editor_layout_mode() == "docked"
            and _renforge_editor_view_mode() == "edit"
        )


    def _renforge_editor_layout_metrics(screen_w=None, screen_h=None, view_mode=None, layout_mode=None):
        """Pure chrome geometry for synthetic and live sizes. Never mutates state."""
        if (screen_w is None) ^ (screen_h is None):
            raise ValueError("invalid editor layout dimensions")
        if screen_w is not None:
            try:
                width = int(screen_w)
                height = int(screen_h)
            except Exception:
                raise ValueError("invalid editor layout dimensions")
            if width <= 0 or height <= 0:
                raise ValueError("invalid editor layout dimensions")
            game_width = width
            game_height = height
            synthetic = True
        else:
            width = int(config.screen_width)
            height = int(config.screen_height)
            game_width = width
            game_height = height
            synthetic = False
        if view_mode is None:
            view_mode = _renforge_editor_view_mode()
        if layout_mode is None:
            layout_mode = _renforge_editor_layout_mode()
        if view_mode not in ("edit", "preview"):
            raise ValueError("invalid editor layout dimensions")
        if layout_mode not in ("overlay", "docked"):
            raise ValueError("invalid editor layout dimensions")

        scale = max(0.45, min(1.35, min(float(width) / 2560.0, float(height) / 1440.0)))

        def ui_px(value, minimum=0):
            px = int(round(float(value) * scale))
            if minimum:
                return max(int(minimum), px)
            return px

        inset = ui_px(_RF_OVERLAY_INSET)
        toolbar_h = ui_px(_RF_DOCK_TOOLBAR_H if layout_mode == "docked" and view_mode == "edit" else _RF_OVERLAY_TOOLBAR_H)
        hud_h = ui_px(_RF_OVERLAY_HUD_H)
        cell = max(28, ui_px(_RF_ICON_BTN, minimum=_RF_ICON_BTN_MIN))
        text_size = max(12, ui_px(_RF_T_XS))
        heading_text_size = max(14, ui_px(_RF_T_SM))

        # Toolbar inclusion: fixed trailing cluster never elides; optional content
        # hides brand → screen → lock → style → disabled tools.
        fixed_ids = [
            "rf_toolbar_tool_select",
            "rf_toolbar_tool_move",
            "rf_toolbar_tool_measure",
            "rf_tools",
            "rf_opacity_down",
            "rf_opacity_up",
            "rf_undo",
            "rf_redo",
            "rf_reset",
            "rf_toolbar_layout_overlay",
            "rf_toolbar_layout_docked",
            "rf_toolbar_view_preview",
            "rf_save",
            "rf_exit",
        ]
        if view_mode == "preview":
            show_brand = show_screen = show_lock = show_disabled_tools = False
            toolbar_rect = [0, 0, width, toolbar_h]
            hud_rect = None
            tree_rect = None
            inspector_rect = None
            style_rect = None
            canvas_rect = [0, 0, width, height]
            canvas_zoom = 1.0
            canvas_offset = [0, 0]
            return {
                "width": width,
                "height": height,
                "game_width": game_width,
                "game_height": game_height,
                "scale": scale,
                "inset": inset,
                "view_mode": view_mode,
                "layout_mode": layout_mode,
                "toolbar_rect": toolbar_rect,
                "hud_rect": hud_rect,
                "tree_rect": tree_rect,
                "inspector_rect": inspector_rect,
                "style_rect": style_rect,
                "canvas_rect": canvas_rect,
                "canvas_zoom": canvas_zoom,
                "canvas_offset": canvas_offset,
                "show_brand": show_brand,
                "show_screen": show_screen,
                "show_lock": show_lock,
                "show_disabled_tools": show_disabled_tools,
                "fixed_control_ids": ["rf_toolbar_view_edit", "rf_save", "rf_exit"],
                "cell": cell,
                "text_size": text_size,
                "heading_text_size": heading_text_size,
                "synthetic": synthetic,
            }

        # Estimate optional content widths (design-token order of elision).
        brand_w = ui_px(220)
        screen_w = ui_px(_RF_BADGE_MAX_W)
        lock_w = ui_px(160)
        disabled_tools_w = cell * 3 + ui_px(18)
        fixed_cluster_w = cell * 14 + ui_px(220)  # tools + actions + segs + save/exit
        budget = width - inset * 2
        show_brand = show_screen = show_lock = show_disabled_tools = True
        used = fixed_cluster_w + brand_w + screen_w + lock_w + disabled_tools_w
        for flag_name, cost in (
            ("show_brand", brand_w),
            ("show_screen", screen_w),
            ("show_lock", lock_w),
            ("show_disabled_tools", disabled_tools_w),
        ):
            if used <= budget:
                break
            if flag_name == "show_brand":
                show_brand = False
            elif flag_name == "show_screen":
                show_screen = False
            elif flag_name == "show_lock":
                show_lock = False
            else:
                show_disabled_tools = False
            used -= cost

        docked_edit = layout_mode == "docked" and view_mode == "edit"
        if docked_edit:
            toolbar_rect = [0, 0, width, toolbar_h]
            rail_w = min(ui_px(_RF_DOCK_RAIL_W), max(ui_px(160), (width - ui_px(160)) // 2))
            content_top = toolbar_h
            content_h = max(1, height - content_top)
            gap = ui_px(_RF_S3)
            # HUD occupies only the center width between rails.
            hud_rect = [rail_w, content_top, max(1, width - rail_w * 2), hud_h]
            tree_rect = [0, content_top, rail_w, content_h]
            right_x = width - rail_w
            right_h = content_h
            inspector_h = max(1, int(round(right_h * 0.58)) - gap // 2)
            style_h = max(1, right_h - inspector_h - gap)
            inspector_rect = [right_x, content_top, rail_w, inspector_h]
            style_rect = [right_x, content_top + inspector_h + gap, rail_w, style_h]
            canvas_x = rail_w
            canvas_y = content_top + hud_h
            canvas_w = max(1, width - rail_w * 2)
            canvas_h = max(1, height - canvas_y)
            canvas_rect = [canvas_x, canvas_y, canvas_w, canvas_h]
            canvas_zoom = min(float(canvas_w) / float(game_width), float(canvas_h) / float(game_height))
            if canvas_zoom <= 0:
                canvas_zoom = 1.0
            offset_x = canvas_x + int(round((canvas_w - game_width * canvas_zoom) / 2.0))
            offset_y = canvas_y + int(round((canvas_h - game_height * canvas_zoom) / 2.0))
            canvas_offset = [int(offset_x), int(offset_y)]
        else:
            # Overlay edit: full-window canvas; chrome floats on top.
            toolbar_rect = [inset, ui_px(_RF_OVERLAY_TOOLBAR_Y), max(1, width - inset * 2), toolbar_h]
            tree_w = ui_px(_RF_OVERLAY_TREE_W)
            panel_w = ui_px(_RF_OVERLAY_PANEL_W)
            tree_y = ui_px(_RF_OVERLAY_TREE_Y)
            remaining = max(1, height - tree_y - inset)
            tree_rect = [inset, tree_y, tree_w, remaining]
            insp_h = max(1, int(round(remaining * 0.58)) - ui_px(_RF_S3) // 2)
            style_h = max(1, remaining - insp_h - ui_px(_RF_S3))
            inspector_rect = [width - inset - panel_w, tree_y, panel_w, insp_h]
            style_rect = [width - inset - panel_w, tree_y + insp_h + ui_px(_RF_S3), panel_w, style_h]
            hud_w = max(1, width - inset * 2 - tree_w - panel_w - ui_px(_RF_S4) * 2)
            hud_rect = [inset + tree_w + ui_px(_RF_S4), tree_y, hud_w, hud_h]
            canvas_rect = [0, 0, width, height]
            canvas_zoom = 1.0
            canvas_offset = [0, 0]

        return {
            "width": width,
            "height": height,
            "game_width": game_width,
            "game_height": game_height,
            "scale": scale,
            "inset": inset,
            "view_mode": view_mode,
            "layout_mode": layout_mode,
            "toolbar_rect": toolbar_rect,
            "hud_rect": hud_rect,
            "tree_rect": tree_rect,
            "inspector_rect": inspector_rect,
            "style_rect": style_rect,
            "canvas_rect": canvas_rect,
            "canvas_zoom": float(canvas_zoom),
            "canvas_offset": canvas_offset,
            "show_brand": show_brand,
            "show_screen": show_screen,
            "show_lock": show_lock,
            "show_disabled_tools": show_disabled_tools,
            "fixed_control_ids": fixed_ids,
            "cell": cell,
            "text_size": text_size,
            "heading_text_size": heading_text_size,
            "synthetic": synthetic,
        }

    def _renforge_editor_live_layout_metrics():
        return _renforge_editor_layout_metrics()

    def _renforge_editor_dock_canvas_offset():
        metrics = _renforge_editor_layout_metrics(view_mode="edit", layout_mode="docked")
        offset = metrics.get("canvas_offset") or [0, 0]
        return (int(offset[0]), int(offset[1]))

    def _renforge_editor_dock_canvas_screen_rect():
        """Screen AABB of the scaled game canvas under docked chrome."""
        metrics = _renforge_editor_layout_metrics(view_mode="edit", layout_mode="docked")
        rect = metrics.get("canvas_rect") or [0, 0, int(config.screen_width), int(config.screen_height)]
        # Use the zoomed game content AABB (centered inside canvas_rect).
        ox, oy = metrics.get("canvas_offset") or [rect[0], rect[1]]
        zoom = float(metrics.get("canvas_zoom") or 1.0)
        gw = int(metrics.get("game_width") or config.screen_width)
        gh = int(metrics.get("game_height") or config.screen_height)
        return [int(ox), int(oy), max(1, int(round(gw * zoom))), max(1, int(round(gh * zoom)))]

    def _renforge_editor_dock_stage_bands():
        """Screen rects (x, y, w, h) to paint around the docked canvas, not over it."""
        sw = int(config.screen_width)
        sh = int(config.screen_height)
        cx, cy, cw, ch = _renforge_editor_dock_canvas_screen_rect()
        bands = []
        if cy > 0:
            bands.append([0, 0, sw, cy])
        bottom = cy + ch
        if bottom < sh:
            bands.append([0, bottom, sw, sh - bottom])
        if cx > 0:
            bands.append([0, cy, cx, ch])
        right = cx + cw
        if right < sw:
            bands.append([right, cy, sw - right, ch])
        return bands

    def _renforge_editor_canvas_handle_px():
        """Handle size in canvas space so on-screen size stays ~constant when docked."""
        size = float(_renforge_editor_ui_px(_RF_HANDLE_PX))
        if _renforge_editor_chrome_docked():
            zoom = float(_renforge_editor_layout_metrics(view_mode="edit", layout_mode="docked").get("canvas_zoom") or 1.0)
            if zoom > 0:
                size = size / zoom
        return max(1, int(round(size)))

    def _renforge_editor_layout_transform(child=None):
        metrics = _renforge_editor_layout_metrics(view_mode="edit", layout_mode="docked")
        ox, oy = metrics.get("canvas_offset") or [0, 0]
        zoom = float(metrics.get("canvas_zoom") or 1.0)
        return Transform(
            child=child,
            crop=(0, 0, config.screen_width, config.screen_height),
            xoffset=int(ox),
            yoffset=int(oy),
            zoom=zoom,
        )

    _renforge_editor_layout_transform._renforge_editor_layout = True

    def _renforge_editor_canvas_transform(child=None):
        if _renforge_editor_chrome_docked():
            return _renforge_editor_layout_transform(child)
        return Transform(child=child)

    def _renforge_editor_layout_layers():
        result = []
        for name in (
            builtins.list(getattr(renpy.config, "bottom_layers", []) or [])
            + builtins.list(getattr(renpy.config, "layers", []) or [])
            + builtins.list(getattr(renpy.config, "top_layers", []) or [])
        ):
            if name != _EDITOR_LAYER and name not in result:
                result.append(name)
        return result

    def _renforge_editor_apply_layout_transform(enabled=None):
        if enabled is None:
            state = _renforge_editor_state()
            enabled = bool(state.active) and _renforge_editor_chrome_docked()
        for layer in _renforge_editor_layout_layers():
            existing = builtins.list(renpy.config.layer_transforms.get(layer, []) or [])
            kept = [
                item for item in existing
                if not getattr(item, "_renforge_editor_layout", False)
            ]
            if enabled:
                kept.append(_renforge_editor_layout_transform)
            if kept:
                renpy.config.layer_transforms[layer] = kept
            else:
                renpy.config.layer_transforms.pop(layer, None)

    def _renforge_editor_layout_transform_count():
        count = 0
        for layer in _renforge_editor_layout_layers():
            for item in renpy.config.layer_transforms.get(layer, []) or []:
                if getattr(item, "_renforge_editor_layout", False):
                    count += 1
        return count

    def _renforge_editor_set_layout_mode(mode):
        if mode in ("overlay", "docked"):
            _renforge_editor_state().layout_mode = mode
            _renforge_editor_apply_layout_transform()
            renpy.restart_interaction()
        return {"ok": True, "layout_mode": _renforge_editor_layout_mode()}

    def _renforge_editor_layout_chrome_snapshot():
        """Mode matrix + canvas AABB for live layout proof."""
        docked = _renforge_editor_chrome_docked()
        ox, oy = _renforge_editor_dock_canvas_offset()
        if docked:
            canvas_aabb = _renforge_editor_dock_canvas_screen_rect()
        else:
            canvas_aabb = [
                0,
                0,
                int(config.screen_width),
                int(config.screen_height),
            ]
        metrics = _renforge_editor_layout_metrics()
        return {
            "layout": _renforge_editor_layout_mode(),
            "view": _renforge_editor_view_mode(),
            "chrome_docked": docked,
            "transforms": _renforge_editor_layout_transform_count(),
            "editor_is_top": _EDITOR_LAYER in renpy.config.top_layers,
            "editor_transforms": len(
                renpy.config.layer_transforms.get(_EDITOR_LAYER, []) or []
            ),
            "canvas_aabb": canvas_aabb,
            "dock_scale": float(metrics.get("canvas_zoom") or _RF_DOCK_SCALE),
            "dock_offset": [int(ox), int(oy)],
            "metrics": metrics,
        }

    def _renforge_editor_screen_to_canvas_point(x, y):
        if not _renforge_editor_chrome_docked():
            return float(x), float(y)
        metrics = _renforge_editor_layout_metrics(view_mode="edit", layout_mode="docked")
        ox, oy = metrics.get("canvas_offset") or [0, 0]
        zoom = float(metrics.get("canvas_zoom") or 1.0) or 1.0
        return (
            (float(x) - float(ox)) / zoom,
            (float(y) - float(oy)) / zoom,
        )

    def _renforge_editor_canvas_to_screen_point(x, y):
        if not _renforge_editor_chrome_docked():
            return float(x), float(y)
        metrics = _renforge_editor_layout_metrics(view_mode="edit", layout_mode="docked")
        ox, oy = metrics.get("canvas_offset") or [0, 0]
        zoom = float(metrics.get("canvas_zoom") or 1.0) or 1.0
        return (
            float(ox) + float(x) * zoom,
            float(oy) + float(y) * zoom,
        )

    def _renforge_editor_screen_to_canvas_rect(rect):
        if not isinstance(rect, (builtins.list, tuple)) or len(rect) != 4:
            return rect
        left, top = _renforge_editor_screen_to_canvas_point(rect[0], rect[1])
        right, bottom = _renforge_editor_screen_to_canvas_point(
            rect[0] + rect[2],
            rect[1] + rect[3],
        )
        return [
            int(round(left)),
            int(round(top)),
            max(1, int(round(right - left))),
            max(1, int(round(bottom - top))),
        ]




    def _renforge_editor_clear_selection():
        """Clear selection chrome/analysis while preserving targets and history."""
        state = _renforge_editor_state()
        state.selected_runtime_key = None
        state.selected_widget_id = None
        state.selected_screen = None
        state.selected_target_key = None
        state.selected_lock_reason = None
        state.selected_original_position = None
        state.selected_source_position = None
        state.selected_original_size = None
        state.selected_source_size = None
        state.selected_rect = None
        state.preview_position = None
        state.preview_size = None
        state.style_color_input = ""
        state.selected_analysis_pending = False
        _renforge_editor_clear_current_analysis(state)
        state.drag_active = False
        state.drag_offset = [0, 0]
        state.drag_start_position = None
        state.resize_active = False
        state.resize_handle = None
        state.resize_start_pointer = None
        state.resize_start_position = None
        state.resize_start_size = None
        state.resize_start_rect = None
        state.snap_anchor_x = None
        state.snap_anchor_y = None
        state.snap_offset_x = None
        state.snap_offset_y = None
        state.snap_candidates_x = None
        state.snap_candidates_y = None
        state.snap_target_x_rect = None
        state.snap_target_y_rect = None
        state.guide_x = None
        state.guide_y = None
        state.guide_x_span = None
        state.guide_y_span = None
        state.label_text = "No selection"
        state.save_enabled = False
        return {"ok": True, "cleared": True}


    def _renforge_editor_select_widget(screen_name, widget_id):
        """Select by identity rather than by coordinate.

        The pointer path resolves a click to whatever is topmost, which is the
        right answer for a click on the game and the wrong one for a click in
        the tree: there the user has already named the widget. Matching the
        focus candidates by id and selecting at that rect's centre keeps a
        single selection path while letting the tree address occluded widgets.
        """
        wanted = str(widget_id or "")
        if not wanted:
            return {"ok": False, "error": "widget_id required"}
        for candidate in _renforge_editor_all_candidates() or []:
            key = candidate.get("runtime_key") or {}
            if str(key.get("widget_id") or "") != wanted:
                continue
            if screen_name and str(key.get("screen") or "") != str(screen_name):
                continue
            rect = candidate.get("rect") or []
            if len(rect) < 4:
                continue
            return _renforge_editor_select(
                int(rect[0]) + int(rect[2]) // 2,
                int(rect[1]) + int(rect[3]) // 2,
                candidate,
            )
        return {"ok": False, "error": "NO_FOCUSABLE_TARGET"}


    def _renforge_editor_select_runtime_key(runtime_key):
        """Select a source-located row without inventing an authored widget id."""
        if not isinstance(runtime_key, builtins.dict):
            return {"ok": False, "error": "runtime_key required"}
        matches = [
            candidate
            for candidate in _renforge_editor_all_candidates()
            if _renforge_editor_same_target_key(candidate.get("runtime_key"), runtime_key)
        ]
        if len(matches) != 1:
            return {
                "ok": False,
                "error": "MULTI_INSTANCE_UNSUPPORTED" if len(matches) > 1 else "NO_FOCUSABLE_TARGET",
            }
        rect = matches[0].get("rect") or []
        if len(rect) < 4:
            return {"ok": False, "error": "UNMEASURED"}
        return _renforge_editor_select(
            int(rect[0]) + int(rect[2]) // 2,
            int(rect[1]) + int(rect[3]) // 2,
            matches[0],
        )


    # ── Canvas decorations (Lot 1.F) ────────────────────────────────────────

    # TL T TR / L R / BL B BR — same order as drawn handles.
    _RF_RESIZE_HANDLE_NAMES = ("TL", "T", "TR", "L", "R", "BL", "B", "BR")
    # Left/top edges also move the origin; resize-only targets keep right/bottom.
    _RF_RESIZE_MOVE_HANDLES = frozenset(("TL", "T", "TR", "L", "BL"))
    _RF_RESIZE_RIGHT_HANDLES = frozenset(("TR", "R", "BR"))
    _RF_RESIZE_LEFT_HANDLES = frozenset(("TL", "L", "BL"))
    _RF_RESIZE_BOTTOM_HANDLES = frozenset(("BL", "B", "BR"))
    _RF_RESIZE_TOP_HANDLES = frozenset(("TL", "T", "TR"))

    def _renforge_editor_handle_points(x, y, w, h, size):
        """Top-left corner of each of the eight resize handles.

        Handles straddle the selection edge rather than sitting inside it, so
        the box the user sees is the box the widget occupies — an inset handle
        would quietly misreport the bounds it is there to describe.
        """
        half = size // 2
        mid_x = x + w // 2 - half
        mid_y = y + h // 2 - half
        left = x - half
        right = x + w - half
        top = y - half
        bottom = y + h - half
        return [
            (left, top), (mid_x, top), (right, top),
            (left, mid_y), (right, mid_y),
            (left, bottom), (mid_x, bottom), (right, bottom),
        ]

    def _renforge_editor_hit_resize_handle(pointer_x, pointer_y):
        """Return handle name under canvas pointer, or None."""
        state = _renforge_editor_state()
        if state.selected_lock_reason not in (None, ""):
            return None
        caps = state.current_capabilities or {}
        if caps.get("resize") is not True:
            return None
        rect = state.selected_rect
        if not (isinstance(rect, (builtins.list, tuple)) and len(rect) == 4):
            return None
        x, y, w, h = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
        if w < 1 or h < 1:
            return None
        handle_size = _renforge_editor_canvas_handle_px()
        # Slightly larger hit target than the drawn square so edge-centre
        # pointers from the protocol land on the handle, not the move path.
        hit_pad = max(2, handle_size // 4)
        points = _renforge_editor_handle_points(x, y, w, h, handle_size)
        can_move = caps.get("move") is True
        px, py = int(pointer_x), int(pointer_y)
        for index, (hx, hy) in enumerate(points):
            name = _RF_RESIZE_HANDLE_NAMES[index]
            if name in _RF_RESIZE_MOVE_HANDLES and not can_move:
                continue
            if (
                px >= hx - hit_pad
                and px < hx + handle_size + hit_pad
                and py >= hy - hit_pad
                and py < hy + handle_size + hit_pad
            ):
                return name
        return None

    def _renforge_editor_begin_resize(handle, pointer_x, pointer_y):
        state = _renforge_editor_state()
        target_key = state.selected_target_key
        target = state.targets.get(target_key) if target_key else None
        if not isinstance(target, builtins.dict):
            return {"ok": False, "error": "NO_SELECTION"}
        rect = state.selected_rect
        if not (isinstance(rect, (builtins.list, tuple)) and len(rect) == 4):
            return {"ok": False, "error": "UNMEASURED"}
        size = (
            target.get("size")
            or target.get("runtime_size")
            or state.preview_size
            or state.selected_original_size
            or [int(rect[2]), int(rect[3])]
        )
        position = (
            target.get("position")
            or state.preview_position
            or state.selected_original_position
            or [int(rect[0]), int(rect[1])]
        )
        if not (isinstance(size, (builtins.list, tuple)) and len(size) == 2):
            return {"ok": False, "error": "NO_SIZE"}
        if not (isinstance(position, (builtins.list, tuple)) and len(position) == 2):
            return {"ok": False, "error": "NO_POSITION"}
        state.drag_active = False
        state.resize_active = True
        state.resize_handle = handle
        state.resize_start_pointer = [int(pointer_x), int(pointer_y)]
        state.resize_start_position = [int(position[0]), int(position[1])]
        state.resize_start_size = [max(1, int(size[0])), max(1, int(size[1]))]
        state.resize_start_rect = [int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])]
        state.last_preview_method = "resize_handle"
        return {"ok": True, "handle": handle}

    def _renforge_editor_apply_resize_from_pointer(pointer_x, pointer_y):
        state = _renforge_editor_state()
        if not state.resize_active or state.resize_handle is None:
            return {"ok": False, "error": "RESIZE_INACTIVE"}
        target_key = state.selected_target_key
        if target_key is None:
            return {"ok": False, "error": "NO_SELECTION"}
        handle = state.resize_handle
        start_ptr = state.resize_start_pointer or [0, 0]
        start_pos = state.resize_start_position or [0, 0]
        start_size = state.resize_start_size or [1, 1]
        dx = int(pointer_x) - int(start_ptr[0])
        dy = int(pointer_y) - int(start_ptr[1])
        x = int(start_pos[0])
        y = int(start_pos[1])
        w = max(1, int(start_size[0]))
        h = max(1, int(start_size[1]))
        if handle in _RF_RESIZE_RIGHT_HANDLES:
            w = max(1, int(start_size[0]) + dx)
        if handle in _RF_RESIZE_LEFT_HANDLES:
            w = max(1, int(start_size[0]) - dx)
            x = int(start_pos[0]) + (int(start_size[0]) - w)
        if handle in _RF_RESIZE_BOTTOM_HANDLES:
            h = max(1, int(start_size[1]) + dy)
        if handle in _RF_RESIZE_TOP_HANDLES:
            h = max(1, int(start_size[1]) - dy)
            y = int(start_pos[1]) + (int(start_size[1]) - h)
        pos_result = _renforge_editor_set_target_position(target_key, [x, y])
        if not pos_result.get("ok", False):
            return pos_result
        size_result = _renforge_editor_set_target_size(target_key, [w, h])
        if not size_result.get("ok", False):
            return size_result
        state.preview_position = [x, y]
        state.preview_size = [w, h]
        state.pointer = [int(pointer_x), int(pointer_y)]
        _renforge_editor_set_label(pointer_x, pointer_y)
        return {"ok": True, "x": x, "y": y, "w": w, "h": h}

    def _renforge_editor_end_resize():
        state = _renforge_editor_state()
        if not state.resize_active:
            return {"ok": True, "changed": False}
        target_key = state.selected_target_key
        target = state.targets.get(target_key) if target_key else None
        before_pos = list(state.resize_start_position or [])
        before_size = list(state.resize_start_size or [])
        after_pos = list(
            (target.get("position") if isinstance(target, builtins.dict) else None)
            or state.preview_position
            or before_pos
        )
        after_size = list(
            (target.get("size") if isinstance(target, builtins.dict) else None)
            or state.preview_size
            or before_size
        )
        changed = (
            len(before_pos) == 2
            and len(after_pos) == 2
            and len(before_size) == 2
            and len(after_size) == 2
            and (
                [int(v) for v in before_pos] != [int(v) for v in after_pos]
                or [int(v) for v in before_size] != [int(v) for v in after_size]
            )
        )
        if changed and isinstance(target, builtins.dict):
            if state.history_index + 1 < len(state.history_entries):
                state.history_entries = state.history_entries[: state.history_index + 1]
            if [int(v) for v in before_pos] == [int(v) for v in after_pos]:
                command = {
                    "target_key": target_key,
                    "kind": "size",
                    "before": [int(before_size[0]), int(before_size[1])],
                    "after": [int(after_size[0]), int(after_size[1])],
                }
            else:
                command = {
                    "target_key": target_key,
                    "kind": "geometry",
                    "before": [int(before_pos[0]), int(before_pos[1])],
                    "after": [int(after_pos[0]), int(after_pos[1])],
                    "before_size": [int(before_size[0]), int(before_size[1])],
                    "after_size": [int(after_size[0]), int(after_size[1])],
                }
            state.history_entries.append(command)
            state.history = list(state.history_entries)
            state.history_index = len(state.history_entries) - 1
        state.resize_active = False
        state.resize_handle = None
        state.resize_start_pointer = None
        state.resize_start_position = None
        state.resize_start_size = None
        state.resize_start_rect = None
        _renforge_editor_refresh_save_enabled()
        renpy.restart_interaction()
        return {"ok": True, "changed": bool(changed)}


    # ── Inspector (Lot 1.C) ─────────────────────────────────────────────────

    def _renforge_editor_inspector_facts():
        """Everything the inspector shows about the current selection.

        Geometry comes from the live selection rect rather than from the source
        literals: what the user is looking at is where the widget actually is,
        including any preview the editor has applied but not yet written.
        """
        state = _renforge_editor_state()
        widget_id = str(getattr(state, "selected_widget_id", "") or "")
        runtime_key = getattr(state, "selected_runtime_key", None)
        if not widget_id and not isinstance(runtime_key, builtins.dict):
            return None
        screen_name = str(getattr(state, "selected_screen", "") or "")
        source = ""
        if isinstance(runtime_key, builtins.dict):
            location = runtime_key.get("source_location") or []
            if len(location) == 2:
                source = "%s:%s" % (location[0], location[1])
        if not source and screen_name and widget_id:
            _screen, widgets = _renforge_editor_widget_map(screen_name)
            widget = widgets.get(widget_id) if widgets else None
            if widget is not None:
                location = _renforge_editor_location(widget)
                if location:
                    source = "%s:%s" % (location[0], location[1])
        return {
            "id": widget_id or "—",
            "screen": screen_name,
            "source": source,
            "rect": _renforge_editor_selection_snapshot(),
            "lock": _renforge_editor_selected_lock(),
        }


    # ── The language of refusal (Lot 2.A) ───────────────────────────────────
    # The editor refuses often, and for good reasons. A refusal the interface
    # does not explain is indistinguishable from a bug: a click that does
    # nothing gets reported as broken, while a locked element with a stated
    # reason is a feature. Every refusal therefore lands in one of three
    # levels, and carries the coordinator's own message as its detail.

    # Classification is by the shape of the code, not a fixed list. The
    # vocabulary grows with every capability — 26 codes in the coordinator and
    # more in the source layer today — so an unclassified code must still land
    # somewhere honest rather than vanish.
    _RF_LOCK_EXPLICIT = {
        "EDITOR_OWNED_TARGET": "locked",
        "SOURCE_READ_FAILED": "refused",
        "ATTESTATION_FAILED": "refused",
    }

    # Work in flight is not a refusal. ANALYZING sits in the same field as the
    # lock codes while the coordinator is still deciding, and announcing
    # "blocked" for the half-second it takes would teach the user to distrust
    # the word.
    _RF_LOCK_PENDING = frozenset({"ANALYZING"})

    _RF_LOCK_REASON_KEYS = {
        "XPOS_LITERAL_REQUIRED": "lock.reason.xpos_literal_required",
        "YPOS_LITERAL_REQUIRED": "lock.reason.ypos_literal_required",
    }

    def _renforge_editor_lock_level(code):
        """Sort a lock code into locked, blocked or refused.

        locked   — this source form is never editable in place
        blocked  — editable in principle, not proven on this instance
        refused  — attempted, rejected, and rolled back

        Unknown codes fall to `blocked`: claiming a capability boundary we have
        not measured would be a stronger statement than the evidence supports.
        """
        if not code:
            return None
        name = str(code).upper()
        if name in _RF_LOCK_PENDING:
            return None
        explicit = _RF_LOCK_EXPLICIT.get(name)
        if explicit is not None:
            return explicit
        if name.endswith("_MISMATCH") or name.startswith("ANALYSIS_"):
            return "refused"
        if (
            name.endswith("_UNSUPPORTED")
            or name.endswith("_REQUIRED")
            or name.endswith("_REJECTED")
            or name.endswith("_AMBIGUOUS")
            or name.startswith("AMBIGUOUS_")
        ):
            return "locked"
        return "blocked"

    def _renforge_editor_lock_message(lock_reason):
        """Return a readable refusal sentence without exposing protocol codes."""
        if isinstance(lock_reason, builtins.dict):
            message = str(lock_reason.get("message") or "").strip()
            if message:
                return message
        code = _renforge_editor_lock_code(lock_reason)
        if not code:
            return ""
        name = str(code).upper()
        exact_key = _RF_LOCK_REASON_KEYS.get(name)
        if exact_key is not None:
            return _renforge_editor_t(exact_key)
        if name.endswith("_LITERAL_REQUIRED"):
            return _renforge_editor_t("lock.reason.literal_required")
        if name.endswith("_UNSUPPORTED") or name.endswith("_REQUIRED"):
            return _renforge_editor_t("lock.reason.unsupported")
        if name.endswith("_MISMATCH"):
            return _renforge_editor_t("lock.reason.mismatch")
        if name.endswith("_AMBIGUOUS") or name.startswith("AMBIGUOUS_"):
            return _renforge_editor_t("lock.reason.ambiguous")
        return _renforge_editor_t("lock.reason.default")

    def _renforge_editor_selected_lock():
        """Current selection's refusal as (level, code, message), or None."""
        reason = getattr(_renforge_editor_state(), "selected_lock_reason", None)
        if not reason:
            return None
        code = _renforge_editor_lock_code(reason)
        level = _renforge_editor_lock_level(code)
        if level is None:
            return None
        return (level, code or "", _renforge_editor_lock_message(reason))

    def _renforge_editor_lock_headline():
        """Short label naming the level, for a control the user reads at a glance."""
        current = _renforge_editor_selected_lock()
        if current is None:
            return ""
        return _renforge_editor_t("lock.%s" % current[0])

    def _renforge_editor_lock_detail():
        """The refusal level and a readable reason, without protocol codes."""
        current = _renforge_editor_selected_lock()
        if current is None:
            return ""
        level, code, message = current
        return "%s — %s" % (
            _renforge_editor_t("lock.%s" % level),
            message or _renforge_editor_lock_message(code),
        )

    def _renforge_editor_lock_color():
        current = _renforge_editor_selected_lock()
        if current is None:
            return _renforge_editor_ui_color("meta")
        return _renforge_editor_ui_color("lock_%s" % current[0])


    def _renforge_editor_can_undo():
        state = _renforge_editor_state()
        return (
            state.history_index >= 0
            or (
                bool(state.last_committed_transaction_id)
                and not bool(state.save_in_progress)
            )
        )


    def _renforge_editor_can_redo():
        state = _renforge_editor_state()
        return state.history_index + 1 < len(state.history_entries)


    def _renforge_editor_normalize_style_color(value):
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        if not normalized.startswith("#"):
            return None
        body = normalized[1:]
        if any(char not in "0123456789abcdef" for char in body):
            return None
        if len(body) == 3:
            body = "".join(ch * 2 for ch in body)
        elif len(body) == 8 and body.endswith("ff"):
            body = body[:6]
        elif len(body) == 6:
            pass
        elif len(body) == 8:
            return "#" + body
        else:
            return None
        return "#" + body


    def _renforge_editor_literal_style_color(value):
        """Validate a writable colour without collapsing its source hex family."""
        if not isinstance(value, str):
            return None
        literal = value.strip().lower()
        if not literal.startswith("#"):
            return None
        body = literal[1:]
        if len(body) not in (3, 6, 8):
            return None
        if any(char not in "0123456789abcdef" for char in body):
            return None
        return literal


    def _renforge_editor_style_color_from_channels(channels):
        try:
            values = [int(channel) for channel in channels]
        except Exception:
            return None
        if len(values) < 3 or any(channel < 0 or channel > 255 for channel in values[:4]):
            return None
        if len(values) >= 4 and values[3] != 255:
            literal = "#%02x%02x%02x%02x" % tuple(values[:4])
        else:
            literal = "#%02x%02x%02x" % tuple(values[:3])
        return _renforge_editor_normalize_style_color(literal)


    def _renforge_editor_style_color_from_widget(widget):
        if widget is None:
            return None
        style = getattr(widget, "style", None)
        color = getattr(style, "color", None) if style is not None else None
        if color is None:
            color = getattr(widget, "color", None)
        if isinstance(color, str):
            return _renforge_editor_normalize_style_color(color)
        if isinstance(color, (builtins.list, builtins.tuple)):
            return _renforge_editor_style_color_from_channels(color)
        # Ren'Py Color objects expose RGBA channels.
        try:
            return _renforge_editor_style_color_from_channels(list(color))
        except Exception:
            return None


    def _renforge_editor_measure_text_rect(screen, widget):
        """Measure a Text in logical screen coordinates from rendered child offsets."""
        if screen is None or widget is None:
            return None
        width = int(getattr(renpy.config, "screen_width", 1280) or 1280)
        height = int(getattr(renpy.config, "screen_height", 720) or 720)
        render_for_size = getattr(getattr(renpy.display, "render", None), "render_for_size", None)
        if not callable(render_for_size):
            return None
        try:
            # Ensure layout containers have current per-child offsets.
            render_for_size(screen, width, height, 0, 0)
        except Exception:
            return None

        seen = set()

        def walk(node, base_x, base_y, depth):
            if node is None or depth > _CACHE_WALK_MAX_DEPTH or id(node) in seen:
                return None
            seen.add(id(node))
            if id(node) == id(widget):
                try:
                    surf = render_for_size(widget, width, height, 0, 0)
                    sw = getattr(surf, "width", None)
                    sh = getattr(surf, "height", None)
                    if sw is None or sh is None:
                        sw, sh = surf.get_size()
                    rect = [int(round(base_x)), int(round(base_y)), int(round(sw)), int(round(sh))]
                except Exception:
                    return None
                return rect if rect[2] > 0 and rect[3] > 0 else None

            class_name = getattr(getattr(node, "__class__", None), "__name__", "unknown")
            # Transform child coordinates require the render matrix, not additive
            # offsets. Keep those text descendants unselectable until proven.
            if class_name == "Transform":
                return None
            children = _renforge_editor_children(node)
            if not children:
                return None
            offsets = getattr(node, "offsets", None)
            for index, child in enumerate(children):
                if offsets is not None and index < len(offsets):
                    try:
                        offset_x = int(offsets[index][0])
                        offset_y = int(offsets[index][1])
                    except Exception:
                        continue
                elif len(children) == 1 and class_name == "ScreenDisplayable":
                    offset_x = offset_y = 0
                else:
                    # No rendered placement proof for this branch.
                    continue
                found = walk(child, base_x + offset_x, base_y + offset_y, depth + 1)
                if found is not None:
                    return found
            return None

        return walk(screen, 0, 0, 0)


    def _renforge_editor_style_color_capable():
        state = _renforge_editor_state()
        caps = state.current_capabilities or {}
        return bool(caps.get("style_color") is True and state.selected_lock_reason in (None, ""))


    def _renforge_editor_style_color_value():
        """Hex (or placeholder) for the selected text colour, without the label prefix."""
        state = _renforge_editor_state()
        target = state.targets.get(state.selected_target_key) if state.selected_target_key else None
        color = None
        if isinstance(target, builtins.dict):
            color = target.get("style_color") or target.get("style_color_baseline")
        if not color:
            color = state.style_color_input or "#------"
        return str(color)

    def _renforge_editor_style_color_label():
        template = _renforge_editor_t("style.color_value")
        return template.replace("{value}", _renforge_editor_style_color_value())


    def _renforge_editor_has_selection():
        state = _renforge_editor_state()
        return state.selected_runtime_key is not None


    def _renforge_editor_can_reset():
        state = _renforge_editor_state()
        target = state.targets.get(state.selected_target_key)
        if not isinstance(target, builtins.dict):
            return False
        return bool(target.get("dirty")) or bool(target.get("zorder_dirty"))


    def _renforge_editor_host_config():
        host = os.environ.get("RENFORGE_EDITOR_HOST")
        port = os.environ.get("RENFORGE_EDITOR_PORT")
        token = os.environ.get("RENFORGE_EDITOR_TOKEN")
        protocol = os.environ.get("RENFORGE_EDITOR_PROTOCOL")
        if not host or not port or not token or not protocol:
            return None
        try:
            return {
                "host": str(host),
                "port": int(port),
                "token": str(token),
                "protocol": int(protocol),
            }
        except Exception:
            return None


    def _renforge_editor_read_json_line(file_obj):
        payload = file_obj.readline(1024 * 1024 + 1)
        if not payload or len(payload) > 1024 * 1024 or not payload.endswith(b"\n"):
            raise RuntimeError("invalid editor host response frame")
        parsed = json.loads(payload.decode("utf-8"))
        if not isinstance(parsed, builtins.dict):
            raise RuntimeError("editor host response must be an object")
        return parsed


    class _RenforgeEditorCoordinatorIO(object):
        def __init__(self):
            import queue as queue_module

            self.requests = queue_module.Queue()
            self.results = queue_module.Queue()
            self.stop = threading.Event()
            self.counter = 0
            state = _renforge_editor_state()
            nonce = getattr(state, "host_client_nonce", None)
            if not nonce:
                nonce = uuid.uuid4().hex
                state.host_client_nonce = nonce
            self.client_nonce = nonce
            self.thread = threading.Thread(
                target=self._loop,
                name="renforge.editor.v1.coordinator",
                daemon=True,
            )
            self.thread.start()

        def _next_request_id(self):
            self.counter += 1
            return "overlay-%d-%s" % (self.counter, uuid.uuid4().hex)

        def submit(self, payload):
            request_id = self._next_request_id()
            self.requests.put(
                {
                    "kind": "echo",
                    "request_id": request_id,
                    "payload": payload,
                    "submitted_thread_id": threading.get_ident(),
                    "submitted_at": time.time(),
                }
            )
            return request_id

        def submit_host(self, command, payload, context=None):
            request_id = self._next_request_id()
            self.requests.put(
                {
                    "kind": "host",
                    "request_id": request_id,
                    "command": str(command),
                    "payload": builtins.dict(payload or {}),
                    "context": context,
                    "submitted_thread_id": threading.get_ident(),
                    "submitted_at": time.time(),
                }
            )
            return request_id

        def _request_host(self, request):
            config = _renforge_editor_host_config()
            if config is None:
                raise RuntimeError("editor host is unavailable")
            last_error = None
            for _attempt in range(2):
                try:
                    with socket.create_connection((config["host"], config["port"]), timeout=5.0) as sock:
                        sock.settimeout(5.0)
                        file_obj = sock.makefile("rb")
                        auth_frame = {
                            "protocol": "renforge-editor",
                            "version": config["protocol"],
                            "token": config["token"],
                            "client_nonce": self.client_nonce,
                        }
                        sock.sendall((json.dumps(auth_frame, separators=(",", ":")) + "\n").encode("utf-8"))
                        auth = _renforge_editor_read_json_line(file_obj)
                        if auth.get("ok") is not True:
                            return auth
                        command_payload = builtins.dict(request.get("payload") or {})
                        if request.get("command") in ("commit", "undo_commit"):
                            command_payload["session_id"] = auth.get("session_id")
                        command_frame = {
                            "protocol": "renforge-editor",
                            "version": config["protocol"],
                            "connection_id": auth.get("connection_id"),
                            "request_id": request["request_id"],
                            "command": request["command"],
                            "payload": command_payload,
                        }
                        sock.sendall(
                            (json.dumps(command_frame, separators=(",", ":")) + "\n").encode("utf-8")
                        )
                        return _renforge_editor_read_json_line(file_obj)
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.05)
            raise RuntimeError("editor host request failed: %s" % last_error)

        def _loop(self):
            import queue as queue_module

            empty = queue_module.Empty
            while not self.stop.is_set():
                try:
                    request = self.requests.get(timeout=0.1)
                except empty:
                    continue
                result = builtins.dict(request)
                try:
                    if request.get("kind") == "host":
                        result["reply"] = self._request_host(request)
                    else:
                        result["payload"] = request.get("payload")
                except Exception as exc:
                    result["reply"] = {
                        "ok": False,
                        "error": {
                            "code": "EDITOR_HOST_UNAVAILABLE",
                            "message": str(exc),
                        },
                    }
                result["worker_thread_id"] = threading.get_ident()
                result["completed_at"] = time.time()
                self.results.put(result)

        def collect_nowait(self):
            import queue as queue_module

            empty = queue_module.Empty
            items = []
            while True:
                try:
                    items.append(self.results.get_nowait())
                except empty:
                    break
            return items


    def _renforge_editor_ensure_coordinator():
        state = _renforge_editor_state()
        coordinator = getattr(state, "coordinator", None)
        if coordinator is None or not getattr(coordinator, "thread", None) or not coordinator.thread.is_alive():
            coordinator = _RenforgeEditorCoordinatorIO()
            state.coordinator = coordinator
        return coordinator

    def _renforge_editor_stop_coordinator():
        state = _renforge_editor_state()
        coordinator = getattr(state, "coordinator", None)
        if coordinator is None:
            return
        coordinator.stop.set()
        thread = getattr(coordinator, "thread", None)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        state.coordinator = None


    def _renforge_editor_children(displayable):
        children = getattr(displayable, "children", None)
        if children is not None and not builtins.isinstance(children, (str, bytes)):
            try:
                return list(children)
            except Exception:
                pass
        child = getattr(displayable, "child", None)
        if child is not None:
            return [child]
        visit = getattr(displayable, "visit", None)
        if callable(visit):
            try:
                visited = visit()
            except Exception:
                return []
            if builtins.isinstance(visited, (builtins.list, tuple)):
                return list(visited)
        return []


    def _renforge_editor_screen_name(focus):
        screen_obj = getattr(focus, "screen", None)
        name = getattr(screen_obj, "screen_name", None)
        if name is None:
            name = getattr(focus, "screen_name", None)
        if not name:
            return None
        try:
            return name[0] if builtins.isinstance(name, (builtins.list, tuple)) else str(name)
        except Exception:
            return None


    def _renforge_editor_location(displayable):
        location = getattr(displayable, "_location", None)
        if builtins.isinstance(location, (builtins.list, tuple)) and len(location) >= 2:
            try:
                return [str(location[0]), int(location[1])]
            except Exception:
                return None
        return None


    def _renforge_editor_widget_map(screen_name):
        get_screen = getattr(getattr(renpy.display, "screen", None), "get_screen", None)
        if not callable(get_screen):
            return None, {}
        try:
            screen = get_screen(screen_name)
        except Exception:
            return None, {}
        if screen is None:
            return None, {}
        widgets = getattr(screen, "widgets", None) or {}
        if not isinstance(widgets, builtins.dict):
            return screen, {}
        return screen, widgets


    def _renforge_editor_cache_index(screen):
        """Map every cached displayable to the SL2 cache path that produced it.

        Ren'Py keys `SLFor` iterations by the author's own loop index
        (`slast.py` `newcaches[index]`) and gives every `use` call site its own
        cache dict carrying an `"ast"` entry. That path is authored data, so it
        identifies one runtime instance of a repeated statement without any
        synthetic id. `screen.widgets` cannot: it holds a single displayable per
        widget id, so the last iteration overwrites its siblings.
        """
        index = {}
        root = getattr(screen, "cache", None)
        if not isinstance(root, builtins.dict):
            return index

        def walk(cache, path, uses, depth):
            if depth > _CACHE_WALK_MAX_DEPTH:
                return
            if isinstance(cache, builtins.dict):
                for key, value in cache.items():
                    if key == "ast":
                        continue
                    # An SLUse cache dict carries its target ast; that marks the
                    # boundary as a call site rather than a loop iteration.
                    boundary = isinstance(value, builtins.dict) and "ast" in value
                    walk(value, path + [key], uses + [boundary], depth + 1)
                return
            displayable = getattr(cache, "displayable", None)
            if displayable is None:
                return
            index[id(displayable)] = {
                "path": tuple(builtins.repr(part) for part in path),
                "use_boundaries": tuple(uses),
                "displayable": displayable,
                "cache": cache,
            }

        walk(root, [], [], 0)
        return index


    def _renforge_editor_statement_widget_ids(screen_name, cache_index):
        """Learn each statement's authored widget id from any one instance.

        `screen.widgets` keeps only the last instance of a repeated statement,
        so its siblings resolve to no id at all. Instances that share a terminal
        cache segment come from the same source statement and therefore carry
        the same authored `id` literal.
        """
        statement_ids = {}
        for entry in cache_index.values():
            path = entry["path"]
            if not path or path[-1] in statement_ids:
                continue
            _screen, widget_id, error = _renforge_editor_resolve_widget_id(
                screen_name,
                entry["displayable"],
            )
            if error is None and widget_id:
                statement_ids[path[-1]] = widget_id
        return statement_ids


    def _renforge_editor_statement_siblings(cache_index):
        """Group cache entries by the statement that produced them.

        Built once per screen per frame: scanning the whole index for every
        focus candidate instead would cost O(candidates × entries).
        """
        siblings = {}
        for entry in cache_index.values():
            path = entry["path"]
            if path:
                siblings.setdefault(path[-1], []).append(entry)
        return siblings


    def _renforge_editor_instance_discriminator(entry, statement_siblings):
        """Describe how many runtime instances share one authored statement.

        Instances of the same statement share the terminal cache segment (the
        statement serial) and differ earlier in the path. The divergent segment
        tells loop iterations apart from repeated `use` call sites.
        """
        path = entry["path"]
        siblings = statement_siblings.get(path[-1]) if path else None
        if not siblings:
            return None
        instance_count = len(siblings)
        # A unique instance keeps the bare static descriptor. Cache paths carry
        # AST serials that are reassigned on every script reload, so they must
        # never reach the descriptor the host compares when rebinding.
        if instance_count <= 1:
            return {"kind": "static", "instance_count": 1}
        depth = _renforge_editor_divergence_depth(siblings)
        kind = "static"
        if depth is not None:
            kind = "use" if any(other["use_boundaries"][depth] for other in siblings) else "loop"
        discriminator = {
            "kind": kind,
            "instance_count": instance_count,
            "instance_key": builtins.list(path),
        }
        if kind == "use":
            discriminator["repeated"] = True
            discriminator["repeated_use"] = True
        elif kind == "loop":
            discriminator["loop"] = True
        return discriminator


    def _renforge_editor_divergence_depth(siblings):
        """First path segment on which sibling instances disagree."""
        for depth in builtins.range(builtins.min(len(other["path"]) for other in siblings)):
            if len(set(other["path"][depth] for other in siblings)) > 1:
                return depth
        return None


    def _renforge_editor_rebind_signature(runtime_key):
        """Identity a target keeps across frames, ignoring focus_list order.

        Repeated statements share screen, id and source location, so the cache
        instance key is what tells their instances apart. It is absent for a
        unique statement, which leaves the signature unchanged.
        """
        discriminator = runtime_key.get("instance_discriminator") or {}
        instance_key = discriminator.get("instance_key")
        return (
            str(runtime_key.get("screen") or ""),
            str(runtime_key.get("widget_id") or ""),
            tuple(runtime_key.get("source_location") or []),
            tuple(instance_key) if isinstance(instance_key, (builtins.list, tuple)) else None,
        )


    def _renforge_editor_descendant_ids(widget):
        found = set()
        stack = [widget]
        while stack:
            current = stack.pop()
            if current is None:
                continue
            current_id = id(current)
            if current_id in found:
                continue
            found.add(current_id)
            stack.extend(_renforge_editor_children(current))
        return found


    def _renforge_editor_resolve_widget_id(screen_name, widget):
        if widget is None or not isinstance(screen_name, str):
            return None, None, "MISSING_WIDGET"
        screen, widgets = _renforge_editor_widget_map(screen_name)
        if not widgets:
            return screen, None, "MISSING_WIDGET_MAP"
        descendants = _renforge_editor_descendant_ids(widget)
        matches = []
        for key, value in widgets.items():
            if value is None:
                continue
            if id(value) in descendants:
                matches.append(str(key))
        if not matches:
            return screen, None, "SYNTHETIC_WIDGET_ID"
        unique = []
        for item in matches:
            if item not in unique:
                unique.append(item)
        if len(unique) != 1:
            return screen, None, "MULTI_INSTANCE_UNSUPPORTED"
        return screen, unique[0], None


    def _renforge_editor_find_ancestry(screen, target):
        found = []
        seen = set()

        def visit(node, ancestry):
            if node is None:
                return False
            node_id = id(node)
            if node_id in seen:
                return False
            seen.add(node_id)
            if node_id == id(target):
                found.extend(ancestry + [node])
                return True
            children = _renforge_editor_children(node)
            for child in children:
                if visit(child, ancestry + [node]):
                    return True
            return False

        visit(screen, [])
        return found


    def _renforge_editor_transform_crop_is_composite(node):
        """True when crop is combined with non-default rotate/zoom (issue #46)."""
        rotate = getattr(node, "rotate", None)
        zoom = getattr(node, "zoom", None)
        xzoom = getattr(node, "xzoom", None)
        yzoom = getattr(node, "yzoom", None)
        if rotate not in (None, 0, 0.0):
            return True
        if zoom not in (None, 1, 1.0):
            return True
        if xzoom not in (None, 1, 1.0):
            return True
        if yzoom not in (None, 1, 1.0):
            return True
        return False


    def _renforge_editor_focus_crop_visibility(focus, target):
        """Compare focus_list size to the widget's unclipped render.

        Returns one of:
          - "full": focus size >= unclipped render on both axes
          - "partial": focus is smaller on either axis (any 1px reduction)
          - "unknown": sizes could not be measured (fail closed)

        Measured on 8.5.3 under pure Transform(crop=): a partially clipped
        textbutton reports e.g. focus height 15 while rendering alone is ~35.
        """
        if focus is None or target is None:
            return "unknown"
        try:
            fw = getattr(focus, "w", None)
            fh = getattr(focus, "h", None)
            if fw is None or fh is None:
                rect = getattr(focus, "rect", None)
                if isinstance(rect, (builtins.list, builtins.tuple)) and len(rect) >= 4:
                    fw, fh = rect[2], rect[3]
            if fw is None or fh is None:
                return "unknown"
            rendered = renpy.display.render.render(target, 4096, 4096, 0, 0)
            rw, rh = rendered.get_size()
            # No slack: a 1–2px top/left clamp still pins focus origin while
            # source xpos/ypos move (Codex re-review P1 on #65).
            if int(fw) < int(rw) or int(fh) < int(rh):
                return "partial"
            return "full"
        except Exception:
            return "unknown"


    def _renforge_editor_crop_state(node, focus=None, target=None):
        class_name = getattr(getattr(node, "__class__", None), "__name__", "unknown")
        if "Viewport" in class_name:
            return "viewport"
        # Ren'Py 8.5.3: Crop(rect, child) is a constructor that returns
        # Transform(child, crop=rect) — there is no runtime Crop class. The
        # class-name branch is retained only as a defensive unknown; live
        # ancestry for Crop()/Transform(crop=) is type Transform (issue #45).
        if "Crop" in class_name:
            return "crop_displayable"
        clipping = getattr(node, "clipping", None)
        if clipping is True:
            return "clipping_true"
        crop = getattr(node, "crop", None)
        if crop not in (None, False):
            if _renforge_editor_transform_crop_is_composite(node):
                if _renforge_editor_find_reverse_fn(node) is None:
                    return "transform_crop_composite"
            visibility = _renforge_editor_focus_crop_visibility(focus, target)
            if visibility == "partial":
                return "transform_crop_partial"
            if visibility == "unknown":
                # Fail closed: never grant move without a full-visibility proof.
                return "transform_crop_unproven"
            return "transform_crop"
        return "none"


    def _renforge_editor_runtime_key_from_focus(focus, ordinal, instances=None):
        screen_name = _renforge_editor_screen_name(focus)
        if not isinstance(screen_name, str):
            return None, "MISSING_INVOCATION_PATH", None, None
        widget = getattr(focus, "widget", None)
        cache_index = instances.get(screen_name) if instances else None
        cache_entry = cache_index["entries"].get(id(widget)) if cache_index else None
        screen, widget_id, resolve_error = _renforge_editor_resolve_widget_id(screen_name, widget)
        if resolve_error == "SYNTHETIC_WIDGET_ID" and cache_entry is not None:
            # A sibling instance of the same statement owns the authored id.
            if cache_index["statement_ids"] is None:
                cache_index["statement_ids"] = _renforge_editor_statement_widget_ids(
                    screen_name,
                    cache_index["entries"],
                )
            statement_id = cache_index["statement_ids"].get(cache_entry["path"][-1])
            if statement_id:
                widget_id, resolve_error = statement_id, None
            else:
                # No authored alias: source location + cache instance identity
                # remains sufficient to select and analyze the target.
                widget_id, resolve_error = None, None
        elif resolve_error in ("SYNTHETIC_WIDGET_ID", "MISSING_WIDGET_MAP") and _renforge_editor_location(widget) is not None:
            widget_id, resolve_error = None, None
        if resolve_error is not None:
            return None, resolve_error, None, None
        if widget_id is not None and (not isinstance(widget_id, str) or not widget_id):
            return None, "SYNTHETIC_WIDGET_ID", None, None
        if screen is None:
            return None, "MISSING_SCREEN", None, None
        named_widget = None
        widgets = getattr(screen, "widgets", None) or {}
        if widget_id is not None and isinstance(widgets, builtins.dict):
            named_widget = widgets.get(widget_id)
        if named_widget is None:
            named_widget = widget
        # The source location belongs to the statement, so a sibling instance
        # from the widgets map carries it; ancestry and geometry must come from
        # the focused instance, whose parents can differ between call sites.
        source_location = _renforge_editor_location(named_widget)
        if source_location is None:
            return None, "MISSING_SOURCE_LOCATION", None, None
        instance_widget = widget if cache_entry is not None else named_widget
        ancestry_nodes = _renforge_editor_find_ancestry(screen, instance_widget)
        if not ancestry_nodes:
            return None, "UNKNOWN_ANCESTRY_TYPE", None, None
        ancestry = []
        for index, node in enumerate(ancestry_nodes):
            class_name = getattr(getattr(node, "__class__", None), "__name__", "unknown")
            if class_name not in _ALLOWED_ANCESTRY_TYPES:
                return None, _renforge_editor_ancestry_lock_code(class_name), None, None
            # Size-compare against the focused widget (usually the Button), not
            # the named map entry which can resolve to a child Text.
            crop_state = _renforge_editor_crop_state(
                node,
                focus=focus,
                target=widget,
            )
            if crop_state not in (
                "none",
                "viewport",
                "crop_displayable",
                "transform_crop",
                "transform_crop_composite",
                "transform_crop_partial",
                "transform_crop_unproven",
                "clipping_true",
            ):
                return None, "UNKNOWN_CROP_STATE", None, None
            editor_owned = bool(
                screen_name in _EDITOR_SCREENS
                or getattr(node, "_renforge_editor_owner", None) == _EDITOR_OWNER
                or getattr(named_widget, "_renforge_editor_owner", None) == _EDITOR_OWNER
            )
            style = getattr(node, "style", None)
            layout = getattr(style, "box_layout", None) if style is not None else None
            if layout is None:
                layout = getattr(node, "default_layout", None)
            layout_name = layout if isinstance(layout, builtins.str) else None
            ancestry.append(
                {
                    "index": int(index),
                    "type": class_name,
                    "source_location": _renforge_editor_location(node),
                    "screen_owner": _EDITOR_OWNER if editor_owned else "game",
                    "crop_state": crop_state,
                    "editor_owned": editor_owned,
                    "layout": layout_name,
                }
            )
        discriminator = {"kind": "static", "instance_count": 1}
        if cache_entry is not None:
            measured = _renforge_editor_instance_discriminator(
                cache_entry,
                cache_index["statement_siblings"],
            )
            if measured is not None:
                discriminator = measured
        discriminator["ordinal"] = int(ordinal)
        key = {
            "screen": screen_name,
            "invocation_path": screen_name,
            "widget_id": widget_id,
            "source_location": source_location,
            "locator": {
                "kind": "source",
                "source_location": list(source_location),
                "statement_kind": str(
                    getattr(getattr(widget, "__class__", None), "__name__", "displayable")
                ).lower(),
            },
            "instance_discriminator": discriminator,
            "ancestry": ancestry,
        }
        return key, None, named_widget, widget


    def _renforge_editor_validate_runtime_key(runtime_key):
        if not isinstance(runtime_key, builtins.dict):
            return "INVALID_RUNTIME_KEY"
        instance_discriminator = runtime_key.get("instance_discriminator")
        if isinstance(instance_discriminator, builtins.dict):
            if instance_discriminator.get("kind") == "loop" or bool(instance_discriminator.get("loop")):
                return "LOOP_INSTANCE_UNSUPPORTED"
            if instance_discriminator.get("kind") == "use" and bool(instance_discriminator.get("repeated")):
                return "REPEATED_USE_UNSUPPORTED"
            if bool(instance_discriminator.get("repeated_use")):
                return "REPEATED_USE_UNSUPPORTED"
            instance_count = instance_discriminator.get("instance_count")
            if isinstance(instance_count, int) and instance_count != 1:
                return "MULTI_INSTANCE_UNSUPPORTED"
        ancestry = runtime_key.get("ancestry")
        if not builtins.isinstance(ancestry, (builtins.list, tuple)) or not ancestry:
            return "UNKNOWN_ANCESTRY_TYPE"
        for node in ancestry:
            if not isinstance(node, builtins.dict):
                return "UNKNOWN_ANCESTRY_TYPE"
            node_type = str(node.get("type") or "")
            if node_type not in _ALLOWED_ANCESTRY_TYPES:
                return _renforge_editor_ancestry_lock_code(node_type)
            crop_state = str(node.get("crop_state") or "")
            if crop_state not in (
                "none",
                "viewport",
                "crop_displayable",
                "transform_crop",
                "transform_crop_composite",
                "transform_crop_partial",
                "transform_crop_unproven",
                "clipping_true",
            ):
                return "UNKNOWN_CROP_STATE"
            # A viewport clips but does not distort: the engine reports focus
            # rects already offset by the scroll, measured across scroll
            # positions in issue #44. Whether *this* viewport shape is editable
            # is the host's decision, so the bridge stops rejecting it here.
            #
            # Issue #45: pure Transform(crop=) / Crop() sugar is the same runtime
            # object (type Transform, crop set, rotate/zoom at defaults). Focus
            # rects are measured in screen space; the host decides editability,
            # including rejecting transform_crop_composite (#46),
            # transform_crop_partial, and transform_crop_unproven with distinct
            # reasons. crop_displayable / clipping_true remain unproven here.
            if crop_state == "transform_crop_composite":
                return "TRANSFORM_CROP_COMPOSITE_UNSUPPORTED"
            if crop_state == "transform_crop_partial":
                return "TRANSFORM_CROP_PARTIAL_UNSUPPORTED"
            if crop_state == "transform_crop_unproven":
                return "TRANSFORM_CROP_UNPROVEN"
            if crop_state in ("crop_displayable", "clipping_true"):
                return "CLIPPED_ANCESTRY_UNSUPPORTED"
        return None


    def _renforge_editor_control_rect(widget_id):
        try:
            focus_list = list(renpy.display.focus.focus_list or [])
        except Exception:
            return None
        for focus in focus_list:
            if _renforge_editor_screen_name(focus) != _EDITOR_SCREEN:
                continue
            widget = getattr(focus, "widget", None)
            _screen, resolved_id, error = _renforge_editor_resolve_widget_id(_EDITOR_SCREEN, widget)
            if error is not None or resolved_id != widget_id:
                continue
            try:
                rect = [
                    int(getattr(focus, "x")),
                    int(getattr(focus, "y")),
                    int(getattr(focus, "w")),
                    int(getattr(focus, "h")),
                ]
            except Exception:
                return None
            if rect[2] > 0 and rect[3] > 0:
                return rect
        return None


    def _renforge_editor_screen_instances(screen_name, instances):
        """Cache-derived instance identity for one screen, built once per frame."""
        if screen_name in instances:
            return instances[screen_name]
        screen, _widgets = _renforge_editor_widget_map(screen_name)
        entries = _renforge_editor_cache_index(screen) if screen is not None else {}
        instances[screen_name] = {
            "entries": entries,
            "statement_siblings": _renforge_editor_statement_siblings(entries),
            # Resolving statement ids costs a descendant walk per statement, so
            # it is deferred until a widget id actually fails to resolve.
            "statement_ids": None,
        }
        return instances[screen_name]


    def _renforge_editor_focus_candidates():
        candidates = []
        instances = {}
        try:
            focus_list = list(renpy.display.focus.focus_list or [])
        except Exception:
            focus_list = []
        for ordinal, focus in enumerate(focus_list):
            x = getattr(focus, "x", None)
            y = getattr(focus, "y", None)
            w = getattr(focus, "w", None)
            h = getattr(focus, "h", None)
            if None in (x, y, w, h):
                continue
            try:
                rect = [int(x), int(y), int(w), int(h)]
            except Exception:
                continue
            if rect[2] <= 0 or rect[3] <= 0:
                continue
            screen_name = _renforge_editor_screen_name(focus)
            screen_rect = list(rect)
            if screen_name not in _EDITOR_SCREENS:
                rect = _renforge_editor_screen_to_canvas_rect(rect)
            if isinstance(screen_name, str):
                _renforge_editor_screen_instances(screen_name, instances)
            runtime_key, resolve_error, named_widget, focused_widget = _renforge_editor_runtime_key_from_focus(
                focus,
                ordinal,
                instances,
            )
            owner_hit = bool(screen_name in _EDITOR_SCREENS)
            if runtime_key is not None:
                for node in runtime_key.get("ancestry", []):
                    if bool(node.get("editor_owned")):
                        owner_hit = True
                        break
            candidate = {
                "focus": focus,
                "rect": rect,
                "screen_rect": screen_rect,
                "ordinal": int(ordinal),
                "runtime_key": runtime_key,
                "resolve_error": resolve_error,
                "named_widget": named_widget,
                "focused_widget": focused_widget,
                "editor_owned": owner_hit,
            }
            candidates.append(candidate)
        counts = {}
        for candidate in candidates:
            key = candidate.get("runtime_key")
            if not isinstance(key, builtins.dict):
                continue
            signature = (
                key.get("screen"),
                key.get("widget_id"),
                tuple(key.get("source_location") or []),
            )
            counts[signature] = counts.get(signature, 0) + 1
        for candidate in candidates:
            key = candidate.get("runtime_key")
            if not isinstance(key, builtins.dict):
                continue
            signature = (
                key.get("screen"),
                key.get("widget_id"),
                tuple(key.get("source_location") or []),
            )
            if counts.get(signature, 0) <= 1:
                continue
            # Repetition the cache walk identified is not ambiguity: the host
            # gets a distinct instance key and answers with the precise
            # repetition lock. Only unidentified duplicates stay ambiguous.
            discriminator = key.get("instance_discriminator") or {}
            if discriminator.get("kind") in ("loop", "use"):
                continue
            candidate["resolve_error"] = "MULTI_INSTANCE_UNSUPPORTED"
        return candidates


    def _renforge_editor_active_game_screens():
        names = []
        seen = set()
        state = _renforge_editor_state()
        for name in (state.editor_session_screen, state.screen, state.selected_screen):
            if isinstance(name, str) and name and name not in _EDITOR_SCREENS and name not in seen:
                seen.add(name)
                names.append(name)
        try:
            sl = renpy.game.context().scene_lists
            layer_map = getattr(sl, "layers", {}) or {}
            for sle in layer_map.get("screens", []) or []:
                d = getattr(sle, "displayable", None)
                sn = getattr(d, "screen_name", None)
                if isinstance(sn, (builtins.list, builtins.tuple)) and sn:
                    name = sn[0]
                elif sn:
                    name = sn
                else:
                    tag = getattr(sle, "tag", None)
                    name = tag
                if isinstance(name, str) and name and name not in _EDITOR_SCREENS and name not in seen:
                    seen.add(name)
                    names.append(name)
        except Exception:
            pass
        return names


    def _renforge_editor_runtime_key_from_text_widget(screen_name, widget, widget_id, ordinal, instances=None):
        if not isinstance(screen_name, str) or not screen_name:
            return None, "MISSING_INVOCATION_PATH", None
        if widget_id is not None and (not isinstance(widget_id, str) or not widget_id):
            return None, "INVALID_WIDGET_ID", None
        try:
            if not isinstance(widget, renpy.text.text.Text):
                return None, "STATEMENT_KIND_MISMATCH", None
        except Exception:
            return None, "STATEMENT_KIND_MISMATCH", None
        screen, widgets = _renforge_editor_widget_map(screen_name)
        if screen is None:
            return None, "MISSING_SCREEN", None
        cache_index = instances.get(screen_name) if instances else None
        cache_entry = cache_index["entries"].get(id(widget)) if cache_index else None
        named_widget = None
        if widget_id is not None and isinstance(widgets, builtins.dict):
            named_widget = widgets.get(widget_id)
        if named_widget is None:
            named_widget = widget
        source_location = _renforge_editor_location(named_widget)
        if source_location is None:
            source_location = _renforge_editor_location(widget)
        if source_location is None:
            return None, "MISSING_SOURCE_LOCATION", None
        ancestry_nodes = _renforge_editor_find_ancestry(screen, widget)
        if not ancestry_nodes:
            ancestry_nodes = _renforge_editor_find_ancestry(screen, named_widget)
        if not ancestry_nodes:
            return None, "UNKNOWN_ANCESTRY_TYPE", None
        ancestry = []
        for index, node in enumerate(ancestry_nodes):
            class_name = getattr(getattr(node, "__class__", None), "__name__", "unknown")
            if class_name not in _ALLOWED_ANCESTRY_TYPES:
                return None, _renforge_editor_ancestry_lock_code(class_name), None
            crop_state = _renforge_editor_crop_state(node, focus=None, target=widget)
            if crop_state not in (
                "none",
                "viewport",
                "crop_displayable",
                "transform_crop",
                "transform_crop_composite",
                "transform_crop_partial",
                "transform_crop_unproven",
                "clipping_true",
            ):
                return None, "UNKNOWN_CROP_STATE", None
            editor_owned = bool(
                screen_name in _EDITOR_SCREENS
                or getattr(node, "_renforge_editor_owner", None) == _EDITOR_OWNER
                or getattr(named_widget, "_renforge_editor_owner", None) == _EDITOR_OWNER
            )
            style = getattr(node, "style", None)
            layout = getattr(style, "box_layout", None) if style is not None else None
            if layout is None:
                layout = getattr(node, "default_layout", None)
            layout_name = layout if isinstance(layout, builtins.str) else None
            ancestry.append(
                {
                    "index": int(index),
                    "type": class_name,
                    "source_location": _renforge_editor_location(node),
                    "screen_owner": _EDITOR_OWNER if editor_owned else "game",
                    "crop_state": crop_state,
                    "editor_owned": editor_owned,
                    "layout": layout_name,
                }
            )
        discriminator = {"kind": "static", "instance_count": 1}
        if cache_entry is not None:
            measured = _renforge_editor_instance_discriminator(
                cache_entry,
                cache_index["statement_siblings"],
            )
            if measured is not None:
                discriminator = measured
        discriminator["ordinal"] = int(ordinal)
        key = {
            "screen": screen_name,
            "invocation_path": screen_name,
            "widget_id": widget_id,
            "source_location": source_location,
            "locator": {
                "kind": "source",
                "source_location": list(source_location),
                "statement_kind": "text",
            },
            "instance_discriminator": discriminator,
            "ancestry": ancestry,
        }
        return key, None, named_widget


    def _renforge_editor_text_candidates(focus_candidates=None):
        """Discover source-backed Text targets, with or without authored ids."""
        candidates = []
        instances = {}
        ordinal = 100000
        if focus_candidates is None:
            focus_candidates = _renforge_editor_focus_candidates()
        focus_covered_ids = set()
        for candidate in focus_candidates or []:
            focused_widget = candidate.get("focused_widget")
            if focused_widget is not None:
                focus_covered_ids.update(_renforge_editor_descendant_ids(focused_widget))
        for screen_name in _renforge_editor_active_game_screens():
            screen, widgets = _renforge_editor_widget_map(screen_name)
            if screen is None or not isinstance(widgets, builtins.dict):
                continue
            screen_instances = _renforge_editor_screen_instances(screen_name, instances)
            widget_ids = {}
            for widget_id, widget in list(widgets.items()):
                if isinstance(widget_id, str) and widget_id:
                    widget_ids[id(widget)] = widget_id
            runtime_widgets = []
            seen_widgets = set()
            for widget in list(widgets.values()) + [
                entry.get("displayable")
                for entry in screen_instances.get("entries", {}).values()
                if isinstance(entry, builtins.dict)
            ]:
                if widget is None or id(widget) in seen_widgets:
                    continue
                seen_widgets.add(id(widget))
                try:
                    is_text = isinstance(widget, renpy.text.text.Text)
                except Exception:
                    is_text = False
                if not is_text:
                    continue
                if id(widget) in focus_covered_ids:
                    continue
                runtime_widgets.append(widget)
            for widget in runtime_widgets:
                widget_id = widget_ids.get(id(widget))
                rect = _renforge_editor_measure_text_rect(screen, widget)
                if rect is None:
                    continue
                runtime_key, resolve_error, named_widget = _renforge_editor_runtime_key_from_text_widget(
                    screen_name,
                    widget,
                    widget_id,
                    ordinal,
                    instances,
                )
                ordinal += 1
                owner_hit = bool(screen_name in _EDITOR_SCREENS)
                if runtime_key is not None:
                    for node in runtime_key.get("ancestry", []):
                        if bool(node.get("editor_owned")):
                            owner_hit = True
                            break
                # Read painted/runtime style from the displayable only. Target
                # preview maps must not override attestation after reload: a
                # stale dirty colour would refuse a correct product undo.
                style_color = _renforge_editor_style_color_from_widget(widget)
                candidates.append(
                    {
                        "focus": None,
                        "rect": rect,
                        "ordinal": int(ordinal - 1),
                        "runtime_key": runtime_key,
                        "resolve_error": resolve_error,
                        "named_widget": named_widget,
                        "focused_widget": widget,
                        "editor_owned": owner_hit,
                        "measurement_method": "scene_tree_text",
                        "style_color": style_color,
                    }
                )
        counts = {}
        for candidate in candidates:
            key = candidate.get("runtime_key")
            if not isinstance(key, builtins.dict):
                continue
            signature = (
                key.get("screen"),
                key.get("widget_id"),
                tuple(key.get("source_location") or []),
            )
            counts[signature] = counts.get(signature, 0) + 1
        for candidate in candidates:
            key = candidate.get("runtime_key")
            if not isinstance(key, builtins.dict):
                continue
            signature = (
                key.get("screen"),
                key.get("widget_id"),
                tuple(key.get("source_location") or []),
            )
            if counts.get(signature, 0) <= 1:
                continue
            discriminator = key.get("instance_discriminator") or {}
            if discriminator.get("kind") in ("loop", "use"):
                continue
            candidate["resolve_error"] = "MULTI_INSTANCE_UNSUPPORTED"
        return candidates


    def _renforge_editor_all_candidates():
        focus_candidates = list(_renforge_editor_focus_candidates())
        return focus_candidates + list(_renforge_editor_text_candidates(focus_candidates))


    def _renforge_editor_barrier():
        renpy.restart_interaction()
        data = renpy.screenshot_to_bytes(None)
        state = _renforge_editor_state()
        state.barrier_counter = int(getattr(state, "barrier_counter", 0)) + 1
        return "%s:%d" % (hashlib.sha256(data).hexdigest(), state.barrier_counter)


    def _renforge_editor_observation_for_candidate(candidate):
        if candidate.get("resolve_error") is not None:
            return None, candidate.get("resolve_error")
        runtime_key = candidate.get("runtime_key")
        if not isinstance(runtime_key, builtins.dict):
            return None, candidate.get("resolve_error") or "UNMEASURED"
        validation = _renforge_editor_validate_runtime_key(runtime_key)
        if validation is not None:
            return None, validation
        frame_id = _renforge_editor_barrier()
        rect = list(candidate.get("rect") or [])
        if len(rect) != 4:
            return None, "UNMEASURED"
        script_generation = int(_renforge_editor_state().script_generation)
        focused_widget = candidate.get("focused_widget")
        measurement_method = candidate.get("measurement_method") or "focus_list"
        observation = {
            "runtime_key": runtime_key,
            "rect": [int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])],
            "measurement_method": measurement_method,
            "frame_id": frame_id,
            "script_generation": script_generation,
            "object_id": id(focused_widget) if focused_widget is not None else None,
        }
        style_color = candidate.get("style_color")
        if style_color is None and focused_widget is not None:
            style_color = _renforge_editor_style_color_from_widget(focused_widget)
        if style_color is not None:
            observation["style_color"] = style_color
        return observation, None


    def _renforge_editor_current_selected_position():
        state = _renforge_editor_state()
        if state.preview_position is not None:
            return list(state.preview_position)
        if state.selected_rect:
            return [int(state.selected_rect[0]), int(state.selected_rect[1])]
        if state.selected_original_position is not None:
            return [int(state.selected_original_position[0]), int(state.selected_original_position[1])]
        candidate, error = _renforge_editor_resolve_selected_candidate()
        if candidate is None:
            return None
        rect = candidate.get("rect") or []
        if len(rect) < 2:
            return None
        return [int(rect[0]), int(rect[1])]


    def _renforge_editor_set_label(pointer_x, pointer_y):
        state = _renforge_editor_state()
        width = int(getattr(renpy.config, "screen_width", 1920) or 1920)
        height = int(getattr(renpy.config, "screen_height", 1080) or 1080)
        label_w = 280
        label_h = 34
        selected_rect = state.selected_rect
        if isinstance(selected_rect, (builtins.list, builtins.tuple)) and len(selected_rect) >= 4:
            raw_x = int(selected_rect[0]) + ((int(selected_rect[2]) - label_w) // 2)
            raw_y = int(selected_rect[1]) - label_h - 10
            if raw_y < 0:
                raw_y = int(selected_rect[1]) + int(selected_rect[3]) + 10
        else:
            raw_x = int(pointer_x) + 14
            raw_y = int(pointer_y) + 14
        x = max(0, min(width - label_w, raw_x))
        y = max(0, min(height - label_h, raw_y))
        hovered = x <= int(pointer_x) < x + label_w and y <= int(pointer_y) < y + label_h
        alpha = 0.20 if hovered else 1.0
        selected = state.selected_widget_id
        if not selected and isinstance(state.selected_runtime_key, builtins.dict):
            location = state.selected_runtime_key.get("source_location") or []
            if len(location) == 2:
                selected = "%s:%s" % (str(location[0]).split("/")[-1], location[1])
        selected = selected or "none"
        selected_pos = _renforge_editor_current_selected_position()
        if selected_pos is not None and len(selected_pos) == 2:
            identity_label = "id=%s" if state.selected_widget_id else "source=%s"
            state.label_text = (identity_label + " x=%d y=%d") % (
                selected,
                int(selected_pos[0]),
                int(selected_pos[1]),
            )
        else:
            state.label_text = "id=%s" % selected
        state.label_rect = [x, y, label_w, label_h]
        state.label_alpha = alpha


    def _renforge_editor_escape_label_text(text):
        value = str(text or "")
        if not value:
            return ""
        return value.replace("{", "{{").replace("}", "}}").replace("[", "[[").replace("]", "]]")


    def _renforge_editor_status_for_observation(runtime_key):
        if not isinstance(runtime_key, dict):
            return "UNMEASURED"
        signature = (
            str(runtime_key.get("screen") or ""),
            str(runtime_key.get("widget_id") or ""),
            tuple(runtime_key.get("source_location") or []),
        )
        return signature


    def _renforge_editor_target_key(runtime_key):
        if not isinstance(runtime_key, builtins.dict):
            return None
        source_location = runtime_key.get("source_location")
        if not isinstance(source_location, (builtins.list, tuple)) or len(source_location) != 2:
            return None
        return json.dumps(
            [
                str(runtime_key.get("screen") or ""),
                str(runtime_key.get("invocation_path") or ""),
                str(runtime_key.get("widget_id") or ""),
                str(source_location[0]),
                int(source_location[1]),
            ],
            separators=(",", ":"),
        )


    def _renforge_editor_clear_current_analysis(state):
        state.current_analysis_id = None
        state.current_source_key = None
        state.current_capabilities = {}


    def _renforge_editor_set_current_analysis(state, analysis_id, source_key, capabilities=None):
        state.current_analysis_id = analysis_id
        state.current_source_key = source_key
        state.current_capabilities = builtins.dict(capabilities or {})


    def _renforge_editor_reset_history(position=None):
        state = _renforge_editor_state()
        state.history = []
        state.history_entries = []
        state.history_index = -1
        state.save_enabled = False


    def _renforge_editor_widget_properties(screen):
        state = _renforge_editor_state()
        properties = {}
        for target in state.targets.values():
            if target.get("screen") != screen:
                continue
            widget_id = target.get("widget_id")
            if not widget_id:
                continue
            props = {}
            style_color = _renforge_editor_literal_style_color(target.get("style_color"))
            comparable_style = _renforge_editor_normalize_style_color(style_color)
            baseline_style = _renforge_editor_normalize_style_color(target.get("style_color_baseline"))
            style_dirty = bool(
                style_color is not None
                and comparable_style != baseline_style
                and (target.get("capabilities") or {}).get("style_color") is True
            )
            props.update(_renforge_editor_collect_target_override_props(target))
            if style_dirty:
                props["color"] = style_color
            if props:
                properties[str(widget_id)] = props
        return properties


    def _renforge_editor_target_dirty(target):
        return bool(
            isinstance(target, builtins.dict)
            and (
                _renforge_editor_target_geometry_style_dirty(target)
                or target.get("zorder_dirty")
            )
        )


    def _renforge_editor_ast_node_for_runtime_key(screen_name, runtime_key):
        """Resolve one live SLDisplayable AST node from a source locator."""
        screen, _widgets = _renforge_editor_widget_map(screen_name)
        screen_definition = getattr(screen, "screen", None) if screen is not None else None
        root = getattr(screen_definition, "ast", None)
        location = runtime_key.get("source_location") if isinstance(runtime_key, builtins.dict) else None
        if root is None or not isinstance(location, (builtins.list, tuple)) or len(location) != 2:
            return None
        wanted = (str(location[0]), int(location[1]))
        matches = []
        seen = set()
        stack = [root]
        while stack:
            node = stack.pop()
            if node is None or id(node) in seen:
                continue
            seen.add(id(node))
            node_location = getattr(node, "location", None)
            if (
                type(node).__name__ == "SLDisplayable"
                and isinstance(node_location, (builtins.list, tuple))
                and len(node_location) >= 2
                and (str(node_location[0]), int(node_location[1])) == wanted
            ):
                matches.append(node)
            children = getattr(node, "children", None)
            if isinstance(children, (builtins.list, tuple)):
                stack.extend(children)
            entries = getattr(node, "entries", None)
            if isinstance(entries, (builtins.list, tuple)):
                for entry in entries:
                    if isinstance(entry, (builtins.list, tuple)):
                        stack.extend(entry)
        return matches[0] if len(matches) == 1 else None


    def _renforge_editor_prepare_anonymous_target_overrides(screen_name):
        """Apply previews to the in-memory Screen Language AST before rebuild.

        This changes neither source bytes nor persistent editor state. The AST
        node is resolved from the source locator on each call, so no Ren'Py
        displayable/AST object is ever pickled into store state.
        """
        state = _renforge_editor_state()
        anonymous_targets = [
            target
            for target in state.targets.values()
            if isinstance(target, builtins.dict)
            and target.get("screen") == screen_name
            and target.get("widget_id") is None
            and isinstance(target.get("runtime_key"), builtins.dict)
        ]
        for target in anonymous_targets:
            runtime_key = target.get("runtime_key")
            node = _renforge_editor_ast_node_for_runtime_key(screen_name, runtime_key)
            keyword_values = getattr(node, "keyword_values", None) if node is not None else None
            if not isinstance(keyword_values, builtins.dict):
                continue
            position = target.get("position") or []
            runtime_baseline = target.get("runtime_baseline") or []
            source_position = target.get("source_position") or []
            if (
                len(position) == 2
                and len(runtime_baseline) == 2
                and len(source_position) == 2
                and (target.get("capabilities") or {}).get("move") is True
            ):
                if target.get("dirty"):
                    next_x, next_y = _renforge_editor_compute_next_position(target)
                else:
                    next_x, next_y = int(source_position[0]), int(source_position[1])
                keyword_values["xpos"] = int(next_x)
                keyword_values["ypos"] = int(next_y)
            style_color = _renforge_editor_literal_style_color(target.get("style_color"))
            baseline_color = _renforge_editor_literal_style_color(target.get("style_color_baseline"))
            if (
                baseline_color is not None
                and (target.get("capabilities") or {}).get("style_color") is True
            ):
                keyword_values["color"] = style_color if target.get("dirty") and style_color is not None else baseline_color
            # Literal-only statements are commonly cached as constant
            # displayables. Invalidate just this statement's runtime cache so
            # the next screen update evaluates the modified keyword values.
            screen, _widgets = _renforge_editor_widget_map(screen_name)
            for entry in _renforge_editor_cache_index(screen).values():
                if _renforge_editor_location(entry.get("displayable")) != list(runtime_key.get("source_location") or []):
                    continue
                cache = entry.get("cache")
                if cache is not None:
                    try:
                        cache.constant = None
                    except Exception:
                        pass


    def _renforge_editor_effective_properties(screen_name, widget_id):
        """Inspector-facing style props: pending override first, then live style.

        Missing values render as the em dash, never fabricated 0/false.
        """
        missing = "—"
        result = {
            "xoffset": missing,
            "yoffset": missing,
            "xanchor": missing,
            "yanchor": missing,
            "xfill": missing,
            "yfill": missing,
        }
        if not isinstance(screen_name, str) or not isinstance(widget_id, str):
            return result
        overrides = _renforge_editor_widget_properties(screen_name).get(str(widget_id)) or {}
        for key in result:
            if key in overrides and overrides[key] is not None:
                result[key] = overrides[key]
        widget = _renforge_editor_find_widget_by_id(screen_name, widget_id)
        style = getattr(widget, "style", None) if widget is not None else None
        for key in result:
            if result[key] is not missing:
                continue
            if style is None:
                continue
            try:
                value = getattr(style, key, None)
            except Exception:
                value = None
            if value is None:
                continue
            result[key] = value
        return result


    def _renforge_editor_find_widget_by_id(screen_name, widget_id):
        if not isinstance(screen_name, str) or not isinstance(widget_id, str):
            return None
        screen, widgets = _renforge_editor_widget_map(screen_name)
        if isinstance(widgets, builtins.dict):
            return widgets.get(widget_id)
        return None


    def _renforge_editor_compute_next_position(target):
        source_position = target.get("source_position") or [0, 0]
        position = target.get("position") or [0, 0]
        runtime_baseline = target.get("runtime_baseline") or [0, 0]

        dx_screen = int(position[0]) - int(runtime_baseline[0])
        dy_screen = int(position[1]) - int(runtime_baseline[1])

        default_x = int(source_position[0]) + dx_screen
        default_y = int(source_position[1]) + dy_screen

        if dx_screen == 0 and dy_screen == 0:
            return default_x, default_y

        screen_name = target.get("screen")
        widget_id = target.get("widget_id")
        widget = None
        if isinstance(screen_name, str) and isinstance(widget_id, str):
            widget = _renforge_editor_find_widget_by_id(screen_name, widget_id)

        if widget is None:
            return default_x, default_y

        reverse_fn = _renforge_editor_find_reverse_fn(widget)
        if reverse_fn is None:
            return default_x, default_y

        p0 = [float(runtime_baseline[0]), float(runtime_baseline[1])]
        p1 = [float(position[0]), float(position[1])]
        m0 = _renforge_editor_matrix_map(reverse_fn, p0)
        m1 = _renforge_editor_matrix_map(reverse_fn, p1)
        if m0 is None or m1 is None:
            return default_x, default_y

        dx_source = float(m1[0]) - float(m0[0])
        dy_source = float(m1[1]) - float(m0[1])

        next_x = int(round(float(source_position[0]) + dx_source))
        next_y = int(round(float(source_position[1]) + dy_source))
        return next_x, next_y


    def _renforge_editor_collect_target_override_props(target):
        props = {}
        if isinstance(target, builtins.dict):
            position = target.get("position")
            runtime_baseline = target.get("runtime_baseline")
            source_position = target.get("source_position")
            position_dirty = bool(
                target.get("dirty")
                and isinstance(position, (builtins.list, tuple))
                and len(position) == 2
                and isinstance(runtime_baseline, (builtins.list, tuple))
                and len(runtime_baseline) == 2
                and isinstance(source_position, (builtins.list, tuple))
                and len(source_position) == 2
                and (target.get("capabilities") or {}).get("move") is not False
            )
            if position_dirty:
                next_x, next_y = _renforge_editor_compute_next_position(target)
                source_key = target.get("source_key") or {}
                position_mode = source_key.get("position_mode") if builtins.isinstance(source_key, builtins.dict) else None
                props.update(_renforge_editor_preview_properties(next_x, next_y, position_mode))
                size = target.get("size")
                runtime_size = target.get("runtime_size")
                source_size = target.get("source_size")
                if (
                    isinstance(size, (builtins.list, tuple))
                    and len(size) == 2
                    and isinstance(runtime_size, (builtins.list, tuple))
                    and len(runtime_size) == 2
                    and isinstance(source_size, (builtins.list, tuple))
                    and len(source_size) == 2
                ):
                    props["xsize"] = int(source_size[0]) + int(size[0]) - int(runtime_size[0])
                    props["ysize"] = int(source_size[1]) + int(size[1]) - int(runtime_size[1])
        return props


    def _renforge_editor_preview_properties(next_x, next_y, position_mode):
        """Build `_widget_properties` overrides for a preview placement.

        Runtime-delta modes (align/offset) use absolute xpos/ypos and neutralize
        concurrent axis props so focus_list tracks the requested TL 1:1.
        """
        # Keep in sync with renforge.editor.source.RUNTIME_DELTA_POSITION_MODES.
        if position_mode in ("align", "offset"):
            return {
                "xpos": next_x,
                "ypos": next_y,
                "xalign": 0.0,
                "yalign": 0.0,
                "xanchor": 0.0,
                "yanchor": 0.0,
                "xoffset": 0,
                "yoffset": 0,
            }
        return {
            "xpos": next_x,
            "ypos": next_y,
        }


    def _renforge_editor_show_target_overrides(screen):
        _renforge_editor_prepare_anonymous_target_overrides(screen)
        properties = _renforge_editor_widget_properties(screen)
        if properties:
            renpy.show_screen(screen, _layer="screens", _widget_properties=properties)
        else:
            renpy.show_screen(screen, _layer="screens")


    def _renforge_editor_set_target_position(target_key, position):
        state = _renforge_editor_state()
        target = state.targets.get(target_key)
        if not isinstance(target, builtins.dict) or position is None or len(position) != 2:
            return {"ok": False, "error": "TARGET_NOT_FOUND"}
        next_position = [int(position[0]), int(position[1])]
        runtime_baseline = target.get("runtime_baseline") or []
        state.save_button_state = "idle"
        target["position"] = next_position
        target["dirty"] = _renforge_editor_target_dirty(target)
        _renforge_editor_show_target_overrides(target.get("screen"))
        if state.selected_target_key == target_key:
            state.preview_position = list(next_position)
            state.selected_original_position = list(runtime_baseline)
            state.selected_source_position = list(target.get("source_position") or [])
            _renforge_editor_set_current_analysis(
                state,
                target.get("analysis_id"),
                target.get("source_key"),
                target.get("capabilities"),
            )
            if state.selected_rect is not None and len(state.selected_rect) == 4:
                state.selected_rect = [
                    int(next_position[0]),
                    int(next_position[1]),
                    int(state.selected_rect[2]),
                    int(state.selected_rect[3]),
                ]
            _renforge_editor_set_label(state.pointer[0], state.pointer[1])
        return {"ok": True, "x": next_position[0], "y": next_position[1]}


    def _renforge_editor_push_history(position, before=None):
        if position is None or len(position) != 2:
            return
        state = _renforge_editor_state()
        target_key = state.selected_target_key
        target = state.targets.get(target_key)
        if not isinstance(target, builtins.dict):
            return
        if before is None:
            before = list(target.get("position") or target.get("runtime_baseline") or [])
        else:
            before = list(before)
        after = [int(position[0]), int(position[1])]
        if len(before) != 2 or before == after:
            return
        if state.history_index + 1 < len(state.history_entries):
            state.history_entries = state.history_entries[: state.history_index + 1]
        command = {"target_key": target_key, "before": before, "after": after}
        state.history_entries.append(command)
        state.history = [list(entry["after"]) for entry in state.history_entries]
        state.history_index = len(state.history_entries) - 1


    def _renforge_editor_collect_intents():
        state = _renforge_editor_state()
        dirty_targets = [
            target
            for target in state.targets.values()
            if isinstance(target, builtins.dict) and target.get("dirty")
        ]
        if not dirty_targets:
            return []
        relative_paths = {
            target.get("source_key", {}).get("relative_path")
            for target in dirty_targets
            if isinstance(target.get("source_key"), builtins.dict)
        }
        if len(relative_paths) != 1 or None in relative_paths:
            return []
        intents = []
        for target in sorted(
            dirty_targets,
            key=lambda item: int(item.get("source_key", {}).get("line") or 0),
        ):
            analysis_id = target.get("analysis_id")
            source_key = target.get("source_key")
            if not (analysis_id and isinstance(source_key, builtins.dict)):
                return []
            capabilities = target.get("capabilities") or {}
            style_color = _renforge_editor_literal_style_color(target.get("style_color"))
            baseline_literal = _renforge_editor_literal_style_color(target.get("style_color_baseline"))
            if capabilities.get("zorder_raise_adjacent_sibling") is True and target.get("zorder_dirty") is True:
                intents.append(
                    {
                        "analysis_id": analysis_id,
                        "source_key": source_key,
                        "operation": "raise_adjacent_sibling",
                        "sibling_widget_id": capabilities.get("zorder_sibling_widget_id"),
                        "sibling_line": capabilities.get("zorder_sibling_line"),
                    }
                )
                continue
            position = target.get("position")
            runtime_baseline = target.get("runtime_baseline")
            source_position = target.get("source_position")
            position_dirty = bool(
                isinstance(position, (builtins.list, tuple))
                and len(position) == 2
                and isinstance(runtime_baseline, (builtins.list, tuple))
                and len(runtime_baseline) == 2
                and isinstance(source_position, (builtins.list, tuple))
                and len(source_position) == 2
                and [int(position[0]), int(position[1])]
                != [int(runtime_baseline[0]), int(runtime_baseline[1])]
            )
            style_dirty = bool(
                style_color is not None
                and baseline_literal is not None
                and len(style_color) == len(baseline_literal)
                and _renforge_editor_normalize_style_color(style_color)
                != _renforge_editor_normalize_style_color(baseline_literal)
            )
            intent = {
                "analysis_id": analysis_id,
                "source_key": source_key,
            }
            if position_dirty:
                if capabilities.get("move") is not True:
                    return []
                next_x, next_y = _renforge_editor_compute_next_position(target)
                intent["x"] = next_x
                intent["y"] = next_y
            if style_dirty:
                if capabilities.get("style_color") is not True:
                    return []
                intent["color"] = style_color
            size = target.get("size")
            runtime_size = target.get("runtime_size")
            source_size = target.get("source_size")
            if (
                isinstance(size, (builtins.list, tuple))
                and len(size) == 2
                and isinstance(runtime_size, (builtins.list, tuple))
                and len(runtime_size) == 2
                and isinstance(source_size, (builtins.list, tuple))
                and len(source_size) == 2
                and source_key.get("size_mode") == _BAR_SIZE_MODE_XSIZE_YSIZE
            ):
                intent["w"] = int(source_size[0]) + int(size[0]) - int(runtime_size[0])
                intent["h"] = int(source_size[1]) + int(size[1]) - int(runtime_size[1])
                if "x" not in intent:
                    if not (
                        isinstance(position, (builtins.list, tuple))
                        and len(position) == 2
                        and isinstance(runtime_baseline, (builtins.list, tuple))
                        and len(runtime_baseline) == 2
                        and isinstance(source_position, (builtins.list, tuple))
                        and len(source_position) == 2
                    ):
                        return []
                    next_x, next_y = _renforge_editor_compute_next_position(target)
                    intent["x"] = next_x
                    intent["y"] = next_y
            if len(intent) == 2:
                return []
            intents.append(intent)
        return intents


    def _renforge_editor_refresh_save_enabled():
        state = _renforge_editor_state()
        state.save_enabled = bool(
            not state.selected_analysis_pending
            and not state.selected_lock_reason
            and _renforge_editor_collect_intents()
        )
        return state.save_enabled


    def _renforge_editor_apply_history_command(command, *, use_before):
        if not isinstance(command, builtins.dict):
            return {"ok": False, "error": "NO_HISTORY"}
        value = command.get("before") if use_before else command.get("after")
        if command.get("kind") == "style_color":
            state = _renforge_editor_state()
            previous_key = state.selected_target_key
            state.selected_target_key = command.get("target_key")
            result = _renforge_editor_set_style_color(value, record=False)
            state.selected_target_key = previous_key
        elif command.get("kind") == "size":
            result = _renforge_editor_set_target_size(command.get("target_key"), value)
        elif command.get("kind") in ("reset", "geometry"):
            result = _renforge_editor_set_target_position(command.get("target_key"), value)
            size_key = "before_size" if use_before else "after_size"
            size_value = command.get(size_key)
            if isinstance(size_value, (builtins.list, tuple)) and len(size_value) == 2:
                size_result = _renforge_editor_set_target_size(
                    command.get("target_key"),
                    size_value,
                )
                if not size_result.get("ok", False):
                    result = size_result
            if command.get("kind") == "reset":
                color_key = "before_color" if use_before else "after_color"
                color_value = command.get(color_key)
                if color_value is not None:
                    previous_key = _renforge_editor_state().selected_target_key
                    _renforge_editor_state().selected_target_key = command.get("target_key")
                    color_result = _renforge_editor_set_style_color(color_value, record=False)
                    _renforge_editor_state().selected_target_key = previous_key
                    if not color_result.get("ok", False):
                        result = color_result
        else:
            result = _renforge_editor_set_target_position(command.get("target_key"), value)
        _renforge_editor_refresh_save_enabled()
        renpy.restart_interaction()
        return result


    def _renforge_editor_set_style_color(color, *, record=True):
        state = _renforge_editor_state()
        if not _renforge_editor_style_color_capable():
            return {"ok": False, "error": "STYLE_COLOR_UNAVAILABLE"}
        refused = _renforge_editor_refuse_structural_mix(for_zorder=False)
        if refused is not None:
            return refused
        literal = _renforge_editor_literal_style_color(color)
        if literal is None:
            return {"ok": False, "error": "STYLE_COLOR_INVALID"}
        target_key = state.selected_target_key
        target = state.targets.get(target_key)
        if not isinstance(target, builtins.dict):
            return {"ok": False, "error": "TARGET_NOT_FOUND"}
        baseline_literal = _renforge_editor_literal_style_color(target.get("style_color_baseline"))
        previous_literal = _renforge_editor_literal_style_color(target.get("style_color")) or baseline_literal
        if baseline_literal is None or len(literal) != len(baseline_literal):
            return {"ok": False, "error": "STYLE_COLOR_HEX_FAMILY_MISMATCH"}
        normalized = _renforge_editor_normalize_style_color(literal)
        baseline = _renforge_editor_normalize_style_color(baseline_literal)
        previous = _renforge_editor_normalize_style_color(previous_literal)
        if previous == normalized:
            state.style_color_input = previous_literal or literal
            return {"ok": True, "color": previous_literal or literal, "changed": False}
        if record:
            if state.history_index + 1 < len(state.history_entries):
                state.history_entries = state.history_entries[: state.history_index + 1]
            state.history_entries.append(
                {
                    "target_key": target_key,
                    "kind": "style_color",
                    "before": previous_literal,
                    "after": literal,
                }
            )
            state.history = list(state.history_entries)
            state.history_index = len(state.history_entries) - 1
        target["style_color"] = literal
        target["dirty"] = _renforge_editor_target_dirty(target)
        state.style_color_input = literal
        state.save_button_state = "idle"
        _renforge_editor_set_current_analysis(
            state,
            target.get("analysis_id"),
            target.get("source_key"),
            target.get("capabilities"),
        )
        _renforge_editor_show_target_overrides(target.get("screen"))
        state.last_preview_method = "_widget_properties_color"
        _renforge_editor_refresh_save_enabled()
        renpy.restart_interaction()
        return {
            "ok": True,
            "color": literal,
            "changed": True,
            "dirty": bool(target.get("dirty")),
            "method": "_widget_properties_color",
            "source_unchanged": True,
        }


    def _renforge_editor_cycle_style_color_preview():
        """Toggle between the authored baseline colour and the fixed proof blue."""
        state = _renforge_editor_state()
        target = state.targets.get(state.selected_target_key)
        if not isinstance(target, builtins.dict):
            return {"ok": False, "error": "TARGET_NOT_FOUND"}
        baseline = _renforge_editor_literal_style_color(target.get("style_color_baseline")) or "#e22b2b"
        current = _renforge_editor_literal_style_color(target.get("style_color")) or baseline
        if len(baseline) == 4:
            proof_color = "#25d"
        elif len(baseline) == 9:
            proof_color = "#2457d6" + baseline[-2:]
        else:
            proof_color = "#2457d6"
        current_norm = _renforge_editor_normalize_style_color(current)
        baseline_norm = _renforge_editor_normalize_style_color(baseline)
        chosen = proof_color if current_norm == baseline_norm else baseline
        return _renforge_editor_set_style_color(chosen, record=True)

    def _renforge_editor_zorder_capable():
        state = _renforge_editor_state()
        if state.selected_target_key is None:
            return False
        target = state.targets.get(state.selected_target_key)
        if not isinstance(target, builtins.dict):
            return False
        caps = target.get("capabilities") or state.current_capabilities or {}
        return caps.get("zorder_raise_adjacent_sibling") is True


    def _renforge_editor_raise_adjacent_sibling(*, record=True):
        state = _renforge_editor_state()
        if not _renforge_editor_zorder_capable():
            return {"ok": False, "error": "ZORDER_UNAVAILABLE"}
        target_key = state.selected_target_key
        target = state.targets.get(target_key)
        if not isinstance(target, builtins.dict):
            return {"ok": False, "error": "TARGET_NOT_FOUND"}
        refused = _renforge_editor_refuse_structural_mix(for_zorder=True)
        if refused is not None:
            return refused
        caps = target.get("capabilities") or state.current_capabilities or {}
        sibling_id = caps.get("zorder_sibling_widget_id")
        sibling_line = caps.get("zorder_sibling_line")
        if not sibling_id or not sibling_line:
            return {"ok": False, "error": "SIBLING_NOT_FOUND"}

        target["zorder_dirty"] = True
        target["dirty"] = True
        state.save_button_state = "idle"
        _renforge_editor_refresh_save_enabled()
        renpy.restart_interaction()
        return {
            "ok": True,
            "sibling_widget_id": sibling_id,
            "sibling_line": sibling_line,
        }


    def _renforge_editor_undo():
        state = _renforge_editor_state()
        if not _renforge_editor_can_undo():
            _renforge_editor_set_status("undo_unavailable")
            return {"ok": False, "error": "UNDO_UNAVAILABLE"}
        if state.history_index >= 0:
            command = state.history_entries[state.history_index]
            state.history_index -= 1
            _renforge_editor_set_status("undo")
            return _renforge_editor_apply_history_command(command, use_before=True)
        transaction_id = state.last_committed_transaction_id
        if not transaction_id or state.save_in_progress:
            _renforge_editor_set_status("undo_unavailable")
            return {"ok": False, "error": "UNDO_UNAVAILABLE"}
        state.save_in_progress = True
        state.save_button_state = "saving"
        state.save_requested = True
        state.save_error = None
        state.save_last_error = None
        state.pending_operation = "undo_commit"
        _renforge_editor_set_status("undoing")
        pending = _renforge_editor_ensure_coordinator().submit_host(
            "undo_commit",
            {"transaction_id": transaction_id},
            {"command": "undo_commit", "transaction_id": transaction_id},
        )
        state.pending_commit_request_id = pending
        renpy.restart_interaction()
        return {"ok": True, "request_id": pending, "transaction_id": transaction_id, "kind": "product_undo"}


    def _renforge_editor_redo():
        state = _renforge_editor_state()
        if not _renforge_editor_can_redo():
            _renforge_editor_set_status("redo_unavailable")
            return {"ok": False, "error": "REDO_UNAVAILABLE"}
        state.history_index += 1
        command = state.history_entries[state.history_index]
        _renforge_editor_set_status("redo")
        return _renforge_editor_apply_history_command(command, use_before=False)


    def _renforge_editor_reset_selected():
        state = _renforge_editor_state()
        target = state.targets.get(state.selected_target_key)
        if not _renforge_editor_can_reset() or not isinstance(target, builtins.dict):
            return {"ok": False, "error": "RESET_UNAVAILABLE"}
        before = list(target.get("position") or [])
        baseline = list(target.get("runtime_baseline") or [])
        before_size = list(target.get("size") or [])
        baseline_size = list(target.get("runtime_size") or [])
        before_color = _renforge_editor_literal_style_color(target.get("style_color"))
        baseline_color = _renforge_editor_literal_style_color(target.get("style_color_baseline"))
        position_dirty = len(before) == 2 and len(baseline) == 2 and before != baseline
        size_dirty = (
            len(before_size) == 2
            and len(baseline_size) == 2
            and before_size != baseline_size
        )
        style_dirty = (
            before_color is not None
            and baseline_color is not None
            and _renforge_editor_normalize_style_color(before_color)
            != _renforge_editor_normalize_style_color(baseline_color)
        )
        zorder_dirty = bool(target.get("zorder_dirty"))
        if not position_dirty and not size_dirty and not style_dirty and not zorder_dirty:
            return {"ok": False, "error": "RESET_UNAVAILABLE"}
        if zorder_dirty and not position_dirty and not size_dirty and not style_dirty:
            target["zorder_dirty"] = False
            target["dirty"] = False
            _renforge_editor_refresh_save_enabled()
            _renforge_editor_set_status("reset")
            renpy.restart_interaction()
            return {"ok": True, "reset": "zorder"}
        if len(before) != 2 or len(baseline) != 2:
            return {"ok": False, "error": "RESET_UNAVAILABLE"}
        if state.history_index + 1 < len(state.history_entries):
            state.history_entries = state.history_entries[: state.history_index + 1]
        command = {
            "target_key": state.selected_target_key,
            "kind": "reset",
            "before": before,
            "after": baseline,
            "before_size": before_size if len(before_size) == 2 else None,
            "after_size": baseline_size if len(baseline_size) == 2 else None,
            "before_color": before_color if style_dirty else None,
            "after_color": baseline_color if style_dirty else None,
        }
        state.history_entries.append(command)
        state.history = [list(entry.get("after") or []) for entry in state.history_entries]
        state.history_index = len(state.history_entries) - 1
        _renforge_editor_set_status("reset")
        state.last_restore_method = "history_reset"
        return _renforge_editor_apply_history_command(command, use_before=False)


    def _renforge_editor_adjust_opacity(delta):
        state = _renforge_editor_state()
        state.opacity = max(0.05, min(1.0, float(state.opacity) + float(delta)))
        state.label_alpha = max(0.15, min(1.0, state.label_alpha + 0.0))
        renpy.restart_interaction()
        return state.opacity


    def _renforge_editor_toggle_tools():
        state = _renforge_editor_state()
        state.tools_visible = not bool(state.tools_visible)
        renpy.restart_interaction()
        return state.tools_visible


    def _renforge_editor_opacity_label():
        template = _renforge_editor_t("toolbar.opacity_value")
        value = int(round(_renforge_editor_opacity() * 100))
        return template.replace("{value}", str(value))


    def _renforge_editor_jump_targets():
        """Story labels the author can warp to.

        Ren'Py registers its own machinery as labels too: ``_``-prefixed ones,
        local ``label.sub`` ones, and the common/ menus (``save_screen``,
        ``main_menu_screen``…). Warping into those strands the player in an
        internal flow, so only labels authored in the project survive.
        """
        try:
            labels = renpy.get_all_labels()
        except Exception:
            return []
        names = []
        for label in labels:
            name = str(label)
            if name.startswith("_") or "." in name:
                continue
            try:
                filename = str(getattr(renpy.game.script.lookup(name), "filename", "") or "")
            except Exception:
                continue
            if "renpy/common" in filename.replace("\\", "/"):
                continue
            names.append(name)
        names.sort()
        return names


    def _renforge_editor_jump_open():
        return bool(getattr(_renforge_editor_state(), "jump_open", False))


    def _renforge_editor_toggle_jump():
        state = _renforge_editor_state()
        state.jump_open = not bool(getattr(state, "jump_open", False))
        renpy.restart_interaction()
        return state.jump_open


    def _renforge_editor_close_jump():
        state = _renforge_editor_state()
        state.jump_open = False
        renpy.restart_interaction()
        return False


    # Dialogue surfaces take required scope args (who/what). Jumping out of a
    # screen while they are still showing leaves them in the layer with an empty
    # scope; the next interact (often a `scene … with dissolve`) then dies with
    # `TypeError: missing a required argument: 'who'`. Tear them down first.
    _RF_JUMP_DISMISS_SCREENS = ("say", "nvl", "bubble", "choice")

    def _renforge_editor_jump_to(label):
        """Warp the running game to `label` without stranding dialogue screens."""
        state = _renforge_editor_state()
        state.jump_open = False
        for name in _RF_JUMP_DISMISS_SCREENS:
            try:
                if renpy.get_screen(name) is not None:
                    renpy.hide_screen(name)
            except Exception:
                pass
        renpy.jump(str(label))


    # Always-on chrome that is not "where the author is working". Taking the
    # first layer entry after a Jump() briefly leaves only quick_menu (say has
    # already been torn down), so the chip would flash "screen quick_menu".
    _RF_JUMP_CONTEXT_CHROME = frozenset({
        "quick_menu",
        "notify",
        "confirm",
        "skip_indicator",
    })
    _RF_JUMP_CONTEXT_PREFER = ("say", "nvl", "bubble", "choice", "main_menu", "game_menu")

    def _renforge_editor_jump_context():
        """Name shown on the jump chip: the selection's screen, else what runs.

        The chip is a context readout before it is a menu, so it must say
        something even with nothing selected — an empty pill reads as broken.
        Prefer dialogue/content screens over chrome, and stick the last good
        name across Jump() transitions so quick_menu never flashes through.
        """
        state = _renforge_editor_state()
        none_label = _renforge_editor_t("toolbar.jump_none")
        selected = str(getattr(state, "selected_screen", "") or "")
        if selected and selected not in _RF_JUMP_CONTEXT_CHROME:
            state.jump_context_last = selected
            return selected
        names = [str(n) for n in _renforge_editor_active_game_screens() if n]
        for prefer in _RF_JUMP_CONTEXT_PREFER:
            if prefer in names:
                state.jump_context_last = prefer
                return prefer
        for name in names:
            if name not in _RF_JUMP_CONTEXT_CHROME:
                state.jump_context_last = name
                return name
        last = str(getattr(state, "jump_context_last", "") or "")
        if last:
            return last
        return none_label


    def _renforge_editor_same_target_key(first, second):
        if not isinstance(first, builtins.dict) or not isinstance(second, builtins.dict):
            return False
        first_key = json.loads(json.dumps(first))
        second_key = json.loads(json.dumps(second))
        first_discriminator = first_key.get("instance_discriminator")
        second_discriminator = second_key.get("instance_discriminator")
        allow_ordinal_drift = (
            isinstance(first_discriminator, builtins.dict)
            and isinstance(second_discriminator, builtins.dict)
            and first_discriminator.get("kind") == "static"
            and second_discriminator.get("kind") == "static"
            and first_discriminator.get("instance_count") == 1
            and second_discriminator.get("instance_count") == 1
        )
        if allow_ordinal_drift:
            first_discriminator.pop("ordinal", None)
            second_discriminator.pop("ordinal", None)
        return first_key == second_key


    def _renforge_editor_anchor_candidates(selected_key):
        candidates_x = []
        candidates_y = []
        for candidate in _renforge_editor_focus_candidates():
            if candidate.get("editor_owned"):
                continue
            runtime_key = candidate.get("runtime_key")
            if runtime_key is None:
                continue
            if _renforge_editor_same_target_key(runtime_key, selected_key):
                continue
            rect = candidate.get("rect") or []
            if len(rect) != 4:
                continue
            target_rect = [int(value) for value in rect]
            left, top, width, height = target_rect
            for anchor in (left, left + width // 2, left + width):
                candidates_x.append({"anchor": anchor, "rect": target_rect})
            for anchor in (top, top + height // 2, top + height):
                candidates_y.append({"anchor": anchor, "rect": target_rect})
        return candidates_x, candidates_y


    def _renforge_editor_apply_snap(desired_x, desired_y, shift):
        state = _renforge_editor_state()
        if shift:
            state.snap_anchor_x = None
            state.snap_anchor_y = None
            state.snap_offset_x = None
            state.snap_offset_y = None
            state.snap_target_x_rect = None
            state.snap_target_y_rect = None
            state.guide_x = None
            state.guide_y = None
            state.guide_x_span = None
            state.guide_y_span = None
            return int(desired_x), int(desired_y), {"snapped_x": False, "snapped_y": False}

        if (
            state.drag_active
            and state.snap_candidates_x is not None
            and state.snap_candidates_y is not None
        ):
            candidates_x = state.snap_candidates_x
            candidates_y = state.snap_candidates_y
        else:
            candidates_x, candidates_y = _renforge_editor_anchor_candidates(
                state.selected_runtime_key
            )
        selected_rect = state.selected_rect or [desired_x, desired_y, 0, 0]
        width = max(0, int(selected_rect[2]))
        height = max(0, int(selected_rect[3]))
        offsets_x = (0, width // 2, width)
        offsets_y = (0, height // 2, height)
        snapped_x = int(desired_x)
        snapped_y = int(desired_y)

        anchor_x = state.snap_anchor_x
        offset_x = state.snap_offset_x
        if (
            anchor_x is not None
            and offset_x is not None
            and state.snap_target_x_rect is not None
            and abs((int(desired_x) + int(offset_x)) - int(anchor_x)) <= _SNAP_RELEASE
        ):
            snapped_x = int(anchor_x) - int(offset_x)
        else:
            state.snap_anchor_x = None
            state.snap_offset_x = None
            state.snap_target_x_rect = None
            closest_x = None
            for candidate in candidates_x:
                anchor = int(candidate["anchor"])
                for offset in offsets_x:
                    distance = abs((int(desired_x) + int(offset)) - anchor)
                    if closest_x is None or distance < closest_x[0]:
                        closest_x = (
                            distance,
                            anchor,
                            int(offset),
                            list(candidate["rect"]),
                        )
            if closest_x is not None and closest_x[0] <= _SNAP_ACQUIRE:
                state.snap_anchor_x = closest_x[1]
                state.snap_offset_x = closest_x[2]
                state.snap_target_x_rect = closest_x[3]
                snapped_x = closest_x[1] - closest_x[2]

        anchor_y = state.snap_anchor_y
        offset_y = state.snap_offset_y
        if (
            anchor_y is not None
            and offset_y is not None
            and state.snap_target_y_rect is not None
            and abs((int(desired_y) + int(offset_y)) - int(anchor_y)) <= _SNAP_RELEASE
        ):
            snapped_y = int(anchor_y) - int(offset_y)
        else:
            state.snap_anchor_y = None
            state.snap_offset_y = None
            state.snap_target_y_rect = None
            closest_y = None
            for candidate in candidates_y:
                anchor = int(candidate["anchor"])
                for offset in offsets_y:
                    distance = abs((int(desired_y) + int(offset)) - anchor)
                    if closest_y is None or distance < closest_y[0]:
                        closest_y = (
                            distance,
                            anchor,
                            int(offset),
                            list(candidate["rect"]),
                        )
            if closest_y is not None and closest_y[0] <= _SNAP_ACQUIRE:
                state.snap_anchor_y = closest_y[1]
                state.snap_offset_y = closest_y[2]
                state.snap_target_y_rect = closest_y[3]
                snapped_y = closest_y[1] - closest_y[2]

        state.guide_x = state.snap_anchor_x
        state.guide_y = state.snap_anchor_y
        if state.guide_x is not None and state.snap_target_x_rect is not None:
            target = state.snap_target_x_rect
            state.guide_x_span = [
                min(snapped_y, int(target[1])),
                max(snapped_y + height, int(target[1]) + int(target[3])),
            ]
        else:
            state.guide_x_span = None
        if state.guide_y is not None and state.snap_target_y_rect is not None:
            target = state.snap_target_y_rect
            state.guide_y_span = [
                min(snapped_x, int(target[0])),
                max(snapped_x + width, int(target[0]) + int(target[2])),
            ]
        else:
            state.guide_y_span = None
        return snapped_x, snapped_y, {
            "snapped_x": state.snap_anchor_x is not None,
            "snapped_y": state.snap_anchor_y is not None,
        }



    def _renforge_editor_target_geometry_style_dirty(target):
        if not isinstance(target, builtins.dict):
            return False
        position = target.get("position") or []
        baseline = target.get("runtime_baseline") or []
        pos_dirty = (
            len(position) == 2
            and len(baseline) == 2
            and [int(position[0]), int(position[1])] != [int(baseline[0]), int(baseline[1])]
        )
        size = target.get("size") or []
        runtime_size = target.get("runtime_size") or []
        size_dirty = (
            len(size) == 2
            and len(runtime_size) == 2
            and [int(size[0]), int(size[1])] != [int(runtime_size[0]), int(runtime_size[1])]
        )
        color = _renforge_editor_literal_style_color(target.get("style_color"))
        baseline_color = _renforge_editor_literal_style_color(target.get("style_color_baseline"))
        style_dirty = (
            color is not None
            and baseline_color is not None
            and _renforge_editor_normalize_style_color(color)
            != _renforge_editor_normalize_style_color(baseline_color)
        )
        return bool(pos_dirty or size_dirty or style_dirty)

    def _renforge_editor_refuse_structural_mix(*, for_zorder=False):
        """Geometry/style and z-order raise are mutually exclusive intents."""
        state = _renforge_editor_state()
        target = state.targets.get(state.selected_target_key)
        if not isinstance(target, builtins.dict):
            return None
        if for_zorder:
            if _renforge_editor_target_geometry_style_dirty(target):
                reason = {
                    "code": "STRUCTURAL_INTENT_COMBINATION_REJECTED",
                    "message": "Raise cannot combine with geometry or style edits",
                }
                state.selected_lock_reason = reason
                _renforge_editor_set_status("locked")
                return {
                    "ok": False,
                    "error": "STRUCTURAL_INTENT_COMBINATION_REJECTED",
                    "lock_reason": reason,
                }
            return None
        if target.get("zorder_dirty") is True:
            reason = {
                "code": "STRUCTURAL_INTENT_COMBINATION_REJECTED",
                "message": "Geometry or style cannot combine with a pending raise",
            }
            state.selected_lock_reason = reason
            _renforge_editor_set_status("locked")
            return {
                "ok": False,
                "error": "STRUCTURAL_INTENT_COMBINATION_REJECTED",
                "lock_reason": reason,
            }
        return None


    def _renforge_editor_apply_preview(x, y, *, shift=False, allow_snap=True, record=False):
        state = _renforge_editor_state()
        if not state.selected_screen or state.selected_target_key is None:
            return {"ok": False, "error": "NO_SELECTION"}
        if state.selected_lock_reason not in (None, ""):
            if not (
                isinstance(state.selected_lock_reason, builtins.dict)
                and state.selected_lock_reason.get("code") == "STRUCTURAL_INTENT_COMBINATION_REJECTED"
            ):
                return {"ok": False, "error": "TARGET_LOCKED"}
        refused = _renforge_editor_refuse_structural_mix(for_zorder=False)
        if refused is not None:
            return refused
        desired_x = int(x)
        desired_y = int(y)
        snap_detail = {"snapped_x": False, "snapped_y": False}
        if allow_snap:
            snapped_x, snapped_y, snap_detail = _renforge_editor_apply_snap(desired_x, desired_y, bool(shift))
        else:
            state.snap_anchor_x = None
            state.snap_anchor_y = None
            state.snap_offset_x = None
            state.snap_offset_y = None
            state.guide_x = None
            state.guide_y = None
            snapped_x, snapped_y = desired_x, desired_y
        if (
            not record
            and state.preview_position is not None
            and len(state.preview_position) == 2
            and [int(snapped_x), int(snapped_y)] == [int(state.preview_position[0]), int(state.preview_position[1])]
        ):
            # A pinned snap or a sub-pixel move: rebuilding the screen would
            # change nothing visible and costs a full interaction restart.
            if state.drag_active:
                renpy.restart_interaction()
            return {
                "ok": True,
                "x": int(snapped_x),
                "y": int(snapped_y),
                "method": "_widget_properties",
                "snap": snap_detail,
                "guide_x": state.guide_x,
                "guide_y": state.guide_y,
            }
        if record:
            _renforge_editor_push_history([int(snapped_x), int(snapped_y)])
        result = _renforge_editor_set_target_position(
            state.selected_target_key,
            [int(snapped_x), int(snapped_y)],
        )
        if result.get("ok") is not True:
            return result
        state.last_preview_method = "_widget_properties"
        _renforge_editor_refresh_save_enabled()
        renpy.restart_interaction()
        return {
            "ok": True,
            "x": int(snapped_x),
            "y": int(snapped_y),
            "method": "_widget_properties",
            "snap": snap_detail,
            "guide_x": state.guide_x,
            "guide_y": state.guide_y,
        }


    def _renforge_editor_restore_preview():
        """Restore the selected target to its baseline using the Reset primitive."""
        state = _renforge_editor_state()
        if not state.selected_screen and state.selected_target_key is None:
            return {"ok": False, "error": "NO_SELECTION"}
        if _renforge_editor_can_reset():
            reply = _renforge_editor_reset_selected()
            if reply.get("ok") is True:
                state.last_restore_method = "history_reset"
                return {"ok": True, "restored": True, "method": "history_reset", "reset": reply}
            if reply.get("error") not in (None, "RESET_UNAVAILABLE"):
                return reply
        # Clean baseline: re-show screen without overrides and clear preview mirrors.
        if state.selected_screen:
            renpy.show_screen(state.selected_screen, _layer="screens")
        target = state.targets.get(state.selected_target_key) if state.selected_target_key else None
        if isinstance(target, builtins.dict):
            baseline = list(target.get("runtime_baseline") or [])
            baseline_size = list(target.get("runtime_size") or [])
            if len(baseline) == 2:
                target["position"] = list(baseline)
                state.preview_position = list(baseline)
                state.selected_original_position = list(baseline)
            if len(baseline_size) == 2:
                target["size"] = list(baseline_size)
                state.preview_size = list(baseline_size)
                state.selected_original_size = list(baseline_size)
            baseline_color = _renforge_editor_literal_style_color(target.get("style_color_baseline"))
            if baseline_color is not None:
                target["style_color"] = baseline_color
                state.style_color_input = baseline_color
            target["zorder_dirty"] = False
            target["dirty"] = False
            if len(baseline) == 2 and len(baseline_size) == 2:
                state.selected_rect = [
                    int(baseline[0]),
                    int(baseline[1]),
                    int(baseline_size[0]),
                    int(baseline_size[1]),
                ]
            _renforge_editor_show_target_overrides(target.get("screen"))
        else:
            state.preview_position = None
            state.preview_size = None
        _renforge_editor_refresh_save_enabled()
        state.last_restore_method = "_widget_properties_revert"
        renpy.restart_interaction()
        return {"ok": True, "restored": True, "method": "_widget_properties_revert"}


    def _renforge_editor_resolve_selected_candidate():
        state = _renforge_editor_state()
        selected_key = state.selected_runtime_key
        if not isinstance(selected_key, builtins.dict):
            return None, "NO_SELECTION"
        matches = []
        loose_matches = []
        selected_screen = selected_key.get("screen")
        selected_widget = selected_key.get("widget_id")
        selected_source = tuple(selected_key.get("source_location") or [])
        for candidate in _renforge_editor_all_candidates():
            if candidate.get("editor_owned"):
                continue
            key = candidate.get("runtime_key")
            if key == selected_key:
                matches.append(candidate)
                continue
            if isinstance(key, builtins.dict):
                if (
                    key.get("screen") == selected_screen
                    and key.get("widget_id") == selected_widget
                    and tuple(key.get("source_location") or []) == selected_source
                ):
                    already_seen = False
                    for seen in loose_matches:
                        seen_key = seen.get("runtime_key")
                        if isinstance(seen_key, builtins.dict) and _renforge_editor_rebind_signature(seen_key) == _renforge_editor_rebind_signature(key):
                            already_seen = True
                            break
                    if not already_seen:
                        loose_matches.append(candidate)
                elif _renforge_editor_rebind_signature(key) == _renforge_editor_rebind_signature(selected_key):
                    already_seen = False
                    for seen in loose_matches:
                        seen_key = seen.get("runtime_key")
                        if isinstance(seen_key, builtins.dict) and _renforge_editor_rebind_signature(seen_key) == _renforge_editor_rebind_signature(key):
                            already_seen = True
                            break
                    if not already_seen:
                        loose_matches.append(candidate)
        if len(matches) == 0:
            if len(loose_matches) == 1:
                return loose_matches[0], None
            if len(loose_matches) > 1:
                return None, "MULTI_INSTANCE_UNSUPPORTED"
            return None, "NO_MATCHING_INSTANCE"
        if len(matches) > 1:
            return None, "MULTI_INSTANCE_UNSUPPORTED"
        return matches[0], None


    def _renforge_editor_accept_observation(observation):
        state = _renforge_editor_state()
        state.accepted_observations.append(observation)
        if len(state.accepted_observations) > 20:
            state.accepted_observations[:] = state.accepted_observations[-20:]


    def _renforge_editor_matrix_map(transform_fn, point):
        if transform_fn is None:
            return None
        x, y = float(point[0]), float(point[1])
        try:
            if hasattr(transform_fn, "transform"):
                mapped = transform_fn.transform(x, y)
            elif callable(transform_fn):
                mapped = transform_fn(x, y)
            else:
                return None
        except Exception:
            return None
        if mapped is None:
            return None
        try:
            return [float(mapped[0]), float(mapped[1])]
        except Exception:
            return None


    def _renforge_editor_find_transform(displayable):
        def _as_candidates(node):
            values = []
            for attr in ("at", "transform", "_transform", "child_transform", "transformation", "transforms"):
                value = getattr(node, attr, None)
                if value is None:
                    continue
                if isinstance(value, (builtins.list, tuple)):
                    values.extend(value)
                else:
                    values.append(value)
            return values

        transforms = []
        seen = set()

        current = displayable
        for _ in range(16):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            for candidate in _as_candidates(current):
                if candidate is None:
                    continue
                if getattr(candidate, "forward", None) is not None or getattr(candidate, "reverse", None) is not None:
                    if candidate not in transforms:
                        transforms.append(candidate)
            if getattr(current, "forward", None) is not None or getattr(current, "reverse", None) is not None:
                if current not in transforms:
                    transforms.append(current)
            name = type(current).__name__
            if name in ("Transform", "ATLTransform", "Motion", "TransformBase"):
                if current not in transforms:
                    transforms.append(current)
            next_child = getattr(current, "child", None)
            if next_child is None:
                next_child = getattr(current, "raw_child", None)
            if next_child is None:
                next_child = getattr(current, "original_child", None)
            current = next_child

        current = displayable
        seen = set()
        for _ in range(8):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            parent = getattr(current, "parent", None)
            if parent is None:
                break
            for candidate in _as_candidates(parent):
                if candidate is None:
                    continue
                if getattr(candidate, "forward", None) is not None or getattr(candidate, "reverse", None) is not None:
                    if candidate not in transforms:
                        transforms.append(candidate)
            if getattr(parent, "forward", None) is not None or getattr(parent, "reverse", None) is not None:
                if parent not in transforms:
                    transforms.append(parent)
            if type(parent).__name__ in ("Transform", "ATLTransform", "Motion", "TransformBase"):
                if parent not in transforms:
                    transforms.append(parent)
            current = parent

        if len(transforms) == 1:
            return transforms[0]
        if len(transforms) > 1:
            return "MULTIPLE_TRANSFORMS"
        return None


    def _renforge_editor_find_reverse_fn(displayable):
        transform_d = _renforge_editor_find_transform(displayable)
        if transform_d is None or transform_d == "MULTIPLE_TRANSFORMS":
            return None
        return getattr(transform_d, "reverse", None)


    def _renforge_editor_screen_quad_from_local(local_quad, transform_displayable, focus_rect):
        for seam in ("forward", "reverse"):
            seam_fn = getattr(transform_displayable, seam, None)
            if seam_fn is None:
                continue
            projected = []
            for point in local_quad:
                mapped = _renforge_editor_matrix_map(seam_fn, point)
                if mapped is None:
                    return None, seam, None, "transform_map_failed"
                projected.append(mapped)
            projected_center = [
                sum(float(point[0]) for point in projected) / len(projected),
                sum(float(point[1]) for point in projected) / len(projected),
            ]
            focus_center = [
                float(focus_rect[0]) + float(focus_rect[2]) / 2.0,
                float(focus_rect[1]) + float(focus_rect[3]) / 2.0,
            ]
            screen_quad = [
                [
                    float(point[0]) + focus_center[0] - projected_center[0],
                    float(point[1]) + focus_center[1] - projected_center[1],
                ]
                for point in projected
            ]
            min_x = min(p[0] for p in screen_quad)
            max_x = max(p[0] for p in screen_quad)
            min_y = min(p[1] for p in screen_quad)
            max_y = max(p[1] for p in screen_quad)
            rect_min_x = float(focus_rect[0]) - 1.5
            rect_max_x = float(focus_rect[0]) + float(focus_rect[2]) + 1.5
            rect_min_y = float(focus_rect[1]) - 1.5
            rect_max_y = float(focus_rect[1]) + float(focus_rect[3]) + 1.5
            if not (rect_min_x <= min_x and max_x <= rect_max_x and rect_min_y <= min_y and max_y <= rect_max_y):
                return None, seam, None, "TRANSFORM_GEOMETRY_UNPROVEN"

            return screen_quad, seam, projected, None
        return None, "transform", None, "transform_seam_unavailable"


    def _renforge_editor_point_in_quad(x, y, quad):
        if not (isinstance(quad, (builtins.list, tuple)) and len(quad) == 4):
            return False
        x, y = float(x), float(y)
        signs = []
        for i in range(4):
            p1 = quad[i]
            p2 = quad[(i + 1) % 4]
            dx = float(p2[0]) - float(p1[0])
            dy = float(p2[1]) - float(p1[1])
            vx = x - float(p1[0])
            vy = y - float(p1[1])
            cross = dx * vy - dy * vx
            if abs(cross) < 1e-9:
                continue
            signs.append(cross > 0)
        if not signs:
            return True
        return all(s == signs[0] for s in signs)


    def _renforge_editor_candidate_hit(candidate, x, y, screen_x=None, screen_y=None):
        rect = candidate.get("rect") or []
        if len(rect) != 4:
            return False
        x, y = int(x), int(y)
        hit_rect = rect
        hit_x, hit_y = x, y
        screen_rect = candidate.get("screen_rect") or []
        if screen_x is not None and screen_y is not None and len(screen_rect) == 4:
            hit_rect = screen_rect
            hit_x, hit_y = int(screen_x), int(screen_y)
        if not (
            hit_rect[0] <= hit_x < hit_rect[0] + hit_rect[2]
            and hit_rect[1] <= hit_y < hit_rect[1] + hit_rect[3]
        ):
            return False

        widget = candidate.get("focused_widget") or candidate.get("named_widget")
        if widget is None and candidate.get("focus") is not None:
            widget = getattr(candidate.get("focus"), "widget", None)

        transform_d = _renforge_editor_find_transform(widget) if widget is not None else None
        if transform_d is None:
            return True

        if transform_d == "MULTIPLE_TRANSFORMS":
            candidate["resolve_error"] = "TRANSFORM_GEOMETRY_UNPROVEN"
            return True

        child_size = getattr(transform_d, "child_size", None)
        if isinstance(child_size, (builtins.list, tuple)) and len(child_size) >= 2:
            w, h = int(child_size[0]), int(child_size[1])
        else:
            w, h = int(rect[2]), int(rect[3])
        if w <= 0 or h <= 0:
            candidate["resolve_error"] = "TRANSFORM_GEOMETRY_UNPROVEN"
            return True

        local_quad = ([0.0, 0.0], [float(w), 0.0], [float(w), float(h)], [0.0, float(h)])
        screen_quad, seam_name, seam_quad, seam_error = _renforge_editor_screen_quad_from_local(
            local_quad,
            transform_d,
            rect,
        )
        if screen_quad is None:
            candidate["resolve_error"] = seam_error or "TRANSFORM_GEOMETRY_UNPROVEN"
            return True

        if _renforge_editor_point_in_quad(x, y, screen_quad):
            return True
        return False


    def _renforge_editor_hit_candidates(x, y, screen_x=None, screen_y=None):
        """Return focus hits first; only fall back to text when no focusable covers the point."""
        focus_hits = []
        for candidate in reversed(_renforge_editor_focus_candidates()):
            if candidate.get("editor_owned"):
                continue
            if _renforge_editor_candidate_hit(candidate, x, y, screen_x, screen_y):
                focus_hits.append(candidate)
        if focus_hits:
            return focus_hits
        text_hits = []
        for candidate in reversed(_renforge_editor_text_candidates()):
            if candidate.get("editor_owned"):
                continue
            if _renforge_editor_candidate_hit(candidate, x, y):
                text_hits.append(candidate)
        return text_hits


    def _renforge_editor_select(x, y, requested_candidate=None):
        state = _renforge_editor_state()
        screen_x, screen_y = None, None
        if requested_candidate is None:
            screen_x, screen_y = x, y
            x, y = _renforge_editor_screen_to_canvas_point(x, y)
        hits = (
            [requested_candidate]
            if isinstance(requested_candidate, builtins.dict)
            else _renforge_editor_hit_candidates(x, y, screen_x, screen_y)
        )
        if not hits:
            _renforge_editor_clear_selection()
            return {"ok": False, "error": "NO_FOCUSABLE_TARGET"}
        # Fail closed when multiple non-focusable text targets cover the same point.
        if (
            len(hits) > 1
            and all(c.get("measurement_method") == "scene_tree_text" for c in hits)
        ):
            state.pointer = [int(x), int(y)]
            state.selected_runtime_key = None
            state.selected_widget_id = None
            state.selected_lock_reason = "AMBIGUOUS_HIT"
            state.selected_rect = None
            _renforge_editor_clear_current_analysis(state)
            state.save_enabled = False
            _renforge_editor_set_label(x, y)
            return {"ok": False, "lock_reason": "AMBIGUOUS_HIT", "error": "AMBIGUOUS_HIT"}
        for candidate in hits:
            if candidate.get("editor_owned"):
                continue
            rect = candidate.get("rect") or []
            if len(rect) != 4:
                continue
            if requested_candidate is None and not _renforge_editor_candidate_hit(
                candidate, x, y, screen_x, screen_y
            ):
                continue
            state.pointer = [int(x), int(y)]
            state.selected_target_key = None
            _renforge_editor_clear_current_analysis(state)
            state.save_enabled = False
            runtime_key = candidate.get("runtime_key")
            selected_screen = runtime_key.get("screen") if isinstance(runtime_key, builtins.dict) else None
            if isinstance(selected_screen, str) and selected_screen:
                state.screen = selected_screen
                # Selection follows transient screens such as say/choice, but
                # must not turn them into session screens that periodic()
                # resurrects later without their required scope arguments.
            if not isinstance(runtime_key, builtins.dict):
                state.selected_runtime_key = None
                state.selected_lock_reason = candidate.get("resolve_error") or "UNMEASURED"
                _renforge_editor_set_label(x, y)
                state.selected_rect = list(rect)
                return {"ok": False, "lock_reason": state.selected_lock_reason}
            if candidate.get("resolve_error") is not None:
                state.selected_runtime_key = runtime_key
                state.selected_widget_id = runtime_key.get("widget_id")
                state.selected_screen = runtime_key.get("screen")
                state.selected_lock_reason = candidate.get("resolve_error")
                state.selected_rect = list(rect)
                _renforge_editor_set_label(x, y)
                return {"ok": False, "lock_reason": state.selected_lock_reason}
            lock = _renforge_editor_validate_runtime_key(runtime_key)
            if lock is not None:
                state.selected_runtime_key = runtime_key
                state.selected_widget_id = runtime_key.get("widget_id")
                state.selected_screen = runtime_key.get("screen")
                state.selected_lock_reason = lock
                _renforge_editor_set_label(x, y)
                state.selected_rect = list(rect)
                observation, _ignore = _renforge_editor_observation_for_candidate(candidate)
                if observation is None:
                    # `_renforge_editor_observation_for_candidate` refuses to build an
                    # observation for exactly the keys this lock rejects, so asking it
                    # again here yields nothing. Fall back to the runtime key that
                    # justifies the lock: the failed-gate UI (#52) needs the instance
                    # discriminator to explain *why* the target is locked.
                    observation = {"runtime_key": runtime_key, "rect": list(rect)}
                return {
                    "ok": False,
                    "lock_reason": lock,
                    "selected": {"widget_id": runtime_key.get("widget_id"), "screen": runtime_key.get("screen")},
                    "observation": observation,
                }
            observation, observe_error = _renforge_editor_observation_for_candidate(candidate)
            if observation is None:
                state.selected_lock_reason = observe_error or "UNMEASURED"
                _renforge_editor_set_label(x, y)
                return {"ok": False, "lock_reason": state.selected_lock_reason}
            runtime_key = observation["runtime_key"]
            target_key = _renforge_editor_target_key(runtime_key)
            state.selected_runtime_key = runtime_key
            state.selected_widget_id = runtime_key.get("widget_id")
            state.selected_screen = runtime_key.get("screen")
            state.selected_target_key = target_key
            state.selected_lock_reason = None
            state.selected_rect = [int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])]
            _renforge_editor_accept_observation(observation)
            target = state.targets.get(target_key)
            if isinstance(target, builtins.dict):
                position = list(target.get("position") or target.get("runtime_baseline") or rect[:2])
                runtime_size = list(target.get("runtime_size") or rect[2:4])
                size = list(target.get("size") or runtime_size)
                state.selected_original_position = list(target.get("runtime_baseline") or rect[:2])
                state.selected_source_position = list(target.get("source_position") or [])
                state.selected_original_size = runtime_size if len(runtime_size) == 2 else None
                state.selected_source_size = list(target.get("source_size") or []) or None
                state.preview_position = position
                state.preview_size = size if len(size) == 2 else None
                state.style_color_input = target.get("style_color") or target.get("style_color_baseline") or ""
                selected_size = size if len(size) == 2 else rect[2:4]
                state.selected_rect = [
                    int(position[0]),
                    int(position[1]),
                    int(selected_size[0]),
                    int(selected_size[1]),
                ]
                _renforge_editor_set_current_analysis(
                    state,
                    target.get("analysis_id"),
                    target.get("source_key"),
                    target.get("capabilities"),
                )
                state.selected_analysis_pending = False
                _renforge_editor_set_status("analyzed")
                _renforge_editor_refresh_save_enabled()
                _renforge_editor_set_label(x, y)
            else:
                state.selected_original_position = [int(rect[0]), int(rect[1])]
                state.selected_source_position = None
                state.preview_position = None
                _renforge_editor_clear_current_analysis(state)
                state.selected_analysis_pending = True if _renforge_editor_host_config() is not None else False
                state.save_enabled = False
                _renforge_editor_set_label(x, y)
                if _renforge_editor_host_config() is not None:
                    state.selected_lock_reason = "ANALYZING"
                    _renforge_editor_set_status("analyzing")
                    state.pending_analysis_key = runtime_key
                    _renforge_editor_ensure_coordinator().submit_host(
                        "analyze_target",
                        {"observation": observation},
                        {
                            "runtime_key": runtime_key,
                            "runtime_baseline": [int(rect[0]), int(rect[1])],
                        },
                    )
            return {
                "ok": True,
                "selected": {"widget_id": state.selected_widget_id, "screen": state.selected_screen},
                "observation": observation,
            }
        _renforge_editor_clear_selection()
        return {"ok": False, "error": "NO_FOCUSABLE_TARGET"}


    def _renforge_editor_set_target_size(target_key, size):
        state = _renforge_editor_state()
        target = state.targets.get(target_key)
        if not isinstance(target, builtins.dict) or size is None or len(size) != 2:
            return {"ok": False, "error": "TARGET_NOT_FOUND"}
        caps = target.get("capabilities") or {}
        if caps.get("resize") is not True:
            return {"ok": False, "error": "RESIZE_UNSUPPORTED"}
        if target.get("zorder_dirty") is True:
            refused = _renforge_editor_refuse_structural_mix(for_zorder=False)
            if refused is not None:
                return refused
        next_size = [max(1, int(size[0])), max(1, int(size[1]))]
        state.save_button_state = "idle"
        target["size"] = next_size
        target["dirty"] = _renforge_editor_target_dirty(target)
        _renforge_editor_show_target_overrides(target.get("screen"))
        if state.selected_target_key == target_key:
            state.preview_size = list(next_size)
            if state.selected_rect is not None and len(state.selected_rect) == 4:
                state.selected_rect = [
                    int(state.selected_rect[0]),
                    int(state.selected_rect[1]),
                    int(next_size[0]),
                    int(next_size[1]),
                ]
            _renforge_editor_set_label(state.pointer[0], state.pointer[1])
        return {"ok": True, "w": next_size[0], "h": next_size[1]}


    def _renforge_editor_resize_context():
        state = _renforge_editor_state()
        if state.selected_target_key is None:
            return None, None, {"ok": False, "error": "NO_SELECTION"}
        target = state.targets.get(state.selected_target_key)
        if not isinstance(target, builtins.dict):
            return None, None, {"ok": False, "error": "TARGET_NOT_FOUND"}
        caps = target.get("capabilities") or state.current_capabilities or {}
        if caps.get("resize") is not True:
            return target, None, {"ok": False, "error": "RESIZE_UNSUPPORTED"}
        base = (
            target.get("size")
            or target.get("runtime_size")
            or state.selected_original_size
            or state.preview_size
        )
        if not (isinstance(base, (builtins.list, tuple)) and len(base) == 2):
            if state.selected_rect is not None and len(state.selected_rect) >= 4:
                base = [int(state.selected_rect[2]), int(state.selected_rect[3])]
            else:
                return target, None, {"ok": False, "error": "NO_SIZE"}
        return target, [int(base[0]), int(base[1])], None


    def _renforge_editor_resize(dw, dh):
        state = _renforge_editor_state()
        _target, before, error = _renforge_editor_resize_context()
        if error is not None:
            return error
        after = [max(1, before[0] + int(dw)), max(1, before[1] + int(dh))]
        if after == before:
            return {"ok": True, "w": after[0], "h": after[1]}
        target_key = state.selected_target_key
        if state.history_index + 1 < len(state.history_entries):
            state.history_entries = state.history_entries[: state.history_index + 1]
        command = {
            "target_key": target_key,
            "kind": "size",
            "before": before,
            "after": after,
        }
        state.history_entries.append(command)
        state.history = [list(entry.get("after") or []) for entry in state.history_entries]
        state.history_index = len(state.history_entries) - 1
        result = _renforge_editor_set_target_size(target_key, after)
        _renforge_editor_refresh_save_enabled()
        renpy.restart_interaction()
        return result


    def _renforge_editor_nudge(dx, dy, shift):
        state = _renforge_editor_state()
        base = state.preview_position or state.selected_original_position
        if base is None:
            candidate, error = _renforge_editor_resolve_selected_candidate()
            if candidate is None:
                return {"ok": False, "error": error}
            rect = candidate.get("rect") or []
            if len(rect) != 4:
                return {"ok": False, "error": "UNMEASURED"}
            base = [int(rect[0]), int(rect[1])]
        step = 10 if shift else 1
        x = int(base[0]) + int(dx) * step
        y = int(base[1]) + int(dy) * step
        return _renforge_editor_apply_preview(x, y, shift=shift, allow_snap=False, record=True)


    def _renforge_editor_event_catcher():
        return renpy.store._renforge_editor_runtime.event_catcher


    def _renforge_editor_event_shift(event):
        mod = getattr(event, "mod", 0)
        if pygame is not None:
            shift_mask = getattr(pygame, "KMOD_SHIFT", 0)
            if shift_mask and mod & shift_mask:
                return True
        return bool(getattr(event, "shift", False))


    def _renforge_editor_event_pos(event, x, y):
        # Displayable.event already normalizes native/Retina coordinates into
        # Ren'Py's logical screen space. SDL's event.pos can remain physical.
        try:
            return int(x), int(y)
        except (TypeError, ValueError, OverflowError):
            pass
        pos = getattr(event, "pos", None)
        if builtins.isinstance(pos, (builtins.list, tuple)) and len(pos) >= 2:
            return int(pos[0]), int(pos[1])
        return 0, 0


    def _renforge_editor_apply_drag_from_pointer(pointer_x, pointer_y, shift):
        state = _renforge_editor_state()
        state.pointer = [int(pointer_x), int(pointer_y)]
        if not state.drag_active:
            base = state.preview_position or state.selected_original_position
            if base is None:
                candidate, error = _renforge_editor_resolve_selected_candidate()
                if candidate is None:
                    return {"ok": False, "error": error}
                rect = candidate.get("rect") or []
                if len(rect) != 4:
                    return {"ok": False, "error": "UNMEASURED"}
                base = [int(rect[0]), int(rect[1])]
            candidates_x, candidates_y = _renforge_editor_anchor_candidates(
                state.selected_runtime_key
            )
            state.snap_candidates_x = candidates_x
            state.snap_candidates_y = candidates_y
            state.drag_active = True
            state.drag_offset = [int(pointer_x) - int(base[0]), int(pointer_y) - int(base[1])]
            state.drag_start_position = list(base)
        desired_x = int(pointer_x) - int(state.drag_offset[0])
        desired_y = int(pointer_y) - int(state.drag_offset[1])
        return _renforge_editor_apply_preview(desired_x, desired_y, shift=shift, allow_snap=True, record=False)


    def _renforge_editor_end_drag():
        state = _renforge_editor_state()
        state.drag_active = False
        state.drag_offset = [0, 0]
        state.snap_candidates_x = None
        state.snap_candidates_y = None
        state.snap_anchor_x = None
        state.snap_anchor_y = None
        state.snap_offset_x = None
        state.snap_offset_y = None
        state.snap_target_x_rect = None
        state.snap_target_y_rect = None
        state.guide_x = None
        state.guide_y = None
        state.guide_x_span = None
        state.guide_y_span = None
        if state.preview_position is not None and state.drag_start_position is not None:
            _renforge_editor_push_history(state.preview_position, before=state.drag_start_position)
        state.drag_start_position = None
        _renforge_editor_refresh_save_enabled()
        return {"ok": True}


    def _renforge_editor_exit():
        state = _renforge_editor_state()
        if state.save_in_progress:
            return {"ok": False, "error": "SAVE_IN_PROGRESS", "active": True}
        dirty_screens = []
        for target in state.targets.values():
            if not isinstance(target, builtins.dict) or not target.get("dirty"):
                continue
            target["dirty"] = False
            screen = target.get("screen")
            if (
                isinstance(screen, str)
                and screen not in dirty_screens
                and renpy.get_screen(screen) is not None
            ):
                dirty_screens.append(screen)
        for screen in dirty_screens:
            renpy.show_screen(screen, _layer="screens")
        state.active = False
        state.screen = None
        state.editor_session_screen = None
        state.selected_runtime_key = None
        state.selected_widget_id = None
        state.selected_screen = None
        state.selected_target_key = None
        state.selected_lock_reason = None
        state.selected_original_position = None
        state.selected_source_position = None
        state.selected_rect = None
        state.selected_analysis_pending = False
        state.preview_position = None
        _renforge_editor_clear_current_analysis(state)
        state.pending_analysis_key = None
        state.history = []
        state.history_entries = []
        state.history_index = -1
        state.targets = {}
        state.save_enabled = False
        state.save_button_state = "idle"
        state.label_text = "No selection"
        state.drag_active = False
        state.resize_active = False
        state.resize_handle = None
        state.resize_start_pointer = None
        state.resize_start_position = None
        state.resize_start_size = None
        state.resize_start_rect = None
        state.snap_anchor_x = None
        state.snap_anchor_y = None
        state.snap_offset_x = None
        state.snap_offset_y = None
        state.snap_candidates_x = None
        state.snap_candidates_y = None
        state.snap_target_x_rect = None
        state.snap_target_y_rect = None
        state.guide_x = None
        state.guide_y = None
        state.guide_x_span = None
        state.guide_y_span = None
        _renforge_editor_apply_layout_transform(False)
        renpy.hide_screen(_EDITOR_SCREEN, layer=_EDITOR_LAYER)
        renpy.restart_interaction()
        return {"ok": True, "active": False}


    def _renforge_editor_handle_event(event, x, y, st):
        state = _renforge_editor_state()
        if not state.active or _renforge_editor_view_mode() == "preview":
            return None
        event_type = getattr(event, "type", None)
        screen_x, screen_y = _renforge_editor_event_pos(event, x, y)
        pointer_x, pointer_y = _renforge_editor_screen_to_canvas_point(screen_x, screen_y)
        key = getattr(event, "key", None)
        shift = _renforge_editor_event_shift(event)
        state.pointer = [int(pointer_x), int(pointer_y)]
        _renforge_editor_set_label(pointer_x, pointer_y)
        if pygame is not None:
            if event_type == getattr(pygame, "MOUSEBUTTONDOWN", None) and getattr(event, "button", 0) == 1:
                # Resize handles win over reselection/move so the drag protocol
                # and live pointer path share one hit order.
                handle = _renforge_editor_hit_resize_handle(pointer_x, pointer_y)
                if handle is not None:
                    begin = _renforge_editor_begin_resize(handle, pointer_x, pointer_y)
                    if begin.get("ok") is True:
                        raise renpy.IgnoreEvent()
                select_reply = _renforge_editor_select(screen_x, screen_y)
                if (
                    select_reply.get("ok") is True
                    and _renforge_editor_tool_allows_drag()
                    and not state.selected_lock_reason
                    and (state.current_capabilities or {}).get("move", True) is True
                ):
                    _renforge_editor_apply_drag_from_pointer(pointer_x, pointer_y, shift)
                raise renpy.IgnoreEvent()
            if event_type == getattr(pygame, "MOUSEMOTION", None) and state.resize_active:
                _renforge_editor_apply_resize_from_pointer(pointer_x, pointer_y)
                raise renpy.IgnoreEvent()
            if event_type == getattr(pygame, "MOUSEMOTION", None) and state.drag_active:
                # Ren'Py already coalesces MOUSEMOTION to the latest event before
                # dispatch. Apply that motion immediately; do not re-queue it.
                _renforge_editor_apply_drag_from_pointer(pointer_x, pointer_y, shift)
                raise renpy.IgnoreEvent()
            if event_type == getattr(pygame, "MOUSEBUTTONUP", None) and getattr(event, "button", 0) == 1:
                if state.resize_active:
                    _renforge_editor_apply_resize_from_pointer(pointer_x, pointer_y)
                    _renforge_editor_end_resize()
                elif state.drag_active:
                    _renforge_editor_apply_drag_from_pointer(pointer_x, pointer_y, shift)
                    _renforge_editor_end_drag()
                else:
                    _renforge_editor_end_drag()
                raise renpy.IgnoreEvent()
            if event_type == getattr(pygame, "KEYDOWN", None):
                if key == getattr(pygame, "K_ESCAPE", None):
                    _renforge_editor_exit()
                    raise renpy.IgnoreEvent()
                if key in (
                    getattr(pygame, "K_LEFT", None),
                    getattr(pygame, "K_RIGHT", None),
                    getattr(pygame, "K_UP", None),
                    getattr(pygame, "K_DOWN", None),
                ):
                    # Leave focus navigation alone when a Ren'Py control is focused
                    # or when the active tool does not own arrow nudges.
                    try:
                        focused = renpy.display.focus.get_focused()
                    except Exception:
                        focused = None
                    if focused is not None:
                        return None
                    if _renforge_editor_tool_mode() not in ("select", "move"):
                        return None
                    dx = dy = 0
                    if key == getattr(pygame, "K_LEFT", None):
                        dx = -1
                    elif key == getattr(pygame, "K_RIGHT", None):
                        dx = 1
                    elif key == getattr(pygame, "K_UP", None):
                        dy = -1
                    else:
                        dy = 1
                    nudge_reply = _renforge_editor_nudge(dx, dy, shift)
                    if not nudge_reply.get("ok", False):
                        return None
                    raise renpy.IgnoreEvent()
        return None


    def _renforge_editor_fake_event(event_type, **attrs):
        event = types.SimpleNamespace(type=event_type)
        for name, value in attrs.items():
            setattr(event, name, value)
        return event


    def _renforge_editor_apply_coordinator_results():
        state = _renforge_editor_state()
        coordinator = _renforge_editor_ensure_coordinator()
        applied = []
        for item in coordinator.collect_nowait():
            applied_item = builtins.dict(item)
            applied_item["applied_thread_id"] = threading.get_ident()
            reply = applied_item.get("reply") or {}
            command = str(applied_item.get("command") or "")
            context = applied_item.get("context") or {}
            analyze_runtime_key = context.get("runtime_key")
            state.coordinator_applied.append(applied_item)
            state.last_coordinator_apply = applied_item
            if reply.get("ok") is True:
                result = reply.get("result")
                if not isinstance(result, builtins.dict):
                    state.save_last_error = "invalid_reply_result"
                    state.save_error = state.save_last_error
                    if command == "analyze_target":
                        if analyze_runtime_key is None or analyze_runtime_key != state.selected_runtime_key or analyze_runtime_key != state.pending_analysis_key:
                            applied_item["stale_analysis"] = True
                            continue
                        state.pending_analysis_key = None
                        state.selected_lock_reason = state.save_last_error
                        state.save_enabled = False
                        state.selected_analysis_pending = False
                        _renforge_editor_clear_current_analysis(state)
                        _renforge_editor_set_status("analyze_failed")
                    elif command in ("commit", "undo_commit"):
                        state.save_in_progress = False
                        state.save_enabled = False
                        _renforge_editor_set_status("commit_failed")
                        state.save_requested = False
                        state.pending_transaction_id = None
                        state.pending_operation = None
                        state.pending_handshake_generation = None
                        state.pending_handshake_sent = False
                        state.pending_reload_requested = False
                    elif command == "commit_status":
                        state.save_in_progress = False
                        _renforge_editor_set_status("status_failed")
                        state.save_requested = False
                    elif command == "reload_handshake":
                        state.save_in_progress = False
                        _renforge_editor_set_status("reload_failed")
                        state.save_enabled = False
                        state.save_requested = False
                        state.pending_transaction_id = None
                        state.pending_operation = None
                        state.pending_handshake_generation = None
                        state.pending_handshake_sent = False
                        state.pending_reload_requested = False
                    else:
                        _renforge_editor_set_status("invalid_result")
                elif command == "analyze_target":
                    if analyze_runtime_key is None or analyze_runtime_key != state.selected_runtime_key or analyze_runtime_key != state.pending_analysis_key:
                        applied_item["stale_analysis"] = True
                        continue
                    state.pending_analysis_key = None
                    state.selected_analysis_pending = False
                    _renforge_editor_set_current_analysis(
                        state,
                        result.get("analysis_id"),
                        result.get("source_key"),
                        result.get("capabilities"),
                    )
                    lock_reason = result.get("lock_reason")
                    if lock_reason is None:
                        state.selected_lock_reason = None
                        if _renforge_editor_status_code() != "reload_committed":
                            _renforge_editor_set_status("analyzed")
                        original_position = result.get("original_position")
                        runtime_baseline = context.get("runtime_baseline") or state.selected_original_position
                        target_key = _renforge_editor_target_key(analyze_runtime_key)
                        if target_key is not None:
                            if (
                                isinstance(original_position, (builtins.list, tuple))
                                and len(original_position) >= 2
                                and isinstance(runtime_baseline, (builtins.list, tuple))
                                and len(runtime_baseline) == 2
                            ):
                                state.selected_source_position = [int(original_position[0]), int(original_position[1])]
                                state.selected_original_position = [int(runtime_baseline[0]), int(runtime_baseline[1])]
                            else:
                                state.selected_source_position = None
                                state.selected_original_position = None
                            original_size = result.get("original_size")
                            runtime_size = None
                            if (
                                isinstance(state.selected_rect, (builtins.list, tuple))
                                and len(state.selected_rect) >= 4
                            ):
                                runtime_size = [int(state.selected_rect[2]), int(state.selected_rect[3])]
                            if (
                                isinstance(original_size, (builtins.list, tuple))
                                and len(original_size) == 2
                                and isinstance(runtime_size, builtins.list)
                                and len(runtime_size) == 2
                                and (state.current_capabilities or {}).get("resize") is True
                            ):
                                state.selected_source_size = [int(original_size[0]), int(original_size[1])]
                                state.selected_original_size = list(runtime_size)
                                state.preview_size = list(runtime_size)
                            else:
                                state.selected_source_size = None
                                state.selected_original_size = None
                                state.preview_size = None
                            state.selected_target_key = target_key
                            source_key = state.current_source_key or {}
                            baseline_style = None
                            if isinstance(source_key, builtins.dict):
                                baseline_style = _renforge_editor_literal_style_color(source_key.get("style_color"))
                            state.style_color_input = baseline_style or ""
                            state.targets[target_key] = {
                                "analysis_id": state.current_analysis_id,
                                "source_key": state.current_source_key,
                                "capabilities": builtins.dict(state.current_capabilities),
                                "runtime_key": analyze_runtime_key,
                                "screen": analyze_runtime_key.get("screen"),
                                "widget_id": analyze_runtime_key.get("widget_id"),
                                "runtime_baseline": list(state.selected_original_position) if state.selected_original_position is not None else None,
                                "source_position": list(state.selected_source_position) if state.selected_source_position is not None else None,
                                "position": list(state.selected_original_position) if state.selected_original_position is not None else None,
                                "runtime_size": list(state.selected_original_size) if state.selected_original_size is not None else None,
                                "source_size": list(state.selected_source_size) if state.selected_source_size is not None else None,
                                "size": list(state.selected_original_size) if state.selected_original_size is not None else None,
                                "style_color_baseline": baseline_style,
                                "style_color": baseline_style,
                                "dirty": False,
                                "generation": int(state.script_generation),
                            }
                        _renforge_editor_refresh_save_enabled()
                    else:
                        state.selected_lock_reason = _renforge_editor_lock_code(lock_reason)
                        state.save_enabled = False
                        _renforge_editor_set_status("locked")
                        _renforge_editor_clear_current_analysis(state)
                elif command in ("commit", "undo_commit"):
                    state.pending_transaction_id = result.get("transaction_id")
                    state.pending_transaction_state = result.get("state")
                    state.pending_operation = command
                    if state.pending_transaction_id is None:
                        state.save_in_progress = False
                        state.save_enabled = False
                        state.save_last_error = "%s missing transaction id" % command
                        _renforge_editor_set_status("commit_failed")
                    else:
                        state.pending_reload_draw_generation = None
                        state.pending_handshake_generation = int(state.script_generation) + 1
                        state.pending_handshake_sent = False
                        state.pending_reload_requested = False
                        state.pending_status_request_id = None
                        state.save_requested = True
                        state.last_commit_status = result
                        state.save_in_progress = True
                        state.save_error = None
                        state.save_last_error = None
                        _renforge_editor_set_status("undo_queued" if command == "undo_commit" else "commit_queued")
                elif command == "commit_status":
                    state.last_commit_status = result
                    state.pending_transaction_state = result.get("state")
                    if result.get("state") == "committed":
                        state.save_in_progress = False
                        state.save_last_error = None
                        state.save_error = None
                        state.save_enabled = False
                        _renforge_editor_set_status("committed")
                        state.save_button_state = "saved"
                    elif result.get("state") != "published":
                        state.save_last_error = result.get("state")
                        state.save_error = str(state.save_last_error)
                elif command == "reload_handshake":
                    if result.get("state") == "committed":
                        committed_tx = state.pending_transaction_id
                        operation = state.pending_operation
                        state.pending_handshake_generation = None
                        state.save_in_progress = False
                        state.save_last_error = None
                        state.save_error = None
                        state.save_button_state = "saved"
                        state.pending_transaction_id = None
                        state.pending_transaction_state = "committed"
                        state.pending_reload_requested = False
                        state.pending_reload_draw_generation = None
                        state.save_enabled = False
                        state.save_requested = False
                        if operation == "undo_commit":
                            state.last_committed_transaction_id = None
                        elif bool(state.pending_commit_is_style_color) or bool(state.pending_commit_is_zorder):
                            state.last_committed_transaction_id = committed_tx
                        else:
                            state.last_committed_transaction_id = None
                        state.pending_commit_is_style_color = False
                        state.pending_commit_is_zorder = False
                        state.pending_operation = None
                        selected_rect = list(state.selected_rect or [])
                        state.targets = {}
                        _renforge_editor_reset_history()
                        _renforge_editor_clear_current_analysis(state)
                        state.selected_target_key = None
                        if len(selected_rect) == 4:
                            screen_x, screen_y = _renforge_editor_canvas_to_screen_point(
                                int(selected_rect[0]) + int(selected_rect[2]) // 2,
                                int(selected_rect[1]) + int(selected_rect[3]) // 2,
                            )
                            _renforge_editor_select(
                                screen_x,
                                screen_y,
                            )
                        _renforge_editor_set_status("reload_committed")
                    else:
                        state.save_last_error = "handshake_failed"
                        state.save_error = state.save_last_error
                        state.save_in_progress = False
                        state.pending_transaction_id = None
                        state.pending_handshake_generation = None
                        _renforge_editor_set_status("reload_handshake_failed")
                else:
                    pass
            else:
                state.save_last_error = "command_failed"
                state.save_error = state.save_last_error
                error_payload = reply.get("error")
                if isinstance(error_payload, builtins.dict):
                    code = error_payload.get("code")
                    message = error_payload.get("message")
                    if code is not None:
                        state.save_last_error = str(code)
                    if message is not None:
                        state.save_error = str(message)
                    state.save_button_state = "idle"
                if command == "analyze_target":
                    state.selected_lock_reason = state.save_last_error
                    state.save_enabled = False
                    state.selected_analysis_pending = False
                    _renforge_editor_clear_current_analysis(state)
                    _renforge_editor_set_status("analyze_failed")
                elif command in ("commit", "undo_commit"):
                    state.save_in_progress = False
                    state.save_enabled = False
                    _renforge_editor_set_status("commit_failed")
                    state.save_button_state = "idle"
                    state.save_requested = False
                    state.pending_transaction_id = None
                    state.pending_operation = None
                    state.pending_handshake_generation = None
                    state.pending_handshake_sent = False
                    state.pending_reload_requested = False
                elif command == "commit_status":
                    state.save_in_progress = False
                    _renforge_editor_set_status("status_failed")
                    state.save_requested = False
                elif command == "reload_handshake":
                    state.save_button_state = "idle"
                    state.save_in_progress = False
                    _renforge_editor_set_status("reload_failed")
                    state.save_enabled = False
                    state.save_requested = False
                    state.pending_transaction_id = None
                    state.pending_operation = None
                    state.pending_handshake_generation = None
                    state.pending_handshake_sent = False
                    state.pending_reload_requested = False
            if command == "analyze_target":
                _renforge_editor_set_label(state.pointer[0], state.pointer[1])
            applied.append(applied_item)
        if len(state.coordinator_applied) > 32:
            state.coordinator_applied[:] = state.coordinator_applied[-32:]
        if applied:
            renpy.restart_interaction()
        return applied


    def _renforge_editor_after_load():
        state = _renforge_editor_state()
        state.script_generation = int(state.script_generation) + 1
        state.pending_reload_draw_generation = None
        _renforge_editor_apply_layout_transform()


    def _renforge_editor_periodic():
        state = _renforge_editor_state()
        if renpy.session.get("_reload_slot"):
            return
        if not state.active:
            if state.editor_injected and renpy.get_screen(_EDITOR_LAUNCHER_SCREEN) is None:
                renpy.show_screen(_EDITOR_LAUNCHER_SCREEN, _layer=_EDITOR_LAYER)
                renpy.restart_interaction()
            return
        editor_session_screen = state.editor_session_screen
        session_shown = True
        if editor_session_screen is not None:
            session_shown = renpy.get_screen(editor_session_screen) is not None
        overlay_shown = renpy.get_screen(_EDITOR_SCREEN) is not None
        if not overlay_shown or not session_shown:
            if not session_shown and editor_session_screen is not None:
                renpy.show_screen(editor_session_screen, _layer="screens")
            if not overlay_shown:
                renpy.show_screen(_EDITOR_SCREEN, _layer=_EDITOR_LAYER)
            renpy.restart_interaction()
            return
        _renforge_editor_apply_coordinator_results()
        if not state.save_in_progress or state.pending_transaction_id is None:
            return
        if not state.pending_reload_requested:
            state.pending_reload_requested = True
            state.pending_reload_started = False
            state.pending_reload_draw_generation = None
            renpy.restart_interaction()
            return
        if not state.pending_reload_started:
            state.pending_reload_started = True
            _renforge_editor_stop_coordinator()
            _renforge_invoke(renpy.reload_script)
            return
        if state.pending_handshake_generation is None:
            return
        if int(state.script_generation) != int(state.pending_handshake_generation):
            return
        if state.pending_reload_draw_generation != state.script_generation:
            state.pending_reload_draw_generation = int(state.script_generation)
            renpy.restart_interaction()
            return
        if not state.pending_handshake_sent:
            state.pending_handshake_sent = True
            state.pending_status_request_id = _renforge_editor_ensure_coordinator().submit_host(
                "reload_handshake",
                {
                    "transaction_id": state.pending_transaction_id,
                    "script_generation": int(state.script_generation),
                },
                {"transaction_id": state.pending_transaction_id},
            )


    def _renforge_editor_h_start(payload):
        payload = payload or {}
        state = _renforge_editor_state()
        screen = payload.get("screen")
        if screen is None:
            return {"ok": False, "error": "screen is required"}
        screen = str(screen)
        state.main_thread_id = threading.get_ident()
        state.active = True
        state.screen = screen
        state.editor_session_screen = screen
        state.selected_runtime_key = None
        state.selected_widget_id = None
        state.selected_screen = None
        state.selected_target_key = None
        state.selected_lock_reason = None
        state.preview_position = None
        state.drag_active = False
        state.drag_offset = [0, 0]
        state.drag_start_position = None
        state.selected_original_position = None
        state.selected_source_position = None
        state.selected_rect = None
        state.selected_analysis_pending = False
        _renforge_editor_clear_current_analysis(state)
        state.history = []
        state.history_entries = []
        state.history_index = -1
        state.targets = {}
        state.save_enabled = False
        state.save_in_progress = False
        state.save_error = None
        state.save_last_error = None
        state.save_requested = False
        state.pending_commit_request_id = None
        state.pending_status_request_id = None
        state.pending_reload_draw_generation = None
        state.pending_attest_request_id = None
        state.last_commit_status = None
        state.pending_transaction_id = None
        state.pending_analysis_key = None
        state.pending_handshake_generation = None
        state.pending_handshake_sent = False
        state.save_button_state = "idle"
        state.pending_reload_requested = False
        state.opacity = 0.9
        state.label_text = "No selection"
        state.last_event_trace = []
        _renforge_editor_ensure_coordinator()
        renpy.show_screen(screen, _layer="screens")
        _renforge_editor_apply_layout_transform()
        renpy.show_screen(_EDITOR_SCREEN, _layer=_EDITOR_LAYER)
        renpy.restart_interaction()
        return {
            "ok": True,
            "active": True,
            "overlay_screen": _EDITOR_SCREEN,
            "owner": _EDITOR_OWNER,
            "save_enabled": False,
            "script_generation": int(state.script_generation),
        }


    def _renforge_editor_save():
        state = _renforge_editor_state()
        if state.selected_lock_reason:
            return {"ok": False, "error": "TARGET_LOCKED"}
        if state.current_analysis_id is None:
            return {"ok": False, "error": "NO_ANALYSIS"}
        if not state.save_enabled or state.save_in_progress:
            return {"ok": False, "error": "SAVE_UNAVAILABLE"}
        intents = _renforge_editor_collect_intents()
        if not intents:
            return {"ok": False, "error": "NO_INTENTS"}
        state.save_in_progress = True
        state.save_button_state = "saving"
        state.save_requested = True
        state.save_error = None
        state.save_last_error = None
        state.pending_commit_is_style_color = bool(
            intents
            and all(
                isinstance(intent, builtins.dict)
                and _renforge_editor_literal_style_color(intent.get("color")) is not None
                for intent in intents
            )
        )
        state.pending_commit_is_zorder = bool(
            intents
            and all(
                isinstance(intent, builtins.dict)
                and intent.get("operation") == "raise_adjacent_sibling"
                for intent in intents
            )
        )
        _renforge_editor_set_status("saving")
        pending = _renforge_editor_ensure_coordinator().submit_host(
            "commit",
            {"intents": intents},
            {"command": "commit"},
        )
        state.pending_commit_request_id = pending
        renpy.restart_interaction()
        return {"ok": True, "request_id": pending, "intents": intents}


    def _renforge_editor_h_save(payload):
        return _renforge_editor_save()


    def _renforge_editor_h_select(payload):
        payload = payload or {}
        x = payload.get("x")
        y = payload.get("y")
        if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
            return {"ok": False, "error": "x and y must be integers"}
        if payload.get("coordinate_space", "logical") != "screen":
            x, y = _renforge_editor_canvas_to_screen_point(x, y)
            x, y = int(round(x)), int(round(y))
        return _renforge_editor_select(x, y)


    def _renforge_editor_h_drag(payload):
        payload = payload or {}
        points = payload.get("points")
        if not builtins.isinstance(points, (builtins.list, tuple)) or not points:
            return {"ok": False, "error": "points must be a non-empty list"}
        shift = bool(payload.get("shift", False))
        state = _renforge_editor_state()
        if not state.active:
            return {"ok": False, "error": "editor is not active"}
        if pygame is None:
            return {"ok": False, "error": "pygame_sdl2 is unavailable"}

        normalized_points = []
        coordinate_space = payload.get("coordinate_space", "logical")
        for point in points:
            if not builtins.isinstance(point, (builtins.list, tuple)) or len(point) < 2:
                return {"ok": False, "error": "invalid point"}
            point_x, point_y = int(point[0]), int(point[1])
            if coordinate_space != "screen":
                point_x, point_y = _renforge_editor_canvas_to_screen_point(point_x, point_y)
            normalized_points.append([int(round(point_x)), int(round(point_y))])

        def dispatch(event, px, py):
            try:
                _renforge_editor_handle_event(event, px, py, 0.0)
            except renpy.IgnoreEvent:
                pass

        def sample(point):
            current = _renforge_editor_state()
            preview = list(current.preview_position or [])
            return {
                "point": list(point),
                "preview_position": preview if len(preview) == 2 else None,
                "guide_x": current.guide_x,
                "guide_y": current.guide_y,
            }

        mod = getattr(pygame, "KMOD_SHIFT", 0) if shift else 0
        first_x, first_y = normalized_points[0]
        dispatch(
            _renforge_editor_fake_event(
                getattr(pygame, "MOUSEBUTTONDOWN", None),
                button=1,
                pos=(first_x, first_y),
                mod=mod,
            ),
            first_x,
            first_y,
        )
        if not state.drag_active and not state.resize_active:
            return {
                "ok": False,
                "error": state.selected_lock_reason or "drag did not start",
            }

        samples = [sample(normalized_points[0])]
        previous_x, previous_y = first_x, first_y
        for px, py in normalized_points[1:]:
            dispatch(
                _renforge_editor_fake_event(
                    getattr(pygame, "MOUSEMOTION", None),
                    pos=(px, py),
                    rel=(px - previous_x, py - previous_y),
                    buttons=(1, 0, 0),
                    mod=mod,
                ),
                px,
                py,
            )
            samples.append(sample([px, py]))
            previous_x, previous_y = px, py

        preview_before_mouse_up = list(state.preview_position or [])
        preview_size_before_mouse_up = list(state.preview_size or [])
        drag_active_before_mouse_up = bool(state.drag_active)
        resize_active_before_mouse_up = bool(state.resize_active)
        dispatch(
            _renforge_editor_fake_event(
                getattr(pygame, "MOUSEBUTTONUP", None),
                button=1,
                pos=(previous_x, previous_y),
                mod=mod,
            ),
            previous_x,
            previous_y,
        )
        return {
            "ok": True,
            "event_method": "_renforge_editor_handle_event",
            "preview_method": state.last_preview_method,
            "samples": samples,
            "preview_before_mouse_up": preview_before_mouse_up,
            "preview_size_before_mouse_up": preview_size_before_mouse_up,
            "drag_active_before_mouse_up": drag_active_before_mouse_up,
            "resize_active_before_mouse_up": resize_active_before_mouse_up,
            "guide_x": state.guide_x,
            "guide_y": state.guide_y,
        }


    def _renforge_editor_h_size(payload):
        payload = payload or {}
        state = _renforge_editor_state()
        if not state.active:
            return {"ok": False, "error": "editor is not active"}
        if "w" in payload or "h" in payload or "width" in payload or "height" in payload:
            _target, base, error = _renforge_editor_resize_context()
            if error is not None:
                return error
            width = payload.get("w", payload.get("width", base[0]))
            height = payload.get("h", payload.get("height", base[1]))
            try:
                width = int(width)
                height = int(height)
            except Exception:
                return {"ok": False, "error": "SIZE_INVALID"}
            return _renforge_editor_resize(width - int(base[0]), height - int(base[1]))
        dw = payload.get("dw", 0)
        dh = payload.get("dh", 0)
        try:
            dw = int(dw)
            dh = int(dh)
        except Exception:
            return {"ok": False, "error": "SIZE_DELTA_INVALID"}
        return _renforge_editor_resize(dw, dh)


    def _renforge_editor_h_key(payload):
        payload = payload or {}
        key_name = str(payload.get("key") or "").lower()
        repeat = int(payload.get("repeat", 1) or 1)
        shift = bool(payload.get("shift", False))
        if repeat < 1:
            repeat = 1
        state = _renforge_editor_state()
        if not state.active:
            return {"ok": False, "error": "editor is not active"}
        if pygame is None:
            return {"ok": False, "error": "pygame_sdl2 is unavailable"}
        key_map = {
            "left": getattr(pygame, "K_LEFT", None),
            "right": getattr(pygame, "K_RIGHT", None),
            "up": getattr(pygame, "K_UP", None),
            "down": getattr(pygame, "K_DOWN", None),
            "escape": getattr(pygame, "K_ESCAPE", None),
        }
        key_value = key_map.get(key_name)
        if key_value is None:
            return {"ok": False, "error": "unsupported key"}
        nudge_map = {
            "left": (-1, 0),
            "right": (1, 0),
            "up": (0, -1),
            "down": (0, 1),
        }
        if key_name == "escape":
            exit_reply = _renforge_editor_exit()
            state.last_event_trace = [{"key": key_name, "shift": shift}]
            if not exit_reply.get("ok", False):
                return exit_reply
            return {"ok": True, "repeat": repeat, "shift": shift, "active": state.active}
        if key_name in nudge_map and _renforge_editor_tool_mode() not in ("select", "move"):
            state.last_event_trace = [{"key": key_name, "shift": shift, "ignored": True}]
            return {"ok": False, "error": "TOOL_MODE_BLOCKED", "active": state.active}
        traces = []
        for _index in range(repeat):
            if key_name in nudge_map:
                dx, dy = nudge_map[key_name]
                nudge_reply = _renforge_editor_nudge(dx, dy, shift)
                if not nudge_reply.get("ok", False):
                    return nudge_reply
                continue
            else:
                event = _renforge_editor_fake_event(
                    pygame.KEYDOWN,
                    key=key_value,
                    mod=getattr(pygame, "KMOD_SHIFT", 0) if shift else 0,
                )
                try:
                    _renforge_editor_handle_event(event, state.pointer[0], state.pointer[1], 0.0)
                except renpy.IgnoreEvent:
                    pass
            traces.append({"key": key_name, "shift": shift})
        state.last_event_trace = traces
        return {"ok": True, "repeat": repeat, "shift": shift, "active": state.active}


    def _renforge_editor_h_undo(payload):
        return _renforge_editor_undo()


    def _renforge_editor_h_redo(payload):
        return _renforge_editor_redo()


    def _renforge_editor_h_reset(payload):
        return _renforge_editor_reset_selected()


    def _renforge_editor_h_observe_selected(payload):
        candidate, error = _renforge_editor_resolve_selected_candidate()
        if candidate is None:
            return {"ok": False, "error": error}
        observation, observe_error = _renforge_editor_observation_for_candidate(candidate)
        if observation is None:
            return {"ok": False, "error": observe_error or "UNMEASURED"}
        _renforge_editor_accept_observation(observation)
        return {"ok": True, "observation": observation}


    def _renforge_editor_h_observe_target(payload):
        payload = payload or {}
        runtime_key = payload.get("runtime_key")
        if not isinstance(runtime_key, builtins.dict):
            return {"ok": False, "error": "runtime_key required"}
        candidates = [
            candidate
            for candidate in _renforge_editor_all_candidates()
            if candidate.get("runtime_key") is not None
            and _renforge_editor_same_target_key(candidate.get("runtime_key"), runtime_key)
        ]
        if len(candidates) > 1:
            # The focus pass and the non-focusable text pass both walk the same
            # screen, so one widget can surface in each (#72). These candidates
            # matched runtime_key under same_target_key, so they denote a single
            # target seen twice, not two instances. Keep the first — focus candidates
            # come first, and focus_list is the reference measurement. Genuine
            # ambiguity is still caught on the signature fallback below.
            candidates = candidates[:1]
        if not candidates:
            # fallback by stable signature
            candidates = []
            wanted = _renforge_editor_rebind_signature(runtime_key)
            for candidate in _renforge_editor_all_candidates():
                candidate_key = candidate.get("runtime_key")
                if not isinstance(candidate_key, builtins.dict):
                    continue
                if _renforge_editor_rebind_signature(candidate_key) == wanted:
                    already_seen = False
                    for seen in candidates:
                        seen_key = seen.get("runtime_key")
                        if isinstance(seen_key, builtins.dict) and _renforge_editor_rebind_signature(seen_key) == wanted:
                            already_seen = True
                            break
                    if not already_seen:
                        candidates.append(candidate)
        if len(candidates) > 1:
            return {"ok": False, "error": "AMBIGUOUS_REBIND"}
        if not candidates:
            return {"ok": False, "error": "NO_MATCHING_TARGET"}
        candidate = candidates[0]
        observation, observe_error = _renforge_editor_observation_for_candidate(candidate)
        if observation is None:
            return {"ok": False, "error": observe_error or "UNMEASURED"}
        _renforge_editor_accept_observation(observation)
        return {"ok": True, "observation": observation}


    def _renforge_editor_h_attest_targets(payload):
        payload = payload or {}
        transaction_id = payload.get("transaction_id")
        if not transaction_id:
            return {"ok": False, "error": "transaction_id required"}
        expected_targets = payload.get("expected_targets")
        if not builtins.isinstance(expected_targets, (builtins.list, tuple)):
            return {"ok": False, "error": "expected_targets must be a list"}
        generation = payload.get("script_generation")
        try:
            generation = int(generation)
        except Exception:
            return {"ok": False, "error": "script_generation must be an integer"}
        state = _renforge_editor_state()
        if generation != state.script_generation:
            return {
                "ok": False,
                "error": "GENERATION_MISMATCH",
                "expected": generation,
                "known": state.script_generation,
            }
        attested = []
        for expected in expected_targets:
            if not isinstance(expected, builtins.dict):
                return {"ok": False, "error": "expected target entry required"}
            source_key = expected.get("source_key")
            if not isinstance(source_key, builtins.dict):
                return {"ok": False, "error": "source_key missing"}
            expected_runtime_key = expected.get("runtime_key")
            if not isinstance(expected_runtime_key, builtins.dict):
                return {"ok": False, "error": "runtime_key missing"}
            widget_id = source_key.get("widget_id")
            if widget_id is not None and (not isinstance(widget_id, str) or not widget_id):
                return {"ok": False, "error": "widget_id invalid"}
            if widget_id is None:
                locator = source_key.get("locator")
                if not (
                    isinstance(locator, builtins.dict)
                    and locator.get("kind") == "source"
                    and locator.get("source_location") == expected_runtime_key.get("source_location")
                ):
                    return {"ok": False, "error": "source locator missing"}
            targets = [
                candidate
                for candidate in _renforge_editor_all_candidates()
                if candidate.get("runtime_key") is not None
                and _renforge_editor_same_target_key(candidate.get("runtime_key"), expected_runtime_key)
                and isinstance(candidate.get("runtime_key", {}).get("source_location"), list)
            ]
            if len(targets) > 1:
                targets = targets[:1]
            if not targets:
                wanted = _renforge_editor_rebind_signature(expected_runtime_key)
                signature_matches = []
                for candidate in _renforge_editor_all_candidates():
                    candidate_key = candidate.get("runtime_key")
                    if not isinstance(candidate_key, builtins.dict):
                        continue
                    if _renforge_editor_rebind_signature(candidate_key) != wanted:
                        continue
                    # The focus pass and the non-focusable text pass both walk the
                    # same screen, so one widget can surface in each (#72). Count
                    # an identical target signature once: it is one target seen twice,
                    # not two instances competing for the rebind.
                    already_seen = False
                    for seen in signature_matches:
                        seen_key = seen.get("runtime_key")
                        if isinstance(seen_key, builtins.dict) and _renforge_editor_rebind_signature(seen_key) == wanted:
                            already_seen = True
                            break
                    if not already_seen:
                        signature_matches.append(candidate)
                if len(signature_matches) > 1:
                    return {"ok": False, "error": "AMBIGUOUS_REBIND", "widget_id": widget_id}
                if len(signature_matches) == 1:
                    targets = signature_matches
            if len(targets) != 1:
                return {"ok": False, "error": "TARGET_NOT_FOUND", "widget_id": widget_id}
            # Forced-refusal test path for style-colour attestation rollback.
            expected_style = _renforge_editor_normalize_style_color(expected.get("style_color"))
            if (
                expected_style is not None
                and os.environ.get("RENFORGE_STYLE_COLOR_LIVE") == "1"
                and bool(state.refuse_next_style_attestation)
            ):
                state.refuse_next_style_attestation = False
                return {
                    "ok": False,
                    "error": "STYLE_COLOR_ATTESTATION_REFUSED",
                    "state": "refused",
                    "widget_id": widget_id,
                    "expected": expected_style,
                }
            observation, observe_error = _renforge_editor_observation_for_candidate(targets[0])
            if observation is None:
                return {"ok": False, "error": observe_error or "UNMEASURED", "widget_id": widget_id}
            if int(observation.get("script_generation", -999)) != generation:
                return {
                    "ok": False,
                    "error": "GENERATION_MISMATCH",
                    "expected": generation,
                    "known": observation.get("script_generation"),
                }
            if expected_style is not None:
                observed_style = _renforge_editor_normalize_style_color(observation.get("style_color"))
                if observed_style != expected_style:
                    return {
                        "ok": False,
                        "error": "TARGET_STYLE_COLOR_MISMATCH",
                        "widget_id": widget_id,
                        "expected": expected_style,
                        "observed": observed_style,
                    }
            expected_position = expected.get("position")
            rect = observation.get("rect") or []
            if isinstance(expected_position, (builtins.list, tuple)) and len(expected_position) == 2:
                expected_x = int(expected_position[0])
                expected_y = int(expected_position[1])
                # Issue #39 requires pixel agreement within one logical pixel.
                if not (abs(int(rect[0]) - expected_x) <= 1 and abs(int(rect[1]) - expected_y) <= 1):
                    return {
                        "ok": False,
                        "error": "TARGET_POSITION_MISMATCH",
                        "widget_id": widget_id,
                        "expected": expected_position,
                        "observed": rect[:2] if isinstance(rect, builtins.list) and len(rect) >= 2 else None,
                    }
            expected_size = expected.get("size")
            if isinstance(expected_size, (builtins.list, tuple)) and len(expected_size) == 2:
                expected_w = int(expected_size[0])
                expected_h = int(expected_size[1])
                if not (
                    isinstance(rect, builtins.list)
                    and len(rect) >= 4
                    and abs(int(rect[2]) - expected_w) <= 1
                    and abs(int(rect[3]) - expected_h) <= 1
                ):
                    return {
                        "ok": False,
                        "error": "TARGET_SIZE_MISMATCH",
                        "widget_id": widget_id,
                        "expected": expected_size,
                        "observed": rect[2:4] if isinstance(rect, builtins.list) and len(rect) >= 4 else None,
                    }
            attested.append(
                {
                    "widget_id": widget_id,
                    "analysis_id": source_key.get("analysis_id"),
                    "observed": observation,
                }
            )
        return {
            "ok": True,
            "state": "all_targets_attested",
            "transaction_id": transaction_id,
            "generation": generation,
            "attested": attested,
        }


    def _renforge_editor_h_restore_preview(payload):
        return _renforge_editor_restore_preview()


    def _renforge_editor_h_set_opacity(payload):
        payload = payload or {}
        opacity = payload.get("opacity")
        try:
            opacity = float(opacity)
        except Exception:
            return {"ok": False, "error": "opacity must be numeric"}
        opacity = max(0.05, min(1.0, opacity))
        state = _renforge_editor_state()
        state.opacity = opacity
        renpy.restart_interaction()
        return {"ok": True, "opacity": opacity}


    def _renforge_editor_h_pointer(payload):
        payload = payload or {}
        x = payload.get("x")
        y = payload.get("y")
        if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
            return {"ok": False, "error": "x and y must be integers"}
        if payload.get("coordinate_space", "logical") == "screen":
            x, y = _renforge_editor_screen_to_canvas_point(x, y)
        state = _renforge_editor_state()
        state.pointer = [int(x), int(y)]
        _renforge_editor_set_label(int(x), int(y))
        renpy.restart_interaction()
        return {"ok": True, "pointer": [int(x), int(y)]}


    def _renforge_editor_h_validate_runtime_key(payload):
        payload = payload or {}
        try:
            if int(payload.get("instance_count", 1) or 1) > 1:
                return {"ok": False, "lock_reason": "MULTI_INSTANCE_UNSUPPORTED"}
        except Exception:
            return {"ok": False, "lock_reason": "MULTI_INSTANCE_UNSUPPORTED"}
        runtime_key = payload.get("runtime_key")
        lock = _renforge_editor_validate_runtime_key(runtime_key)
        if lock is None:
            return {"ok": True}
        return {"ok": False, "lock_reason": lock}




    def _renforge_editor_h_coordinator_submit(payload):
        payload = payload or {}
        state = _renforge_editor_state()
        coordinator = _renforge_editor_ensure_coordinator()
        request_id = coordinator.submit(payload.get("observation"))
        return {
            "ok": True,
            "request_id": request_id,
            "main_thread_id": state.main_thread_id or threading.get_ident(),
        }


    def _renforge_editor_h_coordinator_collect(payload):
        applied = _renforge_editor_apply_coordinator_results()
        return {"ok": True, "applied": applied}



    def _renforge_editor_h_layout_snapshot(payload):
        payload = payload or {}
        width = payload.get("width")
        height = payload.get("height")
        view_mode = payload.get("view_mode")
        layout_mode = payload.get("layout_mode")
        try:
            metrics = _renforge_editor_layout_metrics(
                screen_w=width,
                screen_h=height,
                view_mode=view_mode,
                layout_mode=layout_mode,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "metrics": metrics,
            "toolbar_ids": list(metrics.get("fixed_control_ids") or []),
        }

    def _renforge_editor_h_status(payload):
        state = _renforge_editor_state()
        return {
            "ok": True,
            "active": bool(state.active),
            "save_enabled": bool(state.save_enabled),
            "save_button_state": state.save_button_state,
            "dirty_target_count": len([target for target in state.targets.values() if target.get("dirty")]),
            "selected_widget_id": state.selected_widget_id,
            "selected_runtime_key": state.selected_runtime_key,
            "selected_lock_reason": state.selected_lock_reason,
            "opacity": float(state.opacity),
            "guide_x": state.guide_x,
            "guide_y": state.guide_y,
            "rf_exit_rect": _renforge_editor_control_rect("rf_exit"),
            "last_preview_method": state.last_preview_method,
            "last_restore_method": state.last_restore_method,
            "script_generation": state.script_generation,
            "save_in_progress": bool(state.save_in_progress),
            "save_requested": bool(state.save_requested),
            "save_error": state.save_error,
            "status_code": _renforge_editor_status_code(),
            "status_text": _renforge_editor_status_text(),
            "selected_original_position": state.selected_original_position,
            "selected_source_position": state.selected_source_position,
            "selected_original_size": state.selected_original_size,
            "selected_source_size": state.selected_source_size,
            "preview_position": state.preview_position,
            "preview_size": state.preview_size,
            "history_index": state.history_index,
            "history_length": len(state.history_entries),
            "current_analysis_id": state.current_analysis_id,
            "current_source_key": state.current_source_key,
            "current_capabilities": (
                builtins.dict(state.current_capabilities)
                if state.current_analysis_id is not None
                else {}
            ),
            "pending_transaction_id": state.pending_transaction_id,
            "pending_operation": state.pending_operation,
            "last_committed_transaction_id": state.last_committed_transaction_id,
            "style_color_input": state.style_color_input,
            "refuse_next_style_attestation": bool(state.refuse_next_style_attestation),
            "pending_handshake_generation": state.pending_handshake_generation,
            "pending_handshake_sent": state.pending_handshake_sent,
            "pending_reload_requested": state.pending_reload_requested,
            "accepted_observations": list(state.accepted_observations),
        }


    def _renforge_editor_h_stop(payload):
        return _renforge_editor_exit()


    def _renforge_editor_is_active():
        return bool(_renforge_editor_state().active)




    def _renforge_editor_opacity():
        return float(_renforge_editor_state().opacity)


    def _renforge_editor_guide_x():
        return _renforge_editor_state().guide_x


    def _renforge_editor_guide_y():
        return _renforge_editor_state().guide_y


    def _renforge_editor_guide_snapshot():
        state = _renforge_editor_state()
        line_x = None
        line_y = None
        if state.guide_x is not None and state.guide_x_span is not None:
            start, end = state.guide_x_span
            line_x = [int(state.guide_x), int(start), max(1, int(end) - int(start))]
        if state.guide_y is not None and state.guide_y_span is not None:
            start, end = state.guide_y_span
            line_y = [int(start), int(state.guide_y), max(1, int(end) - int(start))]
        return {"line_x": line_x, "line_y": line_y}


    def _renforge_editor_distance_snapshot():
        state = _renforge_editor_state()
        preview = state.preview_position
        original = state.selected_original_position
        rect = state.selected_rect
        if (
            not isinstance(preview, (builtins.list, builtins.tuple))
            or len(preview) != 2
            or not isinstance(original, (builtins.list, builtins.tuple))
            or len(original) != 2
            or not isinstance(rect, (builtins.list, builtins.tuple))
            or len(rect) != 4
        ):
            return None
        delta_x = int(preview[0]) - int(original[0])
        delta_y = int(preview[1]) - int(original[1])
        return {
            "x": int(rect[0]),
            "y": int(rect[1]),
            "w": int(rect[2]),
            "h": int(rect[3]),
            "delta_x": delta_x,
            "delta_y": delta_y,
            "text_x": "dx %s%d px" % ("+" if delta_x >= 0 else "", delta_x),
            "text_y": "dy %s%d px" % ("+" if delta_y >= 0 else "", delta_y),
        }

    def _renforge_editor_measure_snapshot():
        """Track raw pointer displacement throughout an active drag.

        Snap guides intentionally hold the preview at an anchor. Measurement
        lines still follow the pointer so a small or directional movement is
        visible instead of flashing only after snap release.
        """
        state = _renforge_editor_state()
        if not state.drag_active:
            return None
        pointer = state.pointer
        drag_start = state.drag_start_position
        drag_offset = state.drag_offset
        preview = state.preview_position
        original = state.selected_original_position
        rect = state.selected_rect
        if (
            not isinstance(pointer, (builtins.list, builtins.tuple))
            or len(pointer) != 2
            or not isinstance(rect, (builtins.list, builtins.tuple))
            or len(rect) != 4
        ):
            return None
        if (
            isinstance(drag_start, (builtins.list, builtins.tuple))
            and len(drag_start) == 2
            and isinstance(drag_offset, (builtins.list, builtins.tuple))
            and len(drag_offset) == 2
        ):
            origin_x, origin_y = int(drag_start[0]), int(drag_start[1])
            current_x = int(pointer[0]) - int(drag_offset[0])
            current_y = int(pointer[1]) - int(drag_offset[1])
        elif (
            isinstance(preview, (builtins.list, builtins.tuple))
            and len(preview) == 2
            and isinstance(original, (builtins.list, builtins.tuple))
            and len(original) == 2
        ):
            origin_x, origin_y = int(original[0]), int(original[1])
            current_x, current_y = int(preview[0]), int(preview[1])
        else:
            return None
        width, height = int(rect[2]), int(rect[3])
        dx = current_x - origin_x
        dy = current_y - origin_y
        return {
            "dx": dx,
            "dy": dy,
        }

    def _renforge_editor_label_snapshot():
        state = _renforge_editor_state()
        if (
            state.selected_runtime_key is None
            and state.selected_lock_reason is None
        ) or state.selected_rect is None:
            return None
        rect = list(state.label_rect or [20, 20, 220, 32])
        if len(rect) != 4:
            rect = [20, 20, 220, 32]
        return {
            "x": int(rect[0]),
            "y": int(rect[1]),
            "w": int(rect[2]),
            "h": int(rect[3]),
            "alpha": float(max(0.1, min(1.0, state.label_alpha * state.opacity))),
            "text": _renforge_editor_escape_label_text(state.label_text),
        }



    def _renforge_editor_h_style_color(payload):
        payload = payload or {}
        color = payload.get("color")
        if color is None:
            return {"ok": False, "error": "color required"}
        return _renforge_editor_set_style_color(color, record=True)


    def _renforge_editor_h_zorder(payload):
        return _renforge_editor_raise_adjacent_sibling(record=True)


    def _renforge_editor_h_force_style_attestation_refusal(payload):
        if os.environ.get("RENFORGE_STYLE_COLOR_LIVE") != "1":
            return {"ok": False, "error": "LIVE_TEST_HOOK_UNAVAILABLE"}
        state = _renforge_editor_state()
        state.refuse_next_style_attestation = True
        return {"ok": True, "refuse_next_style_attestation": True}


    _renforge_editor_state()
    _renforge_editor_ensure_coordinator()
    if callable(getattr(renpy.config, "periodic_callbacks", None)):
        pass
    if all(getattr(callback, "__name__", "") != "_renforge_editor_periodic" for callback in renpy.config.periodic_callbacks):
        renpy.config.periodic_callbacks.append(_renforge_editor_periodic)
    if all(getattr(callback, "__name__", "") != "_renforge_editor_after_load" for callback in renpy.config.after_load_callbacks):
        renpy.config.after_load_callbacks.append(_renforge_editor_after_load)

    handlers = globals().get("_RENFORGE_HANDLERS")
    if isinstance(handlers, builtins.dict):
        handlers["editor_task0_start"] = _renforge_editor_h_start
        handlers["editor_task0_stop"] = _renforge_editor_h_stop
        handlers["editor_task0_select"] = _renforge_editor_h_select
        handlers["editor_task0_drag"] = _renforge_editor_h_drag
        handlers["editor_task0_key"] = _renforge_editor_h_key
        handlers["editor_task0_size"] = _renforge_editor_h_size
        handlers["editor_task0_undo"] = _renforge_editor_h_undo
        handlers["editor_task0_redo"] = _renforge_editor_h_redo
        handlers["editor_task0_reset"] = _renforge_editor_h_reset
        handlers["editor_task0_save"] = _renforge_editor_h_save
        handlers["editor_task0_style_color"] = _renforge_editor_h_style_color
        handlers["editor_task0_zorder"] = _renforge_editor_h_zorder
        if os.environ.get("RENFORGE_STYLE_COLOR_LIVE") == "1":
            handlers["editor_task0_force_style_attestation_refusal"] = _renforge_editor_h_force_style_attestation_refusal
        handlers["editor_task0_observe_selected"] = _renforge_editor_h_observe_selected
        handlers["editor_task0_restore_preview"] = _renforge_editor_h_restore_preview
        handlers["editor_task0_set_opacity"] = _renforge_editor_h_set_opacity
        handlers["editor_task0_pointer"] = _renforge_editor_h_pointer
        handlers["editor_task0_validate_runtime_key"] = _renforge_editor_h_validate_runtime_key
        handlers["editor_task0_coordinator_submit"] = _renforge_editor_h_coordinator_submit
        handlers["editor_task0_coordinator_collect"] = _renforge_editor_h_coordinator_collect
        handlers["editor_task0_status"] = _renforge_editor_h_status
        handlers["editor_task0_layout_snapshot"] = _renforge_editor_h_layout_snapshot
        handlers["editor_observe_target"] = _renforge_editor_h_observe_target
        handlers["editor_attest_targets"] = _renforge_editor_h_attest_targets

        # Styles exist once every .rpy has been loaded; bind the thin thumbs now
        # so the first show of the overlay already uses them.
        try:
            _renforge_editor_install_scrollbar_styles()
        except NameError:
            pass


# Scoped chrome styles. The host game keeps its own vscrollbar; only viewports
# with style_prefix "rf" pick these up. Properties are filled at runtime by
# _renforge_editor_install_scrollbar_styles (assets + scaled sizes).
style rf_vscrollbar is vscrollbar
style rf_scrollbar is scrollbar
style rf_viewport is viewport
style rf_side is side
