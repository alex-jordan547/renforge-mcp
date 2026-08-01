default renforge_editor_button_computed_x = 520


screen renforge_editor_button_dupe(label_text, left_x):
    button id "button_dupe_target" xpos left_x ypos 430:
        text label_text
        action NullAction()


screen renforge_editor_button_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "button_root"
        xfill True
        yfill True

        button id "button_target" xpos 200 ypos 180:
            text "BUTTON TARGET" xpos 7
            action NullAction()

        button id "button_computed" xpos renforge_editor_button_computed_x ypos 300:
            text "COMPUTED"
            action NullAction()

        button id "button_in_block":
            xpos 720
            ypos 300
            text "POSITION IN BLOCK"
            action NullAction()

        vbox:
            id "button_container_parent"
            xpos 900
            ypos 120
            spacing 12

            button id "button_container" xpos 0 ypos 0:
                text "CONTAINER"
                action NullAction()

        use renforge_editor_button_dupe("DUPE A", 520)
        use renforge_editor_button_dupe("DUPE B", 720)
