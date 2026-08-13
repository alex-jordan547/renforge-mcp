#!/usr/bin/env bash
# Setup script for RenForge cloud/CI development environments.
# Installs Ren'Py SDK, Python dependencies, and configures virtual display.
#
# Usage:
#   bash scripts/setup_cloud_env.sh [--renpy-version VERSION]
#
# Environment variables:
#   RENPY_SDK_VERSION       - Override Ren'Py version (default: 8.5.3)
#   RENPY_SDK_CACHE_DIR     - SDK cache location (default: ~/.cache/renforge/sdks)
#   DISPLAY                 - X11 display (auto-configured if missing on headless)
#
# Exit codes:
#   0  - Success
#   1  - General error
#   2  - Missing dependencies

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=lib/logging.sh
. "$SCRIPT_DIR/lib/logging.sh"
# shellcheck source=lib/display.sh
. "$SCRIPT_DIR/lib/display.sh"

RENPY_VERSION="${RENPY_SDK_VERSION:-8.5.3}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --renpy-version)
            RENPY_VERSION="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--renpy-version VERSION]"
            echo ""
            echo "Setup script for RenForge cloud/CI development environments."
            echo ""
            echo "Options:"
            echo "  --renpy-version VERSION   Specify Ren'Py SDK version (default: 8.5.3)"
            echo "  -h, --help               Show this help message"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

log_info "RenForge Cloud Environment Setup"
log_info "================================="
log_info "Project root: $PROJECT_ROOT"
log_info "Ren'Py version: $RENPY_VERSION"

log_info "Checking system requirements..."

if ! command -v python3 >/dev/null 2>&1; then
    log_error "python3 not found. Please install Python 3.11 or later."
    exit 2
fi

PYTHON_VERSION="$(python3 --version | awk '{print $2}')"
log_info "Python version: $PYTHON_VERSION"

PYTHON_MAJOR="${PYTHON_VERSION%%.*}"
PYTHON_MINOR="${PYTHON_VERSION#*.}"
PYTHON_MINOR="${PYTHON_MINOR%%.*}"
if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]; }; then
    log_error "Python 3.11 or later is required (found $PYTHON_VERSION)"
    exit 2
fi

log_info "Installing system dependencies for headless Ren'Py..."

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    SUDO="sudo"
else
    SUDO=""
fi

if command -v apt-get >/dev/null 2>&1; then
    log_info "Detected apt-get package manager"
    log_info "Updating package list..."
    $SUDO apt-get update -qq
    log_info "Installing Xvfb and X11 utilities..."
    if ! $SUDO apt-get install -y -qq xvfb x11-utils; then
        log_error "Failed to install Xvfb/X11 utilities"
        exit 2
    fi
    log_success "System dependencies installed"
elif command -v yum >/dev/null 2>&1; then
    log_info "Detected yum package manager"
    log_info "Installing Xvfb and X11 utilities..."
    if ! $SUDO yum install -y -q xorg-x11-server-Xvfb xorg-x11-utils; then
        log_error "Failed to install Xvfb/X11 utilities"
        exit 2
    fi
    log_success "System dependencies installed"
else
    log_warning "Unknown package manager. Please ensure Xvfb is installed manually."
    log_warning "Required packages: xvfb, x11-utils (or equivalent)"
    if ! command -v Xvfb >/dev/null 2>&1 || ! command -v xdpyinfo >/dev/null 2>&1; then
        log_error "Xvfb and xdpyinfo are required. Install them, then re-run this script."
        exit 2
    fi
fi

log_info "Configuring virtual display..."
if ! ensure_virtual_display; then
    exit 1
fi

log_info "Building frontend static assets..."
cd "$PROJECT_ROOT"

# The package build force-includes src/renforge/ui/static, so it must exist
# before pip/uv install.
if [ -d src/renforge/ui/static ] && [ -n "$(ls -A src/renforge/ui/static 2>/dev/null)" ]; then
    log_success "Frontend static assets already present — skipping build"
else
    log_info "Frontend assets missing, building now..."
    if ! command -v npm >/dev/null 2>&1; then
        log_warning "npm not found. Installing dummy static directory to allow Python install."
        log_warning "The web dashboard will not work without a proper frontend build."
        mkdir -p src/renforge/ui/static
        echo '{"note": "Dummy static dir for pip install"}' > src/renforge/ui/static/placeholder.json
    else
        cd ui
        log_info "Installing frontend dependencies..."
        if ! npm ci --silent; then
            log_error "npm ci failed"
            exit 1
        fi
        log_info "Building frontend..."
        if ! npm run build --silent; then
            log_error "Frontend build failed"
            exit 1
        fi
        cd "$PROJECT_ROOT"
        log_success "Frontend built successfully"
    fi
fi

log_info "Installing Python dependencies..."
cd "$PROJECT_ROOT"

if command -v uv >/dev/null 2>&1; then
    log_info "Using uv for dependency installation"
    uv sync --all-extras
    log_success "Dependencies installed via uv"
else
    log_info "uv not found, using pip"
    python3 -m pip install --quiet --upgrade pip
    python3 -m pip install --quiet -e ".[fastmcp,ui,test]"
    log_success "Dependencies installed via pip"
fi

log_info "Installing Ren'Py SDK $RENPY_VERSION..."
PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:$PYTHONPATH}" python3 -c "
from renforge.sdk import get_or_install_sdk
sdk_path = get_or_install_sdk('$RENPY_VERSION')
print(f\"Ren'Py SDK installed at: {sdk_path}\")
"
log_success "Ren'Py SDK $RENPY_VERSION installed"

log_info "Verifying installation..."
PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:$PYTHONPATH}" python3 -c "
import renforge
print('RenForge import ok')
"
log_success "Installation verified"

echo ""
log_success "========================================="
log_success "Cloud environment setup complete!"
log_success "========================================="
echo ""
log_info "Environment details:"
log_info "  Python:      $(python3 --version | awk '{print $2}')"
log_info "  Ren'Py SDK:  $RENPY_VERSION"
log_info "  Display:     $DISPLAY"
log_info "  Project:     $PROJECT_ROOT"
log_info "  Env file:    $RENFORGE_CLOUD_ENV_FILE"
echo ""
log_info "Next steps:"
log_info "  1. Run smoke test:  bash scripts/smoke_renpy_env.sh"
log_info "  2. Run tests:       pytest"
log_info "  3. Run live tests:  bash scripts/run_live_editor_suites.sh"
echo ""
log_info "DISPLAY is persisted in $RENFORGE_CLOUD_ENV_FILE so later shells"
log_info "and scripts/smoke_renpy_env.sh can rediscover the virtual display."
echo ""
