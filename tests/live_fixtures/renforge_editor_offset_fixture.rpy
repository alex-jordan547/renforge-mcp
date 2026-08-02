default renforge_editor_offset_clicks = 0
default renforge_editor_offset_computed_x = 520


screen renforge_editor_offset_dupe(label_text, left_x):
    textbutton label_text id "offset_dupe_target" offset (left_x, 430) action NullAction()


screen renforge_editor_offset_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "offset_root"
        xfill True
        yfill True

        # Supported form: single-line textbutton with literal offset (x, y).
        textbutton "MOVE ME" id "offset_target" offset (200, 180) action SetVariable("renforge_editor_offset_clicks", renforge_editor_offset_clicks + 1)

        textbutton "COMPUTED" id "offset_computed" offset (renforge_editor_offset_computed_x, 300) action NullAction()

        vbox:
            id "offset_container_parent"
            xoffset 900
            yoffset 120
            spacing 12

            textbutton "CONTAINER" id "offset_container" offset (0, 0) action NullAction()

        side "c":
            textbutton "SIDE" id "offset_side" offset (100, 520) action NullAction()

        use renforge_editor_offset_dupe("DUPE A", 520)
        use renforge_editor_offset_dupe("DUPE B", 720)
