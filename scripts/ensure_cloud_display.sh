#!/usr/bin/env bash
# Per-boot helper for Cursor Cloud Agents: make sure Xvfb is up and DISPLAY
# is persisted for later shells. Safe to run after setup_cloud_env.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/logging.sh
. "$SCRIPT_DIR/lib/logging.sh"
# shellcheck source=lib/display.sh
. "$SCRIPT_DIR/lib/display.sh"

ensure_virtual_display
echo "DISPLAY=$DISPLAY"
echo "Python: $(python3 --version 2>/dev/null || echo 'not found')"
echo ""
echo "Run smoke test: bash scripts/smoke_renpy_env.sh"
echo "Run tests: pytest"
echo "Run live tests: bash scripts/run_live_editor_suites.sh"
