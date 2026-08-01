default renforge_editor_pos_clicks = 0
default renforge_editor_pos_computed_x = 520


screen renforge_editor_pos_dupe(label_text, left_x):
    textbutton label_text id "pos_dupe_target" pos (left_x, 430) action NullAction()


screen renforge_editor_pos_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "pos_root"
        xfill True
        yfill True

        # Supported form: single-line textbutton with literal pos (x, y).
        textbutton "MOVE ME" id "pos_target" pos (200, 180) action SetVariable("renforge_editor_pos_clicks", renforge_editor_pos_clicks + 1)

        textbutton "COMPUTED" id "pos_computed" pos (renforge_editor_pos_computed_x, 300) action NullAction()

        vbox:
            id "pos_container_parent"
            xpos 900
            ypos 120
            spacing 12

            textbutton "CONTAINER" id "pos_container" pos (0, 0) action NullAction()

        side "c":
            textbutton "SIDE" id "pos_side" pos (100, 520) action NullAction()

        use renforge_editor_pos_dupe("DUPE A", 520)
        use renforge_editor_pos_dupe("DUPE B", 720)
