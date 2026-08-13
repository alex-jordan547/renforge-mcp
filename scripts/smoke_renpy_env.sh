#!/usr/bin/env bash
# Smoke test for RenForge cloud environment.
# Verifies that Ren'Py SDK can launch and the demo game can boot.
#
# Usage:
#   bash scripts/smoke_renpy_env.sh [--timeout SECONDS]
#
# Environment variables:
#   RENPY_SDK_VERSION       - Ren'Py version to test (default: 8.5.3)
#   DISPLAY                 - X11 display (auto-discovered if unset)
#
# Exit codes:
#   0  - Success (environment is healthy)
#   1  - Test failed
#   2  - Environment not configured

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=lib/logging.sh
. "$SCRIPT_DIR/lib/logging.sh"
# shellcheck source=lib/display.sh
. "$SCRIPT_DIR/lib/display.sh"

LAUNCH_TIMEOUT=90

while [[ $# -gt 0 ]]; do
    case $1 in
        --timeout)
            LAUNCH_TIMEOUT="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--timeout SECONDS]"
            echo ""
            echo "Smoke test for RenForge cloud environment."
            echo ""
            echo "Options:"
            echo "  --timeout SECONDS   Timeout for Ren'Py launch test (default: 90)"
            echo "  -h, --help         Show this help message"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

log_info "RenForge Environment Smoke Test"
log_info "================================"

log_info "Checking display configuration..."
if ! ensure_virtual_display; then
    log_error "Run: bash scripts/setup_cloud_env.sh"
    exit 2
fi
log_info "DISPLAY=$DISPLAY"

DIMENSIONS="$(xdpyinfo -display "$DISPLAY" | awk '/dimensions:/ {print $2; exit}')"
log_success "Display accessible: ${DIMENSIONS:-unknown}"

log_info "Checking Python environment..."
if ! command -v python3 >/dev/null 2>&1; then
    log_error "python3 not found"
    exit 2
fi
log_info "Python version: $(python3 --version | awk '{print $2}')"

log_info "Checking RenForge installation..."
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

if ! python3 -c "import renforge" 2>/dev/null; then
    log_error "RenForge module not found"
    log_error "Run: pip install -e '.[test,ui]' or uv sync --all-extras"
    exit 2
fi
log_success "RenForge module available"

log_info "Checking Ren'Py SDK installation..."
RENPY_VERSION="${RENPY_SDK_VERSION:-8.5.3}"
if ! SDK_CHECK="$(python3 -c "
from renforge.sdk import get_or_install_sdk
print(get_or_install_sdk('$RENPY_VERSION'))
")"; then
    log_error "Failed to get Ren'Py SDK"
    exit 1
fi
log_success "Ren'Py SDK $RENPY_VERSION: $SDK_CHECK"

log_info "Running Ren'Py launch smoke test..."
log_info "This will launch the demo game and verify the bridge can connect"
log_info "Timeout: ${LAUNCH_TIMEOUT}s"

SMOKE_TEST_SCRIPT="${PROJECT_ROOT}/scripts/smoke_renpy_launch.py"
if [ ! -f "$SMOKE_TEST_SCRIPT" ]; then
    log_error "Missing $SMOKE_TEST_SCRIPT"
    exit 2
fi

if python3 "$SMOKE_TEST_SCRIPT" "$LAUNCH_TIMEOUT"; then
    log_success "Ren'Py launch smoke test passed"
else
    log_error "Ren'Py launch smoke test failed"
    exit 1
fi

echo ""
log_success "========================================="
log_success "All smoke tests passed!"
log_success "========================================="
echo ""
log_info "Environment is ready for:"
log_info "  - Running RenForge tests: pytest"
log_info "  - Running live editor tests: bash scripts/run_live_editor_suites.sh"
log_info "  - Launching dashboard: renforge ui"
echo ""
