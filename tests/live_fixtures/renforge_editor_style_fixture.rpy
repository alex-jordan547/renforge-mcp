# Live fixture for issue #50 — text adapter, literal color property only.

default renforge_style_expr_color = "#e22b2b"


screen renforge_editor_style_fixture():
    layer "screens"
    zorder 640

    add Solid("#0a0a12", xysize=(1280, 720))

    # Unlocked target: pure hex string-literal color on single-line text.
    text "STYLE" id "style_color_target" color "#e22b2b" size 96 xpos 240 ypos 220

    # Inherited / not directly authored color — must stay locked.
    text "INHERIT" id "style_color_inherited" size 40 xpos 240 ypos 420

    # Expression color — must stay locked.
    text "EXPR" id "style_color_expr" color renforge_style_expr_color size 40 xpos 240 ypos 500

    # Non-text control present only for focus_list sanity (not a style target).
    textbutton "FOCUS" id "style_focus_control" xpos 900 ypos 80 action NullAction()
