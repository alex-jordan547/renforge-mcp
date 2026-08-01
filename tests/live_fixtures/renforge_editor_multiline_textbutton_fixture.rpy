default renforge_editor_ml_tb_clicks = 0
default renforge_editor_ml_tb_computed_x = 520


screen renforge_editor_ml_tb_dupe(label_text, left_x):
    textbutton label_text:
        id "ml_tb_dupe_target"
        xpos left_x
        ypos 430
        action NullAction()


screen renforge_editor_multiline_textbutton_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "ml_tb_root"
        xfill True
        yfill True

        # Supported multi-line form: id/xpos/ypos in the child block only.
        textbutton "MOVE ME":
            id "ml_tb_target"
            xpos 200
            ypos 180
            action SetVariable("renforge_editor_ml_tb_clicks", renforge_editor_ml_tb_clicks + 1)

        textbutton "COMPUTED":
            id "ml_tb_computed"
            xpos renforge_editor_ml_tb_computed_x
            ypos 300
            action NullAction()

        vbox:
            id "ml_tb_container_parent"
            xpos 900
            ypos 120
            spacing 12

            textbutton "CONTAINER":
                id "ml_tb_container"
                xpos 0
                ypos 0
                action NullAction()

        side "c":
            textbutton "SIDE":
                id "ml_tb_side"
                xpos 100
                ypos 520
                action NullAction()

        use renforge_editor_ml_tb_dupe("DUPE A", 520)
        use renforge_editor_ml_tb_dupe("DUPE B", 720)
