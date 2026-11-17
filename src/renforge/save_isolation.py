"""Application-layer isolation of Ren'Py saves, preferences, and HOME.

Isolation is opt-in. By default MCP and dashboard launches use the user's own
saves and HOME, as they always have. Pass ``savedir=temporary`` (or set
``RENFORGE_ISOLATION=temporary``) to start the game in a disposable session root
so it cannot read or write the user's normal Ren'Py state (saves, persistent
data, preferences, and ``$HOME/.renpy``).

Selected host slots can be copied into an isolated session with
``renforge_saves(action="import")``. This module never bind-mounts the user
tree.

This is application-layer isolation, not an OS sandbox: the game still runs
as the user and can reach any path it names explicitly.
"""

from __future__ import annotations

import errno
import fnmatch
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any, Callable, Iterable, Mapping

from .util.files import (
    make_directory_at_nofollow,
    open_regular_at_nofollow,
    replace_file_at_nofollow,
)

EXISTING_TOKENS: frozenset[str] = frozenset({"existing", "default"})
TEMPORARY_TOKENS: frozenset[str] = frozenset({"", "temporary"})
AUTO_TOKENS: frozenset[str] = frozenset({"auto"})
# Non-empty persistent/preferences values resolve_launch_isolation accepts.
STATE_MODE_TOKENS: frozenset[str] = (
    AUTO_TOKENS | EXISTING_TOKENS | TEMPORARY_TOKENS | {"empty"}
) - {""}
ISOLATION_ENV: str = "RENFORGE_ISOLATION"
SESSION_PREFIX: str = "renforge-session-"
SESSION_ID_PREFIX: str = "sess_"
MULTIPERSISTENT_DIRNAME: str = "multipersistent"
SAVES_DIRNAME: str = "saves"
HOME_DIRNAME: str = "home"
SAVE_DIRECTORY_RE: re.Pattern[str] = re.compile(
    r"^[ \t]*define[ \t]+config\.save_directory[ \t]*=[ \t]*[\"']([^\"'\r\n]+)[\"']",
    re.MULTILINE,
)
SLOT_FILE_RE: re.Pattern[str] = re.compile(
    r"^(?P<slot>.+)-[A-Za-z]{2}\d+\.save(?P<json>\.json)?$"
)
# Per-file caps on what an import copies and what list_user parses.
SLOT_FILE_MAX_BYTES: int = 256 * 1024 * 1024
SIDECAR_MAX_BYTES: int = 1024 * 1024
# config.save_directory is read from game/*.rpy; a larger script is skipped.
RPY_SCAN_MAX_BYTES: int = 4 * 1024 * 1024


def classify_token(value: str | None, *, omitted: str) -> str:
    """Return ``existing``, ``temporary``, ``empty``, or ``path``."""
    if value is None:
        return omitted
    token = str(value).strip()
    if token in AUTO_TOKENS:
        return omitted
    if token in EXISTING_TOKENS:
        return "existing"
    if token in TEMPORARY_TOKENS:
        return "temporary"
    if token == "empty":
        return "empty"
    return "path"


def classify_savedir(savedir: str | None) -> str:
    """Return ``existing``, ``temporary``, or ``path``; omission is isolated."""
    mode = classify_token(savedir, omitted="temporary")
    return "temporary" if mode == "empty" else mode


def configured_isolation_mode(environ: Mapping[str, str] | None = None) -> str:
    """Server-wide default: the user's own saves unless ``RENFORGE_ISOLATION=temporary``.

    Isolation is opt-in: changing every launch's default to isolated saves,
    persistent data, preferences and HOME is a separate contract decision.
    A single launch opts in with ``savedir=temporary``.
    """
    source = os.environ if environ is None else environ
    token = str(source.get(ISOLATION_ENV, "") or "").strip()
    if token == "temporary":
        return "temporary"
    return "existing"


def default_launch_value(
    value: str | None,
    *,
    field: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Fill an omitted MCP/dashboard isolation field from configuration."""
    token = None if value is None else str(value).strip()
    if token and token not in AUTO_TOKENS:
        return token
    if configured_isolation_mode(environ) == "existing":
        return "existing"
    if field in {"persistent", "preferences"}:
        return "empty"
    return "temporary"


def default_launch_savedir(
    savedir: str | None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Coerce omitted MCP/dashboard savedir values to the configured default."""
    return default_launch_value(savedir, field="savedir", environ=environ)


def apply_launch_isolation_defaults(
    *,
    savedir: str | None = None,
    home: str | None = None,
    persistent: str | None = None,
    preferences: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve MCP/dashboard isolation knobs.

    Omitted fields follow ``RENFORGE_ISOLATION`` (default ``existing``).
    ``savedir=existing`` also selects ``home=existing`` unless *home* was set
    explicitly, so user saves under ``~/.renpy`` remain reachable. Explicit
    ``savedir=temporary`` isolates HOME the same way unless *home* is set.
    """
    home_explicit = home is not None and bool(str(home).strip()) and str(home).strip() not in AUTO_TOKENS
    resolved_savedir = default_launch_value(savedir, field="savedir", environ=environ)
    resolved_home = default_launch_value(home, field="home", environ=environ)
    if not home_explicit:
        savedir_mode = classify_savedir(resolved_savedir)
        if savedir_mode == "existing":
            resolved_home = "existing"
        elif savedir_mode == "temporary":
            resolved_home = "temporary"
    state_default = "empty" if classify_savedir(resolved_savedir) == "temporary" else "existing"

    def state_mode(value: str | None) -> str:
        token = "auto" if value is None else str(value).strip()
        return state_default if token in {"", "auto"} else token

    return {
        "savedir": resolved_savedir,
        "home": resolved_home,
        "persistent": state_mode(persistent),
        "preferences": state_mode(preferences),
    }


def _state_mode(value: str | None) -> str:
    mode = classify_token(value, omitted="existing")
    return "empty" if mode == "temporary" else mode


def launch_state_mode_error(
    *,
    savedir: str | None = None,
    home: str | None = None,
    persistent: str | None = None,
    preferences: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Why these launch knobs cannot be combined, or None.

    The dashboard checks this before any launch work starts, and
    :func:`resolve_launch_isolation` raises it, so both reject the same
    requests.
    """
    resolved = apply_launch_isolation_defaults(
        savedir=savedir, home=home, persistent=persistent,
        preferences=preferences, environ=environ,
    )
    modes = {_state_mode(resolved["persistent"]), _state_mode(resolved["preferences"])}
    if not modes <= {"existing", "empty"}:
        return "Persistent/preferences modes must be 'auto', 'existing', or 'empty'."
    if classify_savedir(resolved["savedir"]) != "temporary" and "empty" in modes:
        return (
            "Empty persistent/preferences state requires savedir='temporary'; "
            "use 'existing' state modes to reuse a save directory."
        )
    return None


@dataclass(frozen=True)
class LaunchIsolation:
    """Resolved save, preference, and HOME locations for one launch."""

    savedir_mode: str
    home_mode: str
    persistent_mode: str
    preferences_mode: str
    savedir: Path | None
    home: Path | None
    session_root: Path | None
    cleanup: bool

    @property
    def mode(self) -> str:
        """Backward-compatible alias for :attr:`savedir_mode`."""
        return self.savedir_mode

    @property
    def exposes_user_saves(self) -> bool:
        """Whether this launch can read or write the host Ren'Py save tree."""
        if self.savedir_mode == "existing" or self.home_mode == "existing":
            return True
        if self.savedir_mode == "path" and self.savedir is not None:
            if path_exposes_user_saves(self.savedir):
                return True
        if self.home_mode == "path" and self.home is not None:
            return path_is_host_home(self.home)
        return False

    def command_args(self) -> list[str]:
        if self.savedir is None:
            return []
        return ["--savedir", str(self.savedir)]

    def environ(self, host_env: Mapping[str, str] | None = None) -> dict[str, str]:
        env: dict[str, str] = {}
        if self.savedir is not None:
            path = str(self.savedir)
            env["RENFORGE_SAVEDIR"] = path
            env["RENPY_PATH_TO_SAVES"] = path
            env["RENPY_MULTIPERSISTENT"] = str(self.savedir / MULTIPERSISTENT_DIRNAME)
        if self.home is not None:
            env.update(_home_environ(self.home, host_env=host_env))
        return env


def resolve_save_isolation(
    savedir: str | None,
    *,
    cleanup_on_stop: bool = True,
) -> LaunchIsolation:
    """Savedir-focused resolver used by the low-level launcher tests."""
    return resolve_launch_isolation(savedir, cleanup_on_stop=cleanup_on_stop)


def resolve_launch_isolation(
    savedir: str | None = None,
    *,
    home: str | None = None,
    persistent: str | None = None,
    preferences: str | None = None,
    cleanup_on_stop: bool = True,
    host_env: Mapping[str, str] | None = None,
) -> LaunchIsolation:
    """Create or select isolated save/home directories for one Ren'Py process.

    Omitted fields follow :func:`apply_launch_isolation_defaults`, so every
    launch path resolves them the same way. HOME follows the savedir mode.
    """
    error = launch_state_mode_error(
        savedir=savedir, home=home, persistent=persistent,
        preferences=preferences, environ=host_env,
    )
    if error is not None:
        raise ValueError(error)
    resolved = apply_launch_isolation_defaults(
        savedir=savedir, home=home, persistent=persistent,
        preferences=preferences, environ=host_env,
    )
    savedir, home = resolved["savedir"], resolved["home"]
    persistent, preferences = resolved["persistent"], resolved["preferences"]
    savedir_mode = classify_savedir(savedir)
    home_mode = classify_token(home, omitted="temporary")
    if home_mode == "empty":
        home_mode = "temporary"

    persistent_mode = _state_mode(persistent)
    preferences_mode = _state_mode(preferences)

    needs_session = savedir_mode == "temporary" or home_mode == "temporary"
    session_root: Path | None = None
    savedir_path: Path | None = None
    home_path: Path | None = None
    cleanup = False

    if needs_session:
        session_root = Path(tempfile.mkdtemp(prefix=SESSION_PREFIX))
        cleanup = bool(cleanup_on_stop)

    try:
        if savedir_mode == "temporary":
            assert session_root is not None
            savedir_path = session_root / SAVES_DIRNAME
            savedir_path.mkdir(parents=True, exist_ok=True)
            (savedir_path / MULTIPERSISTENT_DIRNAME).mkdir(parents=True, exist_ok=True)
        elif savedir_mode == "path":
            assert savedir is not None
            savedir_path = Path(savedir).expanduser().resolve()
            savedir_path.mkdir(parents=True, exist_ok=True)
            (savedir_path / MULTIPERSISTENT_DIRNAME).mkdir(parents=True, exist_ok=True)

        if home_mode == "temporary":
            assert session_root is not None
            home_path = session_root / HOME_DIRNAME
            _prepare_isolated_home(home_path)
        elif home_mode == "path":
            assert home is not None
            home_path = Path(home).expanduser().resolve()
            _prepare_isolated_home(home_path)
    except BaseException:
        if session_root is not None:
            shutil.rmtree(session_root, ignore_errors=True)
        raise

    return LaunchIsolation(
        savedir_mode=savedir_mode,
        home_mode=home_mode,
        persistent_mode=persistent_mode,
        preferences_mode=preferences_mode,
        savedir=savedir_path,
        home=home_path,
        session_root=session_root,
        cleanup=cleanup,
    )


def agent_session_id(artifact_session_id: str | None) -> str | None:
    """Return the agent-facing isolation id for a bridge artifact session."""
    if not artifact_session_id:
        return None
    token = str(artifact_session_id).strip()
    if not token:
        return None
    if token.startswith(SESSION_ID_PREFIX):
        return token
    return f"{SESSION_ID_PREFIX}{token}"


def isolation_report(
    isolation: LaunchIsolation,
    *,
    session_id: str | None = None,
    project_path: str | Path | None = None,
) -> dict[str, Any]:
    """JSON object attached to launch/status so agents can see isolation."""
    exposes = isolation.exposes_user_saves
    if isolation.savedir_mode == "path" and isolation.savedir is not None:
        exposes = exposes or path_exposes_user_saves(
            isolation.savedir, project_path=project_path
        )
    return {
        "session_id": session_id,
        "isolation": {
            "savedir_mode": isolation.savedir_mode,
            "home_mode": isolation.home_mode,
            "persistent_mode": isolation.persistent_mode,
            "preferences_mode": isolation.preferences_mode,
            "savedir": str(isolation.savedir) if isolation.savedir is not None else None,
            "home": str(isolation.home) if isolation.home is not None else None,
            "exposes_user_saves": bool(exposes),
        },
    }


def launch_exposes_user_saves(
    *,
    savedir: str | None = None,
    home: str | None = None,
    persistent: str | None = None,
    preferences: str | None = None,
    project_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """True when the resolved launch can read or write the host save tree."""
    resolved = apply_launch_isolation_defaults(
        savedir=savedir,
        home=home,
        persistent=persistent,
        preferences=preferences,
        environ=environ,
    )
    savedir_mode = classify_savedir(resolved["savedir"])
    home_mode = classify_token(resolved["home"], omitted="temporary")
    if home_mode == "empty":
        home_mode = "temporary"
    if savedir_mode == "existing" or home_mode == "existing":
        return True
    if savedir_mode == "path" and path_exposes_user_saves(
        resolved["savedir"], project_path=project_path
    ):
        return True
    if home_mode == "path" and path_is_host_home(resolved["home"]):
        return True
    return False


def path_in_session_root(value: str | Path) -> bool:
    """True only for a path inside a session root this module creates.

    Session roots are ``tempfile.mkdtemp(prefix=SESSION_PREFIX)`` folders, so
    nothing outside one is session-private. A path that cannot be resolved
    is not either: callers deciding whether storage is isolated must treat an
    unknown location as the player's.
    """
    try:
        resolved = Path(value).expanduser().resolve()
        relative = resolved.relative_to(Path(tempfile.gettempdir()).resolve())
    except (OSError, RuntimeError, ValueError, TypeError):
        return False
    return bool(relative.parts) and relative.parts[0].startswith(SESSION_PREFIX)


def path_is_host_home(value: str | Path, *, home: Path | None = None) -> bool:
    """True when *value* is the host HOME directory."""
    try:
        path = Path(value).expanduser().resolve()
        host = (home or Path.home()).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return os.path.normcase(str(path)) == os.path.normcase(str(host))


def path_exposes_user_saves(
    savedir: str | Path,
    project_path: str | Path | None = None,
    *,
    home: Path | None = None,
) -> bool:
    """True when *savedir* is the host Ren'Py tree or the project's ``game/saves``."""
    try:
        path = Path(savedir).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    for root in user_save_roots(project_path, home=home):
        try:
            resolved = root.expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if _is_relative_to(path, resolved) or path == resolved:
            return True
    return False


def user_save_roots(
    project_path: str | Path | None = None,
    *,
    home: Path | None = None,
) -> list[Path]:
    """Host locations that hold the user's normal Ren'Py saves."""
    host_home = home if home is not None else Path.home()
    roots = [host_renpy_root(home=host_home)]
    if project_path not in (None, ""):
        try:
            roots.append(Path(str(project_path)).expanduser().resolve() / "game" / "saves")
        except (OSError, RuntimeError, ValueError):
            pass
    return roots


def host_renpy_root(*, home: Path | None = None) -> Path:
    """Platform directory that contains per-game host save folders."""
    host_home = home if home is not None else Path.home()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "RenPy"
        return host_home / "AppData" / "Roaming" / "RenPy"
    if sys_platform() == "darwin":
        return host_home / "Library" / "RenPy"
    return host_home / ".renpy"


def sys_platform() -> str:
    """Indirection so tests can monkeypatch the platform check."""
    import sys

    return sys.platform


def parse_save_directory(project_root: str | Path) -> str | None:
    """Return ``config.save_directory`` from ``game/*.rpy``, if defined.

    Each script is opened without following links and without blocking, must
    be a regular file, and is read only up to :data:`RPY_SCAN_MAX_BYTES`: the
    game folder is the game's own, so a FIFO swapped in or a huge sparse file
    must not hang or exhaust an observational listing.
    """
    game_dir = Path(project_root) / "game"
    if not game_dir.is_dir():
        return None
    candidates = [game_dir / "options.rpy", *sorted(game_dir.glob("*.rpy"))]
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        text = _read_script_bounded(resolved)
        if text is None:
            continue
        match = SAVE_DIRECTORY_RE.search(text)
        if match:
            name = match.group(1).strip()
            if name:
                return name
    return None


def _read_script_bounded(path: Path) -> str | None:
    try:
        fd, st = open_regular_at_nofollow(path.parent, path.name)
    except (OSError, ValueError):
        return None
    try:
        if st.st_size > RPY_SCAN_MAX_BYTES:
            return None
        data = _read_at_most(fd, RPY_SCAN_MAX_BYTES)
    except OSError:
        return None
    finally:
        os.close(fd)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _read_at_most(fd: int, limit: int) -> bytes | None:
    """Read *fd* to the end, or return None once it holds more than *limit* bytes."""
    data = b""
    while len(data) <= limit:
        chunk = os.read(fd, limit + 1 - len(data))
        if not chunk:
            break
        data += chunk
    return None if len(data) > limit else data


def host_save_directories(
    project_root: str | Path,
    *,
    home: Path | None = None,
) -> list[Path]:
    """Existing host directories that may contain this project's user saves.

    Each directory is returned resolved, once. Later opens walk these paths
    component by component without following links, so a folder swapped for
    a link after this point is refused instead of followed; a link that was
    already there (a ``~/.renpy`` moved to another disk) was followed here.
    """
    directories: list[Path] = []
    save_name = parse_save_directory(project_root)
    # The name comes from the game, so it must stay one folder of the Ren'Py
    # root: "../OtherGame" would read another game's (or any) saves.
    if (
        save_name
        and save_name not in (".", "..")
        and PurePath(save_name).name == save_name
        and PureWindowsPath(save_name).name == save_name
    ):
        root = host_renpy_root(home=home)
        host_dir = root / save_name
        if host_dir.is_dir():
            resolved_host = host_dir.resolve()
            if resolved_host.parent == root.resolve():
                directories.append(resolved_host)
    project_saves = Path(project_root) / "game" / "saves"
    if project_saves.is_dir():
        resolved_saves = project_saves.resolve()
        if resolved_saves not in directories and _is_relative_to(
            resolved_saves, Path(project_root).resolve()
        ):
            directories.append(resolved_saves)
    return directories


def _slot_pattern_error(pattern: Any) -> str | None:
    if pattern is not None and not isinstance(pattern, str):
        return "pattern must be a string"
    return None


def _matches(name: str, pattern: str) -> bool:
    # A glob, not a regex: fnmatch matching cannot backtrack catastrophically,
    # so a caller's pattern cannot stall the server on a long slot name.
    return fnmatch.fnmatchcase(name, pattern)


def list_host_slots(
    project_root: str | Path,
    *,
    pattern: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """List host save slots without touching a running game or writing files.

    ``pattern`` is a glob matched against the whole slot name (``1-*``).
    """
    error = _slot_pattern_error(pattern)
    if error is not None:
        return {"ok": False, "error": error}

    directories = host_save_directories(project_root, home=home)
    keep = None if pattern is None else (lambda name: _matches(name, pattern))
    return {
        "ok": True,
        "slots": _list_slots(directories, keep=keep, with_extra_info=True),
        "directories": [str(path) for path in directories],
    }


def _list_slots(
    directories: Iterable[Path],
    *,
    keep: Callable[[str], bool] | None,
    with_extra_info: bool,
) -> list[dict[str, Any]]:
    """Slots across *directories*; the first folder that has a name wins."""
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for directory in directories:
        for slot in _slots_in_directory(directory, keep=keep, with_extra_info=with_extra_info):
            if slot["name"] not in seen:
                seen.add(slot["name"])
                slots.append(slot)
    return slots


def import_host_slots(
    project_root: str | Path,
    destination: str | Path,
    *,
    slot: str | None = None,
    slots: Iterable[str] | None = None,
    pattern: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Copy selected host slot files into an isolated session savedir.

    The same as :func:`select_host_slots` followed by
    :func:`import_selected_slots`.
    """
    selection = select_host_slots(
        project_root, slot=slot, slots=slots, pattern=pattern, home=home
    )
    if not selection.get("ok"):
        return selection
    return import_selected_slots(selection, destination, project_root=project_root, home=home)


def select_host_slots(
    project_root: str | Path,
    *,
    slot: str | None = None,
    slots: Iterable[str] | None = None,
    pattern: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Resolve import selectors to host slot records with one listing.

    Reads only (a game's scripts and save folders), so callers run it before
    holding a session open: a slow listing must never delay a stop.
    """
    names: list[str] = []
    seen: set[str] = set()

    def _invalid(error: str) -> dict[str, Any]:
        # A dashboard relays this answer; the code keeps it from reading as
        # a transport failure.
        return {"ok": False, "code": "SAVE_IMPORT_INVALID", "error": error}

    def _add(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return "slot names must be non-empty strings"
        token = value.strip()
        if token not in seen:
            seen.add(token)
            names.append(token)
        return None

    if slot is not None:
        error = _add(slot)
        if error is not None:
            return _invalid(error)
    if slots is not None:
        if isinstance(slots, (str, bytes)) or not isinstance(slots, Iterable):
            return _invalid("slots must be a list of slot names")
        for item in slots:
            error = _add(item)
            if error is not None:
                return _invalid(error)

    error = _slot_pattern_error(pattern)
    if error is not None:
        return _invalid(error)

    if not names and pattern is None:
        return _invalid("import requires slot, slots, or pattern")

    def keep(name: str) -> bool:
        return name in seen or (pattern is not None and _matches(name, pattern))

    directories = host_save_directories(project_root, home=home)
    available = _list_slots(directories, keep=keep, with_extra_info=False)
    by_name = {record["name"]: record for record in available}
    selected = [by_name[name] for name in names if name in by_name]
    missing = [name for name in names if name not in by_name]
    selected.extend(record for record in available if record["name"] not in seen)
    return {"ok": True, "slots": selected, "missing": missing}


def import_selected_slots(
    selection: Mapping[str, Any],
    destination: str | Path,
    *,
    project_root: str | Path,
    home: Path | None = None,
) -> dict[str, Any]:
    """Copy the slots :func:`select_host_slots` picked into *destination*.

    *destination* is used as given, never resolved: the session's own folders
    are walked without following links, so a session root or savedir swapped
    for a link (at any time since launch) is refused, not followed. Callers
    pass a canonical path (:func:`renforge.tools.live.saves` resolves only the
    shared temp folder).
    """
    dest = Path(destination).expanduser()
    if path_exposes_user_saves(dest, project_path=project_root, home=home):
        return {
            "ok": False,
            "code": "SAVE_IMPORT_NOT_ISOLATED",
            "error": "import refuses to copy into the user's save tree; launch an isolated session first",
        }
    # Stopping the session removes its root. Creating it again here, after a
    # concurrent stop, would leave copies of the player's saves in a folder
    # nothing cleans up, so only the savedir itself is ever created.
    try:
        make_directory_at_nofollow(dest)
    except FileNotFoundError:
        return {
            "ok": False,
            "code": "NO_ISOLATED_SESSION",
            "error": "the session's save folder is gone; the session has stopped",
        }
    except (OSError, ValueError) as exc:
        # A linked root or savedir, a savedir that is a file, no permission.
        return {
            "ok": False,
            "code": "SAVE_IMPORT_FAILED",
            "error": str(exc),
            "imported": [],
            "savedir": str(dest),
        }

    missing = list(selection.get("missing") or [])
    imported: list[dict[str, Any]] = []
    refused: list[dict[str, str]] = []
    for record in selection.get("slots") or []:
        name = record["name"]
        directory = Path(record["directory"])
        try:
            copied, slot_refused = _copy_slot_files(directory, dest, name)
        except FileNotFoundError as exc:
            if dest.is_dir():
                # The savedir is still there (a temp file removed before its
                # rename, say): the import failed, the session did not stop.
                return {
                    "ok": False,
                    "code": "SAVE_IMPORT_FAILED",
                    "error": str(exc),
                    "imported": imported,
                    "savedir": str(dest),
                }
            # The session stopped mid-copy and its savedir went with it.
            return {
                "ok": False,
                "code": "NO_ISOLATED_SESSION",
                "error": "the session's save folder is gone; the session has stopped",
            }
        except (OSError, ValueError) as exc:
            # A linked savedir, an over-quota slot, a full disk: nothing
            # was written outside the session, but the import is incomplete.
            return {
                "ok": False,
                "code": "SAVE_IMPORT_FAILED",
                "error": str(exc),
                "imported": imported,
                "savedir": str(dest),
            }
        refused.extend(slot_refused)
        if copied:
            imported.append({"name": name, "files": copied, "directory": str(directory)})
        elif not slot_refused:
            missing.append(name)  # its files vanished since the listing

    if not imported:
        failure: dict[str, Any] = {
            "ok": False,
            "code": "NO_MATCHING_SAVE_SLOTS",
            "error": (
                "no host save slot was imported; see refused"
                if refused
                else "no matching host save slots"
            ),
            "missing": missing,
            "savedir": str(dest),
        }
        if refused:
            failure["refused"] = refused
        return failure
    result: dict[str, Any] = {
        "ok": True,
        "imported": imported,
        "savedir": str(dest),
    }
    if missing:
        result["missing"] = missing
    if refused:
        result["refused"] = refused
    return result


def _slots_in_directory(
    directory: Path,
    *,
    keep: Callable[[str], bool] | None = None,
    with_extra_info: bool = True,
) -> list[dict[str, Any]]:
    """Slots in one folder; *keep* filters names before any sidecar is read."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    by_name: dict[str, dict[str, Any]] = {}
    for entry in entries:
        try:
            if not entry.is_file() or entry.is_symlink():
                continue
        except OSError:
            continue
        match = SLOT_FILE_RE.match(entry.name)
        if match is None or match.group("json"):
            continue
        name = match.group("slot")
        if keep is not None and not keep(name):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            mtime = None
        previous = by_name.get(name)
        if previous is not None and (previous.get("mtime") or 0) >= (mtime or 0):
            continue
        record: dict[str, Any] = {"name": name}
        if with_extra_info:
            record["extra_info"] = extra_info_from_sidecar(entry.with_name(entry.name + ".json"))
        record["mtime"] = mtime
        record["directory"] = str(directory)
        by_name[name] = record
    return [by_name[name] for name in sorted(by_name)]


def extra_info_from_sidecar(path: Path) -> str:
    # A linked sidecar could point at any JSON file this process can read, so
    # the leaf is opened without following links at an anchored directory.
    # The parent was resolved once by host_save_directories; resolving it
    # again here would follow a folder swapped for a link since then.
    try:
        fd, _st = open_regular_at_nofollow(path.parent, path.name)
        try:
            data = _read_at_most(fd, SIDECAR_MAX_BYTES)
        finally:
            os.close(fd)
        if data is None:
            return ""
        payload = json.loads(data.decode("utf-8"))
    except (OSError, ValueError, RuntimeError):  # RuntimeError: link loops, deep JSON
        return ""
    if isinstance(payload, dict):
        value = payload.get("_save_name", payload.get("extra_info", ""))
        return value if isinstance(value, str) else ""
    if isinstance(payload, list) and payload:
        last = payload[-1]
        if isinstance(last, dict):
            value = last.get("_save_name", "")
            return value if isinstance(value, str) else ""
    return ""


def _copy_slot_files(
    source_dir: Path, dest_dir: Path, slot: str
) -> tuple[list[str], list[dict[str, str]]]:
    """Copy one slot's files; return the names copied and the files refused.

    A sidecar (``.save.json``) is copied only with its own save file, so a
    slot is never left as a sidecar alone. A file that vanished since the
    listing is skipped; one that is a link, not a regular file, too large or
    unreadable is refused and named, never silently dropped.
    """
    pattern = re.compile(
        r"^%s-[A-Za-z]{2}\d+\.save(?:\.json)?$" % (re.escape(slot),)
    )
    copied: list[str] = []
    refused: list[dict[str, str]] = []
    # Both directories are used as given and never resolved: every open walks
    # them without following links, so a directory or file swapped for a
    # link after the check is refused, not followed. The listing may follow
    # a swapped folder, but it only yields names.
    try:
        names = sorted(os.listdir(source_dir))
    except OSError:
        return copied, refused
    saves = [name for name in names if pattern.match(name) and not name.endswith(".json")]
    sidecars = {name for name in names if pattern.match(name) and name.endswith(".json")}
    for save_name in saves:
        if not _copy_one_slot_file(source_dir, dest_dir, slot, save_name, copied, refused):
            continue
        sidecar = save_name + ".json"
        if sidecar in sidecars:
            _copy_one_slot_file(source_dir, dest_dir, slot, sidecar, copied, refused)
    return copied, refused


def _copy_one_slot_file(
    source_dir: Path,
    dest_dir: Path,
    slot: str,
    name: str,
    copied: list[str],
    refused: list[dict[str, str]],
) -> bool:
    """Copy *name*; record it in *copied* or *refused*. True when copied.

    Source-side problems are refusals. A destination failure (a linked
    savedir, a full disk) raises: the import cannot go on.
    """
    try:
        fd, st = open_regular_at_nofollow(source_dir, name)
    except FileNotFoundError:
        return False  # vanished since the listing
    except (OSError, ValueError) as exc:
        refused.append({"name": slot, "file": name, "reason": _refusal_reason(exc)})
        return False
    try:
        if st.st_size > SLOT_FILE_MAX_BYTES:
            refused.append({"name": slot, "file": name, "reason": "too_large"})
            return False
        try:
            replace_file_at_nofollow(
                dest_dir,
                name,
                fd,
                mode=stat.S_IMODE(st.st_mode),
                mtime_ns=st.st_mtime_ns,
                max_bytes=SLOT_FILE_MAX_BYTES,
            )
        except OSError as exc:
            if exc.errno != errno.EFBIG:
                raise
            # It grew past the cap during the copy; nothing was published.
            refused.append({"name": slot, "file": name, "reason": "too_large"})
            return False
    finally:
        os.close(fd)
    copied.append(name)
    return True


def _refusal_reason(exc: BaseException) -> str:
    """Short token for why a source file was not opened."""
    if isinstance(exc, ValueError):
        return "link"  # a folder on the way was swapped for a link
    code = getattr(exc, "errno", None)
    if code in {errno.ELOOP, getattr(errno, "EMLINK", errno.ELOOP)}:
        return "link"
    if code in {errno.EINVAL, errno.ENXIO, errno.ENODEV, errno.ENOTDIR, errno.EISDIR}:
        return "not_regular"
    return "unreadable"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _prepare_isolated_home(home: Path) -> None:
    for path in set(_home_paths(home).values()):
        path.mkdir(parents=True, exist_ok=True)


def _home_paths(home: Path) -> dict[str, Path]:
    """One mapping drives both directory creation and child environment."""
    paths = {
        "HOME": home,
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local" / "share",
        "TMPDIR": home / "tmp",
        "TMP": home / "tmp",
        "TEMP": home / "tmp",
    }
    if os.name == "nt":
        paths.update({
            "USERPROFILE": home,
            "APPDATA": home / "AppData" / "Roaming",
            "LOCALAPPDATA": home / "AppData" / "Local",
        })
    return paths


def _home_environ(home: Path, *, host_env: Mapping[str, str] | None) -> dict[str, str]:
    env = {name: str(path) for name, path in _home_paths(home).items()}
    env.update(_preserve_x11_auth(os.environ if host_env is None else host_env))
    return env


def _preserve_x11_auth(host_env: Mapping[str, str]) -> dict[str, str]:
    """Keep the host Xauthority file reachable after HOME is replaced."""
    extra: dict[str, str] = {}
    auth = host_env.get("XAUTHORITY")
    if auth:
        auth_path = Path(auth).expanduser()
        if auth_path.is_file():
            extra["XAUTHORITY"] = str(auth_path.resolve())
            return extra
    host_home = host_env.get("HOME")
    if host_home:
        xauth = Path(host_home) / ".Xauthority"
        if xauth.is_file():
            extra["XAUTHORITY"] = str(xauth.resolve())
    return extra
