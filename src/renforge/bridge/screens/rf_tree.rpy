# ── Lot 1.B — scene tree ────────────────────────────────────────────────────
# Reads the live displayable tree every frame rather than caching it: the game
# is running and its screens change under the editor, so a cached tree would
# quietly describe a scene that no longer exists.
screen _rf_tree_disclosure(row):
    if row.get("has_children"):
        button:
            id row["toggle_id"]
            action Function(_renforge_editor_consume, _renforge_editor_toggle_tree_node, row["node_key"])
            tooltip ("Collapse" if row.get("expanded") else "Expand")
            xsize _renforge_editor_ui_px(28, minimum=20)
            ysize _renforge_editor_ui_px(28, minimum=20)
            background Solid("#00000000")
            hover_background Solid(_renforge_editor_ui_color("row_hover"))
            padding (0, 0)
            yalign 0.5
            add _renforge_editor_ui_icon("caret"):
                xysize (
                    _renforge_editor_ui_px(16, minimum=12),
                    _renforge_editor_ui_px(16, minimum=12),
                )
                xalign 0.5
                yalign 0.5
                at Transform(rotate=(0 if row.get("expanded") else -90))
    else:
        null width _renforge_editor_ui_px(28, minimum=20)


screen _rf_editor_tree():
    if _renforge_editor_panel_visible("tree"):
        use _rf_editor_tree_panel()
    else:
        use _rf_editor_panel_restore_tab("tree", "left", "tree_rect", "rf_tree_show")


screen _rf_editor_tree_panel():
    $ _rf_tree = _renforge_editor_tree_rows()
    $ _rf_rows = _rf_tree.get("rows") if isinstance(_rf_tree, dict) else _rf_tree
    $ _rf_tree_total = int(_rf_tree.get("total") or len(_rf_rows or [])) if isinstance(_rf_tree, dict) else len(_rf_rows or [])
    $ _rf_tree_truncated = bool(isinstance(_rf_tree, dict) and (_rf_tree.get("count_truncated") or _rf_tree.get("depth_truncated")))
    $ _rf_tree_count_text = (
        _renforge_editor_t("tree.items_more").replace("{count}", "1000")
        if _rf_tree_truncated
        else _renforge_editor_t("tree.items_count").replace("{count}", str(_rf_tree_total))
    )
    $ _rf_docked = _renforge_editor_chrome_docked()
    $ _rf_tree_rect = _renforge_editor_live_layout_metrics().get("tree_rect")
    $ _rf_tree_panel_w = int(_rf_tree_rect[2]) if _rf_tree_rect is not None else _renforge_editor_ui_px(_RF_OVERLAY_TREE_W)
    $ _rf_tree_header_w = max(1, _rf_tree_panel_w - _renforge_editor_ui_px(104, minimum=52))

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
                fixed:
                    xfill True
                    yfill True
                    hbox:
                        id "rf_tree_header_content"
                        xfill True
                        xmaximum _rf_tree_header_w
                        yalign 0.5
                        text _renforge_editor_t("tree.title"):
                            id "rf_tree_title"
                            color _renforge_editor_ui_color("surface")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_text_px(18, minimum=13)
                            yalign 0.5
                        null width 1 xfill True
                        frame:
                            background Solid(_renforge_editor_ui_color("sunken"))
                            padding (_renforge_editor_ui_px(10), _renforge_editor_ui_px(3))
                            yalign 0.5
                            text _rf_tree_count_text:
                                substitute False
                                id "rf_tree_count"
                                color _renforge_editor_ui_color("meta")
                                font _renforge_editor_ui_font()
                                size _renforge_editor_ui_text_px(15, minimum=11)
                                yalign 0.5
                    hbox:
                        id "rf_tree_panel_action"
                        xalign 1.0
                        yalign 0.5
                        use _rf_editor_panel_hide_button("tree", "left", "rf_tree_hide")

            viewport:
                id "rf_tree_viewport"
                style_prefix "rf"
                xfill True
                yfill True
                mousewheel True
                draggable True
                scrollbars "vertical"
                vbox:
                    xfill True
                    spacing _renforge_editor_ui_px(3)
                    $ _rf_current_screen = ""
                    for row in _rf_rows:
                        if row["depth"] == 0:
                            $ _rf_current_screen = row["id"]
                            # Flat section header: hierarchy without a card inside a card.
                            frame:
                                xfill True
                                padding (_renforge_editor_ui_px(12), _renforge_editor_ui_px(8))
                                background Solid(_renforge_editor_ui_color("panel_head"))
                                top_margin _renforge_editor_ui_px(4)
                                bottom_margin _renforge_editor_ui_px(2)
                                hbox:
                                    spacing _renforge_editor_ui_px(10)
                                    yalign 0.5
                                    use _rf_tree_disclosure(row)
                                    frame:
                                        xsize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                        ysize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                        background Solid(_renforge_editor_ui_color("meta") + "1f")
                                        yalign 0.5
                                        text "S":
                                            color _renforge_editor_ui_color("border")
                                            font _renforge_editor_ui_font()
                                            size _renforge_editor_ui_text_px(17, minimum=12)
                                            xalign 0.5
                                            yalign 0.5
                                    text str(row["id"]):
                                        substitute False
                                        color _renforge_editor_ui_color("surface")
                                        font _renforge_editor_ui_font()
                                        size _renforge_editor_ui_text_px(18, minimum=13)
                                        yalign 0.5
                                    frame:
                                        background Solid("#0000004d")
                                        padding (_renforge_editor_ui_px(6), _renforge_editor_ui_px(2))
                                        yalign 0.5
                                        text _renforge_editor_t("tree.screen"):
                                            color _renforge_editor_ui_color("meta")
                                            font _renforge_editor_ui_font()
                                            size _renforge_editor_ui_text_px(13, minimum=10)
                                            yalign 0.5

                        elif row.get("selectable"):
                            # Interactive Widget item (selectable)
                            frame:
                                background (Solid(_renforge_editor_ui_color("accent") + "33") if row["selected"] else None)
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
                                    use _rf_tree_disclosure(row)
                                    button:
                                        id (str(row.get("toggle_id") or "rf_tree_item") + "_select")
                                        action (
                                            Function(_renforge_editor_consume, _renforge_editor_select_runtime_key, row["runtime_key"])
                                            if row.get("runtime_key")
                                            else Function(_renforge_editor_consume, _renforge_editor_select_widget, row.get("screen_name") or _rf_current_screen, row["id"])
                                        )
                                        background Solid("#00000000")
                                        hover_background Solid(_renforge_editor_ui_color("row_hover"))
                                        padding (0, 0)
                                        xfill True
                                        yalign 0.5
                                        hbox:
                                            spacing _renforge_editor_ui_px(8)
                                            yalign 0.5
                                            frame:
                                                xsize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                                ysize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                                background Solid((row.get("badge_color", _renforge_editor_ui_color("meta")) + "33") if row["selected"] else (_renforge_editor_ui_color("meta") + "1f"))
                                                yalign 0.5
                                                text row["tag"]:
                                                    color (row.get("badge_color", _renforge_editor_ui_color("meta")) if row["selected"] else _renforge_editor_ui_color("border"))
                                                    font _renforge_editor_ui_font()
                                                    size _renforge_editor_ui_text_px(16, minimum=11)
                                                    xalign 0.5
                                                    yalign 0.5
                                            text row["label"]:
                                                substitute False
                                                color (_renforge_editor_ui_color("surface") if row["selected"] else _renforge_editor_ui_color("border"))
                                                font _renforge_editor_ui_font()
                                                size _renforge_editor_ui_text_px(18, minimum=13)
                                                xmaximum _renforge_editor_ui_px(160)
                                                yalign 0.5
                                            frame:
                                                background Solid("#0000004d")
                                                padding (_renforge_editor_ui_px(6), _renforge_editor_ui_px(2))
                                                yalign 0.5
                                                $ _rf_source_location = row.get("source_location") or []
                                                $ _rf_identity = (
                                                    "#" + str(row["id"])
                                                    if row.get("id")
                                                    else (
                                                        str(_rf_source_location[0]).split("/")[-1] + ":" + str(_rf_source_location[1])
                                                        if len(_rf_source_location) == 2
                                                        else "source"
                                                    )
                                                )
                                                text _rf_identity:
                                                    substitute False
                                                    color (_renforge_editor_ui_color("border") if row["selected"] else _renforge_editor_ui_color("meta"))
                                                    font _renforge_editor_ui_font()
                                                    size _renforge_editor_ui_text_px(14, minimum=10)
                                                    xmaximum _renforge_editor_ui_px(140)
                                                    yalign 0.5
                                            if row.get("snippet"):
                                                # Pre-escaped in _renforge_editor_tree_escape; never substitute.
                                                $ _rf_snip = row["snippet"]
                                                text _rf_snip:
                                                    substitute False
                                                    color (_renforge_editor_ui_color("border") if row["selected"] else _renforge_editor_ui_color("meta"))
                                                    font _renforge_editor_ui_font()
                                                    size _renforge_editor_ui_text_px(15, minimum=11)
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
                                use _rf_tree_disclosure(row)
                                frame:
                                    xsize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                    ysize _renforge_editor_ui_px(_RF_TREE_BADGE)
                                    background Solid(_renforge_editor_ui_color("meta") + "1a")
                                    yalign 0.5
                                    text row["tag"]:
                                        color _renforge_editor_ui_color("meta")
                                        font _renforge_editor_ui_font()
                                        size _renforge_editor_ui_text_px(16, minimum=11)
                                        xalign 0.5
                                        yalign 0.5
                                text row["label"]:
                                    substitute False
                                    color _renforge_editor_ui_color("border")
                                    font _renforge_editor_ui_font()
                                    size _renforge_editor_ui_text_px(18, minimum=13)
                                    yalign 0.5
                                if row.get("snippet"):
                                    $ _rf_snip = row["snippet"]
                                    text _rf_snip:
                                        substitute False
                                        color _renforge_editor_ui_color("meta")
                                        font _renforge_editor_ui_font()
                                        size _renforge_editor_ui_text_px(15, minimum=11)
                                        yalign 0.5
