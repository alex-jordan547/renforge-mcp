# ── Lot 1.C — inspector ─────────────────────────────────────────────────────
# Identity, source location and geometry for the current selection, plus the
# refusal when there is one. Anchored to the right edge rather than to a fixed
# x, so it stays on screen whatever width the game runs at.
screen _rf_editor_inspector():

    $ _rf_facts = _renforge_editor_inspector_facts()
    $ _rf_props_by_widget = _renforge_editor_widget_properties(_rf_facts["screen"]) if _rf_facts is not None else {}
    $ _rf_props = _rf_props_by_widget.get(_rf_facts["id"], {}) if _rf_facts is not None else {}
    $ _rf_docked = _renforge_editor_layout_mode() == "docked"

    if _rf_facts is not None:
        frame:
            id "rf_inspector_panel"
            xalign 1.0
            xoffset (0 if _rf_docked else -_renforge_editor_ui_px(32))
            ypos (_renforge_editor_ui_px(124) if _rf_docked else _renforge_editor_ui_px(148))
            xsize _renforge_editor_ui_px(540)
            ysize (_renforge_editor_ui_px(624) if _rf_docked else None)
            background (Solid(_renforge_editor_ui_color("panel")) if _rf_docked else Frame(_renforge_editor_ui_frame("panel"), 25, 25))
            padding (0, 0)

            vbox:
                xfill True
                spacing 0

                frame:
                    xfill True
                    background Frame(_renforge_editor_ui_frame("panel_head"), 25, 25, 25, 2)
                    padding (_renforge_editor_ui_px(20), _renforge_editor_ui_px(12))
                    vbox:
                        spacing _renforge_editor_ui_px(4)
                        text _rf_facts["id"]:
                            id "rf_inspector_name"
                            color _renforge_editor_ui_color("surface")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(24)
                        text (
                            ("screen " + _rf_facts["screen"] if _rf_facts["screen"] else "")
                            + ("  ·  " + _rf_facts["source"] if _rf_facts["source"] else "")
                        ):
                            id "rf_inspector_path"
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(17)

                vbox:
                    xfill True
                    spacing _renforge_editor_ui_px(10)
                    xoffset _renforge_editor_ui_px(20)
                    yoffset _renforge_editor_ui_px(14)

                    if _rf_facts["rect"] is not None:
                        text _renforge_editor_t("inspector.position"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                        hbox:
                            spacing _renforge_editor_ui_px(24)
                            use _rf_editor_field("xpos", str(_rf_facts["rect"]["x"]))
                            use _rf_editor_field("ypos", str(_rf_facts["rect"]["y"]))

                        text _renforge_editor_t("inspector.size"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(8)
                        hbox:
                            spacing _renforge_editor_ui_px(24)
                            use _rf_editor_field("xsize", str(_rf_facts["rect"]["w"]))
                            use _rf_editor_field("ysize", str(_rf_facts["rect"]["h"]))

                        text _renforge_editor_t("inspector.offset"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(8)
                        hbox:
                            spacing _renforge_editor_ui_px(24)
                            use _rf_editor_field("xoffset", str(_rf_props.get("xoffset", "0")))
                            use _rf_editor_field("yoffset", str(_rf_props.get("yoffset", "0")))

                        text _renforge_editor_t("inspector.anchor"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(8)
                        hbox:
                            spacing _renforge_editor_ui_px(24)
                            use _rf_editor_field("xanchor", str(_rf_props.get("xanchor", "0")))
                            use _rf_editor_field("yanchor", str(_rf_props.get("yanchor", "0")))

                        text _renforge_editor_t("inspector.fill"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(8)
                        hbox:
                            spacing _renforge_editor_ui_px(24)
                            use _rf_editor_field("xfill", str(_rf_props.get("xfill", "false")))
                            use _rf_editor_field("yfill", str(_rf_props.get("yfill", "false")))
                    else:
                        text _renforge_editor_t("inspector.no_geometry"):
                            color _renforge_editor_ui_color("meta")
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(17)

                    # A refusal is stated where the values are, not hidden in a
                    # status line the user has to go looking for.
                    if _rf_facts["lock"] is not None:
                        text (
                            _renforge_editor_t("lock.%s" % _rf_facts["lock"][0])
                            + " — " + (_rf_facts["lock"][2] or _rf_facts["lock"][1])
                        ):
                            id "rf_inspector_lock"
                            color _renforge_editor_lock_color()
                            font _renforge_editor_ui_font()
                            size _renforge_editor_ui_px(16)
                            yoffset _renforge_editor_ui_px(10)
                            xmaximum _renforge_editor_ui_px(480)

                null height _renforge_editor_ui_px(28)


# A labelled read-only value. Editing lands in a later slice; showing the value
# with its name is what makes the tree selection mean anything today.
screen _rf_editor_field(key, value):
    hbox:
        spacing _renforge_editor_ui_px(10)
        text key:
            color _renforge_editor_ui_color("meta")
            font _renforge_editor_ui_font()
            size _renforge_editor_ui_px(18)
            yalign 0.5
        text value:
            color _renforge_editor_ui_color("surface")
            font _renforge_editor_ui_font()
            size _renforge_editor_ui_px(20)
            yalign 0.5
