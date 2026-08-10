# ── Lot 1.A — live editor toolbar ──────────────────────────────────────────
# Floating bar in overlay (maquette 32/28/2496×96, r-lg) or full-width rail in
# docked edit (height 124, square). Spacing follows the maquette scale
# (s2/s3/s4/s6). Status lives on the HUD band, not here.

screen _rf_vrule():
    add Solid(_renforge_editor_ui_color("hairline"), xysize=(1, _renforge_editor_ui_px(_RF_VRULE_H))):
        yalign 0.5


screen _rf_icon_btn(btn_id, icon_name, btn_action, tooltip_text, active=False, enabled=True, dim=False, size_key="tool"):
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
        # Keyboard focus reuses the hover style in Ren'Py, so accent hover is
        # also the focus ring (there is no separate focus background property).
        background (
            Frame(_renforge_editor_ui_frame("brand"), _RF_FRAME_BRAND, _RF_FRAME_BRAND)
            if active
            else Solid("#00000000")
        )
        hover_background Solid(_renforge_editor_ui_color("accent") + "55")
        padding (0, 0)
        yalign 0.5
        add _renforge_editor_ui_icon(icon_name):
            xysize (_rf_glyph, _rf_glyph)
            xalign 0.5
            yalign 0.5
            at Transform(alpha=(0.38 if dim or not enabled else 1.0))


screen _rf_seg_btn(btn_id, label, btn_action, pressed=False):
    $ _rf_seg_background = Frame(_renforge_editor_ui_frame("seg_on"), _RF_FRAME_CHIP, _RF_FRAME_CHIP)
    textbutton label:
        id btn_id
        action btn_action
        ysize _renforge_editor_ui_px(_RF_SEG_H)
        background (_rf_seg_background if pressed else Solid("#00000000"))
        hover_background _rf_seg_background
        padding (_renforge_editor_ui_px(_RF_S3), 0)
        text_color _renforge_editor_ui_color("surface" if pressed else "meta")
        text_font _renforge_editor_ui_font()
        text_size _renforge_editor_ui_text_px(_RF_T_XS)
        yalign 0.5


screen _rf_save_btn():
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
                size _renforge_editor_ui_text_px(_RF_T_XS, minimum=14)
                yalign 0.5


screen _rf_editor_preview_toolbar():
    $ _rf_metrics = _renforge_editor_live_layout_metrics()
    $ _rf_toolbar_rect = _rf_metrics["toolbar_rect"]
    frame:
        id "rf_toolbar"
        xpos _rf_toolbar_rect[0]
        ypos _rf_toolbar_rect[1]
        xsize _rf_toolbar_rect[2]
        ysize _rf_toolbar_rect[3]
        background Solid(_renforge_editor_ui_color("panel"))
        padding (_renforge_editor_ui_px(_RF_S4), 0)
        hbox:
            spacing _renforge_editor_ui_px(_RF_S3)
            xfill True
            yalign 0.5
            null width 1 xfill True
            use _rf_seg_btn("rf_toolbar_view_edit", _renforge_editor_t("toolbar.edit"), Function(_renforge_editor_consume, _renforge_editor_set_view_mode, "edit"), pressed=False)
            use _rf_save_btn()
            use _rf_icon_btn("rf_exit", "exit", Function(_renforge_editor_consume, _renforge_editor_exit), _renforge_editor_t("toolbar.exit"), enabled=(not _renforge_editor_state().save_in_progress), size_key="action")


screen _rf_editor_toolbar(tools_visible):
    if _renforge_editor_view_mode() == "preview":
        use _rf_editor_preview_toolbar()
    else:
        use _rf_editor_edit_toolbar(tools_visible)
        if _renforge_editor_jump_open():
            use _rf_toolbar_jump_menu()
    use _rf_toolbar_tooltip()


screen _rf_toolbar_jump_menu():
    $ _rf_jump_anchor = _renforge_editor_control_rect("rf_toolbar_jump")
    $ _rf_jump_bar = _renforge_editor_live_layout_metrics()["toolbar_rect"]
    $ _rf_jump_x = int(_rf_jump_anchor[0]) if _rf_jump_anchor else int(_rf_jump_bar[0])
    $ _rf_jump_y = (int(_rf_jump_anchor[1]) + int(_rf_jump_anchor[3])) if _rf_jump_anchor else (int(_rf_jump_bar[1]) + int(_rf_jump_bar[3]))
    $ _rf_jump_labels = _renforge_editor_jump_targets()

    # A click anywhere else closes the menu. The catcher only exists while the
    # menu is open, so it never steals a click from the game underneath.
    button:
        id "rf_toolbar_jump_dismiss"
        action Function(_renforge_editor_consume, _renforge_editor_close_jump)
        xfill True
        yfill True
        background Solid("#00000000")

    frame:
        id "rf_toolbar_jump_menu"
        xpos _rf_jump_x
        ypos (_rf_jump_y + _renforge_editor_ui_px(_RF_S2))
        xsize _renforge_editor_ui_px(_RF_OVERLAY_PANEL_W)
        background Frame(_renforge_editor_ui_frame("panel"), _RF_FRAME_PANEL, _RF_FRAME_PANEL)
        padding (_renforge_editor_ui_px(_RF_S2), _renforge_editor_ui_px(_RF_S2))
        if _rf_jump_labels:
            viewport:
                id "rf_toolbar_jump_viewport"
                style_prefix "rf"
                ymaximum _renforge_editor_ui_px(720)
                scrollbars "vertical"
                mousewheel True
                vbox:
                    spacing _renforge_editor_ui_px(2)
                    xfill True
                    for _rf_jump_label in _rf_jump_labels:
                        textbutton _rf_jump_label:
                            # hide say/nvl/bubble before jump — see _renforge_editor_jump_to
                            action Function(_renforge_editor_consume, _renforge_editor_jump_to, _rf_jump_label)
                            xfill True
                            ysize _renforge_editor_ui_px(_RF_SEG_H)
                            background Solid("#00000000")
                            hover_background Solid(_renforge_editor_ui_color("row_hover"))
                            padding (_renforge_editor_ui_px(_RF_S3), 0)
                            text_color _renforge_editor_ui_color("surface")
                            text_font _renforge_editor_ui_font()
                            text_size _renforge_editor_ui_text_px(_RF_T_XS)
                            text_xalign 0.0
        else:
            text _renforge_editor_t("toolbar.jump_empty"):
                id "rf_toolbar_jump_empty"
                color _renforge_editor_ui_color("meta")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_text_px(_RF_T_XS)


screen _rf_toolbar_tooltip():
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


screen _rf_editor_edit_toolbar(tools_visible):
    $ _rf_facts = _renforge_editor_inspector_facts()
    $ _rf_screen = _rf_facts["screen"] if _rf_facts and _rf_facts.get("screen") else _renforge_editor_jump_context()
    $ _rf_tool_mode = _renforge_editor_tool_mode()
    $ _rf_view_mode = _renforge_editor_view_mode()
    $ _rf_layout_mode = _renforge_editor_layout_mode()
    $ _rf_docked = _renforge_editor_chrome_docked()
    $ _rf_metrics = _renforge_editor_live_layout_metrics()
    $ _rf_toolbar_rect = _rf_metrics["toolbar_rect"]
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
        xpos _rf_toolbar_rect[0]
        ypos _rf_toolbar_rect[1]
        xsize _rf_toolbar_rect[2]
        ysize _rf_toolbar_rect[3]
        if _rf_docked:
            background Solid(_renforge_editor_ui_color("panel"))
        else:
            background Frame(_renforge_editor_ui_frame("panel"), _RF_FRAME_PANEL, _RF_FRAME_PANEL)
        padding (_renforge_editor_ui_px(_RF_S4), 0)

        hbox:
            spacing _renforge_editor_ui_px(_RF_S2)
            yalign 0.5
            xfill True

            # ── Brand ──────────────────────────────────────────────────────
            if _rf_metrics["show_brand"]:
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
                            size _renforge_editor_ui_text_px(_RF_T_SM, minimum=14)
                            xalign 0.5
                            yalign 0.5
                    text _renforge_editor_t("toolbar.brand"):
                        id "rf_toolbar_brand"
                        color _renforge_editor_ui_color("surface")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_text_px(_RF_T_XS)
                        yalign 0.5

            if _rf_metrics["show_screen"]:
                button:
                    id "rf_toolbar_jump"
                    action Function(_renforge_editor_consume, _renforge_editor_toggle_jump)
                    sensitive (not _renforge_editor_state().save_in_progress)
                    tooltip _renforge_editor_t("toolbar.jump")
                    ysize _renforge_editor_ui_px(52)
                    xmaximum _renforge_editor_ui_px(_RF_BADGE_MAX_W)
                    xfill False
                    background Solid("#00000000")
                    hover_background Solid(_renforge_editor_ui_color("row_hover"))
                    padding (_renforge_editor_ui_px(_RF_S3), 0)
                    yalign 0.5
                    hbox:
                        spacing _renforge_editor_ui_px(_RF_S2)
                        yalign 0.5
                        text (_renforge_editor_t("toolbar.screen") + " " + _rf_screen):
                            id "rf_toolbar_screen_text"
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_text_px(_RF_T_XS)
                            xmaximum _renforge_editor_ui_px(_RF_BADGE_TEXT_MAX_W)
                            yalign 0.5
                        add _renforge_editor_ui_icon("caret"):
                            xysize (
                                _renforge_editor_ui_px(20, minimum=14),
                                _renforge_editor_ui_px(20, minimum=14),
                            )
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
                    if _rf_metrics["show_disabled_tools"]:
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
                textbutton _renforge_editor_t("toolbar.guides"):
                    id "rf_tools"
                    action Function(_renforge_editor_consume, _renforge_editor_toggle_tools)
                    tooltip _rf_t_tools
                    ysize _renforge_editor_ui_px(_RF_SEG_H)
                    background (Solid(_renforge_editor_ui_color("seg_on")) if tools_visible else Solid("#00000000"))
                    hover_background Solid(_renforge_editor_ui_color("row_hover"))
                    padding (_renforge_editor_ui_px(_RF_S3), 0)
                    text_color _renforge_editor_ui_color("surface" if tools_visible else "meta")
                    text_font _renforge_editor_ui_font()
                    text_size _renforge_editor_ui_text_px(_RF_T_XS)
                    yalign 0.5
                use _rf_icon_btn("rf_opacity_down", "minus", Function(_renforge_editor_consume, _renforge_editor_adjust_opacity, -0.1), _rf_t_opacity_down, size_key="action")
                text _renforge_editor_opacity_label():
                    id "rf_opacity_value"
                    color _renforge_editor_ui_color("surface")
                    font _renforge_editor_ui_font()
                    size _renforge_editor_ui_text_px(_RF_T_XS)
                    yalign 0.5
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
            use _rf_save_btn()

            use _rf_icon_btn("rf_exit", "exit", Function(_renforge_editor_consume, _renforge_editor_exit), _rf_t_exit, enabled=(not _renforge_editor_state().save_in_progress), size_key="action")

            if _rf_metrics["show_lock"] and _renforge_editor_selected_lock() is not None:
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
                        size _renforge_editor_ui_text_px(_RF_T_MICRO)
                        yalign 0.5
