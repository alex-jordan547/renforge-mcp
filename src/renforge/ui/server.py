from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import json
import threading
import webbrowser
from pathlib import Path
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
from .ws import WebSocketHub


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


def create_ui_app(project_root: Path, ui_token: str) -> Starlette:
    static_dir = Path(__file__).resolve().parent / "static"
    assets_dir = static_dir / "assets"
    hub = WebSocketHub()
    stop_event = asyncio.Event()
    tasks: list[asyncio.Task[Any]] = []

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
        return JSONResponse({"ok": True, "project": str(project_root)})

    async def project(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse({"ok": True, "project": str(project_root)})

    async def story_map(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(build_story_map(str(project_root)))

    async def coverage(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(_read_autopilot(project_root))

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

        return JSONResponse({"ok": True, "events": read_recent_activity(project_root, limit=limit)})

    async def assets(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(project_ops.assets(str(project_root)))

    async def languages(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(project_ops.languages(str(project_root)))

    async def translation_stats(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        language = request.query_params.get("language")
        if not language:
            return JSONResponse({"ok": False, "error": "language is required"}, status_code=400)
        return JSONResponse(project_ops.translation_stats(str(project_root), language))

    async def translation_strings(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        language = request.query_params.get("language")
        if not language:
            return JSONResponse({"ok": False, "error": "language is required"}, status_code=400)
        from ..translation import list_translation_strings
        return JSONResponse({"ok": True, "strings": list_translation_strings(project_root, language)})

    async def file(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        raw_path = request.query_params.get("path", "")
        result = resolve_game_file_path(str(project_root), raw_path)
        status = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=status)

    async def lint(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(run_lint(str(project_root)))

    async def live_state(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(live.game_state(str(project_root)))

    async def live_choices(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(live.list_choices(str(project_root)))

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
        return JSONResponse(live.poll_events(str(project_root), since=since))

    async def warp(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        payload = _as_dict(await _read_json(request))
        target = payload.get("target")
        if not isinstance(target, str) or not target:
            return JSONResponse({"ok": False, "error": "target is required"}, status_code=400)
        resolved = resolve_warp_target(str(project_root), target)
        if not resolved.get("ok"):
            return JSONResponse({"ok": False, "error": resolved.get("error", "invalid warp target")}, status_code=400)
        return JSONResponse(live.launch_game(str(project_root), warp=str(resolved["target"])))

    async def advance(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        return JSONResponse(live.advance(str(project_root)))

    async def control(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        payload = _as_dict(await _read_json(request))
        action = payload.get("action")
        if not isinstance(action, str) or not action:
            return JSONResponse({"ok": False, "error": "action is required"}, status_code=400)
        return JSONResponse(live.control(str(project_root), action))

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
            str(project_root),
            version=version,
            warp=warp,
        )
        return JSONResponse(result)

    async def stop(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        result = await asyncio.to_thread(live.stop_game, str(project_root))
        return JSONResponse(result)

    async def select_choice(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        payload = _as_dict(await _read_json(request))
        return JSONResponse(
            live.select_choice(
                str(project_root),
                text=payload.get("text"),
                index=payload.get("index"),
            )
        )

    async def eval_route(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        payload = _as_dict(await _read_json(request))
        return JSONResponse(live.eval_expr(str(project_root), str(payload.get("expr", ""))))

    async def set_var(request: Request):
        if not await _check_token(request):
            return _unauthorized()
        payload = _as_dict(await _read_json(request))
        return JSONResponse(
            live.set_var(
                str(project_root),
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
            png = live.screenshot_png(str(project_root), width=width, height=height)
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
        tasks.append(asyncio.create_task(poll_bridge(project_root, hub, stop_event)))
        tasks.append(asyncio.create_task(tail_activity(project_root, hub, stop_event)))

    async def on_shutdown() -> None:
        stop_event.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    routes: list[Any] = [
        Route("/", index, methods=["GET"]),
        Route("/api/health", health, methods=["GET"]),
        Route("/api/project", project, methods=["GET"]),
        Route("/api/story-map", story_map, methods=["GET"]),
        Route("/api/coverage", coverage, methods=["GET"]),
        Route("/api/timeline/recent", activity_recent, methods=["GET"]),
        Route("/api/activity/recent", activity_recent, methods=["GET"]),
        Route("/api/assets", assets, methods=["GET"]),
        Route("/api/languages", languages, methods=["GET"]),
        Route("/api/translation-stats", translation_stats, methods=["GET"]),
        Route("/api/translation-strings", translation_strings, methods=["GET"]),
        Route("/api/file", file, methods=["GET"]),
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
