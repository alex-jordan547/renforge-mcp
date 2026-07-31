# RenForge editor playground.
#
# A standalone manual test bed for the visual editor. Deliberately kept out of
# village_gate_choices: every focusable on screen contributes snap anchors, so
# adding these controls there would change the acceptance fixture's snap
# results. Open it in game with Escape, then "Editor playground".

screen editor_playground():
    tag menu
    modal True

    # A plain canvas: the red alignment guides must stand out, and the demo art
    # would only add noise to a positioning test bed.
    add Solid("#12141a")

    text "Editor playground" id "pg_title" xpos 60 ypos 40 size 28

    # Literal positions: editable, and deliberately aligned so the red guides
    # have something to catch. Alpha/Beta share a y, Alpha/Gamma/Bar share an x.
    textbutton "Alpha" id "pg_alpha" xpos 120 ypos 140 action NullAction()
    textbutton "Beta" id "pg_beta" xpos 420 ypos 140 action NullAction()
    textbutton "Gamma" id "pg_gamma" xpos 120 ypos 300 action NullAction()

    # Displayable kinds other than textbutton.
    add Transform("wisp glow", zoom=0.25) id "pg_sprite" xpos 700 ypos 260
    imagebutton id "pg_imgbtn" idle Transform("wisp glow", zoom=0.2) xpos 880 ypos 100 action NullAction()
    bar id "pg_bar" value 50 range 100 xpos 120 ypos 460 xysize (240, 26)

    frame id "pg_frame" xpos 640 ypos 460 xysize (240, 96):
        text "Framed panel"

    # Children of a dynamic layout: the hbox computes their position, so the
    # editor must refuse to write these back.
    hbox id "pg_row" xpos 640 ypos 580 spacing 20:
        textbutton "Row left" id "pg_row_left" action NullAction()
        textbutton "Row right" id "pg_row_right" action NullAction()

    # Two instances from a single source line with no stable identity: the
    # ambiguous-instance lock case.
    for _pg_i in range(2):
        textbutton "Clone" xpos 120 + _pg_i * 140 ypos 580 action NullAction()

    textbutton "Close" id "pg_close" xpos 1100 ypos 40 action Return()
