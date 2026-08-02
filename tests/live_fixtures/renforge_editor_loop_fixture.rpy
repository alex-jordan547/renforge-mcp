default renforge_editor_loop_clicks = 0
default renforge_editor_loop_items = ["ALPHA", "BETA", "GAMMA"]


# Inner screen reached through a repeated `use`. Its position is a literal, so a
# single authored line backs every call site.
screen renforge_editor_loop_used():
    textbutton "USED" id "loop_used_target" xpos 60 ypos 40 action NullAction()


screen renforge_editor_loop_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "loop_root"
        xfill True
        yfill True

        # Case A: one source line, N runtime widgets, literal position.
        # Every instance lands on the same coordinates because the literal
        # cannot depend on the loop variable.
        for loop_label in renforge_editor_loop_items:
            textbutton loop_label id "loop_literal_target" xpos 200 ypos 160 action SetVariable("renforge_editor_loop_clicks", renforge_editor_loop_clicks + 1)

        # Case B: same loop, position derived from the loop index. This is the
        # only way N instances occupy N distinct places, and it is an expression.
        for loop_index, loop_label in enumerate(renforge_editor_loop_items):
            textbutton loop_label id "loop_expr_target" xpos 200 + loop_index * 160 ypos 300 action NullAction()

        # Case C: loop with an explicit index expression, which Ren'Py uses to key
        # the SL2 cache. Position is still layout-computed inside the vbox.
        vbox:
            id "loop_vbox_parent"
            xpos 900
            ypos 120
            spacing 10

            for loop_label index loop_label in renforge_editor_loop_items:
                textbutton loop_label id "loop_vbox_target" action NullAction()

        # Case D: repeated `use` of a screen whose target carries a literal
        # position. Two distinct call sites, one authored line.
        fixed:
            id "loop_use_left"
            xpos 200
            ypos 460
            xysize (300, 90)
            use renforge_editor_loop_used

        fixed:
            id "loop_use_right"
            xpos 620
            ypos 460
            xysize (300, 90)
            use renforge_editor_loop_used

        # Control: a unique, non-repeated target on the same screen, so the probe
        # can show the discriminator distinguishes repetition from uniqueness.
        textbutton "UNIQUE" id "loop_unique_target" xpos 200 ypos 600 action NullAction()
