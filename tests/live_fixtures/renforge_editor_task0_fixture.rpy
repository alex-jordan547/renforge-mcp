default renforge_editor_task0_clicks = 0

screen renforge_editor_task0_dupe_slot(label_text, left_x):
    textbutton label_text:
        id "task0_dupe_target"
        xpos left_x
        ypos 430
        action NullAction()

screen renforge_editor_task0_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "task0_root"
        xfill True
        yfill True

        textbutton "ANCHOR":
            id "task0_anchor"
            xpos 360
            ypos 210
            action NullAction()

        fixed:
            id "task0_target_parent"
            xpos 40
            ypos 30
            xsize 400
            ysize 300

            textbutton "MOVE ME" id "task0_target" xpos 180 ypos 210 action SetVariable("renforge_editor_task0_clicks", renforge_editor_task0_clicks + 1)

        textbutton "OVERLAP TOP" id "task0_top" xpos 170 ypos 200 action NullAction()

        viewport:
            id "task0_clip_parent"
            xpos 580
            ypos 120
            xsize 120
            ysize 60
            draggable False
            mousewheel False
            textbutton "CLIPPED":
                id "task0_clipped"
                xpos 0
                ypos 0
                xsize 240
                ysize 60
                action NullAction()

        use renforge_editor_task0_dupe_slot("DUPE A", 720)
        use renforge_editor_task0_dupe_slot("DUPE B", 860)
