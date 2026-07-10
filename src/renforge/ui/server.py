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

from ..tools import live
from ..tools import project_ops
from ..lint import run_lint
from .activity import read_recent_activity, tail_activity
from .graph import build_story_map, resolve_game_file_path, resolve_warp_target
from .poller import poll_bridge
from .ws import WebSocketHub, build_ws_envelope


def _renforge_version() -> str:
    try:
        return _package_version("renforge")
    except PackageNotFoundError:
        return "dev"


def _unauthorized() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "invalid token"}, status_code=401)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_autopilot(project_root: Path) -> dict[str, Any]:
    path = project_root / ".renforge" / "autopilot.json"
    if not path.exists():
        return {"ok": False, "error": f"coverage file not found: {path}"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"cannot read coverage: {type(exc).__name__}: {exc}"}
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
    def __init__(self, project_root: Path, hub: WebSocketHub) -> None:
        self.root = project_root
        self.hub = hub
        self.generation = 0
        self._lock = asyncio.Lock()
        self._stop_event: asyncio.Event | None = None
        self._tasks: list[asyncio.Task[Any]] = []

    async def start(self) -> None:
        async with self._lock:
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


def create_ui_app(project_root: Path, ui_token: str) -> Starlette:
    static_dir = Path(__file__).resolve().parent / "static"
    assets_dir = static_dir / "assets"
    hub = WebSocketHub()
    runtime = _ProjectRuntime(project_root, hub)

    async def _check_token(request: Request) -> bool:
        return request.query_params.get("token") == ui_token

    async def index(_: Request):
        path = static_dir / "index.html"
        if path.exists():
            return FileResponse(path)
        return JSONResponse({"ok": False, "error": "missing ui static page"}, status_code=404)

    async def health(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse({"ok": True, "project": str(runtime.root)})

    async def project(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse({"ok": True, "project": str(runtime.root), "version": _renforge_version()})

    async def project_browser(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        result = await asyncio.to_thread(
            _browse_project_directories,
            runtime.root,
            request.query_params.get("root_id"),
            request.query_params.get("path", ""),
        )
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    async def select_project(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        payload = _as_dict(await _read_json(request))
        root_id = payload.get("root_id")
        raw_path = payload.get("path")
        if not isinstance(root_id, str) or not isinstance(raw_path, str):
            return JSONResponse({"ok": False, "error": "root_id and path are required"}, status_code=400)
        roots = _project_browser_roots(runtime.root)
        selected = roots.get(root_id)
        if selected is None:
            return JSONResponse({"ok": False, "error": "unknown browse root"}, status_code=400)
        try:
            target = _resolve_browser_path(selected[1], raw_path)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        if not target.is_dir():
            return JSONResponse({"ok": False, "error": "folder not found"}, status_code=404)
        if not _is_renpy_project(target):
            return JSONResponse({"ok": False, "error": "selected folder is not a Ren'Py project (missing game/)"}, status_code=422)
        result = await runtime.switch(target)
        return JSONResponse(result, status_code=200 if result.get("ok") else 409)

    async def story_map(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(build_story_map(str(runtime.root)))

    async def coverage(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(_read_autopilot(runtime.root))

    async def activity_recent(request: Request):
        if not await _check_token(request):
            return _unauthorized()

        raw_limit = request.query_params.get("n", "20")
        try:
            limit = int(raw_limit)
            if limit < 0:
                limit = 0
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "n must be a non-negative integer"}, status_code=400)

        return JSONResponse({"ok": True, "events": read_recent_activity(runtime.root, limit=limit)})

    async def assets(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(project_ops.assets(str(runtime.root)))

    async def languages(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(project_ops.languages(str(runtime.root)))

    async def translation_stats(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        language = request.query_params.get("language")
        if not language:
            return JSONResponse({"ok": False, "error": "language is required"}, status_code=400)
        return JSONResponse(project_ops.translation_stats(str(runtime.root), language))

    async def translation_strings(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        language = request.query_params.get("language")
        if not language:
            return JSONResponse({"ok": False, "error": "language is required"}, status_code=400)
        from ..translation import list_translation_strings
        return JSONResponse({"ok": True, "strings": list_translation_strings(runtime.root, language)})

    async def file(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        raw_path = request.query_params.get("path", "")
        result = resolve_game_file_path(str(runtime.root), raw_path)
        status = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=status)

    async def files(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(await asyncio.to_thread(_list_script_files, runtime.root))

    async def lint(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(run_lint(str(runtime.root)))

    async def live_state(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(live.game_state(str(runtime.root)))

    async def live_choices(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(live.list_choices(str(runtime.root)))

    async def debug_events(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        raw_since = request.query_params.get("since", "0")
        try:
            since = int(raw_since)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "since must be an integer"}, status_code=400)
        if since < 0:
            since = 0
        return JSONResponse(live.poll_events(str(runtime.root), since=since))

    async def warp(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        payload = _as_dict(await _read_json(request))
        target = payload.get("target")
        if not isinstance(target, str) or not target:
            return JSONResponse({"ok": False, "error": "target is required"}, status_code=400)
        resolved = resolve_warp_target(str(runtime.root), target)
        if not resolved.get("ok"):
            return JSONResponse({"ok": False, "error": resolved.get("error", "invalid warp target")}, status_code=400)
        return JSONResponse(live.launch_game(str(runtime.root), warp=str(resolved["target"])))

    async def advance(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(live.advance(str(runtime.root)))

    async def control(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        payload = _as_dict(await _read_json(request))
        action = payload.get("action")
        if not isinstance(action, str) or not action:
            return JSONResponse({"ok": False, "error": "action is required"}, status_code=400)
        return JSONResponse(live.control(str(runtime.root), action))

    async def launch(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        payload = _as_dict(await _read_json(request))
        version = payload.get("version", "stable")
        warp = payload.get("warp")
        if not isinstance(version, str) or not version:
            return JSONResponse({"ok": False, "error": "version must be a non-empty string"}, status_code=400)
        if warp is not None and not isinstance(warp, str):
            return JSONResponse({"ok": False, "error": "warp must be a string"}, status_code=400)
        result = await asyncio.to_thread(
            live.launch_game,
            str(runtime.root),
            version=version,
            warp=warp,
        )
        return JSONResponse(result)

    async def stop(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        result = await asyncio.to_thread(live.stop_game, str(runtime.root))
        return JSONResponse(result)

    async def select_choice(request: Request):
        if not await _check_token(request):
            return _unauthorized()
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
            return _unauthorized()
        payload = _as_dict(await _read_json(request))
        return JSONResponse(live.eval_expr(str(runtime.root), str(payload.get("expr", ""))))

    async def set_var(request: Request):
        if not await _check_token(request):
            return _unauthorized()
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
            return _unauthorized()
        payload = _as_dict(await _read_json(request))
        try:
            width = int(payload.get("width", 0) or 0)
            height = int(payload.get("height", 0) or 0)
            png = live.screenshot_png(str(runtime.root), width=width, height=height)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=200)
        return JSONResponse(
            {
                "ok": True,
                "format": "png",
                "base64": base64.b64encode(png).decode("ascii"),
            }
        )

    async def ws_endpoint(websocket: WebSocket):
        if websocket.query_params.get("token") != ui_token:
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
    app = create_ui_app(project_root, ui_token)

    browser_host = "127.0.0.1" if host == "0.0.0.0" else ("[::1]" if host == "::" else host)
    target = f"http://{browser_host}:{port}/?token={ui_token}"
    print(f"RenForge dashboard: {target}", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(target)).start()

    server = Server(Config(app, host=host, port=port, log_level="warning"))
    server.run()
    return 0
