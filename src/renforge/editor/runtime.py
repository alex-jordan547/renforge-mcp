from __future__ import annotations

from typing import Any, Protocol
import time
from pathlib import Path
from typing import Callable

from ..bridge.client import BridgeClient, BridgeError, BridgeProtocolError
from .exceptions import EditorError


class RuntimeProbe(Protocol):
    def observe(self, runtime_key: dict[str, Any], *, deadline: float) -> dict[str, Any]:
        """Return an independent observation for a runtime key."""

    def attest(
        self,
        *,
        transaction_id: str,
        script_generation: int,
        deadline: float,
        expected_targets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Perform reload attestation for a published transaction."""


class BridgeRuntimeProbe:
    """Observe and attest editor targets through the injected Ren'Py bridge."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        client_factory: Callable[..., BridgeClient] = BridgeClient.from_project,
        max_retries: int = 1,
    ):
        self._project_root = Path(project_root)
        self._client_factory = client_factory
        self._max_retries = max(0, int(max_retries))

    def _client(self, deadline: float) -> BridgeClient:
        remaining = float(deadline) - time.monotonic()
        if remaining <= 0:
            raise EditorError("RUNTIME_PROBE_TIMEOUT", "runtime probe request deadline exceeded")
        try:
            return self._client_factory(self._project_root, timeout=remaining)
        except (OSError, ValueError, KeyError, BridgeError) as exc:
            raise EditorError("RUNTIME_PROBE_UNAVAILABLE", f"unable to connect to runtime bridge: {exc}") from exc

    def _request(
        self,
        command: str,
        payload: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        last_error: BaseException | None = None
        for _ in range(self._max_retries + 1):
            try:
                reply = self._client(deadline).request(command, payload)
                break
            except EditorError as exc:
                if exc.code == "RUNTIME_PROBE_TIMEOUT":
                    raise exc
                last_error = exc
            except (OSError, ValueError, BridgeError, BridgeProtocolError) as exc:
                last_error = exc
        else:
            if isinstance(last_error, EditorError):
                raise last_error
            raise EditorError("RUNTIME_PROBE_FAILED", f"runtime bridge request failed: {last_error}") from last_error

        if not isinstance(reply, dict):
            raise EditorError("RUNTIME_PROBE_FAILED", "runtime bridge returned a non-object reply")
        if reply.get("ok") is not True:
            error = reply.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or "RUNTIME_PROBE_FAILED")
                message = str(error.get("message") or code)
            else:
                code = "RUNTIME_PROBE_FAILED"
                message = str(error or "runtime bridge rejected the request")
            raise EditorError(code, message)
        return reply

    def observe(self, runtime_key: dict[str, Any], *, deadline: float) -> dict[str, Any]:
        reply = self._request(
            "editor_observe_target",
            {"runtime_key": runtime_key},
            deadline,
        )
        observation = reply.get("observation")
        if not isinstance(observation, dict):
            raise EditorError("INDEPENDENT_OBSERVATION_INVALID", "runtime bridge omitted observation")
        return observation

    def attest(
        self,
        *,
        transaction_id: str,
        script_generation: int,
        deadline: float,
        expected_targets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._request(
            "editor_attest_targets",
            {
                "transaction_id": transaction_id,
                "script_generation": script_generation,
                "expected_targets": expected_targets,
            },
            deadline,
        )
