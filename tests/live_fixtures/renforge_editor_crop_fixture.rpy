# Issue #45 — pure Transform(crop=) ancestry (Crop is sugar for the same runtime).
#
# In Ren'Py 8.5.3, Crop(rect, child) returns Transform(child, crop=rect).
# This fixture authors the measured shape as `fixed at Transform(crop=...)`
# so ancestry is the real runtime type, not a fictional Crop class.

default renforge_editor_crop_clicks = 0
default renforge_editor_crop_computed_y = 80


screen renforge_editor_crop_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "crop_root"
        xfill True
        yfill True

        # Outside control: same statement shape, no crop ancestor.
        textbutton "OUTSIDE" id "crop_outside" xpos 40 ypos 40 action NullAction()

        # Pure crop window under test. The crop rect is (0, 0, 300, 200) of the
        # fixed's own coordinate space. Children with ypos past the crop edge
        # are partially or fully painted-away while their layout may still exist.
        # No rotate/zoom here — those are issue #46.
        fixed:
            id "crop_window"
            xpos 200
            ypos 160
            at Transform(crop=(0, 0, 300, 200))

            # Fully inside the crop — seven-step write target.
            textbutton "MOVE ME" id "crop_target" xpos 20 ypos 40 action SetVariable("renforge_editor_crop_clicks", renforge_editor_crop_clicks + 1)

            # Expression ypos — must stay locked for the source-form reason.
            textbutton "COMPUTED" id "crop_computed" xpos 20 ypos renforge_editor_crop_computed_y action NullAction()

            # Layout container inside the crop — CONTAINER_POSITION_UNSUPPORTED.
            vbox:
                id "crop_container_parent"
                xpos 20
                ypos 100
                spacing 8

                textbutton "CONTAINER" id "crop_container" action NullAction()

            # Partially clipped: top at child y=185, natural height ~35 → bottom
            # past the crop edge at 200. Measured on 8.5.3: focus height is
            # already clipped (e.g. 15px) rather than reporting the full layout
            # box — focus tracks visible geometry under pure Transform(crop=).
            textbutton "PARTIAL" id "crop_partial" xpos 160 ypos 185 action NullAction()

            # Fully outside the crop rect (y >= 200). Measured: absent from
            # list_ui_elements (not painted / not focus-listed for selection).
            textbutton "FULLCLIP" id "crop_fullclip" xpos 20 ypos 250 action NullAction()

        # Issue #46 references: identical labels outside any transform, so the
        # composite rects below can be compared against an untransformed one.
        # Same text means the same natural width, which is what makes the zoom
        # factor and the rotation's AABB growth measurable from focus rects.
        textbutton "CROP+ROT" id "crop_rotate_reference" xpos 40 ypos 560 action NullAction()
        textbutton "CROP+ZOOM" id "crop_zoom_reference" xpos 40 ypos 610 action NullAction()

        # Still locked (issue #46): crop combined with rotate.
        fixed:
            id "crop_rotate_window"
            xpos 560
            ypos 160
            at Transform(crop=(0, 0, 220, 160), rotate=15)

            textbutton "CROP+ROT" id "crop_with_rotate" xpos 20 ypos 40 action NullAction()

        # Still locked (issue #46): crop combined with non-default zoom.
        fixed:
            id "crop_zoom_window"
            xpos 560
            ypos 380
            at Transform(crop=(0, 0, 220, 160), zoom=1.25)

            textbutton "CROP+ZOOM" id "crop_with_zoom" xpos 20 ypos 40 action NullAction()
