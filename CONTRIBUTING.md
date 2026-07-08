# Contributing to RenForge

Thanks for your interest! RenForge is young (alpha) and contributions of all
kinds are welcome: bug reports, feature ideas, docs, code.

## Reporting bugs / requesting features

Open an issue using the templates. For bugs, include your OS, Python version,
how you installed RenForge (`pip` / `uvx`), and the exact command + output.

## Development setup

```bash
git clone https://github.com/alex-jordan547/renforge-mcp.git
cd renforge-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[fastmcp,ui,test]"
pytest
```

The dashboard frontend lives in `ui/` (Vite + React + TypeScript):

```bash
cd ui
npm ci
npm run dev      # dev server proxying to the Python backend on :8765
npm run build    # builds into src/renforge/ui/static/ (committed)
```

## Pull requests

- Keep PRs focused: one change per PR.
- Run `pytest` before submitting; add tests for behavior changes.
- If you touch the frontend, run `npm run build` and commit the updated
  `src/renforge/ui/static/` assets.

## Project layout

See the [Architecture section of the README](README.md#architecture).
