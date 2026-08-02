# Spike fixture for issue #49 — two overlapping direct sibling buttons.

screen renforge_editor_zorder_fixture():
    layer "screens"
    zorder 640

    add Solid("#0a0a0a", xysize=(1280, 720))

    button id "zorder_target" xpos 220 ypos 220 xsize 180 ysize 100:
        action NullAction()
        background None
        hover_background None
        add Solid("#d83a3a", xysize=(180, 100))

    # This separator must remain byte-identical while the blocks move.
    button id "zorder_sibling" xpos 260 ypos 220 xsize 180 ysize 100:
        action NullAction()
        background None
        hover_background None
        add Solid("#2457d6", xysize=(180, 100))
