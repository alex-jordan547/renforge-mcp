# RenForge

RenForge is a lightweight **MCP** (Model Context Protocol) server scaffold for
Ren'Py project tooling.

This repository is currently **alpha** and intentionally small: it provides a
runnable CLI, a minimal MCP entrypoint, and room for future project/bridge
features.

## Install (dev)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[fastmcp]"  # optional: installs fastmcp for full MCP runtime
```

## Usage

```bash
renforge --version
renforge inspect <project>
renforge serve [--project <project>]
```

`renforge --version` prints the package version.
`renforge inspect` prints a lightweight project summary.
`renforge serve` starts the MCP server when MCP/fastmcp is available, otherwise
starts in compatibility mode with a clear message.

## Project status

This is an **alpha** foundation focused on:

- Public packaging via `hatchling`
- `renforge` console entrypoint (`renforge = renforge.cli:main`)
- Minimal CLI (`--version`, `serve`, `inspect`)
- Minimal MCP app loader with graceful fallback when optional MCP dependencies are
  missing

The in-game bridge is live: it injects into a running project, publishes its
port/token, and lets a client inspect state, eval expressions, read/write
variables, and capture screenshots (the AI can literally see the game). The web
dashboard remains a V2 goal.
