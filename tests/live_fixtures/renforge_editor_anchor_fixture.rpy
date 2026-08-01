default renforge_editor_anchor_clicks = 0
default renforge_editor_anchor_computed_x = 520


screen renforge_editor_anchor_dupe(label_text, left_x):
    textbutton label_text id "anchor_dupe_target" xpos left_x ypos 430 anchor (0.5, 0.5) action NullAction()


screen renforge_editor_anchor_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "anchor_root"
        xfill True
        yfill True

        # Supported form: xpos/ypos + pure literal anchor; move patches xy, preserves anchor.
        textbutton "MOVE ME" id "anchor_target" xpos 400 ypos 300 anchor (0.5, 0.5) action SetVariable("renforge_editor_anchor_clicks", renforge_editor_anchor_clicks + 1)

        textbutton "COMPUTED" id "anchor_computed" xpos renforge_editor_anchor_computed_x ypos 200 anchor (0.5, 0.5) action NullAction()

        vbox:
            id "anchor_container_parent"
            xpos 900
            ypos 120
            spacing 12

            textbutton "CONTAINER" id "anchor_container" xpos 0 ypos 0 anchor (0.5, 0.5) action NullAction()

        side "c":
            textbutton "SIDE" id "anchor_side" xpos 100 ypos 520 anchor (0.5, 0.5) action NullAction()

        use renforge_editor_anchor_dupe("DUPE A", 520)
        use renforge_editor_anchor_dupe("DUPE B", 720)
