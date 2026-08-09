# ── Lot 1.C — inspector ─────────────────────────────────────────────────────
# Identity, source location and geometry for the current selection, plus the
# refusal when there is one. Anchored to the right edge rather than to a fixed
# x, so it stays on screen whatever width the game runs at.
# Fields are read-only displays (maquette language); free-text edit is deferred.
screen _rf_editor_inspector():

    $ _rf_facts = _renforge_editor_inspector_facts()
    $ _rf_props = (
        _renforge_editor_effective_properties(_rf_facts["screen"], _rf_facts["id"])
        if _rf_facts is not None else {}
    )
    $ _rf_docked = _renforge_editor_chrome_docked()
    $ _rf_xa = _rf_props.get("xanchor", "—") if _rf_facts is not None else "—"
    $ _rf_ya = _rf_props.get("yanchor", "—") if _rf_facts is not None else "—"

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
                if _rf_facts is not None:
                    hbox:
                        spacing _renforge_editor_ui_px(12)
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
                            text _rf_facts["id"]:
                                id "rf_inspector_name"
                                substitute False
                                color _renforge_editor_ui_color("surface")
                                font _renforge_editor_ui_font()
                                size _renforge_editor_ui_px(24)
                            text (
                                ("screen " + _rf_facts["screen"] if _rf_facts["screen"] else "")
                                + ("  ·  " + _rf_facts["source"] if _rf_facts["source"] else "")
                            ):
                                id "rf_inspector_path"
                                substitute False
                                color _renforge_editor_ui_color("meta")
                                font _renforge_editor_ui_font()
                                size _renforge_editor_ui_px(17)
                else:
                    text _renforge_editor_t("inspector.none"):
                        id "rf_inspector_name"
                        color _renforge_editor_ui_color("meta")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(18)

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

                    text _renforge_editor_t("inspector.read_only"):
                        id "rf_inspector_read_only"
                        color _renforge_editor_ui_color("meta")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(15)
                        yoffset _renforge_editor_ui_px(6)

                    if _rf_facts["lock"] is not None:
                        text (
                            _renforge_editor_t("lock.%s" % _rf_facts["lock"][0])
                            + " — " + (_rf_facts["lock"][2] or _rf_facts["lock"][1])
                        ):
                            id "rf_inspector_lock"
                            substitute False
                            color _renforge_editor_lock_color()
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(10)
                            xmaximum _renforge_editor_ui_px(480)

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
