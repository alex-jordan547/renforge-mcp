# ── Lot 1.E — status band ───────────────────────────────────────────────────
# Overlay: maquette left 536 / top 148 / 1180×56 / r-sm.
# Docked: band between rails under the toolbar.
screen _rf_editor_hud():
    $ _rf_state = _renforge_editor_state()
    $ _rf_facts = _renforge_editor_inspector_facts()
    $ _rf_pending_count = len(_renforge_editor_collect_intents() or [])
    $ _rf_pending_text = _renforge_editor_t("hud.pending").replace("{count}", str(_rf_pending_count))
    $ _rf_selected_id = _rf_facts["id"] if (_rf_facts is not None and _rf_facts.get("id")) else None
    $ _rf_selection_text = (_renforge_editor_t("hud.selection").replace("{value}", str(_rf_selected_id))) if _rf_selected_id else _renforge_editor_t("hud.none")
    $ _rf_docked = _renforge_editor_chrome_docked()
    $ _rf_status = _renforge_editor_status_text()
    $ _rf_reload_failed = bool(getattr(_rf_state, "save_error", None) or getattr(_rf_state, "save_last_error", None)) and not bool(getattr(_rf_state, "save_in_progress", False))
    $ _rf_reloading = bool(getattr(_rf_state, "save_in_progress", False) or getattr(_rf_state, "pending_reload_requested", False) or getattr(_rf_state, "pending_handshake_sent", False))
    $ _rf_reload_key = ("reload.failed" if _rf_reload_failed else ("reload.reloading" if _rf_reloading else "reload.active"))
    $ _rf_reload_color = (
        _renforge_editor_ui_color("lock_refused") if _rf_reload_failed
        else (_renforge_editor_ui_color("warn") if _rf_reloading else _renforge_editor_ui_color("accent_bright"))
    )
    # Expire transient status notices. Must return None (see status_tick):
    # Function(non-None) ends the interaction and advances dialogue under us.
    timer 0.25 repeat True action Function(_renforge_editor_status_tick)

    frame:
        id "rf_hud_band"
        if _rf_docked:
            xpos _renforge_editor_ui_px(_RF_DOCK_HUD_X)
            xanchor 0.0
            ypos _renforge_editor_ui_px(_RF_DOCK_HUD_Y)
            xsize _renforge_editor_ui_px(_RF_DOCK_HUD_W)
            background Solid(_renforge_editor_ui_color("panel"))
        else:
            xpos _renforge_editor_ui_px(_RF_OVERLAY_HUD_X)
            xanchor 0.0
            ypos _renforge_editor_ui_px(_RF_OVERLAY_HUD_Y)
            xsize _renforge_editor_ui_px(_RF_OVERLAY_HUD_W)
            ysize _renforge_editor_ui_px(_RF_OVERLAY_HUD_H)
            background Frame(_renforge_editor_ui_frame("chip"), _RF_FRAME_CHIP, _RF_FRAME_CHIP)
        padding (_renforge_editor_ui_px(_RF_S4), 0)

        hbox:
            yalign 0.5
            spacing _renforge_editor_ui_px(_RF_S3)

            add Solid(_rf_reload_color, xysize=(_renforge_editor_ui_px(12), _renforge_editor_ui_px(12))):
                id "rf_hud_dot"
                yalign 0.5

            text _renforge_editor_t(_rf_reload_key):
                id "rf_hud_reload"
                color _renforge_editor_ui_color("surface")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_text_px(_RF_T_MICRO)
                yalign 0.5

            add Solid(_renforge_editor_ui_color("hairline"), xysize=(1, _renforge_editor_ui_px(24))):
                id "rf_hud_sep0"
                yalign 0.5

            text _rf_status:
                id "rf_hud_status"
                substitute False
                color _renforge_editor_ui_color("meta")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_text_px(_RF_T_MICRO)
                yalign 0.5

            add Solid(_renforge_editor_ui_color("hairline"), xysize=(1, _renforge_editor_ui_px(24))):
                id "rf_hud_sep1"
                yalign 0.5

            text _rf_pending_text:
                id "rf_hud_pending"
                substitute False
                color _renforge_editor_ui_color("meta")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_text_px(_RF_T_MICRO)
                yalign 0.5

            add Solid(_renforge_editor_ui_color("hairline"), xysize=(1, _renforge_editor_ui_px(24))):
                id "rf_hud_sep2"
                yalign 0.5

            text _rf_selection_text:
                id "rf_hud_selection"
                substitute False
                color _renforge_editor_ui_color("meta")
                font _renforge_editor_ui_font()
                size _renforge_editor_ui_text_px(_RF_T_MICRO)
                yalign 0.5
