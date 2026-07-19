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

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/screenshots/live-dark.png" />
  <img src=".github/screenshots/live.png" alt="RenForge dashboard — live control of a running game" />
</picture>

<table>
  <tr>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset=".github/screenshots/storymap-dark.png" />
        <img src=".github/screenshots/storymap.png" alt="Story map — interactive graph of labels and transitions" />
      </picture>
    </td>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset=".github/screenshots/assets-dark.png" />
        <img src=".github/screenshots/assets.png" alt="Assets — orphaned, missing and undefined asset audit" />
      </picture>
    </td>
  </tr>
  <tr>
    <td align="center"><em>Story map — click a node to warp the running game</em></td>
    <td align="center"><em>Asset audit — orphans, missing files, undefined images</em></td>
  </tr>
</table>

## Quick start — dashboard

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/) — nothing else.
You don't even need Ren'Py installed: RenForge reuses an SDK it finds on your
machine, or downloads one automatically on first launch.

```bash
uvx --from "renforge[ui]@latest" renforge ui
```

Then choose your game in the dashboard's project picker — no path to type. (Or
skip the picker with `--project /path/to/your/game`.)

## Install

`uvx` needs no install at all. For a persistent `renforge` command on your
PATH, use [pipx](https://pipx.pypa.io/):

```bash
# Full install — MCP + CLI + web dashboard
pipx install "renforge[ui]"
renforge ui

# Slim install — MCP server + CLI only (no dashboard deps)
pipx install renforge
renforge serve
```

`[ui]` pulls in the optional dashboard stack (Starlette, uvicorn, watchfiles).
Skip it if you only need the MCP server or CLI.

On managed systems (Debian/Ubuntu), plain `pip install` is blocked by
[PEP 668](https://peps.python.org/pep-0668/) — use `uvx` or `pipx` instead.

## Update

| Installed with | How to update |
| --- | --- |
| `uvx … @latest` | Nothing to do — `@latest` fetches the newest release on each start |
| pipx (full or slim) | `pipx upgrade renforge` |
| pip / venv | `pip install -U "renforge[ui]"` (or `renforge` for slim) |

What's new: [CHANGELOG.md](CHANGELOG.md) ·
[GitHub releases](https://github.com/alex-jordan547/renforge-mcp/releases).

## Set up the MCP server (AI agents)

The server command is the same for every client:

```bash
uvx renforge@latest serve
```

Every RenForge tool takes a `project_path` argument, so the agent passes your
game's path on each call — copy the configs below as-is, no path substitution.

**Claude Code**

```bash
claude mcp add renforge -- uvx renforge@latest serve
```

**Codex CLI**

```bash
codex mcp add renforge -- uvx renforge@latest serve
```

**Claude Desktop, Cursor, Windsurf, Cline, Gemini CLI** — same `mcpServers`
JSON shape in each client's config file:

```json
{
  "mcpServers": {
    "renforge": {
      "command": "uvx",
      "args": ["renforge@latest", "serve"]
    }
  }
}
```

| Client | Config file |
| --- | --- |
| Claude Desktop | `claude_desktop_config.json` |
| Cursor | `.cursor/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| Cline | `cline_mcp_settings.json` |
| Gemini CLI | `~/.gemini/settings.json` |

VS Code (Copilot), Zed, Windows PATH issues, and the pipx variant are covered
in the [MCP guide](docs/MCP.md#client-configuration).

**Verify it works** — ask the agent:

> Inspect my Ren'Py project at /path/to/game

The agent should call `renforge_inspect_project` with that path and return a
JSON summary of the project (labels, scripts, assets, and related metadata).

## What it does

- **Project inspection** — summarize structure, scan scripts/labels/assets,
  parse lint output.
- **Live game control** — launch a project with an injected in-game bridge, then
  advance dialogue, list/select choices, evaluate expressions, get/set store
  variables, send focused text/key/scroll input, poll pushed events, and capture
  frames the model can literally see.
- **Autopilot** — auto-play the game across branches and report label coverage
  and crashes.
- **Assets & translations** — find orphaned/missing image+audio assets, list
  languages, compute translation stats, generate/update `game/tl/<lang>/` files,
  export dialogue as text.
- **Builds** — package desktop distributions and web builds.
- **Docs** — search and read Ren'Py's offline documentation.
- **Web dashboard** — live story map, activity log, autopilot coverage, lint
  view, and game-state controls (default `127.0.0.1:8765`).

## CLI

```bash
renforge --version
renforge inspect <project>      # lightweight project summary (JSON)
renforge serve [--project .]    # start the MCP server (stdio transport)
renforge ui [--project <project>] [--port 8765]  # start the web dashboard
```

## Documentation

- **[MCP guide](docs/MCP.md)** — full tool catalogue, agent workflows
  (hot reload, saves, pixel-perfect placement), safety guards, troubleshooting.
- **[Architecture](docs/ARCHITECTURE.md)** — code layout, live-control flow,
  Ren'Py SDK resolution, packaging.
- **[Contributing](CONTRIBUTING.md)** — dev setup, frontend build, PRs.
- **[Changelog](CHANGELOG.md)** — release history.
- `examples/demo_game/` — small sample Ren'Py project to try everything on.

## License

MIT
