"""Private bridge control-directory and bridge-info metadata helpers."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final, Mapping

from renforge.bridge.client import BridgeProtocolError
from renforge.launch_env import LaunchError
from renforge.util.files import (
    PrivatePathError,
    atomic_write_private_json,
    ensure_private_directory,
    read_regular_file_nofollow,
)

_CONTROL_DIR_PARTS: Final[tuple[str, str]] = (".renforge", "control")
_BRIDGE_INFO_NAME: Final[str] = "bridge.json"
_BRIDGE_INFO_MAX_BYTES: Final[int] = 16 * 1024
_BRIDGE_HOST: Final[str] = "127.0.0.1"
_SCHEMA_VERSION: Final[int] = 1
_PROTOCOL_VERSION: Final[int] = 1
_STATE_STARTING: Final[str] = "starting"
_STATE_READY: Final[str] = "ready"
_ALLOWED_STATES: Final[frozenset[str]] = frozenset({_STATE_STARTING, _STATE_READY})
_SESSION_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_BRIDGE_INFO_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "protocol_version",
        "state",
        "session_id",
        "project_root",
        "host",
        "port",
        "token",
    }
)
_VALIDATION_MESSAGE: Final[str] = "bridge metadata failed validation"


@dataclass(frozen=True)
class BridgeInfo:
    """Validated bridge discovery record published under the private control dir."""

    schema_version: int
    protocol_version: int
    state: str
    session_id: str
    project_root: str
    host: str
    port: int
    token: str = field(repr=False)


def ensure_control_dir(project_root: Path) -> Path:
    """Create or validate ``<canonical-root>/.renforge/control`` as a private directory."""
    root = _canonical_project_root(project_root)
    control = _control_dir(root)
    try:
        return ensure_private_directory(control)
    except PrivatePathError as exc:
        raise _control_directory_error(exc) from exc


def validate_control_dir(project_root: Path) -> Path:
    """Validate an existing private control directory without creating it."""
    root = _canonical_project_root(project_root)
    control = _control_dir(root)
    try:
        try:
            control.lstat()
        except FileNotFoundError as exc:
            raise PrivatePathError(
                "PRIVATE_DIRECTORY_UNSAFE",
                "control directory does not exist: %s" % control,
            ) from exc
        return ensure_private_directory(control)
    except PrivatePathError as exc:
        raise _control_directory_error(exc) from exc


def write_starting_bridge_info(
    project_root: Path,
    *,
    session_id: str,
    token: str,
) -> BridgeInfo:
    """Reserve starting bridge metadata (port 0) under the private control directory."""
    root = _canonical_project_root(project_root)
    ensure_control_dir(root)
    if not _is_session_id(session_id) or not _is_token(token):
        raise BridgeProtocolError(_VALIDATION_MESSAGE)

    info = BridgeInfo(
        schema_version=_SCHEMA_VERSION,
        protocol_version=_PROTOCOL_VERSION,
        state=_STATE_STARTING,
        session_id=session_id,
        project_root=str(root),
        host=_BRIDGE_HOST,
        port=0,
        token=token,
    )
    _write_bridge_info(root, info)
    return info


def read_bridge_info(
    project_root: Path,
    *,
    require_ready: bool = True,
    expected_session_id: str | None = None,
) -> BridgeInfo:
    """Read and validate private bridge metadata for *project_root*."""
    root = _canonical_project_root(project_root)
    validate_control_dir(root)
    path = _bridge_info_path(root)
    try:
        raw = read_regular_file_nofollow(path, max_bytes=_BRIDGE_INFO_MAX_BYTES)
    except PrivatePathError as exc:
        raise BridgeProtocolError(_VALIDATION_MESSAGE) from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeProtocolError(_VALIDATION_MESSAGE) from exc

    info = _parse_bridge_info(payload, expected_project_root=root)
    if require_ready and info.state != _STATE_READY:
        raise BridgeProtocolError(_VALIDATION_MESSAGE)
    if expected_session_id is not None and info.session_id != expected_session_id:
        raise BridgeProtocolError(_VALIDATION_MESSAGE)
    return info


def _control_directory_error(exc: PrivatePathError) -> LaunchError:
    return LaunchError(
        "BRIDGE_CONTROL_DIRECTORY_UNSAFE",
        exc.message,
        phase="preparing_control_directory",
    )


def _canonical_project_root(project_root: Path) -> Path:
    try:
        canonical = Path(project_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise LaunchError(
            "BRIDGE_CONTROL_DIRECTORY_UNSAFE",
            "project root is not usable: %s" % project_root,
            phase="preparing_control_directory",
        ) from exc
    if not canonical.is_dir():
        raise LaunchError(
            "BRIDGE_CONTROL_DIRECTORY_UNSAFE",
            "project root is not a directory: %s" % canonical,
            phase="preparing_control_directory",
        )
    return canonical


def _control_dir(project_root: Path) -> Path:
    return project_root.joinpath(*_CONTROL_DIR_PARTS)


def _bridge_info_path(project_root: Path) -> Path:
    return _control_dir(project_root) / _BRIDGE_INFO_NAME


def _is_session_id(value: object) -> bool:
    return isinstance(value, str) and _SESSION_ID_RE.fullmatch(value) is not None


def _is_token(value: object) -> bool:
    return isinstance(value, str) and _TOKEN_RE.fullmatch(value) is not None


def _write_bridge_info(project_root: Path, info: BridgeInfo) -> None:
    path = _bridge_info_path(project_root)
    payload = asdict(info)
    try:
        atomic_write_private_json(path, payload, max_bytes=_BRIDGE_INFO_MAX_BYTES)
    except PrivatePathError as exc:
        raise BridgeProtocolError(_VALIDATION_MESSAGE) from exc


def _parse_bridge_info(payload: object, *, expected_project_root: Path) -> BridgeInfo:
    if not isinstance(payload, Mapping):
        raise BridgeProtocolError(_VALIDATION_MESSAGE)

    data = dict(payload)
    if set(data) != _BRIDGE_INFO_KEYS:
        raise BridgeProtocolError(_VALIDATION_MESSAGE)

    schema_version = data["schema_version"]
    protocol_version = data["protocol_version"]
    state = data["state"]
    session_id = data["session_id"]
    project_root = data["project_root"]
    host = data["host"]
    port = data["port"]
    token = data["token"]

    if (
        type(schema_version) is not int
        or isinstance(schema_version, bool)
        or schema_version != _SCHEMA_VERSION
        or type(protocol_version) is not int
        or isinstance(protocol_version, bool)
        or protocol_version != _PROTOCOL_VERSION
    ):
        raise BridgeProtocolError(_VALIDATION_MESSAGE)
    if not isinstance(state, str) or state not in _ALLOWED_STATES:
        raise BridgeProtocolError(_VALIDATION_MESSAGE)
    if not _is_session_id(session_id) or not _is_token(token):
        raise BridgeProtocolError(_VALIDATION_MESSAGE)
    if not isinstance(project_root, str) or not project_root:
        raise BridgeProtocolError(_VALIDATION_MESSAGE)
    if project_root != str(expected_project_root):
        raise BridgeProtocolError(_VALIDATION_MESSAGE)
    if host != _BRIDGE_HOST:
        raise BridgeProtocolError(_VALIDATION_MESSAGE)
    if type(port) is not int or isinstance(port, bool):
        raise BridgeProtocolError(_VALIDATION_MESSAGE)
    if state == _STATE_STARTING:
        if port != 0:
            raise BridgeProtocolError(_VALIDATION_MESSAGE)
    elif port < 1 or port > 65535:
        raise BridgeProtocolError(_VALIDATION_MESSAGE)

    return BridgeInfo(
        schema_version=_SCHEMA_VERSION,
        protocol_version=_PROTOCOL_VERSION,
        state=state,
        session_id=session_id,
        project_root=project_root,
        host=_BRIDGE_HOST,
        port=port,
        token=token,
    )


__all__ = [
    "BridgeInfo",
    "ensure_control_dir",
    "read_bridge_info",
    "validate_control_dir",
    "write_starting_bridge_info",
]
