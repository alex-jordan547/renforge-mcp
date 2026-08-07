screen _rf_editor_canvas_decor(tools_visible, selection, label, distance, measure, guide):
    if tools_visible and guide["line_x"] is not None:
        add Solid("#ff3b30", xysize=(1, max(1, int(guide["line_x"][2])))):
            id "rf_guide_x"
            xpos int(guide["line_x"][0])
            ypos int(guide["line_x"][1])

    if tools_visible and guide["line_y"] is not None:
        add Solid("#ff3b30", xysize=(max(1, int(guide["line_y"][2])), 1)):
            id "rf_guide_y"
            xpos int(guide["line_y"][0])
            ypos int(guide["line_y"][1])

    $ guide_x = _renforge_editor_guide_x()
    $ guide_y = _renforge_editor_guide_y()
    $ show_dx = distance is not None and (guide_x is not None or (measure is not None and measure["dx"] != 0))
    $ show_dy = distance is not None and (guide_y is not None or (measure is not None and measure["dy"] != 0))

    if tools_visible and show_dx:
        $ anchor_x = guide_x if guide_x is not None else int(distance["x"]) + int(distance["w"])
        $ distance_x = max(4, min(config.screen_width - 92, int(anchor_x) + 8))
        $ distance_y = max(48, min(config.screen_height - 28, int(distance["y"]) + int(distance["h"]) + 6))
        frame:
            id "rf_distance_x"
            xpos distance_x
            ypos distance_y
            background Solid(_renforge_editor_ui_color("panel"))
            padding (_renforge_editor_ui_px(12), _renforge_editor_ui_px(6))
            text distance["text_x"]:
                id "rf_distance_x_text"
                color _renforge_editor_ui_color("surface")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_px(24)

    if tools_visible and show_dy:
        $ anchor_y = guide_y if guide_y is not None else int(distance["y"]) + int(distance["h"])
        $ distance_x = max(4, min(config.screen_width - 92, int(distance["x"]) + int(distance["w"]) + 6))
        $ distance_y = max(48, min(config.screen_height - 28, int(anchor_y) + 8))
        frame:
            id "rf_distance_y"
            xpos distance_x
            ypos distance_y
            background Solid(_renforge_editor_ui_color("panel"))
            padding (_renforge_editor_ui_px(12), _renforge_editor_ui_px(6))
            text distance["text_y"]:
                id "rf_distance_y_text"
                color _renforge_editor_ui_color("surface")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_px(24)

    if tools_visible and selection is not None:
        $ selection_x = int(selection["x"])
        $ selection_y = int(selection["y"])
        $ selection_w = int(selection["w"])
        $ selection_h = int(selection["h"])
        $ selection_color = selection["color"]
        $ handle = _renforge_editor_canvas_handle_px()
        for handle_x, handle_y in _renforge_editor_handle_points(selection_x, selection_y, selection_w, selection_h, handle):
            add Solid(selection_color, xysize=(handle, handle)):
                xpos handle_x
                ypos handle_y
            add Solid(_renforge_editor_ui_color("surface"), xysize=(max(1, handle - 6), max(1, handle - 6))):
                xpos handle_x + 3
                ypos handle_y + 3
        add Solid(selection_color, xysize=(selection_w, 2)):
            xpos selection_x
            ypos selection_y
        add Solid(selection_color, xysize=(selection_w, 2)):
            xpos selection_x
            ypos selection_y + selection_h - 2
        add Solid(selection_color, xysize=(2, selection_h)):
            xpos selection_x
            ypos selection_y
        add Solid(selection_color, xysize=(2, selection_h)):
            xpos selection_x + selection_w - 2
            ypos selection_y

    if tools_visible and label is not None:
        frame:
            id "rf_label"
            xpos int(label["x"])
            ypos int(label["y"])
            xsize int(label["w"])
            ysize int(label["h"])
            background Solid(_renforge_editor_ui_color("scrim"))
            padding (_renforge_editor_ui_px(20), _renforge_editor_ui_px(10))
            at Transform(alpha=float(label["alpha"]))
            text label["text"]:
                color _renforge_editor_ui_color("surface")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_px(28)
                xalign 0.0
                yalign 0.5
