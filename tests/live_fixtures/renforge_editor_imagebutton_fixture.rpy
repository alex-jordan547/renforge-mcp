default renforge_editor_imagebutton_clicks = 0

screen renforge_editor_imagebutton_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "imgbtn_root"
        xfill True
        yfill True

        textbutton "ANCHOR" id "imgbtn_anchor" xpos 360 ypos 210 action NullAction()

        imagebutton id "imgbtn_target" idle Solid("#4c6ef5", xysize=(96, 56)) xpos 200 ypos 180 action SetVariable("renforge_editor_imagebutton_clicks", renforge_editor_imagebutton_clicks + 1)
