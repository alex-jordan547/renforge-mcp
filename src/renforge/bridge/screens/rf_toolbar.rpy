# ── Lot 1.A — live editor toolbar ──────────────────────────────────────────
# Floating bar in overlay (maquette 32/28/2496×96, r-lg) or full-width rail in
# docked edit (height 124, square). Spacing follows the maquette scale
# (s2/s3/s4/s6). Status lives on the HUD band, not here.

screen _rf_vrule():
    add Solid(_renforge_editor_ui_color("hairline"), xysize=(1, _renforge_editor_ui_px(_RF_VRULE_H))):
        yalign 0.5


screen _rf_icon_btn(btn_id, icon_name, btn_action, tooltip_text="", active=False, enabled=True, dim=False, size_key="tool"):
    $ _rf_btn = (
        _renforge_editor_ui_px(_RF_ICON_BTN, minimum=_RF_ICON_BTN_MIN)
        if size_key == "tool"
        else _renforge_editor_ui_px(_RF_ICON_ACTION, minimum=_RF_ICON_ACTION_MIN)
    )
    $ _rf_glyph = (
        _renforge_editor_ui_px(_RF_ICON_GLYPH, minimum=_RF_ICON_GLYPH_MIN)
        if size_key == "tool"
        else _renforge_editor_ui_px(_RF_ICON_GLYPH_SM, minimum=_RF_ICON_GLYPH_SM_MIN)
    )
    button:
        id btn_id
        action btn_action
        sensitive enabled
        tooltip tooltip_text
        xsize _rf_btn
        ysize _rf_btn
        background (
            Frame(_renforge_editor_ui_frame("brand"), _RF_FRAME_BRAND, _RF_FRAME_BRAND)
            if active
            else Solid("#00000000")
        )
        hover_background Solid(_renforge_editor_ui_color("row_hover"))
        focused_background Solid(_renforge_editor_ui_color("accent") + "55")
        selected_background Frame(_renforge_editor_ui_frame("brand"), _RF_FRAME_BRAND, _RF_FRAME_BRAND)
        padding (0, 0)
        yalign 0.5
        add _renforge_editor_ui_icon(icon_name):
            xysize (_rf_glyph, _rf_glyph)
            xalign 0.5
            yalign 0.5
            at Transform(alpha=(0.38 if dim or not enabled else 1.0))


screen _rf_seg_btn(btn_id, label, btn_action, pressed=False):
    textbutton label:
        id btn_id
        action btn_action
        ysize _renforge_editor_ui_px(_RF_SEG_H)
        background (Solid(_renforge_editor_ui_color("seg_on")) if pressed else Solid("#00000000"))
        padding (_renforge_editor_ui_px(_RF_S3), 0)
        text_color _renforge_editor_ui_color("surface" if pressed else "meta")
        text_font _renforge_editor_ui_font()
        text_size _renforge_editor_ui_px(_RF_T_XS)
        yalign 0.5


screen _rf_editor_toolbar(tools_visible):
    $ _rf_facts = _renforge_editor_inspector_facts()
    $ _rf_screen = _rf_facts["screen"] if _rf_facts and _rf_facts.get("screen") else (getattr(_renforge_editor_state(), "selected_screen", "") or "")
    $ _rf_tool_mode = _renforge_editor_tool_mode()
    $ _rf_view_mode = _renforge_editor_view_mode()
    $ _rf_layout_mode = _renforge_editor_layout_mode()
    $ _rf_docked = _renforge_editor_chrome_docked()
    # Catalogue keys kept live for i18n contract.
    $ _rf_t_select = _renforge_editor_t("toolbar.select")
    $ _rf_t_move = _renforge_editor_t("toolbar.move")
    $ _rf_t_measure = _renforge_editor_t("toolbar.measure")
    $ _rf_t_picker = _renforge_editor_t("toolbar.picker")
    $ _rf_t_text = _renforge_editor_t("toolbar.text")
    $ _rf_t_hand = _renforge_editor_t("toolbar.hand")
    $ _rf_t_exit = _renforge_editor_t("toolbar.exit")
    $ _rf_t_undo = _renforge_editor_t("toolbar.undo")
    $ _rf_t_redo = _renforge_editor_t("toolbar.redo")
    $ _rf_t_reset = _renforge_editor_t("toolbar.reset")
    $ _rf_t_tools = _renforge_editor_t("toolbar.tools_on" if tools_visible else "toolbar.tools_off")
    $ _rf_t_opacity_down = _renforge_editor_t("toolbar.opacity_down")
    $ _rf_t_opacity_up = _renforge_editor_t("toolbar.opacity_up")

    frame:
        id "rf_toolbar"
        if _rf_docked:
            xalign 0.0
            ypos 0
            xsize config.screen_width
            ysize _renforge_editor_ui_px(_RF_DOCK_TOOLBAR_H)
            background Solid(_renforge_editor_ui_color("panel"))
        else:
            xpos _renforge_editor_ui_px(_RF_OVERLAY_INSET)
            ypos _renforge_editor_ui_px(_RF_OVERLAY_TOOLBAR_Y)
            xsize _renforge_editor_ui_px(_RF_OVERLAY_TOOLBAR_W)
            ysize _renforge_editor_ui_px(_RF_OVERLAY_TOOLBAR_H)
            background Frame(_renforge_editor_ui_frame("panel"), _RF_FRAME_PANEL, _RF_FRAME_PANEL)
        padding (_renforge_editor_ui_px(_RF_S4), 0)

        hbox:
            spacing _renforge_editor_ui_px(_RF_S4)
            yalign 0.5
            xfill True

            # ── Brand ──────────────────────────────────────────────────────
            hbox:
                spacing _renforge_editor_ui_px(_RF_S3)
                yalign 0.5
                xoffset _renforge_editor_ui_px(_RF_S2)
                frame:
                    id "rf_toolbar_brand_icon"
                    xsize _renforge_editor_ui_px(_RF_BRAND_MARK)
                    ysize _renforge_editor_ui_px(_RF_BRAND_MARK)
                    background Frame(_renforge_editor_ui_frame("brand"), _RF_FRAME_BRAND, _RF_FRAME_BRAND)
                    padding (0, 0)
                    yalign 0.5
                    text "R":
                        id "rf_toolbar_brand_r"
                        color _renforge_editor_ui_color("accent_on")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(_RF_T_SM)
                        xalign 0.5
                        yalign 0.5
                text _renforge_editor_t("toolbar.brand"):
                    id "rf_toolbar_brand"
                    color _renforge_editor_ui_color("surface")
                    font _renforge_editor_ui_font()
                    size _renforge_editor_ui_px(_RF_T_XS)
                    yalign 0.5

            if _rf_screen:
                frame:
                    id "rf_toolbar_screen_badge"
                    ysize _renforge_editor_ui_px(52)
                    xmaximum _renforge_editor_ui_px(_RF_BADGE_MAX_W)
                    xfill False
                    background Frame(_renforge_editor_ui_frame("chip"), _RF_FRAME_CHIP, _RF_FRAME_CHIP)
                    padding (_renforge_editor_ui_px(_RF_S3), 0)
                    yalign 0.5
                    text (_renforge_editor_t("toolbar.screen") + " " + _rf_screen):
                        id "rf_toolbar_screen_text"
                        color _renforge_editor_ui_color("meta")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(_RF_T_XS)
                        xmaximum _renforge_editor_ui_px(_RF_BADGE_TEXT_MAX_W)
                        yalign 0.5

            use _rf_vrule()

            # ── Tools well ─────────────────────────────────────────────────
            frame:
                id "rf_toolbar_tool_modes"
                background Frame(_renforge_editor_ui_frame("tools"), _RF_FRAME_TOOLS, _RF_FRAME_TOOLS)
                padding (_renforge_editor_ui_px(6), _renforge_editor_ui_px(6))
                yalign 0.5
                hbox:
                    spacing _renforge_editor_ui_px(6)
                    yalign 0.5
                    use _rf_icon_btn("rf_toolbar_tool_select", "select", Function(_renforge_editor_consume, _renforge_editor_set_tool_mode, "select"), _rf_t_select, active=(_rf_tool_mode == "select"))
                    use _rf_icon_btn("rf_toolbar_tool_move", "move", Function(_renforge_editor_consume, _renforge_editor_set_tool_mode, "move"), _rf_t_move, active=(_rf_tool_mode == "move"))
                    use _rf_icon_btn("rf_toolbar_tool_measure", "measure", Function(_renforge_editor_consume, _renforge_editor_set_tool_mode, "measure"), _rf_t_measure, active=(_rf_tool_mode == "measure"))
                    use _rf_icon_btn("rf_toolbar_tool_picker", "picker", NullAction(), _rf_t_picker, enabled=False, dim=True)
                    use _rf_icon_btn("rf_toolbar_tool_text", "text", NullAction(), _rf_t_text, enabled=False, dim=True)
                    use _rf_icon_btn("rf_toolbar_tool_hand", "hand", NullAction(), _rf_t_hand, enabled=False, dim=True)

            null width 1 xfill True

            # ── Session actions ────────────────────────────────────────────
            hbox:
                spacing _renforge_editor_ui_px(4)
                yalign 0.5
                use _rf_icon_btn("rf_undo", "undo", Function(_renforge_editor_consume, _renforge_editor_undo), _rf_t_undo, enabled=_renforge_editor_can_undo(), size_key="action")
                use _rf_icon_btn("rf_redo", "redo", Function(_renforge_editor_consume, _renforge_editor_redo), _rf_t_redo, enabled=_renforge_editor_can_redo(), size_key="action")
                use _rf_icon_btn("rf_reset", "reset", Function(_renforge_editor_consume, _renforge_editor_reset_selected), _rf_t_reset, enabled=_renforge_editor_can_reset(), size_key="action")
                use _rf_icon_btn("rf_tools", "eye", Function(_renforge_editor_consume, _renforge_editor_toggle_tools), _rf_t_tools, active=tools_visible, size_key="action")
                use _rf_icon_btn("rf_opacity_down", "minus", Function(_renforge_editor_consume, _renforge_editor_adjust_opacity, -0.1), _rf_t_opacity_down, size_key="action")
                use _rf_icon_btn("rf_opacity_up", "plus", Function(_renforge_editor_consume, _renforge_editor_adjust_opacity, 0.1), _rf_t_opacity_up, size_key="action")

            use _rf_vrule()

            # ── Layout segment ─────────────────────────────────────────────
            frame:
                id "rf_toolbar_layout_modes"
                background Frame(_renforge_editor_ui_frame("chip"), _RF_FRAME_CHIP, _RF_FRAME_CHIP)
                padding (_renforge_editor_ui_px(5), _renforge_editor_ui_px(5))
                yalign 0.5
                hbox:
                    spacing _renforge_editor_ui_px(4)
                    yalign 0.5
                    use _rf_seg_btn("rf_toolbar_layout_overlay", _renforge_editor_t("toolbar.overlay"), Function(_renforge_editor_consume, _renforge_editor_set_layout_mode, "overlay"), pressed=(_rf_layout_mode == "overlay"))
                    use _rf_seg_btn("rf_toolbar_layout_docked", _renforge_editor_t("toolbar.docked"), Function(_renforge_editor_consume, _renforge_editor_set_layout_mode, "docked"), pressed=(_rf_layout_mode == "docked"))

            # ── View segment ───────────────────────────────────────────────
            frame:
                id "rf_toolbar_view_modes"
                background Frame(_renforge_editor_ui_frame("chip"), _RF_FRAME_CHIP, _RF_FRAME_CHIP)
                padding (_renforge_editor_ui_px(5), _renforge_editor_ui_px(5))
                yalign 0.5
                hbox:
                    spacing _renforge_editor_ui_px(4)
                    yalign 0.5
                    use _rf_seg_btn("rf_toolbar_view_preview", _renforge_editor_t("toolbar.preview"), Function(_renforge_editor_consume, _renforge_editor_set_view_mode, "preview"), pressed=(_rf_view_mode == "preview"))
                    use _rf_seg_btn("rf_toolbar_view_edit", _renforge_editor_t("toolbar.edit"), Function(_renforge_editor_consume, _renforge_editor_set_view_mode, "edit"), pressed=(_rf_view_mode == "edit"))

            use _rf_vrule()

            # ── Primary CTA ────────────────────────────────────────────────
            button:
                id "rf_save"
                action Function(_renforge_editor_consume, _renforge_editor_save)
                sensitive _renforge_editor_save_enabled()
                ysize _renforge_editor_ui_px(_RF_SAVE_H)
                background Frame(_renforge_editor_ui_frame("pill_accent"), _RF_FRAME_PILL, _RF_FRAME_PILL)
                hover_background Frame(_renforge_editor_ui_frame("pill_accent"), _RF_FRAME_PILL, _RF_FRAME_PILL)
                insensitive_background Frame(_renforge_editor_ui_frame("chip"), _RF_FRAME_CHIP, _RF_FRAME_CHIP)
                padding (_renforge_editor_ui_px(_RF_S6), 0)
                yalign 0.5
                hbox:
                    spacing _renforge_editor_ui_px(_RF_S2)
                    yalign 0.5
                    add _renforge_editor_ui_icon("save"):
                        xysize (
                            _renforge_editor_ui_px(22, minimum=18),
                            _renforge_editor_ui_px(22, minimum=18),
                        )
                        yalign 0.5
                    text _renforge_editor_save_label():
                        id "rf_save_text"
                        color _renforge_editor_ui_color("accent_on")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(_RF_T_XS, minimum=14)
                        yalign 0.5

            use _rf_icon_btn("rf_exit", "exit", Function(_renforge_editor_consume, _renforge_editor_exit), _rf_t_exit, enabled=(not _renforge_editor_state().save_in_progress), size_key="action")

            if _renforge_editor_selected_lock() is not None:
                hbox:
                    spacing _renforge_editor_ui_px(_RF_S2)
                    yalign 0.5
                    add _renforge_editor_ui_icon("lock"):
                        xysize (
                            _renforge_editor_ui_px(22, minimum=18),
                            _renforge_editor_ui_px(22, minimum=18),
                        )
                        yalign 0.5
                        at Transform(alpha=0.9)
                    text _renforge_editor_lock_headline():
                        id "rf_lock"
                        color _renforge_editor_lock_color()
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(_RF_T_MICRO)
                        yalign 0.5


    $ _rf_tip = GetTooltip()
    if _rf_tip:
        frame:
            id "rf_toolbar_tooltip"
            background Solid(_renforge_editor_ui_color("panel"))
            padding (_renforge_editor_ui_px(12), _renforge_editor_ui_px(8))
            xmaximum _renforge_editor_ui_px(480)
            text _rf_tip:
                substitute False
                color _renforge_editor_ui_color("surface")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_text_px(_RF_T_MICRO)
