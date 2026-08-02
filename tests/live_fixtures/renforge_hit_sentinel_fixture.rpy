# Spike fixture for issue #43 — non-focusable hit regions with unique paint colours.
# Dark stage so isolation masks and ground-truth colour sampling stay separable.

default renforge_hit_sentinel_ready = False

# Unique RGB triples (no shared channel patterns) for ground-truth classification.
# add solid:        #E52E2E
# text:             #2EE5A0  (via color)
# frame bg:         #2E6BE5
# rotated solid:    #E5C82E
# clipped child:    #C82EE5
# viewport child:   #2EE5E5
# textbutton idle:  focusable control for regression only


screen renforge_hit_sentinel_fixture():
    layer "screens"
    zorder 700

    fixed:
        id "hit_root"
        xfill True
        yfill True

        # Full-window dark stage for colour-mask ground truth.
        add Solid("#0a0a0cff", xysize=(1280, 720)):
            xpos 0
            ypos 0

        # 1. Axis-aligned non-focusable solid add.
        add Solid("#e52e2eff", xysize=(160, 100)):
            id "hit_add"
            xpos 80
            ypos 80

        # 2. Plain text (never in focus_list).
        text "SPIKE TEXT":
            id "hit_text"
            xpos 280
            ypos 100
            size 36
            color "#2ee5a0"

        # 3. Decorative frame with solid background.
        frame:
            id "hit_frame"
            xpos 80
            ypos 220
            xsize 180
            ysize 100
            background Solid("#2e6be5ff")
            text "Frame" color "#ffffff" size 22

        # 4. Rotated solid — AABB must over-report corners.
        add Solid("#e5c82eff", xysize=(140, 80)):
            id "hit_rotated"
            xpos 320
            ypos 240
            at Transform(rotate=25)

        # 5. Clipping parent + overflowing child.
        fixed:
            id "hit_clip_parent"
            xpos 520
            ypos 80
            xsize 100
            ysize 80
            clipping True
            add Solid("#c82ee5ff", xysize=(200, 80)):
                id "hit_clipped_child"
                xpos 0
                ypos 0

        # 6. Viewport with non-focusable content (scroll offset interaction).
        viewport:
            id "hit_viewport"
            xpos 520
            ypos 220
            xsize 160
            ysize 100
            draggable True
            mousewheel True
            yinitial 40
            scrollbars None

            fixed:
                xsize 160
                ysize 220
                add Solid("#2ee5e5ff", xysize=(120, 60)):
                    id "hit_viewport_child"
                    xpos 20
                    ypos 80

        # 7. Focusable regression control.
        textbutton "FOCUS ME":
            id "hit_focusable"
            xpos 80
            ypos 360
            xsize 160
            ysize 48
            action NullAction()
            background Solid("#888888ff")
            text_color "#ffffff"

    timer 0.01 action SetVariable("renforge_hit_sentinel_ready", True)
