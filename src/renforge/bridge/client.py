"""RenForge TCP bridge client primitives."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BridgeError(RuntimeError):
    """Base error for bridge client failures."""


class BridgeProtocolError(BridgeError):
    """Raised when the bridge response is malformed or invalid."""


@dataclass
class BridgeConfig:
    host: str = "127.0.0.1"
    port: int = 0
    token: str = ""
    timeout: float = 5.0


class BridgeClient:
    """Client speaking one-request-per-connection newline-delimited JSON."""

    def __init__(self, config: BridgeConfig):
        self._config = config

    @classmethod
    def from_project(cls, project_root: str | Path, *, timeout: float = 5.0) -> "BridgeClient":
        """Build a client from ``<project_root>/.renforge/bridge.json``.

        The running bridge publishes its host/port/token there on startup.
        """
        info_path = Path(project_root) / ".renforge" / "bridge.json"
        data = json.loads(info_path.read_text(encoding="utf-8"))
        return cls(
            BridgeConfig(
                host=str(data.get("host", "127.0.0.1")),
                port=int(data["port"]),
                token=str(data.get("token", "")),
                timeout=timeout,
            )
        )

    def request(self, command: str, payload: dict[str, Any] | None = None) -> dict:
        body = {
            "token": self._config.token,
            "command": command,
            "payload": payload,
        }

        with socket.create_connection(
            (self._config.host, self._config.port), timeout=self._config.timeout
        ) as sock:
            sock.settimeout(self._config.timeout)
            payload_bytes = (json.dumps(body) + "\n").encode("utf-8")

            try:
                sock.sendall(payload_bytes)
            except OSError as exc:
                raise BridgeError(f"bridge send failed: {exc}") from exc

            file_obj = sock.makefile("r", encoding="utf-8")
            try:
                response_line = file_obj.readline()
            except OSError as exc:
                raise BridgeError(f"bridge read failed: {exc}") from exc

        if not response_line:
            raise BridgeProtocolError("bridge response was empty")

        try:
            response = json.loads(response_line)
        except json.JSONDecodeError as exc:
            raise BridgeProtocolError("bridge response is not valid JSON") from exc

        if not isinstance(response, dict):
            raise BridgeProtocolError("bridge response must be a JSON object")

        return response

    def _checked(self, command: str, payload: dict[str, Any] | None = None) -> dict:
        reply = self.request(command, payload)
        if reply.get("error") is not None:
            raise BridgeError(f"bridge error on '{command}': {reply['error']}")
        return reply

    def ping(self) -> dict:
        return self.request("ping")

    def get_state(self) -> dict:
        return self._checked("get_state")

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

    def control(self, action: str) -> dict:
        """Run a named runtime control action inside the Ren'Py bridge."""
        return self._checked("control", {"action": action})

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
        reply = self.request("click_element", payload)
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
