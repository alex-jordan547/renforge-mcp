# ── Lot 1.B — scene tree ────────────────────────────────────────────────────
# Reads the live displayable tree every frame rather than caching it: the game
# is running and its screens change under the editor, so a cached tree would
# quietly describe a scene that no longer exists.
screen _rf_editor_tree():
    $ _rf_rows = _renforge_editor_tree_rows()

    frame:
        id "rf_tree_panel"
        xpos _renforge_editor_ui_px(32)
        ypos _renforge_editor_ui_px(148)
        xsize _renforge_editor_ui_px(480)
        ysize _renforge_editor_ui_px(940)
        background Frame(_renforge_editor_ui_frame("panel"), 25, 25)
        padding (0, 0)

        vbox:
            xfill True
            yfill True
            spacing 0

            frame:
                xfill True
                ysize _renforge_editor_ui_px(76)
                background Frame(_renforge_editor_ui_frame("panel_head"), 25, 25, 25, 2)
                padding (_renforge_editor_ui_px(20), _renforge_editor_ui_px(10))
                hbox:
                    xfill True
                    yalign 0.5
                    text _renforge_editor_t("tree.title"):
                        id "rf_tree_title"
                        color _renforge_editor_ui_color("meta")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(18)
                        yalign 0.5
                    null width 1 xfill True
                    text ("%d" % len(_rf_rows)):
                        id "rf_tree_count"
                        color _renforge_editor_ui_color("surface")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(18)
                        yalign 0.5

            frame:
                id "rf_tree_filter"
                xfill True
                ysize _renforge_editor_ui_px(50)
                background Solid(_renforge_editor_ui_color("sunken"))
                padding (_renforge_editor_ui_px(20), _renforge_editor_ui_px(10))
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
                    spacing _renforge_editor_ui_px(2)
                    $ _rf_current_screen = ""
                    for row in _rf_rows:
                        if row["depth"] == 0:
                            $ _rf_current_screen = row["id"]

                        if row["depth"] > 0 and row["id"]:
                            button:
                                id ("rf_tree_item_" + str(_rf_current_screen) + "_" + str(row["id"]))
                                action Function(_renforge_editor_consume, _renforge_editor_select_widget, _rf_current_screen, row["id"])
                                hover_background Solid(_renforge_editor_ui_color("sunken"))
                                xfill True
                                padding (_renforge_editor_ui_px(4), _renforge_editor_ui_px(2))
                                hbox:
                                    xalign 0.0
                                    spacing _renforge_editor_ui_px(8)
                                    null width _renforge_editor_tree_indent(row["depth"])
                                    text row["tag"]:
                                        color (_renforge_editor_ui_color("accent_bright") if row["selected"] else _renforge_editor_ui_color("meta"))
                                        font _renforge_editor_ui_font()
                                        size _renforge_editor_ui_px(17)
                                        yalign 0.5
                                    text (row["label"] + ("  " + row["id"] if row["id"] else "")):
                                        color (_renforge_editor_ui_color("surface") if row["selected"] else _renforge_editor_ui_color("border"))
                                        font _renforge_editor_ui_font()
                                        size _renforge_editor_ui_px(19)
                                        yalign 0.5
                        else:
                            hbox:
                                xalign 0.0
                                spacing _renforge_editor_ui_px(8)
                                null width _renforge_editor_tree_indent(row["depth"])
                                text row["tag"]:
                                    color (_renforge_editor_ui_color("accent_bright") if row["selected"] else _renforge_editor_ui_color("meta"))
                                    font _renforge_editor_ui_font()
                                    size _renforge_editor_ui_px(17)
                                    yalign 0.5
                                text (row["label"] + ("  " + row["id"] if row["id"] else "")):
                                    color (_renforge_editor_ui_color("surface") if row["selected"] else _renforge_editor_ui_color("border"))
                                    font _renforge_editor_ui_font()
                                    size _renforge_editor_ui_px(19)
                                    yalign 0.5
