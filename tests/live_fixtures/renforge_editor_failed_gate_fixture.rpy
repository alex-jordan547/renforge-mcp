default renforge_editor_failed_gate_clicks = 0

screen renforge_editor_failed_gate_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "failed_gate_root"
        xfill True
        yfill True

        # Gate family 1: Missing source identity.
        # A widget authored without an explicit `id` clause.
        textbutton "NO_ID" xpos 100 ypos 100 action SetVariable("renforge_editor_failed_gate_clicks", renforge_editor_failed_gate_clicks + 1)

        # Gate family 2: Clipping ancestry.
        # Target with composite transform (crop combined with rotate).
        fixed:
            id "clipping_parent"
            xpos 400
            ypos 100
            xysize (250, 150)

            textbutton "CLIPPED" id "clipped_composite_target" action NullAction() at Transform(crop=(0, 0, 100, 50), rotate=15.0)

        # Gate family 3: Repeated runtime instance.
        # Target instantiated inside a `for` loop.
        fixed:
            id "loop_parent"
            xpos 750
            ypos 100
            xysize (250, 200)

            for loop_item in ["ALPHA", "BETA"]:
                textbutton loop_item id "repeated_loop_target" action NullAction()

        # Control target: Unlocked, single instance, explicit id, no clipping.
        textbutton "UNLOCKED" id "unlocked_control_target" xpos 100 ypos 500 action NullAction()
