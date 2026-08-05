# ── Lot 1.A — live editor toolbar ──────────────────────────────────────────
# Screen region for the top toolbar overlay. Preserves all widget ids, actions,
# sensitive conditions, and translation keys expected by live tests while
# updating visual presentation and layout.

screen _rf_editor_toolbar(tools_visible):
    $ _rf_facts = _renforge_editor_inspector_facts()
    $ _rf_screen = _rf_facts["screen"] if _rf_facts and _rf_facts.get("screen") else (getattr(_renforge_editor_state(), "selected_screen", "") or "")

    frame:
        id "rf_toolbar"
        xalign 0.5
        ypos _renforge_editor_ui_px(28)
        background Frame(_renforge_editor_ui_frame("panel"), 25, 25)
        padding (_renforge_editor_ui_px(20), _renforge_editor_ui_px(14))

        hbox:
            spacing _renforge_editor_ui_px(16)
            yalign 0.5

            # Brand logo block
            frame:
                id "rf_toolbar_brand_icon"
                xsize _renforge_editor_ui_px(44)
                ysize _renforge_editor_ui_px(44)
                background Solid(_renforge_editor_ui_color("accent"))
                padding (0, 0)
                yalign 0.5
                text "R":
                    id "rf_toolbar_brand_r"
                    color _renforge_editor_ui_color("accent_on")
                    font _renforge_editor_ui_font()
                    size _renforge_editor_ui_px(24)
                    xalign 0.5
                    yalign 0.5

            text "RENFORGE - LIVE":
                id "rf_toolbar_brand"
                color _renforge_editor_ui_color("surface")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_px(18)
                yalign 0.5

            # Selected screen badge
            if _rf_screen:
                frame:
                    id "rf_toolbar_screen_badge"
                    background Solid(_renforge_editor_ui_color("sunken"))
                    padding (_renforge_editor_ui_px(12), _renforge_editor_ui_px(6))
                    yalign 0.5
                    text (_renforge_editor_t("toolbar.screen") + " " + _rf_screen):
                        id "rf_toolbar_screen_text"
                        color _renforge_editor_ui_color("meta")
                        font _renforge_editor_ui_font()
                        size _renforge_editor_ui_px(16)
                        yalign 0.5

            # Flexible space
            null width 1 xfill True

            # Existing actions group in required order
            textbutton _renforge_editor_t("toolbar.exit"):
                id "rf_exit"
                action Function(_renforge_editor_consume, _renforge_editor_exit)
                sensitive not _renforge_editor_state().save_in_progress
                text_color _renforge_editor_ui_color("surface")
                text_font _renforge_editor_ui_font()
                text_size _renforge_editor_ui_px(17)
                yalign 0.5

            textbutton _renforge_editor_t("toolbar.undo"):
                id "rf_undo"
                action Function(_renforge_editor_consume, _renforge_editor_undo)
                sensitive _renforge_editor_can_undo()
                text_color _renforge_editor_ui_color("surface")
                text_font _renforge_editor_ui_font()
                text_size _renforge_editor_ui_px(17)
                yalign 0.5

            textbutton _renforge_editor_t("toolbar.redo"):
                id "rf_redo"
                action Function(_renforge_editor_consume, _renforge_editor_redo)
                sensitive _renforge_editor_can_redo()
                text_color _renforge_editor_ui_color("surface")
                text_font _renforge_editor_ui_font()
                text_size _renforge_editor_ui_px(17)
                yalign 0.5

            textbutton _renforge_editor_t("toolbar.reset"):
                id "rf_reset"
                action Function(_renforge_editor_consume, _renforge_editor_reset_selected)
                sensitive _renforge_editor_has_selection()
                text_color _renforge_editor_ui_color("surface")
                text_font _renforge_editor_ui_font()
                text_size _renforge_editor_ui_px(17)
                yalign 0.5

            textbutton (_renforge_editor_t("toolbar.tools_on") if tools_visible else _renforge_editor_t("toolbar.tools_off")):
                id "rf_tools"
                action Function(_renforge_editor_consume, _renforge_editor_toggle_tools)
                background Solid(_renforge_editor_ui_color("accent") if tools_visible else _renforge_editor_ui_color("panel_head"))
                padding (_renforge_editor_ui_px(12), _renforge_editor_ui_px(6))
                text_color _renforge_editor_ui_color("accent_on")
                text_font _renforge_editor_ui_font()
                text_size _renforge_editor_ui_px(17)
                yalign 0.5

            textbutton _renforge_editor_t("toolbar.opacity_down"):
                id "rf_opacity_down"
                action Function(_renforge_editor_consume, _renforge_editor_adjust_opacity, -0.1)
                text_color _renforge_editor_ui_color("surface")
                text_font _renforge_editor_ui_font()
                text_size _renforge_editor_ui_px(17)
                yalign 0.5

            textbutton _renforge_editor_t("toolbar.opacity_up"):
                id "rf_opacity_up"
                action Function(_renforge_editor_consume, _renforge_editor_adjust_opacity, 0.1)
                text_color _renforge_editor_ui_color("surface")
                text_font _renforge_editor_ui_font()
                text_size _renforge_editor_ui_px(17)
                yalign 0.5

            text _renforge_editor_status_text():
                id "rf_toolbar_status"
                color _renforge_editor_ui_color("meta")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_px(16)
                yalign 0.5
                xminimum _renforge_editor_ui_px(120)

            textbutton _renforge_editor_save_label():
                id "rf_save"
                action Function(_renforge_editor_consume, _renforge_editor_save)
                sensitive _renforge_editor_save_enabled()
                background Solid(_renforge_editor_ui_color("accent"))
                hover_background Solid(_renforge_editor_ui_color("accent_bright"))
                insensitive_background Solid(_renforge_editor_ui_color("sunken"))
                padding (_renforge_editor_ui_px(14), _renforge_editor_ui_px(6))
                text_color _renforge_editor_ui_color("accent_on")
                text_font _renforge_editor_ui_font()
                text_size _renforge_editor_ui_px(17)
                yalign 0.5

            if _renforge_editor_style_color_capable():
                textbutton _renforge_editor_style_color_label():
                    id "rf_style_color"
                    action Function(_renforge_editor_consume, _renforge_editor_cycle_style_color_preview)
                    sensitive not _renforge_editor_state().save_in_progress
                    background Solid(_renforge_editor_ui_color("accent"))
                    hover_background Solid(_renforge_editor_ui_color("accent_bright"))
                    padding (_renforge_editor_ui_px(14), _renforge_editor_ui_px(6))
                    text_color _renforge_editor_ui_color("accent_on")
                    text_font _renforge_editor_ui_font()
                    text_size _renforge_editor_ui_px(17)
                    yalign 0.5

            if _renforge_editor_selected_lock() is not None:
                text _renforge_editor_lock_headline():
                    id "rf_lock"
                    color _renforge_editor_lock_color()
                    font _renforge_editor_ui_font()
                    size _renforge_editor_ui_px(16)
                    yalign 0.5
