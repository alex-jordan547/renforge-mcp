default renforge_editor_bar_value = 50
default renforge_editor_bar_computed_x = 520
default renforge_editor_bar_computed_val = 40
default renforge_editor_bar_style_val = 40
default renforge_editor_bar_container_val = 40
default renforge_editor_bar_dupe_val = 40
default renforge_editor_bar_side_val = 40
default renforge_editor_bar_xysize_val = 40
default renforge_editor_bar_constraint_val = 40


style renforge_bar_style_pos is bar:
    xpos 700
    ypos 180
    xmaximum 200
    ymaximum 24


screen renforge_editor_bar_dupe(left_x):
    bar value VariableValue("renforge_editor_bar_dupe_val", range=100) id "bar_dupe_target" xpos left_x ypos 430 xsize 160 ysize 24


screen renforge_editor_bar_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "bar_root"
        xfill True
        yfill True

        bar value VariableValue("renforge_editor_bar_value", range=100) id "bar_target" xpos 200 ypos 180 xsize 240 ysize 24

        # Issue #47: move-unlocked, resize-locked forms (pure fixed parent).
        bar value VariableValue("renforge_editor_bar_xysize_val", range=100) id "bar_xysize" xpos 200 ypos 260 xysize (180, 24)

        bar value VariableValue("renforge_editor_bar_constraint_val", range=100) id "bar_size_constraint" xpos 420 ypos 260 xsize 160 ysize 24 yfill True

        bar value VariableValue("renforge_editor_bar_computed_val", range=100) id "bar_computed" xpos renforge_editor_bar_computed_x ypos 300 xsize 200 ysize 24

        bar value VariableValue("renforge_editor_bar_style_val", range=100) style "renforge_bar_style_pos" id "bar_style"

        vbox:
            id "bar_container_parent"
            xpos 900
            ypos 120
            spacing 12

            bar value VariableValue("renforge_editor_bar_container_val", range=100) id "bar_container" xpos 0 ypos 0 xsize 160 ysize 24


        side "c":
            bar value VariableValue("renforge_editor_bar_side_val", range=100) id "bar_side" xpos 100 ypos 520 xsize 200 ysize 24

        use renforge_editor_bar_dupe(520)
        use renforge_editor_bar_dupe(720)
