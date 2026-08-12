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

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# Default Ren'Py version
RENPY_VERSION="${RENPY_SDK_VERSION:-8.5.3}"

# Parse command line arguments
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

# Step 1: Check system requirements
log_info "Checking system requirements..."

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    log_error "python3 not found. Please install Python 3.11 or later."
    exit 2
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
log_info "Python version: $PYTHON_VERSION"

# Check Python version meets minimum requirement (3.11+)
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]); then
    log_error "Python 3.11 or later is required (found $PYTHON_VERSION)"
    exit 2
fi

# Step 2: Install system dependencies for headless Ren'Py
log_info "Installing system dependencies for headless Ren'Py..."

if command -v apt-get &> /dev/null; then
    # Debian/Ubuntu
    log_info "Detected apt-get package manager"
    
    # Check if we need sudo
    if [ "$EUID" -ne 0 ]; then
        SUDO="sudo"
    else
        SUDO=""
    fi
    
    # Update package list
    log_info "Updating package list..."
    $SUDO apt-get update -qq
    
    # Install Xvfb and X11 utilities
    log_info "Installing Xvfb and X11 utilities..."
    $SUDO apt-get install -y -qq xvfb x11-utils 2>&1 | grep -v "^Selecting\|^Preparing\|^Unpacking\|^Setting up" || true
    
    log_success "System dependencies installed"
elif command -v yum &> /dev/null; then
    # RHEL/CentOS/Fedora
    log_info "Detected yum package manager"
    
    if [ "$EUID" -ne 0 ]; then
        SUDO="sudo"
    else
        SUDO=""
    fi
    
    log_info "Installing Xvfb and X11 utilities..."
    $SUDO yum install -y -q xorg-x11-server-Xvfb xorg-x11-utils
    
    log_success "System dependencies installed"
else
    log_warning "Unknown package manager. Please ensure Xvfb is installed manually."
    log_warning "Required packages: xvfb, x11-utils (or equivalent)"
fi

# Step 3: Set up virtual display (Xvfb)
log_info "Configuring virtual display..."

if [ -z "${DISPLAY:-}" ]; then
    log_info "DISPLAY not set, configuring Xvfb on :99"
    export DISPLAY=:99
    
    # Check if Xvfb is already running on :99
    if ! xdpyinfo -display :99 >/dev/null 2>&1; then
        log_info "Starting Xvfb on display :99 (1920x1080x24)..."
        Xvfb :99 -screen 0 1920x1080x24 >/dev/null 2>&1 &
        XVFB_PID=$!
        
        # Wait for Xvfb to be ready (max 30 attempts, 0.5s each = 15s total)
        for i in $(seq 1 30); do
            if xdpyinfo -display :99 >/dev/null 2>&1; then
                log_success "Xvfb started successfully"
                break
            fi
            sleep 0.5
        done
        
        # Verify Xvfb is running
        if ! xdpyinfo -display :99 >/dev/null 2>&1; then
            log_error "Failed to start Xvfb"
            exit 1
        fi
        
        # Show display info
        DIMENSIONS=$(xdpyinfo -display :99 | grep dimensions | awk '{print $2}')
        log_info "Display dimensions: $DIMENSIONS"
        
        # Save PID for cleanup
        echo "$XVFB_PID" > /tmp/renforge_xvfb.pid
    else
        log_success "Xvfb already running on :99"
    fi
else
    log_info "DISPLAY already set to: $DISPLAY"
fi

# Step 4: Build frontend static assets (required before pip install)
log_info "Building frontend static assets..."

cd "$PROJECT_ROOT"

# The package build force-includes src/renforge/ui/static, so it must exist
# before pip/uv install. Check if it already exists and is populated.
if [ -d src/renforge/ui/static ] && [ -n "$(ls -A src/renforge/ui/static 2>/dev/null)" ]; then
    log_success "Frontend static assets already present — skipping build"
else
    log_info "Frontend assets missing, building now..."
    
    # Check if Node.js is available
    if ! command -v npm &> /dev/null; then
        log_warning "npm not found. Installing dummy static directory to allow Python install."
        log_warning "The web dashboard will not work without a proper frontend build."
        mkdir -p src/renforge/ui/static
        echo '{"note": "Dummy static dir for pip install"}' > src/renforge/ui/static/placeholder.json
    else
        cd ui
        log_info "Installing frontend dependencies..."
        npm ci --silent 2>&1 | grep -v "^npm WARN\|^added\|^removed" || true
        
        log_info "Building frontend..."
        npm run build --silent
        
        cd "$PROJECT_ROOT"
        log_success "Frontend built successfully"
    fi
fi

# Step 5: Install Python dependencies
log_info "Installing Python dependencies..."

cd "$PROJECT_ROOT"

# Check if uv is available
if command -v uv &> /dev/null; then
    log_info "Using uv for dependency installation"
    uv sync --all-extras
    log_success "Dependencies installed via uv"
else
    log_info "uv not found, using pip"
    
    # Install package in editable mode with all extras
    python3 -m pip install --quiet --upgrade pip
    python3 -m pip install --quiet -e ".[fastmcp,ui,test]"
    
    log_success "Dependencies installed via pip"
fi

# Step 6: Install Ren'Py SDK
log_info "Installing Ren'Py SDK $RENPY_VERSION..."

# Use the RenForge SDK installer
PYTHONPATH="${PROJECT_ROOT}/src" python3 -c "
from renforge.sdk import get_or_install_sdk
from pathlib import Path

sdk_path = get_or_install_sdk('$RENPY_VERSION')
print(f'Ren\'Py SDK installed at: {sdk_path}')
"

log_success "Ren'Py SDK $RENPY_VERSION installed"

# Step 7: Verify installation
log_info "Verifying installation..."

# Check that renforge module can be imported
PYTHONPATH="${PROJECT_ROOT}/src" python3 -c "
import renforge
print(f'RenForge version: {renforge.__version__ if hasattr(renforge, \"__version__\") else \"dev\"}')
"

log_success "Installation verified"

# Step 8: Summary
echo ""
log_success "========================================="
log_success "Cloud environment setup complete!"
log_success "========================================="
echo ""
log_info "Environment details:"
log_info "  Python:      $(python3 --version | cut -d' ' -f2)"
log_info "  Ren'Py SDK:  $RENPY_VERSION"
log_info "  Display:     $DISPLAY"
log_info "  Project:     $PROJECT_ROOT"
echo ""
log_info "Next steps:"
log_info "  1. Run smoke test:  bash scripts/smoke_renpy_env.sh"
log_info "  2. Run tests:       pytest"
log_info "  3. Run live tests:  bash scripts/run_live_editor_suites.sh"
echo ""
log_info "To use in your shell session:"
log_info "  export DISPLAY=$DISPLAY"
log_info "  export PYTHONPATH=${PROJECT_ROOT}/src"
echo ""
