default renforge_editor_viewport_clicks = 0
default renforge_editor_viewport_computed_y = 120


screen renforge_editor_viewport_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "viewport_root"
        xfill True
        yfill True

        # Proven shape under test: a scrollable viewport whose child is a plain
        # `fixed`, holding a single-line textbutton with literal xpos/ypos.
        # The content is deliberately larger than the frame so the viewport has
        # somewhere to scroll to. No `scrollbars`: that wraps the viewport in a
        # `Side`, which is a separately unproven ancestry (see the locked
        # control below). No `draggable` either: it turns the editor's own click
        # into a scroll, so the two drag behaviours fight.
        viewport:
            id "viewport_frame"
            xpos 200
            ypos 160
            xysize (420, 260)
            child_size (900, 700)
            mousewheel True

            # Every control sits inside the band that stays fully visible at
            # both proof offsets (child y 120..235, frame window 0..260 then
            # 120..380). Ren'Py scrolls a viewport to reveal a partially hidden
            # focused widget, which would move the geometry under the proof.
            fixed:
                id "viewport_content"

                textbutton "COMPUTED" id "viewport_computed" xpos 60 ypos renforge_editor_viewport_computed_y action NullAction()

                textbutton "MOVE ME" id "viewport_target" xpos 60 ypos 160 action SetVariable("renforge_editor_viewport_clicks", renforge_editor_viewport_clicks + 1)

                vbox:
                    id "viewport_container_parent"
                    xpos 60
                    ypos 200
                    spacing 12

                    textbutton "CONTAINER" id "viewport_container" action NullAction()

        # Control: same statement shape outside any viewport, so the proof can
        # show the viewport is what changes and not the adapter.
        textbutton "OUTSIDE" id "viewport_outside" xpos 200 ypos 520 action NullAction()

        # Still locked: `scrollbars` wraps the viewport in a `Side`, which is not
        # in the ancestry allowlist.
        viewport:
            id "viewport_scrollbar_frame"
            xpos 200
            ypos 560
            xysize (300, 120)
            child_size (600, 400)
            mousewheel True
            scrollbars "vertical"

            fixed:
                textbutton "SCROLLBAR" id "viewport_scrollbar_target" xpos 20 ypos 20 action NullAction()

        # Still locked: a viewport nested inside another viewport is a different
        # ancestry shape and was never measured.
        viewport:
            id "viewport_outer_nested"
            xpos 760
            ypos 160
            xysize (300, 200)
            child_size (600, 500)
            mousewheel True

            fixed:
                id "viewport_nested_content"

                viewport:
                    id "viewport_inner_nested"
                    xpos 20
                    ypos 20
                    xysize (220, 140)
                    child_size (400, 300)
                    mousewheel True

                    fixed:
                        textbutton "NESTED" id "viewport_nested_target" xpos 20 ypos 20 action NullAction()
