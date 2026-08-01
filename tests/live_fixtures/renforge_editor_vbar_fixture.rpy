default renforge_editor_vbar_value = 50
default renforge_editor_vbar_computed_x = 520
default renforge_editor_vbar_computed_val = 40
default renforge_editor_vbar_style_val = 40
default renforge_editor_vbar_missing_val = 40
default renforge_editor_vbar_container_val = 40
default renforge_editor_vbar_dupe_val = 40
default renforge_editor_vbar_side_val = 40


style renforge_vbar_style_pos is vbar:
    xpos 700
    ypos 180
    xmaximum 24
    ymaximum 200


screen renforge_editor_vbar_dupe(left_y):
    vbar value VariableValue("renforge_editor_vbar_dupe_val", range=100) id "vbar_dupe_target" xpos 520 ypos left_y xsize 24 ysize 160


screen renforge_editor_vbar_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "vbar_root"
        xfill True
        yfill True

        vbar value VariableValue("renforge_editor_vbar_value", range=100) id "vbar_target" xpos 200 ypos 180 xsize 24 ysize 240

        vbar value VariableValue("renforge_editor_vbar_computed_val", range=100) id "vbar_computed" xpos renforge_editor_vbar_computed_x ypos 300 xsize 24 ysize 200

        vbar value VariableValue("renforge_editor_vbar_style_val", range=100) style "renforge_vbar_style_pos" id "vbar_style"

        vbar value VariableValue("renforge_editor_vbar_missing_val", range=100) id "vbar_missing_position" xsize 24 ysize 160

        vbox:
            id "vbar_container_parent"
            xpos 900
            ypos 120
            spacing 12

            vbar value VariableValue("renforge_editor_vbar_container_val", range=100) id "vbar_container" xpos 0 ypos 0 xsize 24 ysize 160

        side "c":
            vbar value VariableValue("renforge_editor_vbar_side_val", range=100) id "vbar_side" xpos 100 ypos 520 xsize 24 ysize 200

        use renforge_editor_vbar_dupe(430)
        use renforge_editor_vbar_dupe(680)
