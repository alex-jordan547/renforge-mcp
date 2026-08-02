# Spike fixture for issue #51 — Animated element editing through _widget_properties seam.

transform renforge_pos_anim:
    ease 1.0 xpos 300
    ease 1.0 xpos 100
    repeat

transform renforge_pulse_alpha:
    ease 1.0 alpha 0.4
    ease 1.0 alpha 1.0
    repeat

screen renforge_editor_animated_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "animated_root"
        xfill True
        yfill True

        add Solid("#0a0a0a", xysize=(1280, 720)):
            xpos 0
            ypos 0

        # Variant 1: ATL position animation
        button id "anim_pos_target" xpos 100 ypos 100 xsize 160 ysize 80 at renforge_pos_anim:
            action NullAction()
            background Solid("#e52e2e")
            text "POS ANIM" color "#ffffff" size 18

        # Variant 2: Non-positional ATL pulse animation
        button id "anim_style_target" xpos 400 ypos 100 xsize 160 ysize 80 at renforge_pulse_alpha:
            action NullAction()
            background Solid("#2ee56b")
            text "STYLE ANIM" color "#ffffff" size 18

        # Variant 3: Stationary Transform wrapper
        button id "anim_static_transform" xpos 100 ypos 300 xsize 160 ysize 80 at Transform(zoom=1.0):
            action NullAction()
            background Solid("#2e6be5")
            text "TRANSFORM" color "#ffffff" size 18
