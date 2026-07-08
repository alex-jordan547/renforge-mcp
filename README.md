# RenForge

RenForge is an **MCP (Model Context Protocol) server, CLI, and web dashboard**
for working with [Ren'Py](https://www.renpy.org/) visual-novel projects.

It lets an AI agent — or a human via the dashboard — inspect a project, launch
and drive a running game, read/write game state, capture screenshots, generate
translations, find orphaned assets, run builds, and search Ren'Py's docs.

> Status: **alpha**, actively developed. The core surfaces (MCP tools, in-game
> bridge, CLI, dashboard) are functional; APIs may still change.

## What it does

- **Project inspection** — summarize structure, scan scripts/labels/assets,
  parse lint output.
- **Live game control** — launch a project with an injected in-game bridge, then
  advance dialogue, list/select choices, evaluate expressions, get/set store
  variables, poll pushed events, and capture frames the model can literally see.
- **Autopilot** — auto-play the game across branches and report label coverage
  and crashes.
- **Assets & translations** — find orphaned/missing image+audio assets, list
  languages, compute translation stats, generate/update `game/tl/<lang>/` files,
  export dialogue as text.
- **Builds** — package desktop distributions and web builds.
- **Docs** — search and read Ren'Py's offline documentation.
- **Web dashboard** — Starlette + WebSocket UI with a live story map, activity
  log, autopilot coverage, lint view, and game-state controls.

## Quick start

Requires Python 3.11+. With [uv](https://docs.astral.sh/uv/) installed, no
setup is needed:

```bash
# Start the web dashboard on your project
uvx --from "renforge[ui]" renforge ui --project /path/to/your/game

# Or add the MCP server to Claude Code
claude mcp add renforge -- uvx --from "renforge[fastmcp]" renforge serve --project /path/to/your/game
```

For Claude Desktop (or any MCP client using JSON config):

```json
{
  "mcpServers": {
    "renforge": {
      "command": "uvx",
      "args": [
        "--from", "renforge[fastmcp]", "renforge",
        "serve", "--project", "/path/to/your/game"
      ]
    }
  }
}
```

Prefer pip? `pip install "renforge[fastmcp,ui]"` gives you the `renforge` CLI.

## Install (dev)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[fastmcp]"  # full MCP runtime (fastmcp)
pip install -e ".[ui]"       # dashboard (starlette, uvicorn, watchfiles)
pip install -e ".[test]"     # pytest
```

The base install only requires `mcp>=1.0.0`; the server falls back to a
compatibility mode with a clear message if `fastmcp` is not installed.

## Usage

### CLI

```bash
renforge --version
renforge inspect <project>      # lightweight project summary (JSON)
renforge serve [--project .]    # start the MCP server (stdio transport)
renforge ui --project <project> [--port 8765]   # start the web dashboard
```

### MCP server

`renforge serve` exposes the tools below to any MCP client. A subset:

- `renforge_inspect_project`, `renforge_scan_project`, `renforge_parse_lint`
- `renforge_launch`, `renforge_stop`
- `renforge_game_state`, `renforge_advance`, `renforge_list_choices`,
  `renforge_select_choice`, `renforge_eval`, `renforge_get_var`,
  `renforge_set_var`, `renforge_poll_events`, `renforge_screenshot`
- `renforge_autopilot`
- `renforge_assets`, `renforge_languages`, `renforge_translation_stats`,
  `renforge_generate_translations`, `renforge_export_dialogue`
- `renforge_web_build`, `renforge_distribute`
- `renforge_search_docs`, `renforge_get_doc`, `renforge_list_docs`

### Live control

`renforge_launch` injects a bridge into `<project>/game/` (removed on teardown)
and starts the game. Live tools require a display — under WSLg it works
directly; headless CI should wrap the call with `xvfb-run`.

### Web dashboard

`renforge ui --project <project>` serves a dashboard (default `127.0.0.1:8765`)
with a story map, activity log, autopilot coverage, lint view, and live game
controls over WebSocket.

## Examples

A small sample project lives in `examples/demo_game/`.

## Architecture

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
  util/             # filesystem + subprocess helpers
  sdk.py            # Ren'Py SDK download/cache
  scanner.py        # script/label/asset scanning
  lint.py           # lint runner + parsing
  autopilot.py      # branch auto-play + coverage
  translation.py    # translation generation/stats
```

Packaging uses `hatchling`; the console script is
`renforge = renforge.cli:main`.

## License

MIT
