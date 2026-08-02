# Spike fixture for issue #48 — rotate-aware geometry and paint-separability proof.
# Intent: one focusable rotated control, one same-shape unrotated control,
# and one additional focusable control for ambiguity sanity.
default renforge_rotation_isolation = "all"

default renforge_rotation_target_fill = "#e5c82e"
default renforge_rotation_reference_fill = "#2e6be5"
default renforge_rotation_other_fill = "#2ee5a0"

default renforge_rotation_font_color = "#f4f4f5"

default renforge_rotation_target_text = "ROTATED"
default renforge_rotation_reference_text = "REFERENCE"
default renforge_rotation_other_text = "OTHER"

default renforge_rotation_target_size = 140
default renforge_rotation_target_depth = 80

default renforge_rotation_target_x = 220
default renforge_rotation_target_y = 220

default renforge_rotation_reference_x = 420

default renforge_rotation_reference_y = 220

default renforge_rotation_other_x = 220

default renforge_rotation_other_y = 430

screen renforge_editor_rotation_fixture():
    layer "screens"
    zorder 640

    fixed:
        id "rotation_root"
        xfill True
        yfill True

        # Dark stage to make painted-mask checks stable.
        add Solid("#0a0a0a", xysize=(1280, 720)):
            xpos 0
            ypos 0

        # Focusable rotated target (same declared shape as reference).
        if renforge_rotation_isolation in ("all", "rotation_target"):
            button id "rotation_target" xpos 220 ypos 220 xsize 160 ysize renforge_rotation_target_depth:
                action NullAction()
                add Transform(
                    Solid(renforge_rotation_target_fill, xysize=(80, 36)),
                    rotate=15,
                    xalign=0.5,
                    yalign=0.5,
                )

        # Same-shape unrotated control (same button shape).
        if renforge_rotation_isolation in ("all", "rotation_reference"):
            button id "rotation_reference" xpos 420 ypos 220 xsize 160 ysize renforge_rotation_target_depth:
                action NullAction()
                background Solid(renforge_rotation_reference_fill)
                text str(renforge_rotation_reference_text):
                    color renforge_rotation_font_color
                    size 18

        # Additional same-depth focusable control for control-plane sanity.
        if renforge_rotation_isolation in ("all", "rotation_other"):
            button id "rotation_other" xpos 220 ypos 430 xsize 160 ysize renforge_rotation_target_depth:
                action NullAction()
                background Solid(renforge_rotation_other_fill)
                text str(renforge_rotation_other_text):
                    color renforge_rotation_font_color
                    size 18
