"""RenForge TCP bridge client primitives."""

from __future__ import annotations

import base64
import hashlib
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from renforge.util.frames import (
    BRIDGE_REQUEST_MAX_BYTES,
    BRIDGE_RESPONSE_MAX_BYTES,
    BRIDGE_WRITE_DEADLINE_SECONDS,
    FRAME_TOO_LARGE,
    FRAME_TIMEOUT,
    RESPONSE_TOO_LARGE,
    TRUNCATED_FRAME,
    FrameError,
    decode_json_object,
    encode_json_line,
    recv_until_newline,
    remaining_timeout,
    send_all_deadline,
)


class BridgeError(RuntimeError):
    """Base error for bridge client failures."""


class BridgeProtocolError(BridgeError):
    """Raised when the bridge response is malformed or invalid."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class BridgeConfig:
    host: str = "127.0.0.1"
    port: int = 0
    token: str = field(default="", repr=False)
    timeout: float = 5.0


class BridgeClient:
    """Client speaking one-request-per-connection newline-delimited JSON."""

    def __init__(self, config: BridgeConfig):
        self._config = config

    @classmethod
    def from_project(cls, project_root: str | Path, *, timeout: float = 5.0) -> "BridgeClient":
        """Build a client from private control-directory bridge metadata.

        The running bridge publishes host/port/token under
        ``<project_root>/.renforge/control/bridge.json`` on startup.
        """
        # Local import avoids the cycle: control.py imports BridgeProtocolError.
        from renforge.bridge.control import read_bridge_info

        info = read_bridge_info(Path(project_root), require_ready=True)
        return cls(
            BridgeConfig(
                host=info.host,
                port=info.port,
                token=info.token,
                timeout=timeout,
            )
        )

    def request(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        *,
        deadline: float | None = None,
    ) -> dict:
        body = {
            "token": self._config.token,
            "command": command,
            "payload": payload,
        }
        absolute_deadline = (
            float(deadline)
            if deadline is not None
            else time.monotonic() + max(0.001, float(self._config.timeout))
        )
        try:
            payload_bytes = encode_json_line(body)
        except (TypeError, ValueError) as exc:
            raise BridgeProtocolError("bridge request is not JSON-serializable") from exc
        if len(payload_bytes) > BRIDGE_REQUEST_MAX_BYTES:
            raise BridgeProtocolError(
                f"bridge request exceeds {BRIDGE_REQUEST_MAX_BYTES} bytes",
                code=FRAME_TOO_LARGE,
            )

        try:
            connect_timeout = remaining_timeout(absolute_deadline)
            with socket.create_connection(
                (self._config.host, self._config.port),
                timeout=connect_timeout,
            ) as sock:
                write_deadline = min(
                    absolute_deadline,
                    time.monotonic() + BRIDGE_WRITE_DEADLINE_SECONDS,
                )
                send_all_deadline(sock, payload_bytes, deadline=write_deadline)
                line = recv_until_newline(
                    sock,
                    max_bytes=BRIDGE_RESPONSE_MAX_BYTES,
                    deadline=absolute_deadline,
                )
                response = decode_json_object(line)
        except FrameError as exc:
            code = exc.code
            if code == FRAME_TOO_LARGE:
                code = RESPONSE_TOO_LARGE if "response" in exc.message else FRAME_TOO_LARGE
            if code == FRAME_TOO_LARGE and len(payload_bytes) <= BRIDGE_REQUEST_MAX_BYTES:
                code = RESPONSE_TOO_LARGE
            raise BridgeProtocolError(exc.message, code=code) from exc
        except OSError as exc:
            raise BridgeError(f"bridge connection failed: {exc}") from exc

        return response

    def _checked(self, command: str, payload: dict[str, Any] | None = None) -> dict:
        reply = self.request(command, payload)
        if reply.get("error") is not None:
            raise BridgeError(f"bridge error on '{command}': {reply['error']}")
        return reply

    @staticmethod
    def _normalize_error_reply(reply: dict) -> dict:
        if reply.get("error") is None or reply.get("ok") is False:
            return reply
        result = dict(reply)
        result["ok"] = False
        return result

    def ping(self) -> dict:
        return self.request("ping")

    def get_state(
        self,
        include: list[str] | tuple[str, ...] | None = None,
        *,
        state_profile: str | None = None,
        variables: list[str] | tuple[str, ...] | None = None,
    ) -> dict:
        """Return live state, with optional compact metrics/audio sections.

        ``state_profile`` may be ``minimal``, ``interaction``, ``debug``, or
        ``full`` (default on the bridge). ``variables`` selects additional store
        paths when the profile is not full.
        """
        payload: dict[str, Any] = {}
        if include is not None:
            payload["include"] = list(include)
        if state_profile is not None:
            payload["state_profile"] = state_profile
        if variables is not None:
            payload["variables"] = list(variables)
        return self._checked("get_state", payload or None)

    def get_metrics(self) -> dict:
        """Return render, image-cache, and logical/physical window metrics."""
        return self._checked("get_metrics")

    def get_audio_state(self) -> dict:
        """Return the current file, volume, and pause state for each channel."""
        return self._checked("get_audio_state")

    def inspect_screen(self, name: str) -> dict:
        """Inspect an active screen's layer, scope, and passed arguments."""
        reply = self.request("inspect_screen", {"name": name})
        if reply.get("error") is not None and reply.get("active") is not False:
            result = dict(reply)
            result["ok"] = False
            return result
        return reply

    def eval_expr(self, expr: str) -> Any:
        return self._checked("eval", {"expr": expr})["value"]

    def get_var(self, name: str) -> Any:
        return self._checked("get_var", {"name": name})["value"]

    def set_var(self, name: str, value: Any) -> dict:
        return self._checked("set_var", {"name": name, "value": value})

    def screenshot(self, width: int = 0, height: int = 0) -> bytes:
        """Return the current game frame as PNG bytes."""
        reply = self._checked("screenshot", {"width": width, "height": height})
        encoded = reply.get("base64")
        if not encoded:
            raise BridgeProtocolError("screenshot reply missing 'base64' data")
        return base64.b64decode(encoded)

    def screenshot_hash(self, width: int = 0, height: int = 0) -> str:
        """Return a SHA-256 fingerprint of the current game frame.

        The bridge includes the fingerprint in newer screenshot replies.  For
        older injected bridges (which only return ``base64``), it is computed
        locally so callers can still use it as a click guard.
        """
        reply = self._checked("screenshot", {"width": width, "height": height})
        encoded = reply.get("base64")
        if not encoded:
            raise BridgeProtocolError("screenshot reply missing 'base64' data")
        digest = reply.get("sha256")
        if isinstance(digest, str) and digest:
            return digest
        try:
            data = base64.b64decode(encoded)
        except (ValueError, TypeError) as exc:
            raise BridgeProtocolError("screenshot reply has invalid 'base64' data") from exc
        return hashlib.sha256(data).hexdigest()

    def advance(self) -> dict:
        """Advance the current dialogue (posts a 'dismiss' event)."""
        return self._checked("advance")

    def control(
        self,
        action: str,
        *,
        interaction_id: str | None = None,
    ) -> dict:
        """Run a named runtime control action inside the Ren'Py bridge."""
        payload: dict[str, Any] = {"action": action}
        if interaction_id is not None:
            payload["interaction_id"] = interaction_id
        reply = self.request("control", payload)
        if reply.get("error") is not None:
            result = dict(reply)
            result["ok"] = False
            return result
        return reply

    def send_input(
        self,
        *,
        text: str | None = None,
        key: str | None = None,
        scroll: dict[str, Any] | None = None,
        drag: dict[str, Any] | None = None,
        submit: bool = False,
    ) -> dict:
        """Send exactly one text, named-key, scroll, or drag input operation."""
        payload: dict[str, Any] = {
            "text": text,
            "key": key,
            "scroll": scroll,
            "drag": drag,
            "submit": bool(submit),
        }
        # Keep omitted optional modes out of the wire payload so callers can
        # distinguish an explicit empty text operation from a missing mode.
        payload = {name: value for name, value in payload.items() if value is not None}
        reply = self.request("send_input", payload)
        if reply.get("error") is not None:
            result = dict(reply)
            result["ok"] = False
            return result
        return reply

    def save_slot(self, slot: str, *, extra_info: str = "") -> dict:
        """Save the current game state under a named slot."""
        return self._normalize_error_reply(
            self.request("save_slot", {"slot": slot, "extra_info": extra_info})
        )

    def load_slot(self, slot: str) -> dict:
        """Schedule loading a named save slot inside the Ren'Py main loop."""
        return self._normalize_error_reply(self.request("load_slot", {"slot": slot}))

    def list_slots(self, *, regexp: str | None = None) -> dict:
        """Return named save slots with compact metadata and no screenshots."""
        return self._normalize_error_reply(self.request("list_slots", {"regexp": regexp}))

    def poll_events(self, since: int = 0) -> dict:
        """Return pushed events with ``seq > since`` plus the current cursor.

        Reply shape: ``{"events": [...], "cursor": <int>}``.
        """
        return self._checked("poll_events", {"since": since})

    def list_choices(self) -> list[dict[str, Any]]:
        """Return the on-screen focusable choices as ``[{"index", "text"}, ...]``."""
        return self._checked("list_choices")["choices"]

    def select_choice(self, text: str | None = None, index: int | None = None) -> dict:
        """Select a menu option by visible text (preferred) or by index."""
        return self._checked("select_choice", {"text": text, "index": index})

    def list_ui_elements(
        self,
        *,
        screen: str | None = None,
        text: str | None = None,
        element_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return visible focusable controls and their screen-space bounds.

        Each element has a stable-for-the-current-frame ``id``, optional
        ``text``, ``type``/``role``, ``screen``, ``enabled``, and a ``bounds``
        object containing integer ``x``, ``y``, ``width`` and ``height``.
        Optional filters are applied by the bridge before the response is
        returned.
        """
        return self.list_ui_elements_info(
            screen=screen,
            text=text,
            element_type=element_type,
        )["elements"]

    def list_ui_elements_info(
        self,
        *,
        screen: str | None = None,
        text: str | None = None,
        element_type: str | None = None,
    ) -> dict[str, Any]:
        """Return UI elements plus the frame id used to guard a click."""
        payload: dict[str, Any] = {}
        if screen is not None:
            payload["screen"] = screen
        if text is not None:
            payload["text"] = text
        if element_type is not None:
            payload["type"] = element_type
        reply = self._checked("list_ui_elements", payload or None)
        elements = reply.get("elements")
        if not isinstance(elements, list):
            raise BridgeProtocolError("list_ui_elements reply missing 'elements' list")
        return reply

    def click_element(
        self,
        text: str | None = None,
        id: str | None = None,
        *,
        screen: str | None = None,
        exact: bool = False,
        element_id: str | None = None,
        expected_frame_id: str | None = None,
        interaction_id: str | None = None,
    ) -> dict:
        """Click a visible focusable element by text or its returned ``id``.

        Text matching is case-insensitive and substring-based by default. Set
        ``exact=True`` when duplicate/partial labels should not be accepted.
        ``element_id`` is an alias for ``id`` for callers that avoid Python's
        built-in name.
        """
        if id is None:
            id = element_id
        payload: dict[str, Any] = {"text": text, "id": id, "exact": bool(exact)}
        if screen is not None:
            payload["screen"] = screen
        if expected_frame_id is not None:
            payload["expected_frame_id"] = expected_frame_id
        if interaction_id is not None:
            payload["interaction_id"] = interaction_id
        reply = self.request("click_element", payload)
        if reply.get("error") is not None:
            result = dict(reply)
            result["ok"] = False
            return result
        return reply

    def hover_element(
        self,
        text: str | None = None,
        id: str | None = None,
        *,
        screen: str | None = None,
        exact: bool = False,
        element_id: str | None = None,
        expected_frame_id: str | None = None,
    ) -> dict[str, Any]:
        """Move the pointer over a visible control without clicking it."""
        if id is None:
            id = element_id
        payload: dict[str, Any] = {"text": text, "id": id, "exact": bool(exact)}
        if screen is not None:
            payload["screen"] = screen
        if expected_frame_id is not None:
            payload["expected_frame_id"] = expected_frame_id
        reply = self.request("hover_element", payload)
        if reply.get("error") is not None:
            result = dict(reply)
            result["ok"] = False
            return result
        return reply

    def get_ui_element_bounds(
        self,
        text: str | None = None,
        id: str | None = None,
        *,
        screen: str | None = None,
        exact: bool = False,
        element_id: str | None = None,
        expected_frame_id: str | None = None,
    ) -> dict[str, Any]:
        """Return focus and painted bounds for a visible UI element."""
        if id is None:
            id = element_id
        payload: dict[str, Any] = {"text": text, "id": id, "exact": bool(exact)}
        if screen is not None:
            payload["screen"] = screen
        if expected_frame_id is not None:
            payload["expected_frame_id"] = expected_frame_id
        reply = self.request("get_ui_element_bounds", payload)
        if reply.get("error") is not None:
            result = dict(reply)
            result["ok"] = False
            return result
        return reply

    def get_displayable_bounds(
        self,
        tag: str,
        *,
        layer: str | None = None,
    ) -> dict[str, Any]:
        """Return where a shown image ``tag`` was rendered, in logical pixels.

        The reply carries ``bounds`` (integer ``x``/``y``/``width``/``height``),
        ``center``, and ``coordinate_space: "logical"``. When the tag is not
        showing, ``ok`` is ``False`` and ``showing_tags`` lists what is on the
        layer instead. A guard error is a normal control result here, so this
        does not raise on a missing tag.
        """
        payload: dict[str, Any] = {"tag": tag}
        if layer is not None:
            payload["layer"] = layer
        reply = self.request("get_displayable_bounds", payload)
        if reply.get("error") is not None:
            result = dict(reply)
            result["ok"] = False
            return result
        return reply

    def position_element(
        self,
        tag: str,
        *,
        layer: str | None = None,
        **placement: float,
    ) -> dict[str, Any]:
        """Reposition a showing image ``tag`` and return its new bounds.

        ``placement`` accepts any of ``xpos``, ``ypos``, ``xanchor``,
        ``yanchor``, ``xalign``, ``yalign``, ``xoffset``, ``yoffset``, ``zoom``
        and ``rotate``. At least one is required. The tag keeps its current
        attributes, and the reply mirrors :meth:`get_displayable_bounds` plus an
        ``applied`` echo of the placement that was set.
        """
        payload: dict[str, Any] = {"tag": tag}
        if layer is not None:
            payload["layer"] = layer
        for key, value in placement.items():
            if value is not None:
                payload[key] = value
        reply = self.request("show_displayable", payload)
        if reply.get("error") is not None:
            result = dict(reply)
            result["ok"] = False
            return result
        return reply

    def scene_tree(
        self,
        *,
        detail: str | None = None,
        layers: list[str] | tuple[str, ...] | None = None,
        types: list[str] | tuple[str, ...] | None = None,
        screen: str | None = None,
        ids: list[str] | tuple[str, ...] | None = None,
        include: list[str] | tuple[str, ...] | None = None,
        max_depth: int | None = None,
        max_nodes: int | None = None,
        max_text_chars: int | None = None,
    ) -> dict[str, Any]:
        """Return the full perceived scene in logical coordinates.

        Unlike :meth:`list_ui_elements` (focusables only), this walks every
        layer's scene list plus the focus list, reporting each node's ``id``,
        ``type``, ``layer``, ``screen``, ``bounds`` and ``zorder``. ``detail``
        is ``semantic`` (default), ``layout`` or ``raw``;
        ``layers``/``types``/``screen``/``ids`` scope the returned nodes;
        ``include`` opts into extra per-node fields. ``max_depth``,
        ``max_nodes``, and ``max_text_chars`` can lower bridge traversal and
        text-size caps for bounded callers.
        The reply always carries an ``omitted`` completeness hint and reports
        traversal caps through ``truncated`` and ``limits``.
        """
        payload: dict[str, Any] = {}
        if detail is not None:
            payload["detail"] = detail
        if layers is not None:
            payload["layers"] = list(layers)
        if types is not None:
            payload["types"] = list(types)
        if screen is not None:
            payload["screen"] = screen
        if ids is not None:
            payload["ids"] = list(ids)
        if include is not None:
            payload["include"] = list(include)
        if max_depth is not None:
            payload["max_depth"] = max_depth
        if max_nodes is not None:
            payload["max_nodes"] = max_nodes
        if max_text_chars is not None:
            payload["max_text_chars"] = max_text_chars
        reply = self.request("scene_tree", payload or None)
        if reply.get("error") is not None:
            result = dict(reply)
            result["ok"] = False
            return result
        return reply

    def hit_test(
        self,
        x: int | float,
        y: int | float,
        *,
        coordinate_space: str = "logical",
    ) -> dict:
        """Inspect the interactive focus stack at a point."""
        reply = self.request(
            "hit_test",
            {"x": x, "y": y, "coordinate_space": coordinate_space},
        )
        if reply.get("error") is not None:
            result = dict(reply)
            result["ok"] = False
            return result
        return reply

    def click_at(
        self,
        x: int | float,
        y: int | float,
        *,
        expected_screenshot: str | dict[str, Any] | None = None,
        expected_state: dict[str, Any] | None = None,
        expected_screenshot_hash: str | None = None,
        expected_frame_id: str | None = None,
        coordinate_space: str = "logical",
    ) -> dict:
        """Click screen coordinates, optionally guarded by frame/state.

        ``expected_screenshot`` may be a SHA-256 digest (or a bridge screenshot
        guard object containing ``sha256``/``base64``), while
        ``expected_state`` is a subset of ``get_state()`` that must still
        match. A failed guard returns ``{"ok": False, "error": ...}`` and no
        click is sent to Ren'Py.
        """
        payload: dict[str, Any] = {
            "x": x,
            "y": y,
            "coordinate_space": coordinate_space,
        }
        if expected_screenshot is not None:
            payload["expected_screenshot"] = expected_screenshot
        elif expected_screenshot_hash is not None:
            payload["expected_screenshot"] = expected_screenshot_hash
        elif expected_frame_id is not None:
            payload["expected_frame_id"] = expected_frame_id
        if expected_state is not None:
            payload["expected_state"] = expected_state
        reply = self.request("click_at", payload)
        if reply.get("error") is not None:
            # A stale-frame/state guard is an expected control result, not a
            # transport failure. Keep it structured so an agent can refresh
            # the frame and retry safely.
            result = dict(reply)
            result["ok"] = False
            return result
        return reply
