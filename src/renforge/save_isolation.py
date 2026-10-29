"""Application-layer isolation of Ren'Py saves, preferences, and HOME.

MCP and dashboard launches default to a disposable session root so a running
game cannot read or write the user's normal Ren'Py state (saves, persistent
data, preferences, and ``$HOME/.renpy``). Pass ``savedir=existing`` /
``home=existing`` / ``RENFORGE_ISOLATION=existing`` to use the user's files.

Selected host slots can be copied into an isolated session with
``renforge_saves(action="import")``. This module never bind-mounts the user
tree.

This is application-layer isolation, not an OS sandbox: the game still runs
as the user and can reach any path it names explicitly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

EXISTING_TOKENS: frozenset[str] = frozenset({"existing", "default"})
TEMPORARY_TOKENS: frozenset[str] = frozenset({"", "temporary"})
AUTO_TOKENS: frozenset[str] = frozenset({"auto"})
ISOLATION_ENV: str = "RENFORGE_ISOLATION"
SESSION_PREFIX: str = "renforge-session-"
SESSION_ID_PREFIX: str = "sess_"
MULTIPERSISTENT_DIRNAME: str = "multipersistent"
SAVES_DIRNAME: str = "saves"
HOME_DIRNAME: str = "home"
SAVE_DIRECTORY_RE: re.Pattern[str] = re.compile(
    r"^\s*define\s+config\.save_directory\s*=\s*[\"']([^\"']+)[\"']",
    re.MULTILINE,
)
SLOT_FILE_RE: re.Pattern[str] = re.compile(
    r"^(?P<slot>.+)-[A-Za-z]{2}\d+\.save(?P<json>\.json)?$"
)


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
    """Server-wide default: ``temporary`` unless ``RENFORGE_ISOLATION=existing``."""
    source = os.environ if environ is None else environ
    token = str(source.get(ISOLATION_ENV, "temporary") or "temporary").strip()
    if token in EXISTING_TOKENS:
        return "existing"
    return "temporary"


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

    Omitted fields follow ``RENFORGE_ISOLATION`` (default ``temporary``).
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
            return path_exposes_user_saves(self.savedir)
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

    All launch paths use the same isolated defaults. ``savedir=existing``
    opts into the game's normal locations and state; HOME follows that mode.
    """
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

    persistent_mode = classify_token(persistent, omitted="existing")
    if persistent_mode == "temporary":
        persistent_mode = "empty"
    preferences_mode = classify_token(preferences, omitted="existing")
    if preferences_mode == "temporary":
        preferences_mode = "empty"

    if not {persistent_mode, preferences_mode} <= {"existing", "empty"}:
        raise ValueError("Persistent/preferences modes must be 'auto', 'existing', or 'empty'.")

    if savedir_mode != "temporary" and "empty" in {persistent_mode, preferences_mode}:
        raise ValueError(
            "Empty persistent/preferences state requires savedir='temporary'; "
            "use 'existing' state modes to reuse a save directory."
        )

    needs_session = savedir_mode == "temporary" or home_mode == "temporary"
    session_root: Path | None = None
    savedir_path: Path | None = None
    home_path: Path | None = None
    cleanup = False

    if needs_session:
        session_root = Path(tempfile.mkdtemp(prefix=SESSION_PREFIX))
        cleanup = bool(cleanup_on_stop)

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
    """Return ``config.save_directory`` from ``game/*.rpy``, if defined."""
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
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        match = SAVE_DIRECTORY_RE.search(text)
        if match:
            name = match.group(1).strip()
            if name:
                return name
    return None


def host_save_directories(
    project_root: str | Path,
    *,
    home: Path | None = None,
) -> list[Path]:
    """Existing host directories that may contain this project's user saves."""
    directories: list[Path] = []
    save_name = parse_save_directory(project_root)
    if save_name:
        host_dir = host_renpy_root(home=home) / save_name
        if host_dir.is_dir():
            directories.append(host_dir)
    project_saves = Path(project_root) / "game" / "saves"
    if project_saves.is_dir() and project_saves not in directories:
        directories.append(project_saves)
    return directories


def list_host_slots(
    project_root: str | Path,
    *,
    regexp: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """List host save slots without touching a running game or writing files."""
    compiled = None
    if regexp is not None:
        if not isinstance(regexp, str):
            return {"ok": False, "error": "regexp must be a string"}
        try:
            compiled = re.compile(regexp)
        except re.error as exc:
            return {"ok": False, "error": f"invalid regexp: {exc}"}

    directories = host_save_directories(project_root, home=home)
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for directory in directories:
        for slot in _slots_in_directory(directory):
            name = slot["name"]
            if name in seen:
                continue
            if compiled is not None and compiled.search(name) is None:
                continue
            seen.add(name)
            slots.append(slot)
    return {
        "ok": True,
        "slots": slots,
        "directories": [str(path) for path in directories],
    }


def import_host_slots(
    project_root: str | Path,
    destination: str | Path,
    *,
    slot: str | None = None,
    slots: Iterable[str] | None = None,
    regexp: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Copy selected host slot files into an isolated session savedir."""
    dest = Path(destination).expanduser().resolve()
    if path_exposes_user_saves(dest, project_path=project_root, home=home):
        return {
            "ok": False,
            "code": "SAVE_IMPORT_NOT_ISOLATED",
            "error": "import refuses to copy into the user's save tree; launch an isolated session first",
        }
    dest.mkdir(parents=True, exist_ok=True)

    resolved = select_host_slot_names(
        project_root, slot=slot, slots=slots, regexp=regexp, home=home
    )
    if not resolved.get("ok"):
        return resolved
    selected = list(resolved.get("names") or [])
    missing = list(resolved.get("missing") or [])
    listed = list_host_slots(project_root, regexp=None, home=home)
    if not listed.get("ok"):
        return listed
    available = {item["name"]: item for item in listed.get("slots", [])}

    imported: list[dict[str, Any]] = []
    for name in selected:
        record = available.get(name)
        if record is None:
            continue
        directory = Path(record["directory"])
        copied = _copy_slot_files(directory, dest, name)
        imported.append({"name": name, "files": copied, "directory": str(directory)})

    if not imported:
        return {
            "ok": False,
            "error": "no matching host save slots",
            "missing": missing,
            "savedir": str(dest),
        }
    result: dict[str, Any] = {
        "ok": True,
        "imported": imported,
        "savedir": str(dest),
    }
    if missing:
        result["missing"] = missing
    return result


def select_host_slot_names(
    project_root: str | Path,
    *,
    slot: str | None = None,
    slots: Iterable[str] | None = None,
    regexp: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Resolve import selectors to host slot names."""
    names: list[str] = []
    seen: set[str] = set()

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
            return {"ok": False, "error": error}
    if slots is not None:
        if isinstance(slots, (str, bytes)) or not isinstance(slots, Iterable):
            return {"ok": False, "error": "slots must be a list of slot names"}
        for item in slots:
            error = _add(item)
            if error is not None:
                return {"ok": False, "error": error}

    compiled = None
    if regexp is not None:
        if not isinstance(regexp, str):
            return {"ok": False, "error": "regexp must be a string"}
        try:
            compiled = re.compile(regexp)
        except re.error as exc:
            return {"ok": False, "error": f"invalid regexp: {exc}"}

    if not names and compiled is None:
        return {"ok": False, "error": "import requires slot, slots, or regexp"}

    listed = list_host_slots(project_root, regexp=None, home=home)
    if not listed.get("ok"):
        return listed
    available = [item["name"] for item in listed.get("slots", [])]
    selected: list[str] = []
    missing: list[str] = []
    if names:
        available_set = set(available)
        for name in names:
            if name in available_set:
                selected.append(name)
            else:
                missing.append(name)
    if compiled is not None:
        for name in available:
            if name in selected:
                continue
            if compiled.search(name) is not None:
                selected.append(name)
    return {
        "ok": True,
        "names": selected,
        "missing": missing,
        "directories": listed.get("directories", []),
    }


def _slots_in_directory(directory: Path) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return slots
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
        sidecar = entry.with_name(entry.name + ".json")
        extra_info = extra_info_from_sidecar(sidecar)
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            mtime = None
        previous = by_name.get(name)
        if previous is not None and (previous.get("mtime") or 0) >= (mtime or 0):
            continue
        by_name[name] = {
            "name": name,
            "extra_info": extra_info,
            "mtime": mtime,
            "directory": str(directory),
        }
    slots.extend(by_name[name] for name in sorted(by_name))
    return slots


def extra_info_from_sidecar(path: Path) -> str:
    # A linked sidecar could point at any JSON file this process can read.
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
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


def _copy_slot_files(source_dir: Path, dest_dir: Path, slot: str) -> list[str]:
    pattern = re.compile(
        r"^%s-[A-Za-z]{2}\d+\.save(?:\.json)?$" % (re.escape(slot),)
    )
    copied: list[str] = []
    dest_root = dest_dir.resolve()
    source_root = source_dir.resolve()
    try:
        entries = list(source_dir.iterdir())
    except OSError:
        return copied
    for entry in entries:
        if pattern.match(entry.name) is None:
            continue
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            source = entry.resolve()
        except OSError:
            continue
        if not _is_relative_to(source, source_root):
            continue
        destination = (dest_root / source.name).resolve()
        if destination.parent != dest_root:
            continue
        shutil.copy2(source, destination)
        copied.append(source.name)
    copied.sort()
    return copied


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
