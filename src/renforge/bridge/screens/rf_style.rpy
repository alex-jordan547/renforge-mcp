# ── Lot 1.D — Ren'Py style editor ───────────────────────────────────────────
# Panel anchored bottom-right. Only text + literal hex colour is editable; every
# other selection is locked with a reason (never silent, never a dead click).
screen _rf_editor_style():

    $ _rf_facts = _renforge_editor_inspector_facts()
    $ _rf_docked = _renforge_editor_chrome_docked()
    $ _rf_color_capable = _renforge_editor_style_color_capable() if _rf_facts is not None else False
    $ _rf_color_hex = _renforge_editor_style_color_value() if _rf_color_capable else ""

    frame:
        id "rf_style_panel"
        if _rf_docked:
            xalign 1.0
            xoffset 0
            ypos _renforge_editor_ui_px(_RF_DOCK_STYLE_Y)
            xsize _renforge_editor_ui_px(_RF_DOCK_RAIL_W)
            ysize _renforge_editor_ui_px(_RF_DOCK_STYLE_H)
            background Solid(_renforge_editor_ui_color("panel"))
        else:
            xpos _renforge_editor_ui_px(_RF_OVERLAY_STYLE_X)
            ypos _renforge_editor_ui_px(_RF_OVERLAY_STYLE_Y)
            xsize _renforge_editor_ui_px(_RF_OVERLAY_PANEL_W)
            ysize _renforge_editor_ui_px(_RF_OVERLAY_STYLE_H)
            background Frame(_renforge_editor_ui_frame("panel"), _RF_FRAME_PANEL, _RF_FRAME_PANEL)
        padding (0, 0)

        vbox:
            xfill True
            spacing 0

            frame:
                xfill True
                background Frame(_renforge_editor_ui_frame("panel_head"), _RF_FRAME_PANEL, _RF_FRAME_PANEL, _RF_FRAME_PANEL, 2)
                padding (_renforge_editor_ui_px(20), _renforge_editor_ui_px(12))
                hbox:
                    xfill True
                    yalign 0.5
                    text _renforge_editor_t("style.title"):
                        id "rf_style_title"
                        color _renforge_editor_ui_color("meta")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(18)
                        yalign 0.5
                    null width 1 xfill True
                    if _rf_facts is not None:
                        text _rf_facts["id"]:
                            id "rf_style_name"
                            color _renforge_editor_ui_color("surface")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(18)
                            yalign 0.5
                    else:
                        text _renforge_editor_t("style.none"):
                            id "rf_style_name"
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(18)
                            yalign 0.5

            vbox:
                xfill True
                spacing _renforge_editor_ui_px(12)
                xoffset _renforge_editor_ui_px(20)
                yoffset _renforge_editor_ui_px(14)

                if _rf_facts is None:
                    text _renforge_editor_t("style.none"):
                        color _renforge_editor_ui_color("meta")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(17)
                elif _rf_color_capable:
                    text _renforge_editor_t("style.color_label"):
                        color _renforge_editor_ui_color("meta")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(16)
                    hbox:
                        spacing _renforge_editor_ui_px(12)
                        yalign 0.5
                        add Solid(
                            (_rf_color_hex if str(_rf_color_hex).startswith("#") and len(str(_rf_color_hex)) >= 7 else _renforge_editor_ui_color("accent")),
                            xysize=(_renforge_editor_ui_px(36), _renforge_editor_ui_px(36)),
                        ):
                            id "rf_style_swatch"
                            yalign 0.5
                        frame:
                            background Solid(_renforge_editor_ui_color("sunken"))
                            padding (_renforge_editor_ui_px(12), _renforge_editor_ui_px(8))
                            text str(_rf_color_hex):
                                id "rf_style_color_value"
                                color _renforge_editor_ui_color("surface")
                                font _renforge_editor_ui_font()
                                size _renforge_editor_ui_px(18)
                    textbutton _renforge_editor_t("style.color"):
                        id "rf_style_cycle"
                        action Function(_renforge_editor_consume, _renforge_editor_cycle_style_color_preview)
                        sensitive not _renforge_editor_state().save_in_progress
                        background Solid(_renforge_editor_ui_color("accent"))
                        padding (_renforge_editor_ui_px(12), _renforge_editor_ui_px(6))
                        text_color _renforge_editor_ui_color("accent_on")
                        text_font _renforge_editor_ui_font()
                        text_size _renforge_editor_ui_px(16)
                else:
                    text _renforge_editor_t("style.locked"):
                        id "rf_style_color_value"
                        color _renforge_editor_ui_color("meta")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(17)

                if _rf_facts is not None and _rf_facts["lock"] is not None:
                    text (
                        _renforge_editor_t("lock.%s" % _rf_facts["lock"][0])
                        + " — " + (_rf_facts["lock"][2] or _rf_facts["lock"][1])
                    ):
                        id "rf_style_lock"
                        color _renforge_editor_lock_color()
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(16)
                        xmaximum _renforge_editor_ui_px(480)

            null height _renforge_editor_ui_px(28)
