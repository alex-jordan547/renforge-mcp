# ── Lot 1.E — status band ───────────────────────────────────────────────────
# Horizontal status band anchored below the toolbar. Displays live hot-reload
# indicator, count of pending unwritten changes, and active selection.
screen _rf_editor_hud():
    $ _rf_state = _renforge_editor_state()
    $ _rf_facts = _renforge_editor_inspector_facts()
    $ _rf_pending_count = len(getattr(_rf_state, "history_entries", None) or [])
    $ _rf_pending_text = str(_rf_pending_count) + " " + _renforge_editor_t("hud.pending")
    $ _rf_selected_id = _rf_facts["id"] if (_rf_facts is not None and _rf_facts.get("id")) else None
    $ _rf_selection_text = (_renforge_editor_t("hud.selection") + " " + str(_rf_selected_id)) if _rf_selected_id else _renforge_editor_t("hud.none")
    $ _rf_docked = _renforge_editor_layout_mode() == "docked"

    frame:
        id "rf_hud_band"
        xpos (_renforge_editor_ui_px(576) if _rf_docked else 0.5)
        xanchor (0.0 if _rf_docked else 0.5)
        ypos _renforge_editor_ui_px(148)
        xsize _renforge_editor_ui_px(1408 if _rf_docked else 1180)
        background (Solid(_renforge_editor_ui_color("panel")) if _rf_docked else Frame(_renforge_editor_ui_frame("panel"), 25, 25))
        padding (_renforge_editor_ui_px(20), _renforge_editor_ui_px(12))

        hbox:
            xalign 0.5
            yalign 0.5
            spacing _renforge_editor_ui_px(16)

            add Solid(_renforge_editor_ui_color("accent_bright"), xysize=(_renforge_editor_ui_px(12), _renforge_editor_ui_px(12))):
                id "rf_hud_dot"
                yalign 0.5

            text _renforge_editor_t("hud.reload"):
                id "rf_hud_reload"
                color _renforge_editor_ui_color("surface")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_px(18)
                yalign 0.5

            add Solid(_renforge_editor_ui_color("hairline"), xysize=(_renforge_editor_ui_px(1), _renforge_editor_ui_px(18))):
                id "rf_hud_sep1"
                yalign 0.5

            text _rf_pending_text:
                id "rf_hud_pending"
                color _renforge_editor_ui_color("surface")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_px(18)
                yalign 0.5

            add Solid(_renforge_editor_ui_color("hairline"), xysize=(_renforge_editor_ui_px(1), _renforge_editor_ui_px(18))):
                id "rf_hud_sep2"
                yalign 0.5

            text _rf_selection_text:
                id "rf_hud_selection"
                color _renforge_editor_ui_color("surface")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_px(18)
                yalign 0.5
