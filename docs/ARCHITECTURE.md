# RenForge architecture

## Code layout

```
src/renforge/
  cli.py            # argparse entrypoint (inspect / serve / ui)
  server.py         # MCP app bootstrap + fallback + tool registration
  bridge/           # in-game .rpy bridge, launcher, and client
  tools/
    live.py         # running-game control (launch, eval, screenshot, ...)
    project_ops.py  # assets, translations, builds, docs
    static.py       # inspect / scan / parse-lint
  ui/               # Starlette dashboard (server, ws, graph, activity, poller)
  dashboard_client.py # private display-bound delegation to the dashboard
  image_ops.py       # local/live image crop and zoom primitives
  navigation.py      # shared label and file:line warp resolution
  session_registry.py # dashboard-to-MCP active-project discovery
  symbols.py         # Ren'Py-aware token/reference lookup
  util/             # filesystem + subprocess helpers
  sdk.py            # Ren'Py SDK download/cache
  scanner.py        # script/label/asset scanning
  lint.py           # lint runner + parsing
  autopilot.py      # branch auto-play + coverage
  translation.py    # translation generation/stats
```

The dashboard frontend sources live in `ui/` (Vite + React + TypeScript) and
build into `src/renforge/ui/static/`, which is committed so the published
package ships a ready-to-serve dashboard.

## Live control flow

`renforge_launch` injects a temporary bridge into `<project>/game/` (removed on
teardown) and starts the game. If the matching dashboard is running, MCP
delegates launch to that process so it inherits the dashboard's display
environment; otherwise it launches directly (`display=auto` starts Xvfb and a
dummy SDL audio driver when no display is available). Fully headless CI can
wrap direct launches with `xvfb-run`.

MCP launch calls wait no more than 20 seconds. Slow startups continue in a
daemon launch task tracked per project; `renforge_launch_status` exposes the
`starting`, `ready`, or `failed` result, and `renforge_stop` signals the launch
task so the launcher terminates Ren'Py and removes injected artifacts.

The dashboard publishes its selected project in a per-user local runtime
registry. Agents call `renforge_info` or `renforge_context` first instead of
guessing the game path. `renforge_jump` resolves a label to `file:line` and
restarts through Ren'Py's supported warp path; `renforge_new_game` starts a
fresh process at the project's `start` label through that same path.

## Ren'Py SDK resolution

RenForge does not require a pre-installed Ren'Py. `sdk.py` first checks
conventional SDK locations inside the detected project and uses one only when
its launcher and version are compatible. It then checks the explicit
`RENPY_SDK_HOME` override before falling back to the managed
`~/.cache/renforge/sdks/` cache. Missing or invalid cached SDKs are installed
under an inter-process lock and published atomically. Override the stable
version with `RENPY_SDK_STABLE_VERSION`.

## Packaging

Packaging uses `hatchling`; the console script is
`renforge = renforge.cli:main`. Optional dependency groups:

- `ui` — dashboard (starlette, uvicorn, watchfiles)
- `test` — pytest
- `fastmcp` — alias for the base install (fastmcp is a core dependency)

The server falls back to a compatibility mode with a clear message if
`fastmcp` is not installed (for example after a minimal manual install).
