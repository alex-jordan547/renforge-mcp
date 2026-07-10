# RenForge

[![PyPI](https://img.shields.io/pypi/v/renforge)](https://pypi.org/project/renforge/)
[![Python](https://img.shields.io/pypi/pyversions/renforge)](https://pypi.org/project/renforge/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/alex-jordan547/renforge-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/alex-jordan547/renforge-mcp/actions/workflows/ci.yml)

RenForge is an **MCP (Model Context Protocol) server, CLI, and web dashboard**
for working with [Ren'Py](https://www.renpy.org/) visual-novel projects.

It lets an AI agent — or a human via the dashboard — inspect a project, launch
and drive a running game, read/write game state, capture screenshots, generate
translations, find orphaned assets, run builds, and search Ren'Py's docs.

> Status: **alpha**, actively developed. The core surfaces (MCP tools, in-game
> bridge, CLI, dashboard) are functional; APIs may still change.

![RenForge dashboard — live control of a running game](.github/screenshots/live.png)

<table>
  <tr>
    <td width="50%">
      <img src=".github/screenshots/storymap.png" alt="Story map — interactive graph of labels and transitions" />
    </td>
    <td width="50%">
      <img src=".github/screenshots/assets.png" alt="Assets — orphaned, missing and undefined asset audit" />
    </td>
  </tr>
  <tr>
    <td align="center"><em>Story map — click a node to warp the running game</em></td>
    <td align="center"><em>Asset audit — orphans, missing files, undefined images</em></td>
  </tr>
</table>

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
```

Prefer pip? `pip install renforge` gives you the MCP server and CLI;
`pip install "renforge[ui]"` adds the dashboard.

## Use with your AI agent

The MCP server command is the same everywhere:

```bash
uvx renforge serve
```

Every RenForge tool takes a `project_path` argument, so the agent passes your
game's path on each call — no path substitution in the configs below. Copy them
as-is. The optional `--project` flag on `renforge serve` still sets a default
project for activity logging.

| Client | Where to configure |
| --- | --- |
| [Claude Code](#claude-code) | `claude mcp add` (one command) |
| [Claude Desktop](#claude-desktop-cursor-windsurf-cline-gemini-cli) | `claude_desktop_config.json` |
| [Cursor](#claude-desktop-cursor-windsurf-cline-gemini-cli) | `.cursor/mcp.json` |
| [Windsurf](#claude-desktop-cursor-windsurf-cline-gemini-cli) | `~/.codeium/windsurf/mcp_config.json` |
| [Cline](#claude-desktop-cursor-windsurf-cline-gemini-cli) | `cline_mcp_settings.json` |
| [Gemini CLI](#claude-desktop-cursor-windsurf-cline-gemini-cli) | `~/.gemini/settings.json` |
| [VS Code (Copilot)](#vs-code-github-copilot) | `.vscode/mcp.json` |
| [Zed](#zed) | `settings.json` → `context_servers` |
| [Codex CLI](#codex-cli) | `codex mcp add` or `~/.codex/config.toml` |

### Claude Code

```bash
claude mcp add renforge -- uvx renforge serve
```

### Claude Desktop, Cursor, Windsurf, Cline, Gemini CLI

These clients share the same `mcpServers` JSON shape — add this to the config
file listed in the table above:

```json
{
  "mcpServers": {
    "renforge": {
      "command": "uvx",
      "args": ["renforge", "serve"]
    }
  }
}
```

### VS Code (GitHub Copilot)

`.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "renforge": {
      "command": "uvx",
      "args": ["renforge", "serve"]
    }
  }
}
```

### Zed

In `settings.json`:

```json
{
  "context_servers": {
    "renforge": {
      "source": "custom",
      "command": "uvx",
      "args": ["renforge", "serve"]
    }
  }
}
```

### Codex CLI

```bash
codex mcp add renforge -- uvx renforge serve
```

Or edit `~/.codex/config.toml` (on Windows: `%USERPROFILE%\.codex\config.toml`):

```toml
[mcp_servers.renforge]
command = "uvx"
args = ["renforge", "serve"]
```

### Verify it works

After configuring your client, ask the agent:

> Inspect my Ren'Py project at /path/to/game

The agent should call `renforge_inspect_project` with that path and return a
JSON summary of the project (labels, scripts, assets, and related metadata).

> **Windows / GUI clients:** Desktop apps (Claude Desktop, Cursor, etc.) may not
> inherit your shell `PATH`. If `uvx` is not found, set `command` to the
> absolute path of `uvx` (for example `C:\Users\you\.local\bin\uvx.exe`).

> Don't have `uv`? Replace `uvx renforge` with `renforge` after a
> `pip install renforge`.

## Install (dev)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .             # MCP runtime (includes fastmcp)
pip install -e ".[fastmcp]"  # alias for the base install
pip install -e ".[ui]"       # dashboard (starlette, uvicorn, watchfiles)
pip install -e ".[test]"     # pytest
```

The server falls back to a compatibility mode with a clear message if
`fastmcp` is not installed (for example after a minimal manual install).

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
