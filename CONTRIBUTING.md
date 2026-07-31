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
npm run build    # builds into src/renforge/ui/static/ (generated, ignored)
```

The generated `src/renforge/ui/static/` tree is not committed and is regenerated
for local testing by running `npm run build` from `ui/`.

## Pull requests

- Keep PRs focused: one change per PR.
- Run `pytest` before submitting; add tests for behavior changes.
- If you touch the frontend, run `npm run build`; generated static assets must be
  left uncommitted.

## Locale and i18n integration

- Canonical UI copy lives in `ui/src/i18n/locales/en.json`.
- `zh-CN` currently has complete coverage. If a future translation is missing,
  the dashboard intentionally displays its raw i18n key instead of silently
  falling back to English.
- Keep key paths stable. Prefer adding keys only when code needs new copy; do not
  rename existing keys unless there is duplicated/conflicting usage.
- Use interpolated values (`{{count}}`, `{{error}}`, `{{language}}`, etc.) for
  dynamic fragments and pass data through t()-calls.
- Scanner command:
  - `npm --prefix ui run i18n:check`
  - The repository enforces hardcoded text / unknown key checks and should be green
    before merge.
- Allowlist policy (in `ui/scripts/i18n-allowlist.json`):
  - Dynamic families only (`pages.translation.status.*`,
    `pages.translation.badge.*`, `pages.diagnostics.severity.*`).
  - Runtime backend `errors.<code>` translations.
  - Non-user-facing or structurally unavailable keys only.
  - Each allowlist entry requires a reason.
- `zh-CN` contribution steps:
  - Only add real Chinese translations for missing keys when available.
  - Preserve existing `zh-CN` values exactly.
  - Do not modify `zh-CN` with fake English/placeholder content.
- Backend API error handling uses `errors.<error_code>` in
  `src/renforge/ui/errors.py`; add matching canonical English entries in
  `ui/src/i18n/locales/en.json` for all stable backend codes and
  `errors.unexpected`.

## Releases and acknowledgements

- Keep contributor acknowledgements under `### Thanks` in the relevant
  `CHANGELOG.md` release entry so they appear in the GitHub release notes.
- Mention contributors by GitHub handle and add first-time contributors to the
  permanent Contributors section at the end of `README.md`.

## Project layout

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
