"""Runtime interaction and control MCP tools."""

from __future__ import annotations

from typing import Any

TOOL_NAMES = (
    "renforge_advance",
    "renforge_control",
    "renforge_send_input",
    "renforge_saves",
    "renforge_list_choices",
    "renforge_select_choice",
    "renforge_list_ui_elements",
    "renforge_click_element",
    "renforge_hover_element",
    "renforge_get_ui_element_bounds",
    "renforge_click_at",
    "renforge_get_displayable_bounds",
    "renforge_position_element",
    "renforge_hit_test",
)


def build_wrappers(context):
    live = context.live
    _log_tool_call = context.log_tool_call

    def renforge_advance(project_path: str) -> dict:
        """Advance the current dialogue."""
        return _log_tool_call(
            name="renforge_advance",
            params={"project_path": project_path},
            project_root=project_path,
            fn=live.advance,
            args=(project_path,),
            kwargs={},
        )


    def renforge_control(
        project_path: str,
        action: str,
        interaction_id: str = "",
        wait_for_effect: bool = False,
        effect_timeout: float = 5.0,
        authorize: bool = False,
    ) -> dict:
        """Run a runtime action: advance, rollback, toggle_skip, toggle_auto,
        toggle_afm, game_menu, hide_windows, quick_save, quick_load,
        reload_script, restart_interaction, or quit.

        Emits correlated business events (quick_save.completed, skip.stopped,
        …). Set wait_for_effect=true to block until the matching event appears.
        Destructive actions require authorize=true when RENFORGE_POLICY=enforce.
        """
        return _log_tool_call(
            name="renforge_control",
            params={
                "project_path": project_path,
                "action": action,
                "interaction_id": interaction_id,
                "wait_for_effect": wait_for_effect,
                "effect_timeout": effect_timeout,
                "authorize": authorize,
            },
            project_root=project_path,
            fn=live.control,
            args=(project_path, action),
            kwargs={
                "interaction_id": interaction_id or None,
                "wait_for_effect": wait_for_effect,
                "effect_timeout": effect_timeout,
            },
        )


    def renforge_send_input(
        project_path: str,
        text: str | None = None,
        key: str | None = None,
        scroll: dict[str, Any] | None = None,
        submit: bool = False,
    ) -> dict:
        """Send exactly one input mode: text, named key, or scroll object.

        ``text`` posts character-by-character TEXTINPUT events to a focused
        Ren'Py Input; ``submit`` optionally presses Enter after the text.
        ``key`` accepts readable names such as enter, esc, arrows, pageup,
        pagedown, backspace, delete, home, end, space, tab, and function keys.
        ``scroll`` is ``{"x": ..., "y": ..., "direction": "up"|"down"}``
        in logical game coordinates, with optional integer ``amount``.
        Exactly one of text, key, and scroll must be supplied.
        """
        return _log_tool_call(
            name="renforge_send_input",
            params={
                "project_path": project_path,
                "text": text,
                "key": key,
                "scroll": scroll,
                "submit": submit,
            },
            project_root=project_path,
            fn=live.send_input,
            args=(project_path,),
            kwargs={
                "text": text,
                "key": key,
                "scroll": scroll,
                "submit": submit,
            },
        )


    def renforge_saves(
        project_path: str,
        action: str,
        slot: str | None = None,
        extra_info: str | None = None,
        regexp: str | None = None,
        authorize: bool = False,
    ) -> dict:
        """Save, load, or list named save slots without screenshot payloads.

        ``load`` requires authorize=true when RENFORGE_POLICY=enforce.
        """
        return _log_tool_call(
            name="renforge_saves",
            params={
                "project_path": project_path,
                "action": action,
                "slot": slot,
                "extra_info": extra_info,
                "regexp": regexp,
                "authorize": authorize,
            },
            project_root=project_path,
            fn=live.saves,
            args=(project_path, action),
            kwargs={"slot": slot, "extra_info": extra_info, "regexp": regexp},
        )


    def renforge_list_choices(project_path: str) -> dict:
        """List the on-screen menu choices (text + index)."""
        return _log_tool_call(
            name="renforge_list_choices",
            params={"project_path": project_path},
            project_root=project_path,
            fn=live.list_choices,
            args=(project_path,),
            kwargs={},
        )


    def renforge_select_choice(
        project_path: str,
        text: str | None = None,
        index: int | None = None,
    ) -> dict:
        """Select a menu choice by visible text (preferred) or by index."""
        return _log_tool_call(
            name="renforge_select_choice",
            params={"project_path": project_path, "text": text, "index": index},
            project_root=project_path,
            fn=live.select_choice,
            args=(project_path,),
            kwargs={
                "text": text or None,
                "index": index if isinstance(index, int) and index >= 0 else None,
            },
        )


    def renforge_list_ui_elements(
        project_path: str,
        screen: str = "",
        text: str = "",
        element_type: str = "",
    ) -> dict:
        """List visible focusable Ren'Py controls with bounds and frame guard."""
        return _log_tool_call(
            name="renforge_list_ui_elements",
            params={
                "project_path": project_path,
                "screen": screen,
                "text": text,
                "element_type": element_type,
            },
            project_root=project_path,
            fn=live.list_ui_elements,
            args=(project_path,),
            kwargs={
                "screen": screen or None,
                "text": text or None,
                "element_type": element_type or None,
            },
        )


    def renforge_click_element(
        project_path: str,
        text: str = "",
        element_id: str = "",
        screen: str = "",
        exact: bool = False,
        expected_frame_id: str = "",
        interaction_id: str = "",
        wait_for_effect: bool = False,
        effect_timeout: float = 5.0,
    ) -> dict:
        """Click a visible control by text/id, guarded against a stale frame.

        Returns received_by when another control owns the hit point. With
        wait_for_effect=true, wait for a correlated business event such as
        quick_save.completed when the clicked action is recognizable.
        """
        return _log_tool_call(
            name="renforge_click_element",
            params={
                "project_path": project_path,
                "text": text,
                "element_id": element_id,
                "screen": screen,
                "exact": exact,
                "expected_frame_id": expected_frame_id,
                "interaction_id": interaction_id,
                "wait_for_effect": wait_for_effect,
                "effect_timeout": effect_timeout,
            },
            project_root=project_path,
            fn=live.click_element,
            args=(project_path,),
            kwargs={
                "text": text or None,
                "element_id": element_id or None,
                "screen": screen or None,
                "exact": exact,
                "expected_frame_id": expected_frame_id or None,
                "interaction_id": interaction_id or None,
                "wait_for_effect": wait_for_effect,
                "effect_timeout": effect_timeout,
            },
        )


    def renforge_hover_element(
        project_path: str,
        text: str = "",
        element_id: str = "",
        screen: str = "",
        exact: bool = False,
        expected_frame_id: str = "",
    ) -> dict:
        """Move the pointer over a visible control without clicking it."""
        return _log_tool_call(
            name="renforge_hover_element",
            params={
                "project_path": project_path,
                "text": text,
                "element_id": element_id,
                "screen": screen,
                "exact": exact,
                "expected_frame_id": expected_frame_id,
            },
            project_root=project_path,
            fn=live.hover_element,
            args=(project_path,),
            kwargs={
                "text": text or None,
                "element_id": element_id or None,
                "screen": screen or None,
                "exact": exact,
                "expected_frame_id": expected_frame_id or None,
            },
        )


    def renforge_get_ui_element_bounds(
        project_path: str,
        text: str = "",
        element_id: str = "",
        screen: str = "",
        exact: bool = False,
        expected_frame_id: str = "",
    ) -> dict:
        """Report focus bounds and rendered painted bounds for a UI element."""
        return _log_tool_call(
            name="renforge_get_ui_element_bounds",
            params={
                "project_path": project_path,
                "text": text,
                "element_id": element_id,
                "screen": screen,
                "exact": exact,
                "expected_frame_id": expected_frame_id,
            },
            project_root=project_path,
            fn=live.get_ui_element_bounds,
            args=(project_path,),
            kwargs={
                "text": text or None,
                "element_id": element_id or None,
                "screen": screen or None,
                "exact": exact,
                "expected_frame_id": expected_frame_id or None,
            },
        )


    def renforge_click_at(
        project_path: str,
        x: float,
        y: float,
        expected_frame_id: str = "",
        expected_state: dict[str, Any] | None = None,
        coordinate_space: str = "logical",
    ) -> dict:
        """Click screen coordinates with optional frame/state safety guards."""
        return _log_tool_call(
            name="renforge_click_at",
            params={
                "project_path": project_path,
                "x": x,
                "y": y,
                "expected_frame_id": expected_frame_id,
                "expected_state": expected_state,
                "coordinate_space": coordinate_space,
            },
            project_root=project_path,
            fn=live.click_at,
            args=(project_path, x, y),
            kwargs={
                "expected_frame_id": expected_frame_id or None,
                "expected_state": expected_state,
                "coordinate_space": coordinate_space,
            },
        )


    def renforge_get_displayable_bounds(
        project_path: str,
        tag: str,
        layer: str = "",
    ) -> dict:
        """Report where a shown image tag was rendered, in logical coordinates."""
        return _log_tool_call(
            name="renforge_get_displayable_bounds",
            params={"project_path": project_path, "tag": tag, "layer": layer},
            project_root=project_path,
            fn=live.get_displayable_bounds,
            args=(project_path, tag),
            kwargs={"layer": layer or None},
        )


    def renforge_position_element(
        project_path: str,
        tag: str,
        xpos: int | float | None = None,
        ypos: int | float | None = None,
        xanchor: int | float | None = None,
        yanchor: int | float | None = None,
        xalign: int | float | None = None,
        yalign: int | float | None = None,
        xoffset: int | float | None = None,
        yoffset: int | float | None = None,
        zoom: float | None = None,
        rotate: float | None = None,
        layer: str = "",
    ) -> dict:
        """Reposition a shown image tag live and return its new logical bounds.

        Provide at least one placement field. The tag keeps its current
        attributes. Positions follow Ren'Py's rule: an integer is absolute
        pixels (``xpos=600`` is 600px), a float is a fraction of the screen
        (``xpos=0.5`` is the centre). Use this to converge on coordinates
        interactively, then write the final values into the ``.rpy`` script.
        """
        placement = {
            "xpos": xpos,
            "ypos": ypos,
            "xanchor": xanchor,
            "yanchor": yanchor,
            "xalign": xalign,
            "yalign": yalign,
            "xoffset": xoffset,
            "yoffset": yoffset,
            "zoom": zoom,
            "rotate": rotate,
        }
        placement = {key: value for key, value in placement.items() if value is not None}
        return _log_tool_call(
            name="renforge_position_element",
            params={"project_path": project_path, "tag": tag, "layer": layer, **placement},
            project_root=project_path,
            fn=live.position_element,
            args=(project_path, tag),
            kwargs={"layer": layer or None, **placement},
        )


    def renforge_hit_test(
        project_path: str,
        x: float,
        y: float,
        coordinate_space: str = "logical",
    ) -> dict:
        """Inspect the interactive focus stack at a coordinate.

        Returns topmost and underneath focusable controls so agents can detect
        transparent overlays that intercept clicks.
        """
        return _log_tool_call(
            name="renforge_hit_test",
            params={
                "project_path": project_path,
                "x": x,
                "y": y,
                "coordinate_space": coordinate_space,
            },
            project_root=project_path,
            fn=live.hit_test,
            args=(project_path, x, y),
            kwargs={"coordinate_space": coordinate_space},
        )


    return {name: value for name, value in locals().items() if name in TOOL_NAMES}


def register(registrar, wrappers) -> None:
    registrar.register_many(wrappers, TOOL_NAMES)
