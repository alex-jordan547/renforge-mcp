default renforge_editor_align_clicks = 0
default renforge_editor_align_computed = 0.4


screen renforge_editor_align_dupe(label_text, x_frac):
    textbutton label_text id "align_dupe_target" align (x_frac, 0.6) action NullAction()


screen renforge_editor_align_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "align_root"
        xfill True
        yfill True

        # Supported form: single-line textbutton with pure align (fx, fy).
        # Full-screen fixed parent => pixels ≈ (fx * 1280, fy * 720) with default anchor.
        textbutton "MOVE ME" id "align_target" align (0.2, 0.25) action SetVariable("renforge_editor_align_clicks", renforge_editor_align_clicks + 1)

        textbutton "COMPUTED" id "align_computed" align (renforge_editor_align_computed, 0.4) action NullAction()

        vbox:
            id "align_container_parent"
            xpos 900
            ypos 120
            spacing 12

            textbutton "CONTAINER" id "align_container" align (0.0, 0.0) action NullAction()

        side "c":
            textbutton "SIDE" id "align_side" align (0.1, 0.7) action NullAction()

        use renforge_editor_align_dupe("DUPE A", 0.4)
        use renforge_editor_align_dupe("DUPE B", 0.55)
