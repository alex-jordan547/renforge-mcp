init 1101 python:
    _RF_EDITOR_PANEL_NAMES = frozenset(("tree", "inspector", "style"))
    _RF_EDITOR_PANEL_ICONS = {
        "tree": "panel-tree",
        "inspector": "panel-inspector",
        "style": "panel-style",
    }
    _RF_EDITOR_PANEL_LABEL_KEYS = {
        "tree": "panel.tree",
        "inspector": "panel.inspector",
        "style": "panel.style",
    }

    def _renforge_editor_hidden_panels():
        state = _renforge_editor_state()
        hidden = getattr(state, "hidden_panels", None)
        if not isinstance(hidden, set):
            hidden = set()
            state.hidden_panels = hidden
        return hidden

    def _renforge_editor_panel_visible(panel_name):
        return panel_name in _RF_EDITOR_PANEL_NAMES and panel_name not in _renforge_editor_hidden_panels()

    def _renforge_editor_panel_tooltip(panel_name, action):
        label = _renforge_editor_t(_RF_EDITOR_PANEL_LABEL_KEYS[panel_name])
        return _renforge_editor_t("panel.%s" % action).replace("{panel}", label)

    def _renforge_editor_panel_text(value, limit):
        value = str(value or "")
        return value if len(value) <= limit else value[:limit - 1] + "…"

    def _renforge_editor_toggle_panel(panel_name):
        if panel_name not in _RF_EDITOR_PANEL_NAMES:
            return {"ok": False, "error": "unknown editor panel"}
        hidden = _renforge_editor_hidden_panels()
        if panel_name in hidden:
            hidden.remove(panel_name)
        else:
            hidden.add(panel_name)
        renpy.restart_interaction()
        return {"ok": True, "panel": panel_name, "visible": panel_name not in hidden}


screen _rf_editor_panel_hide_button(panel_name, side, control_id):
    $ _rf_hide_icon = "panel-collapse-left" if side == "left" else "panel-collapse-right"
    button:
        id control_id
        action Function(_renforge_editor_consume, _renforge_editor_toggle_panel, panel_name)
        tooltip _renforge_editor_panel_tooltip(panel_name, "hide")
        xsize _renforge_editor_ui_px(32, minimum=28)
        ysize _renforge_editor_ui_px(32, minimum=28)
        background Solid("#00000000")
        hover_background Solid(_renforge_editor_ui_color("row_hover"))
        padding (0, 0)
        yalign 0.5
        add _renforge_editor_ui_icon(_rf_hide_icon):
            xysize (
                _renforge_editor_ui_px(18, minimum=14),
                _renforge_editor_ui_px(18, minimum=14),
            )
            xalign 0.5
            yalign 0.5


screen _rf_editor_panel_restore_tab(panel_name, side, rect_key, control_id):
    $ _rf_panel_rect = _renforge_editor_live_layout_metrics().get(rect_key)
    if _rf_panel_rect is not None:
        $ _rf_tab_w = _renforge_editor_ui_px(34, minimum=26)
        $ _rf_tab_h = _renforge_editor_ui_px(56, minimum=42)
        $ _rf_tab_inset = _renforge_editor_ui_px(4, minimum=2)
        $ _rf_tab_x = (
            int(_rf_panel_rect[0]) + _rf_tab_inset
            if side == "left"
            else int(_rf_panel_rect[0] + _rf_panel_rect[2]) - _rf_tab_w - _rf_tab_inset
        )
        $ _rf_show_icon = _RF_EDITOR_PANEL_ICONS[panel_name]
        button:
            id control_id
            action Function(_renforge_editor_consume, _renforge_editor_toggle_panel, panel_name)
            tooltip _renforge_editor_panel_tooltip(panel_name, "show")
            xpos _rf_tab_x
            ypos int(_rf_panel_rect[1]) + _renforge_editor_ui_px(10, minimum=6)
            xsize _rf_tab_w
            ysize _rf_tab_h
            background Solid(_renforge_editor_ui_color("panel_head"))
            hover_background Solid(_renforge_editor_ui_color("row_hover"))
            padding (0, 0)
            fixed:
                add Solid(
                    _renforge_editor_ui_color("accent_bright"),
                    xysize=(_renforge_editor_ui_px(2, minimum=1), _rf_tab_h),
                ):
                    xalign (1.0 if side == "left" else 0.0)
                add _renforge_editor_ui_icon(_rf_show_icon):
                    xysize (
                        _renforge_editor_ui_px(20, minimum=15),
                        _renforge_editor_ui_px(20, minimum=15),
                    )
                    xalign 0.5
                    yalign 0.5


# ── Lot 1.C — inspector ─────────────────────────────────────────────────────
# Identity, source location and geometry for the current selection, plus the
# refusal when there is one. Anchored to the right edge rather than to a fixed
# x, so it stays on screen whatever width the game runs at.
# Fields are read-only displays (maquette language); free-text edit is deferred.
screen _rf_editor_inspector():
    if _renforge_editor_panel_visible("inspector"):
        use _rf_editor_inspector_panel()
    else:
        use _rf_editor_panel_restore_tab("inspector", "right", "inspector_rect", "rf_inspector_show")


screen _rf_editor_inspector_panel():

    $ _rf_facts = _renforge_editor_inspector_facts()
    $ _rf_props = (
        _renforge_editor_effective_properties(_rf_facts["screen"], _rf_facts["id"])
        if _rf_facts is not None else {}
    )
    $ _rf_docked = _renforge_editor_chrome_docked()
    $ _rf_xa = _rf_props.get("xanchor", "—") if _rf_facts is not None else "—"
    $ _rf_ya = _rf_props.get("yanchor", "—") if _rf_facts is not None else "—"
    $ _rf_inspector_rect = _renforge_editor_live_layout_metrics().get("inspector_rect")
    $ _rf_inspector_panel_w = int(_rf_inspector_rect[2]) if _rf_inspector_rect is not None else _renforge_editor_ui_px(_RF_OVERLAY_PANEL_W)
    $ _rf_inspector_identity_w = max(1, _rf_inspector_panel_w - _renforge_editor_ui_px(104, minimum=52))
    $ _rf_inspector_text_w = max(1, _rf_inspector_identity_w - _renforge_editor_ui_px(_RF_TREE_BADGE + 12))
    $ _rf_inspector_header_name = _renforge_editor_panel_text(_rf_facts["id"], 22) if _rf_facts is not None else ""
    $ _rf_inspector_header_path = _renforge_editor_panel_text(
        (("screen " + _rf_facts["screen"] if _rf_facts["screen"] else "") + ("  ·  " + _rf_facts["source"] if _rf_facts["source"] else "")),
        34,
    ) if _rf_facts is not None else ""

    frame:
        id "rf_inspector_panel"
        if _rf_docked:
            xalign 1.0
            xoffset 0
            ypos _renforge_editor_ui_px(_RF_DOCK_INSPECTOR_Y)
            xsize _renforge_editor_ui_px(_RF_DOCK_RAIL_W)
            ysize _renforge_editor_ui_px(_RF_DOCK_INSPECTOR_H)
            background Solid(_renforge_editor_ui_color("panel"))
        else:
            xpos _renforge_editor_ui_px(_RF_OVERLAY_INSPECTOR_X)
            ypos _renforge_editor_ui_px(_RF_OVERLAY_INSPECTOR_Y)
            xsize _renforge_editor_ui_px(_RF_OVERLAY_PANEL_W)
            ysize _renforge_editor_ui_px(_RF_OVERLAY_INSPECTOR_H)
            background Frame(_renforge_editor_ui_frame("panel"), _RF_FRAME_PANEL, _RF_FRAME_PANEL)
        padding (0, 0)

        vbox:
            xfill True
            spacing 0

            frame:
                xfill True
                background Frame(_renforge_editor_ui_frame("panel_head"), _RF_FRAME_PANEL, _RF_FRAME_PANEL, _RF_FRAME_PANEL, 2)
                padding (_renforge_editor_ui_px(20), _renforge_editor_ui_px(12))
                fixed:
                    xfill True
                    ysize _renforge_editor_ui_px(56, minimum=28)
                    if _rf_facts is not None:
                        hbox:
                            id "rf_inspector_identity"
                            spacing _renforge_editor_ui_px(12)
                            xmaximum _rf_inspector_identity_w
                            yalign 0.5
                            frame:
                                xsize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                ysize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                background Solid(_renforge_editor_ui_color("sunken"))
                                text "·":
                                    color _renforge_editor_ui_color("accent_bright")
                                    font _renforge_editor_ui_font()
                                    size _renforge_editor_ui_px(18)
                                    xalign 0.5
                                    yalign 0.5
                            vbox:
                                spacing _renforge_editor_ui_px(4)
                                xmaximum _rf_inspector_text_w
                                text _rf_inspector_header_name:
                                    id "rf_inspector_name"
                                    substitute False
                                    color _renforge_editor_ui_color("surface")
                                    font _renforge_editor_ui_font()
                                    size _renforge_editor_ui_px(24)
                                    xmaximum _rf_inspector_text_w
                                text _rf_inspector_header_path:
                                    id "rf_inspector_path"
                                    substitute False
                                    color _renforge_editor_ui_color("meta")
                                    font _renforge_editor_ui_font()
                                    size _renforge_editor_ui_px(17)
                                    xmaximum _rf_inspector_text_w
                    else:
                        text _renforge_editor_t("inspector.none"):
                            id "rf_inspector_name"
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(18)
                            xmaximum _rf_inspector_identity_w
                            yalign 0.5
                    hbox:
                        id "rf_inspector_panel_action"
                        xalign 1.0
                        yalign 0.5
                        use _rf_editor_panel_hide_button("inspector", "right", "rf_inspector_hide")

            if _rf_facts is not None:
                vbox:
                    xfill True
                    spacing _renforge_editor_ui_px(10)
                    xoffset _renforge_editor_ui_px(20)
                    yoffset _renforge_editor_ui_px(14)

                    if _rf_facts["rect"] is not None:
                        text _renforge_editor_t("inspector.position"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                        hbox:
                            spacing _renforge_editor_ui_px(12)
                            use _rf_editor_field("xpos", str(_rf_facts["rect"]["x"]))
                            use _rf_editor_field("ypos", str(_rf_facts["rect"]["y"]))

                        text _renforge_editor_t("inspector.offset"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(8)
                        hbox:
                            spacing _renforge_editor_ui_px(12)
                            use _rf_editor_field("xoffset", str(_rf_props.get("xoffset", "—")))
                            use _rf_editor_field("yoffset", str(_rf_props.get("yoffset", "—")))

                        text _renforge_editor_t("inspector.anchor"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(8)
                        hbox:
                            id "rf_inspector_anchor_grid"
                            spacing _renforge_editor_ui_px(16)
                            yalign 0.5
                            # Display-only 3×3; free-text capture is deferred (plan 1.C).
                            vbox:
                                spacing _renforge_editor_ui_px(4)
                                for _rf_row in (0, 1, 2):
                                    hbox:
                                        spacing _renforge_editor_ui_px(4)
                                        for _rf_col in (0, 1, 2):
                                            $ _rf_on = _renforge_editor_anchor_cell_on(_rf_xa, _rf_ya, _rf_col, _rf_row)
                                            frame:
                                                xsize _renforge_editor_ui_px(_RF_ANCHOR_CELL)
                                                ysize _renforge_editor_ui_px(_RF_ANCHOR_CELL)
                                                background Solid(_renforge_editor_ui_color("accent" if _rf_on else "sunken"))
                                                add Solid(
                                                    _renforge_editor_ui_color("accent_bright" if _rf_on else "meta"),
                                                    xysize=(_renforge_editor_ui_px(7), _renforge_editor_ui_px(7)),
                                                ):
                                                    xalign 0.5
                                                    yalign 0.5
                            vbox:
                                spacing _renforge_editor_ui_px(8)
                                use _rf_editor_field("xanchor", str(_rf_xa))
                                use _rf_editor_field("yanchor", str(_rf_ya))

                        text _renforge_editor_t("inspector.size"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(8)
                        hbox:
                            spacing _renforge_editor_ui_px(12)
                            use _rf_editor_field("xsize", str(_rf_facts["rect"]["w"]))
                            use _rf_editor_field("ysize", str(_rf_facts["rect"]["h"]))

                        text _renforge_editor_t("inspector.fill"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(8)
                        hbox:
                            spacing _renforge_editor_ui_px(12)
                            use _rf_editor_field("xfill", str(_rf_props.get("xfill", "—")))
                            use _rf_editor_field("yfill", str(_rf_props.get("yfill", "—")))
                    else:
                        text _renforge_editor_t("inspector.no_geometry"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(17)

                    # Ownership chain for style_gui_dialogue position mode
                    $ _rf_status = _renforge_editor_task0_status()
                    $ _rf_position_mode = _rf_status.get("position_mode") if _rf_status else None
                    if _rf_position_mode == "style_gui_dialogue":
                        text _renforge_editor_t("inspector.ownership_chain"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(12)
                        text _renforge_editor_t("inspector.ownership_style_position"):
                            color _renforge_editor_ui_color("surface")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(15)
                            yoffset _renforge_editor_ui_px(4)
                        text _renforge_editor_t("inspector.global_scope_notice"):
                            color _renforge_editor_ui_color("accent")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(14)
                            yoffset _renforge_editor_ui_px(8)

                    text _renforge_editor_t("inspector.read_only"):
                        id "rf_inspector_read_only"
                        color _renforge_editor_ui_color("meta")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(15)
                        yoffset _renforge_editor_ui_px(6)

                null height _renforge_editor_ui_px(28)


# A labelled read-only value in a sunken field chip (maquette field language).
screen _rf_editor_field(key, value):
    frame:
        background Solid(_renforge_editor_ui_color("sunken"))
        padding (_renforge_editor_ui_px(12), _renforge_editor_ui_px(8))
        hbox:
            spacing _renforge_editor_ui_px(10)
            text key:
                color _renforge_editor_ui_color("meta")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_px(16)
                yalign 0.5
            text value:
                substitute False
                color _renforge_editor_ui_color("surface")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_px(18)
                yalign 0.5
