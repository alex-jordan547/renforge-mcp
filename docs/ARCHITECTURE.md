# RenForge architecture

## Code layout

```
src/renforge/
  cli.py            # argparse entrypoint (inspect / serve / ui)
  server.py         # MCP app bootstrap + FastMCP/fallback; delegates tool registration
  tool_definitions.py # canonical 54-tool public contract (names, schemas, annotations)
  tool_schema.py    # shared JSON Schema fragments used by the contract catalog
  tool_registration/
    registry.py     # backend-aware ToolRegistrar; clones wrappers before annotating
    wrappers.py     # shared context, activity logging, PNG content helper
    project_analysis.py  # info, inspect, scan, lint, references
    lifecycle.py    # launch, jump, new_game, stop
    runtime_state.py # game state, eval, vars, wait, errors
    interaction.py  # control, input, saves, choices, clicks
    inspection.py   # screenshots, scene tree, measure
    scenarios.py    # run_scenario, autopilot
    content_build.py # assets, translations, builds, docs
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
  policy.py          # operation-level risk classification and enforcement
  util/             # filesystem + subprocess helpers
  sdk.py            # Ren'Py SDK download/cache
  scanner.py        # script/label/asset scanning
  lint.py           # lint runner + parsing
  autopilot.py      # branch auto-play + coverage
  translation.py    # translation generation/stats
```

The dashboard frontend sources live in `ui/` (Vite + React + TypeScript) and
build into `src/renforge/ui/static/`. CI/release regenerates this directory
with `npm --prefix ui ci && npm --prefix ui run build`, then validates bundled
assets before packaging. Generated static files stay ignored in source checkouts
and are included in wheel/sdist artifacts so PyPI and `uvx` users never need Node.

## MCP tool registration

`server.py` only constructs the FastMCP (or compatibility) app and calls
`tool_registration.register_all_tools`. Each domain module owns a disjoint
`TOOL_NAMES` tuple and the corresponding wrapper bodies. `ToolRegistrar`
looks up the matching `ToolDefinition`, clones the wrapper so registration
never mutates shared `__annotations__` or `__doc__`, then applies description,
`ToolAnnotations`, and JSON Schema metadata. Registration is fail-closed:
unknown names, parameter drift, duplicate registrations, and backends that
cannot accept required metadata raise instead of shipping a partial catalog.

The public 54-tool contract is snapshotted in
`tests/snapshots/mcp_public_tool_contract.json`. Intentional API changes must
update that file; accidental ones fail CI.

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
version with `RENPY_SDK_STABLE_VERSION`. Discovery and graph inspection never
rewrite files under an existing or shared SDK root, including read-only
installations.

## Graph inspection and `--json-dump`

Story Map uses Ren'Py's `compile --json-dump` for authoritative label
locations. The pinned default SDK is **8.5.3**. From at least Ren'Py **8.4.1**
through **8.5.x** and upstream master, `Script.namemap` is keyed by `Node`
(`self.namemap[node] = node`; `Node.__hash__` / `__eq__` use `.name`).
Released 8.5.3 `renpy/dump.py` still filters with `isinstance(name, str)`,
which drops every label. Upstream master `dump.py` unwraps `Node` keys
before that check; the namemap itself stays Node-keyed.

RenForge follows that dump unwrap without waiting on a new SDK release and
without patching installed `dump.py`. Each dump subprocess loads a temporary
`.rpe.py` adapter from `RENPY_SEARCHPATH` that wraps `renpy.dump.dump` and,
only when keys are not already strings, presents a string-keyed namemap for
the duration of the dump. Because namemap keys remain `Node` objects even
after upstream `dump.py` is fixed, the adapter still normalizes the map on
those SDKs. It is a no-op only when keys are already strings. Concurrent
inspections use separate adapter directories and cannot race on SDK source
contents.

Supported dump shapes:

- Node-keyed `namemap` on Ren'Py **8.4.1+** and **8.5.x** (including the
  default 8.5.3 SDK): the adapter presents a string-keyed copy for the dump
- String-keyed namemaps, if present: the adapter leaves the map unchanged
- Future SDKs whose `dump.py` already unwraps `Node` keys: namemap stays
  Node-keyed, so the adapter still normalizes it

## Packaging

Packaging uses `hatchling`; the console script is
`renforge = renforge.cli:main`. Optional dependency groups:

- `ui` — dashboard (starlette, uvicorn, watchfiles)
- `test` — pytest
- `fastmcp` — alias for the base install (fastmcp is a core dependency)

The server falls back to a compatibility mode with a clear message if
`fastmcp` is not installed (for example after a minimal manual install).

## Runtime policy

`policy.py` classifies high-risk MCP calls (`renforge_control`,
`renforge_saves`, `renforge_eval`, `renforge_run_scenario`) from the tool name
and validated parameters, then allows or denies them inside `_log_tool_call`
before the live implementation runs. MCP `ToolAnnotations` remain static
discovery hints; this layer is invocation-time enforcement. Defaults stay
permissive (`RENFORGE_POLICY=off`). See [POLICY.md](POLICY.md).
