default renforge_editor_slider_value = 50
default renforge_editor_slider_computed_x = 520
default renforge_editor_slider_computed_val = 40
default renforge_editor_slider_style_val = 40
default renforge_editor_slider_missing_val = 40
default renforge_editor_slider_container_val = 40
default renforge_editor_slider_dupe_val = 40
default renforge_editor_slider_side_val = 40


# Style-driven position without inline xpos/ypos (locked).
style renforge_slider_style_pos is slider:
    xpos 700
    ypos 180
    xmaximum 200
    ymaximum 24


screen renforge_editor_slider_dupe(left_x):
    # Measured form: Ren'Py has no screen-language "slider" keyword.
    # Sliders are authored as bar + style "slider".
    bar value VariableValue("renforge_editor_slider_dupe_val", range=100) style "slider" id "slider_dupe_target" xpos left_x ypos 430 xsize 160 ysize 24


screen renforge_editor_slider_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "slider_root"
        xfill True
        yfill True

        bar value VariableValue("renforge_editor_slider_value", range=100) style "slider" id "slider_target" xpos 200 ypos 180 xsize 240 ysize 24

        bar value VariableValue("renforge_editor_slider_computed_val", range=100) style "slider" id "slider_computed" xpos renforge_editor_slider_computed_x ypos 300 xsize 200 ysize 24

        bar value VariableValue("renforge_editor_slider_style_val", range=100) style "renforge_slider_style_pos" id "slider_style"

        bar value VariableValue("renforge_editor_slider_missing_val", range=100) style "slider" id "slider_missing_position" xsize 160 ysize 24

        vbox:
            id "slider_container_parent"
            xpos 900
            ypos 120
            spacing 12

            bar value VariableValue("renforge_editor_slider_container_val", range=100) style "slider" id "slider_container" xpos 0 ypos 0 xsize 160 ysize 24

        side "c":
            bar value VariableValue("renforge_editor_slider_side_val", range=100) style "slider" id "slider_side" xpos 100 ypos 520 xsize 200 ysize 24

        use renforge_editor_slider_dupe(520)
        use renforge_editor_slider_dupe(720)
