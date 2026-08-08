# ── Lot 1.B — scene tree ────────────────────────────────────────────────────
# Reads the live displayable tree every frame rather than caching it: the game
# is running and its screens change under the editor, so a cached tree would
# quietly describe a scene that no longer exists.
screen _rf_editor_tree():
    $ _rf_rows = _renforge_editor_tree_rows()
    $ _rf_docked = _renforge_editor_chrome_docked()

    frame:
        id "rf_tree_panel"
        xpos (0 if _rf_docked else _renforge_editor_ui_px(_RF_OVERLAY_TREE_X))
        ypos _renforge_editor_ui_px(_RF_DOCK_TREE_Y if _rf_docked else _RF_OVERLAY_TREE_Y)
        xsize _renforge_editor_ui_px(_RF_DOCK_RAIL_W if _rf_docked else _RF_OVERLAY_TREE_W)
        # Fill the left rail to the bottom when docked to prevent layout voids.
        ysize (
            (config.screen_height - _renforge_editor_ui_px(_RF_DOCK_TREE_Y))
            if _rf_docked
            else _renforge_editor_ui_px(_RF_OVERLAY_TREE_H)
        )
        background (Solid(_renforge_editor_ui_color("panel")) if _rf_docked else Frame(_renforge_editor_ui_frame("panel"), _RF_FRAME_PANEL, _RF_FRAME_PANEL))
        padding (0, 0)

        vbox:
            xfill True
            yfill True
            spacing 0

            frame:
                xfill True
                ysize _renforge_editor_ui_px(76)
                background Frame(_renforge_editor_ui_frame("panel_head"), _RF_FRAME_PANEL, _RF_FRAME_PANEL, _RF_FRAME_PANEL, 2)
                padding (_renforge_editor_ui_px(20), _renforge_editor_ui_px(10))
                hbox:
                    xfill True
                    yalign 0.5
                    text _renforge_editor_t("tree.title"):
                        id "rf_tree_title"
                        color _renforge_editor_ui_color("surface")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(18)
                        yalign 0.5
                    null width 1 xfill True
                    frame:
                        background Solid(_renforge_editor_ui_color("accent_bright") + "26")
                        padding (_renforge_editor_ui_px(10), _renforge_editor_ui_px(3))
                        yalign 0.5
                        text ("%d items" % len(_rf_rows)):
                            id "rf_tree_count"
                            color _renforge_editor_ui_color("accent_bright")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(15)
                            yalign 0.5

            # Filter/Search bar
            frame:
                id "rf_tree_filter"
                xfill True
                ysize _renforge_editor_ui_px(50)
                background Solid(_renforge_editor_ui_color("sunken"))
                padding (_renforge_editor_ui_px(16), _renforge_editor_ui_px(10))
                hbox:
                    spacing _renforge_editor_ui_px(10)
                    yalign 0.5
                    add _renforge_editor_ui_icon("search"):
                        xysize (
                            _renforge_editor_ui_px(22, minimum=18),
                            _renforge_editor_ui_px(22, minimum=18),
                        )
                        yalign 0.5
                        at Transform(alpha=0.65)
                    text _renforge_editor_t("tree.filter"):
                        id "rf_tree_filter_text"
                        color _renforge_editor_ui_color("meta")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(16)
                        yalign 0.5

            viewport:
                id "rf_tree_viewport"
                xfill True
                yfill True
                mousewheel True
                draggable True
                vbox:
                    xfill True
                    spacing _renforge_editor_ui_px(3)
                    $ _rf_current_screen = ""
                    for row in _rf_rows:
                        if row["depth"] == 0:
                            $ _rf_current_screen = row["id"]
                            # Premium Screen header card
                            frame:
                                xfill True
                                padding (_renforge_editor_ui_px(12), _renforge_editor_ui_px(8))
                                background Frame(_renforge_editor_ui_frame("panel_head"), _RF_FRAME_PANEL, _RF_FRAME_PANEL)
                                top_margin _renforge_editor_ui_px(4)
                                bottom_margin _renforge_editor_ui_px(2)
                                hbox:
                                    spacing _renforge_editor_ui_px(10)
                                    yalign 0.5
                                    frame:
                                        xsize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                        ysize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                        background Solid(_renforge_editor_ui_color("tree_screen") + "33")
                                        yalign 0.5
                                        text "S":
                                            color _renforge_editor_ui_color("tree_screen")
                                            font _renforge_editor_ui_font()
                                            size _renforge_editor_ui_px(17)
                                            xalign 0.5
                                            yalign 0.5
                                    text str(row["id"]):
                                        substitute False
                                        color _renforge_editor_ui_color("surface")
                                        font _renforge_editor_ui_font()
                                        size _renforge_editor_ui_px(18)
                                        yalign 0.5
                                    frame:
                                        background Solid("#0000004d")
                                        padding (_renforge_editor_ui_px(6), _renforge_editor_ui_px(2))
                                        yalign 0.5
                                        text "SCREEN":
                                            color _renforge_editor_ui_color("tree_screen")
                                            font _renforge_editor_ui_font()
                                            size _renforge_editor_ui_px(13)
                                            yalign 0.5

                        elif row["id"]:
                            # Interactive Widget item (selectable)
                            button:
                                id ("rf_tree_item_" + str(_rf_current_screen) + "_" + str(row["id"]))
                                action Function(_renforge_editor_consume, _renforge_editor_select_widget, _rf_current_screen, row["id"])
                                background (Solid(_renforge_editor_ui_color("accent") + "33") if row["selected"] else None)
                                hover_background Solid(_renforge_editor_ui_color("row_hover"))
                                xfill True
                                padding (_renforge_editor_ui_px(4), _renforge_editor_ui_px(4))
                                hbox:
                                    xalign 0.0
                                    spacing _renforge_editor_ui_px(8)
                                    yalign 0.5
                                    if row["selected"]:
                                        add Solid(_renforge_editor_ui_color("accent_bright"), xysize=(_renforge_editor_ui_px(4), _renforge_editor_ui_px(30))):
                                            yalign 0.5
                                    else:
                                        null width _renforge_editor_ui_px(4)
                                    null width _renforge_editor_tree_indent(row["depth"])
                                    add Solid(_renforge_editor_ui_color("tree_guide"), xysize=(_renforge_editor_ui_px(2), _renforge_editor_ui_px(24))):
                                        yalign 0.5
                                    frame:
                                        xsize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                        ysize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                        background Solid(row.get("badge_color", _renforge_editor_ui_color("meta")) + "2e")
                                        yalign 0.5
                                        text row["tag"]:
                                            color (row.get("badge_color", _renforge_editor_ui_color("meta")))
                                            font _renforge_editor_ui_font()
                                            size _renforge_editor_ui_px(16)
                                            xalign 0.5
                                            yalign 0.5
                                    text row["label"]:
                                        substitute False
                                        color (_renforge_editor_ui_color("surface") if row["selected"] else _renforge_editor_ui_color("border"))
                                        font _renforge_editor_ui_font()
                                        size _renforge_editor_ui_px(18)
                                        yalign 0.5
                                    frame:
                                        background Solid("#0000004d")
                                        padding (_renforge_editor_ui_px(6), _renforge_editor_ui_px(2))
                                        yalign 0.5
                                        text ("#" + str(row["id"])):
                                            substitute False
                                            color _renforge_editor_ui_color("accent_bright")
                                            font _renforge_editor_ui_font()
                                            size _renforge_editor_ui_px(14)
                                            yalign 0.5
                                    if row.get("snippet"):
                                        # Pre-escaped in _renforge_editor_tree_escape; never substitute.
                                        $ _rf_snip = row["snippet"]
                                        text _rf_snip:
                                            substitute False
                                            color (_renforge_editor_ui_color("warn") if row["tag"] in ("T", "I") else _renforge_editor_ui_color("meta"))
                                            font _renforge_editor_ui_font()
                                            size _renforge_editor_ui_px(15)
                                            yalign 0.5

                        else:
                            # Structural element (layout / scaffolding container)
                            hbox:
                                xalign 0.0
                                spacing _renforge_editor_ui_px(8)
                                yalign 0.5
                                null width _renforge_editor_ui_px(8)
                                null width _renforge_editor_tree_indent(row["depth"])
                                add Solid(_renforge_editor_ui_color("tree_guide"), xysize=(_renforge_editor_ui_px(2), _renforge_editor_ui_px(24))):
                                    yalign 0.5
                                frame:
                                    xsize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                    ysize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                    background Solid(row.get("badge_color", _renforge_editor_ui_color("meta")) + "26")
                                    yalign 0.5
                                    text row["tag"]:
                                        color (row.get("badge_color", _renforge_editor_ui_color("meta")))
                                        font _renforge_editor_ui_font()
                                        size _renforge_editor_ui_px(16)
                                        xalign 0.5
                                        yalign 0.5
                                text row["label"]:
                                    substitute False
                                    color _renforge_editor_ui_color("border")
                                    font _renforge_editor_ui_font()
                                    size _renforge_editor_ui_px(18)
                                    yalign 0.5
                                if row.get("snippet"):
                                    $ _rf_snip = row["snippet"]
                                    text _rf_snip:
                                        substitute False
                                        color _renforge_editor_ui_color("meta")
                                        font _renforge_editor_ui_font()
                                        size _renforge_editor_ui_px(15)
                                        yalign 0.5
