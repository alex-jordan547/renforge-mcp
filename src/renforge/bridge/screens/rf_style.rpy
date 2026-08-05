# ── Lot 1.D — Ren'Py style editor ───────────────────────────────────────────
# Panel anchored bottom-right showing current style selection facts, text color,
# and lock/refusal state.
screen _rf_editor_style():

    $ _rf_facts = _renforge_editor_inspector_facts()

    if _rf_facts is not None:
        frame:
            id "rf_style_panel"
            xalign 1.0
            xoffset -_renforge_editor_ui_px(32)
            ypos _renforge_editor_ui_px(800)
            xsize _renforge_editor_ui_px(540)
            background Frame(_renforge_editor_ui_frame("panel"), 25, 25)
            padding (0, 0)

            vbox:
                xfill True
                spacing 0

                frame:
                    xfill True
                    background Frame(_renforge_editor_ui_frame("panel_head"), 25, 25, 25, 2)
                    padding (_renforge_editor_ui_px(20), _renforge_editor_ui_px(12))
                    hbox:
                        xfill True
                        yalign 0.5
                        text _renforge_editor_t("style.title"):
                            id "rf_style_title"
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(18)
                            yalign 0.5
                        null width 1 xfill True
                        text _rf_facts["id"]:
                            id "rf_style_name"
                            color _renforge_editor_ui_color("surface")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(18)
                            yalign 0.5

                vbox:
                    xfill True
                    spacing _renforge_editor_ui_px(10)
                    xoffset _renforge_editor_ui_px(20)
                    yoffset _renforge_editor_ui_px(14)

                    if _renforge_editor_style_color_capable():
                        text _renforge_editor_style_color_label():
                            id "rf_style_color_value"
                            color _renforge_editor_ui_color("surface")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(18)

                    if _rf_facts["lock"] is not None:
                        text (
                            _renforge_editor_t("lock.%s" % _rf_facts["lock"][0])
                            + " — " + (_rf_facts["lock"][2] or _rf_facts["lock"][1])
                        ):
                            id "rf_style_lock"
                            color _renforge_editor_lock_color()
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(10)
                            xmaximum _renforge_editor_ui_px(480)

                null height _renforge_editor_ui_px(28)
