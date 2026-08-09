from __future__ import annotations

import asyncio
import base64
import os
from contextlib import asynccontextmanager
import json
import string
import threading
import webbrowser
from importlib.metadata import PackageNotFoundError, version as _package_version
from pathlib import Path, PurePosixPath
from secrets import token_urlsafe
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .. import session_registry
from ..tools import live
from ..tools import project_ops
from ..lint import run_lint
from .activity import read_recent_activity, tail_activity
from .graph import build_story_map, resolve_game_file_path, resolve_warp_target
from .errors import error_response
from .poller import poll_bridge
from .ws import WebSocketHub, build_ws_envelope


def _renforge_version() -> str:
    try:
        return _package_version("renforge")
    except PackageNotFoundError:
        return "dev"


def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def _ui_assets_missing(static_root: Path) -> JSONResponse:
    return error_response(
        code="ui_assets_missing",
        error="UI assets are not built yet",
        status_code=503,
        details={
            "path": str(static_root),
            "hint": "cd ui && npm ci && npm run build or install a RenForge wheel",
        },
    )


def _unauthorized(_request: Request) -> JSONResponse:
    return error_response(
        code="invalid_token",
        error="invalid token",
        status_code=401,
        details={},
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_autopilot(project_root: Path) -> dict[str, Any]:
    path = project_root / ".renforge" / "autopilot.json"
    if not path.exists():
        return {"ok": False, "error": "coverage file not found"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "error": "cannot read coverage"}
    if isinstance(payload, dict):
        return {"ok": True, "path": str(path), "coverage": payload}
    return {"ok": False, "error": "coverage file has invalid JSON format"}


async def _read_json(request: Request) -> dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        return {}


def _project_markers(path: Path) -> list[str]:
    markers = ["game"] if (path / "game").is_dir() else []
    for marker in ("game/options.rpy", "game/script.rpy"):
        if (path / marker).is_file():
            markers.append(marker)
    return markers


def _is_renpy_project(path: Path) -> bool:
    return path.is_dir() and (path / "game").is_dir()


def _project_browser_roots(project_root: Path) -> dict[str, tuple[str, Path]]:
    candidates = [
        ("current-project", "Current project", project_root),
        ("project-parent", "Current project parent", project_root.parent),
        ("home", "Home", Path.home()),
    ]
    if os.name == "nt":
        candidates.extend(
            (f"drive-{letter.lower()}", f"Drive {letter}:", Path(f"{letter}:\\"))
            for letter in string.ascii_uppercase
            if Path(f"{letter}:\\").is_dir()
        )
    else:
        # Under WSL the Windows drives are mounted below /mnt.
        candidates.append(("windows-drives", "Windows drives", Path("/mnt")))
    roots: dict[str, tuple[str, Path]] = {}
    seen: set[Path] = set()
    for root_id, label, path in candidates:
        resolved = path.expanduser().resolve()
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        roots[root_id] = (label, resolved)
    return roots


def _resolve_browser_path(root: Path, raw_path: str) -> Path:
    if not isinstance(raw_path, str) or "\x00" in raw_path or "\\" in raw_path:
        raise ValueError("invalid folder path")
    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError("folder path must stay inside the selected root")

    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("symbolic links cannot be selected")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("folder path must stay inside the selected root")
    return resolved


def _browse_project_directories(project_root: Path, root_id: str | None, raw_path: str) -> dict[str, Any]:
    roots = _project_browser_roots(project_root)
    selected_root_id = root_id or next(iter(roots), "")
    selected = roots.get(selected_root_id)
    if selected is None:
        return {"ok": False, "error": "unknown browse root"}

    _label, root = selected
    try:
        directory = _resolve_browser_path(root, raw_path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not directory.is_dir():
        return {"ok": False, "error": "folder not found"}

    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        with os.scandir(directory) as scan:
            children = sorted(scan, key=lambda child: child.name.casefold())
    except OSError:
        return {"ok": False, "error": "folder is not accessible"}
    for child in children:
        if len(entries) >= 500:
            truncated = True
            break
        try:
            if child.name.startswith(".") or child.is_symlink() or not child.is_dir(follow_symlinks=False):
                continue
            is_project = (directory / child.name / "game").is_dir()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "path": (directory / child.name).relative_to(root).as_posix(),
                "project": is_project,
                "markers": ["game"] if is_project else [],
            }
        )

    relative = directory.relative_to(root)
    path = "" if relative == Path(".") else relative.as_posix()
    parent = relative.parent
    parent_path = "" if not path or parent == Path(".") else parent.as_posix()
    return {
        "ok": True,
        "roots": [{"id": item_id, "label": label, "path": str(root_path)} for item_id, (label, root_path) in roots.items()],
        "root_id": selected_root_id,
        "path": path,
        "parent_path": parent_path,
        "project": _is_renpy_project(directory),
        "markers": _project_markers(directory),
        "entries": entries,
        "truncated": truncated,
    }


def _list_script_files(project_root: Path) -> dict[str, Any]:
    game_root = project_root / "game"
    if not game_root.is_dir():
        return {"ok": True, "files": []}
    try:
        files = sorted(
            "game/" + path.relative_to(game_root).as_posix()
            for path in game_root.rglob("*.rpy")
            if path.is_file()
        )
    except OSError:
        return {"ok": False, "error": "could not list project scripts", "files": []}
    return {"ok": True, "files": files}


class _ProjectRuntime:
    def __init__(
        self,
        project_root: Path,
        hub: WebSocketHub,
        dashboard_url: str | None = None,
        dashboard_token: str | None = None,
    ) -> None:
        self.root = project_root
        self.hub = hub
        self.dashboard_url = dashboard_url
        self.dashboard_token = dashboard_token
        self.generation = 0
        self._lock = asyncio.Lock()
        self._stop_event: asyncio.Event | None = None
        self._tasks: list[asyncio.Task[Any]] = []

    async def start(self) -> None:
        async with self._lock:
            if self.dashboard_url is not None:
                session_registry.publish_dashboard(
                    self.root,
                    url=self.dashboard_url,
                    token=self.dashboard_token,
                )
            self._start_feeds()

    def _start_feeds(self) -> None:
        self._stop_event = asyncio.Event()
        self._tasks = [
            asyncio.create_task(poll_bridge(self.root, self.hub, self._stop_event)),
            asyncio.create_task(tail_activity(self.root, self.hub, self._stop_event)),
        ]

    async def _stop_feeds(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._stop_event = None
        self._tasks = []

    async def switch(self, target: Path) -> dict[str, Any]:
        async with self._lock:
            if target == self.root:
                return {"ok": True, "project": str(self.root), "generation": self.generation}
            current_state = await asyncio.to_thread(live.game_state, str(self.root))
            if current_state.get("ok") is True:
                return {"ok": False, "error": "stop the running game before switching projects", "running": True}
            await self._stop_feeds()
            self.root = target
            self.generation += 1
            if self.dashboard_url is not None:
                session_registry.publish_dashboard(self.root, token=self.dashboard_token)
            self._start_feeds()

        await self.hub.broadcast(
            build_ws_envelope(
                kind="project",
                type="project-changed",
                payload={"project": str(target), "generation": self.generation},
            )
        )
        return {"ok": True, "project": str(target), "generation": self.generation}

    async def shutdown(self) -> None:
        async with self._lock:
            await self._stop_feeds()
            if self.dashboard_url is not None:
                session_registry.clear_dashboard()


def create_ui_app(project_root: Path, ui_token: str, dashboard_url: str | None = None) -> Starlette:
    static_dir = _static_dir()
    assets_dir = static_dir / "assets"
    brand_dir = static_dir / "brand"
    hub = WebSocketHub()
    runtime = _ProjectRuntime(project_root, hub, dashboard_url, ui_token)

    async def _check_token(request: Request) -> bool:
        import hmac

        provided = request.query_params.get("token") or ""
        return hmac.compare_digest(str(provided).encode("utf-8"), str(ui_token).encode("utf-8"))

    async def index(_: Request):
        path = static_dir / "index.html"
        if path.exists():
            return FileResponse(path)
        return _ui_assets_missing(static_dir)

    async def health(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        return JSONResponse({"ok": True, "project": str(runtime.root)})

    async def project(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        return JSONResponse({"ok": True, "project": str(runtime.root), "version": _renforge_version()})

    async def project_browser(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        result = await asyncio.to_thread(
            _browse_project_directories,
            runtime.root,
            request.query_params.get("root_id"),
            request.query_params.get("path", ""),
        )
        if not result.get("ok"):
            error = str(result.get("error", "unknown"))
            if error == "unknown browse root":
                return error_response(
                    code="project_browser_unknown_root",
                    error=error,
                    status_code=400,
                    details={
                        "root_id": request.query_params.get("root_id"),
                        "path": request.query_params.get("path", ""),
                    },
                )
            if error == "folder path must stay inside the selected root":
                return error_response(
                    code="project_folder_outside_root",
                    error=error,
                    status_code=400,
                    details={
                        "root_id": request.query_params.get("root_id"),
                        "path": request.query_params.get("path", ""),
                    },
                )
            if error == "folder not found":
                return error_response(
                    code="project_folder_not_found",
                    error=error,
                    status_code=400,
                    details={
                        "root_id": request.query_params.get("root_id"),
                        "path": request.query_params.get("path", ""),
                    },
                )
            if error == "folder is not accessible":
                return error_response(
                    code="project_folder_not_accessible",
                    error=error,
                    status_code=400,
                    details={
                        "root_id": request.query_params.get("root_id"),
                        "path": request.query_params.get("path", ""),
                    },
                )
            return error_response(
                code="project_browser_failed",
                error=error,
                status_code=400,
                details={
                    "root_id": request.query_params.get("root_id"),
                    "path": request.query_params.get("path", ""),
                },
            )
        return JSONResponse(result)

    async def select_project(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        payload = _as_dict(await _read_json(request))
        root_id = payload.get("root_id")
        raw_path = payload.get("path")
        if not isinstance(root_id, str) or not isinstance(raw_path, str):
            return error_response(
                code="project_selection_payload_invalid",
                error="root_id and path are required",
                status_code=400,
                details={"root_id": root_id, "path": raw_path},
            )
        roots = _project_browser_roots(runtime.root)
        selected = roots.get(root_id)
        if selected is None:
            return error_response(
                code="project_browser_unknown_root",
                error="unknown browse root",
                status_code=400,
                details={"root_id": root_id},
            )
        try:
            target = _resolve_browser_path(selected[1], raw_path)
        except ValueError as exc:
            error = str(exc)
            if error == "folder path must stay inside the selected root":
                return error_response(
                    code="project_folder_outside_root",
                    error=error,
                    status_code=400,
                    details={"root_id": root_id, "path": raw_path},
                )
            return error_response(
                code="project_folder_invalid",
                error=error,
                status_code=400,
                details={"root_id": root_id, "path": raw_path},
            )
        if not target.is_dir():
            return error_response(
                code="project_folder_not_found",
                error="folder not found",
                status_code=404,
                details={"root_id": root_id, "path": raw_path},
            )
        if not _is_renpy_project(target):
            return error_response(
                code="project_not_renpy_project",
                error="selected folder is not a Ren'Py project (missing game/)",
                status_code=422,
                details={"root_id": root_id, "path": raw_path},
            )
        result = await runtime.switch(target)
        if not result.get("ok"):
            running = bool(result.get("running"))
            return error_response(
                code="project_switch_blocked",
                error=(
                    "stop the running game before switching projects"
                    if running
                    else "project switch blocked"
                ),
                status_code=409,
                details={
                    "root_id": root_id,
                    "path": raw_path,
                    "running": running,
                },
            )
        return JSONResponse(result)

    async def story_map(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        result = build_story_map(str(runtime.root))
        if not result.get("ok"):
            message = str(result.get("error", "unknown"))
            if message.startswith("Project root does not exist"):
                return error_response(
                    code="story_map_root_missing",
                    error="project root does not exist",
                    status_code=200,
                    details={},
                )
            return error_response(
                code="story_map_failed",
                error="story map failed",
                status_code=200,
                details={},
            )
        return JSONResponse(result)

    async def coverage(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        result = _read_autopilot(runtime.root)
        if not result.get("ok"):
            message = str(result.get("error", "unknown"))
            if message.startswith("coverage file not found"):
                return error_response(
                    code="coverage_file_missing",
                    error=message,
                    status_code=200,
                    details={},
                )
            return error_response(
                code="coverage_read_failed",
                error="cannot read coverage",
                status_code=200,
                details={},
            )
        return JSONResponse(result)

    async def activity_recent(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)

        raw_limit = request.query_params.get("n", "20")
        try:
            limit = int(raw_limit)
            if limit < 0:
                limit = 0
        except (TypeError, ValueError):
            return error_response(
                code="timeline_limit_invalid",
                error="n must be a non-negative integer",
                status_code=400,
                details={"n": raw_limit},
            )

        return JSONResponse({"ok": True, "events": read_recent_activity(runtime.root, limit=limit)})

    async def assets(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        result = project_ops.assets(str(runtime.root))
        if (not result.get("ok") and result.get("error") is not None) or ("ok" not in result and "error" in result):
            message = str(result.get("error", "unknown"))
            if message.startswith("no game/"):
                return error_response(
                    code="assets_game_root_missing",
                    error="no game/ directory found",
                    status_code=200,
                    details={},
                )
            return error_response(
                code="assets_read_failed",
                error="assets read failed",
                status_code=200,
                details={},
            )
        return JSONResponse(result)

    async def languages(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        return JSONResponse(project_ops.languages(str(runtime.root)))

    async def translation_stats(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        language = request.query_params.get("language")
        if not language:
            return error_response(
                code="translation_language_missing",
                error="language is required",
                status_code=400,
                details={"parameter": "language"},
            )
        return JSONResponse(project_ops.translation_stats(str(runtime.root), language))

    async def translation_strings(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        language = request.query_params.get("language")
        if not language:
            return error_response(
                code="translation_language_missing",
                error="language is required",
                status_code=400,
                details={"parameter": "language"},
            )
        from ..translation import list_translation_strings
        return JSONResponse({"ok": True, "strings": list_translation_strings(runtime.root, language)})

    async def file(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        raw_path = request.query_params.get("path", "")
        result = resolve_game_file_path(str(runtime.root), raw_path)
        if not result.get("ok"):
            message = str(result.get("error", "unknown"))
            if (
                message == "path must be inside game/"
                or message == "path is required"
                or message == "path is required to point to a file inside game/"
                or message == "path must be relative to game/"
            ):
                return error_response(
                    code="file_path_out_of_bounds",
                    error=message,
                    status_code=400,
                    details={"path": raw_path},
                )
            if message.startswith("path does not point to a file"):
                return error_response(
                    code="file_not_found",
                    error="path does not point to a file",
                    status_code=400,
                    details={"path": raw_path},
                )
            return error_response(
                code="file_access_failed",
                error="file access failed",
                status_code=400,
                details={"path": raw_path},
            )
        status = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=status)

    async def files(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        return JSONResponse(await asyncio.to_thread(_list_script_files, runtime.root))

    async def lint(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        return JSONResponse(run_lint(str(runtime.root)))

    async def live_state(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        return JSONResponse(live.game_state(str(runtime.root)))

    async def live_choices(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        return JSONResponse(live.list_choices(str(runtime.root)))

    async def debug_events(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        raw_since = request.query_params.get("since", "0")
        try:
            since = int(raw_since)
        except (TypeError, ValueError):
            return error_response(
                code="debug_events_since_invalid",
                error="since must be an integer",
                status_code=400,
                details={"since": raw_since},
            )
        if since < 0:
            since = 0
        return JSONResponse(live.poll_events(str(runtime.root), since=since))

    def _start_live_launch(**launch_kwargs: Any) -> dict[str, Any]:
        """Own dashboard launches through live.start_launch (never bare launch_game)."""
        project_path = str(runtime.root)

        def _worker(project_root: Path, cancel_event: threading.Event) -> dict[str, Any]:
            return live.launch_game(
                str(project_root),
                cancel_event=cancel_event,
                **launch_kwargs,
            )

        return live.start_launch(
            project_path,
            _worker,
            editor=bool(launch_kwargs.get("editor", True)),
        )

    async def warp(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        payload = _as_dict(await _read_json(request))
        target = payload.get("target")
        if not isinstance(target, str) or not target:
            return error_response(
                code="warp_target_missing",
                error="target is required",
                status_code=400,
                details={"target": target},
            )
        resolved = resolve_warp_target(str(runtime.root), target)
        if not resolved.get("ok"):
            error = str(resolved.get("error", "invalid warp target"))
            return error_response(
                code="warp_target_unknown" if error.startswith("unknown label") else "warp_target_invalid",
                error=error,
                status_code=400,
                details={"target": target},
            )
        result = await asyncio.to_thread(
            _start_live_launch,
            version=str(payload.get("version") or "stable"),
            warp=str(resolved["target"]),
            editor=True,
            display=str(payload.get("display") or "auto"),
            audio=str(payload.get("audio") or "auto"),
            savedir=payload.get("savedir"),
            persistent=str(payload.get("persistent") or "existing"),
            cleanup_on_stop=bool(payload.get("cleanup_on_stop", True)),
            timeout=payload.get("timeout"),
        )
        return JSONResponse(result)

    async def advance(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        return JSONResponse(live.advance(str(runtime.root)))

    async def control(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        payload = _as_dict(await _read_json(request))
        action = payload.get("action")
        if not isinstance(action, str) or not action:
            return error_response(
                code="live_action_missing",
                error="action is required",
                status_code=400,
                details={"action": action},
            )
        return JSONResponse(live.control(str(runtime.root), action))

    async def launch(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        payload = _as_dict(await _read_json(request))
        version = payload.get("version", "stable")
        warp = payload.get("warp")
        editor = payload.get("editor", True)
        display = payload.get("display", "auto")
        audio = payload.get("audio", "auto")
        savedir = payload.get("savedir")
        persistent = payload.get("persistent", "existing")
        cleanup_on_stop = payload.get("cleanup_on_stop", True)
        timeout = payload.get("timeout")
        if not isinstance(version, str) or not version:
            return error_response(
                code="launch_version_invalid",
                error="version must be a non-empty string",
                status_code=400,
                details={"version": version},
            )
        if warp is not None and not isinstance(warp, str):
            return error_response(
                code="live_warp_invalid",
                error="warp must be a string",
                status_code=400,
                details={"warp": warp},
            )
        if not isinstance(editor, bool):
            return error_response(
                code="launch_editor_invalid",
                error="editor must be a boolean",
                status_code=400,
                details={"editor": editor},
            )
        if not isinstance(display, str) or not display:
            return error_response(
                code="launch_display_invalid",
                error="display must be a non-empty string",
                status_code=400,
                details={"display": display},
            )
        if not isinstance(audio, str) or not audio:
            return error_response(
                code="launch_audio_invalid",
                error="audio must be a non-empty string",
                status_code=400,
                details={"audio": audio},
            )
        if savedir is not None and not isinstance(savedir, str):
            return error_response(
                code="launch_savedir_invalid",
                error="savedir must be a string",
                status_code=400,
                details={"savedir": savedir},
            )
        if not isinstance(persistent, str) or not persistent:
            return error_response(
                code="launch_persistent_invalid",
                error="persistent must be a non-empty string",
                status_code=400,
                details={"persistent": persistent},
            )
        if not isinstance(cleanup_on_stop, bool):
            return error_response(
                code="launch_cleanup_on_stop_invalid",
                error="cleanup_on_stop must be a boolean",
                status_code=400,
                details={"cleanup_on_stop": cleanup_on_stop},
            )
        if timeout is not None and not isinstance(timeout, (int, float)):
            return error_response(
                code="launch_timeout_invalid",
                error="timeout must be a number",
                status_code=400,
                details={"timeout": timeout},
            )
        result = await asyncio.to_thread(
            _start_live_launch,
            version=version,
            warp=warp,
            editor=True,
            display=display,
            audio=audio,
            savedir=savedir,
            persistent=persistent,
            cleanup_on_stop=cleanup_on_stop,
            timeout=float(timeout) if timeout is not None else None,
        )
        return JSONResponse(result)

    async def launch_status(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        result = await asyncio.to_thread(live.launch_status, str(runtime.root))
        return JSONResponse(result)

    async def stop(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        result = await asyncio.to_thread(live.stop_game, str(runtime.root))
        return JSONResponse(result)

    async def select_choice(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        payload = _as_dict(await _read_json(request))
        return JSONResponse(
            live.select_choice(
                str(runtime.root),
                text=payload.get("text"),
                index=payload.get("index"),
            )
        )

    async def eval_route(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        payload = _as_dict(await _read_json(request))
        return JSONResponse(live.eval_expr(str(runtime.root), str(payload.get("expr", ""))))

    async def set_var(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        payload = _as_dict(await _read_json(request))
        return JSONResponse(
            live.set_var(
                str(runtime.root),
                str(payload.get("name", "")),
                payload.get("value"),
            )
        )

    async def screenshot(request: Request):
        if not await _check_token(request):
            return _unauthorized(request)
        payload = _as_dict(await _read_json(request))
        try:
            width = int(payload.get("width", 0) or 0)
            height = int(payload.get("height", 0) or 0)
            png = live.screenshot_png(str(runtime.root), width=width, height=height)
        except Exception:
            return error_response(
                code="screenshot_failed",
                error="screenshot failed",
                status_code=200,
                details={},
            )
        return JSONResponse(
            {
                "ok": True,
                "format": "png",
                "base64": base64.b64encode(png).decode("ascii"),
            }
        )

    async def ws_endpoint(websocket: WebSocket):
        import hmac

        provided = websocket.query_params.get("token") or ""
        if not hmac.compare_digest(str(provided).encode("utf-8"), str(ui_token).encode("utf-8")):
            await websocket.close(code=4401)
            return

        await hub.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(websocket)

    async def on_startup() -> None:
        await runtime.start()

    async def on_shutdown() -> None:
        await runtime.shutdown()

    routes: list[Any] = [
        Route("/", index, methods=["GET"]),
        Route("/api/health", health, methods=["GET"]),
        Route("/api/project", project, methods=["GET"]),
        Route("/api/project/browser", project_browser, methods=["GET"]),
        Route("/api/project", select_project, methods=["POST"]),
        Route("/api/story-map", story_map, methods=["GET"]),
        Route("/api/coverage", coverage, methods=["GET"]),
        Route("/api/timeline/recent", activity_recent, methods=["GET"]),
        Route("/api/activity/recent", activity_recent, methods=["GET"]),
        Route("/api/assets", assets, methods=["GET"]),
        Route("/api/languages", languages, methods=["GET"]),
        Route("/api/translation-stats", translation_stats, methods=["GET"]),
        Route("/api/translation-strings", translation_strings, methods=["GET"]),
        Route("/api/file", file, methods=["GET"]),
        Route("/api/files", files, methods=["GET"]),
        Route("/api/lint", lint, methods=["GET"]),
        Route("/api/advance", advance, methods=["POST"]),
        Route("/api/live/control", control, methods=["POST"]),
        Route("/api/live/launch", launch, methods=["POST"]),
        Route("/api/live/status", launch_status, methods=["GET"]),
        Route("/api/live/stop", stop, methods=["POST"]),
        Route("/api/select-choice", select_choice, methods=["POST"]),
        Route("/api/eval", eval_route, methods=["POST"]),
        Route("/api/set-var", set_var, methods=["POST"]),
        Route("/api/live/state", live_state, methods=["GET"]),
        Route("/api/live/choices", live_choices, methods=["GET"]),
        Route("/api/debug/events", debug_events, methods=["GET"]),
        Route("/api/warp", warp, methods=["POST"]),
        Route("/api/screenshot", screenshot, methods=["POST"]),
        WebSocketRoute("/ws", ws_endpoint),
    ]

    @asynccontextmanager
    async def app_lifespan(_app: Starlette):
        await on_startup()
        try:
            yield
        finally:
            await on_shutdown()

    app = Starlette(routes=routes, lifespan=app_lifespan)

    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    if brand_dir.exists():
        app.mount("/brand", StaticFiles(directory=str(brand_dir)), name="brand")

    return app


def run_ui_server(
    project: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> int:
    from uvicorn import Config, Server

    project_root = Path(project).expanduser().resolve()
    ui_token = token_urlsafe(16)
    browser_host = "127.0.0.1" if host == "0.0.0.0" else ("[::1]" if host == "::" else host)
    dashboard_url = f"http://{browser_host}:{port}/"
    target = f"{dashboard_url}?token={ui_token}"
    app = create_ui_app(project_root, ui_token, dashboard_url=dashboard_url)
    print(f"RenForge dashboard: {target}", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(target)).start()

    server = Server(Config(app, host=host, port=port, log_level="warning"))
    server.run()
    return 0
