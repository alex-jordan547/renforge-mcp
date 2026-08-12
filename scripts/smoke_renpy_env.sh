#!/usr/bin/env bash
# Smoke test for RenForge cloud environment.
# Verifies that Ren'Py SDK can launch and the demo game can boot.
#
# Usage:
#   bash scripts/smoke_renpy_env.sh [--timeout SECONDS]
#
# Environment variables:
#   RENPY_SDK_VERSION       - Ren'Py version to test (default: 8.5.3)
#   DISPLAY                 - X11 display (required for headless testing)
#
# Exit codes:
#   0  - Success (environment is healthy)
#   1  - Test failed
#   2  - Environment not configured

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

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# Default timeout for launch test
LAUNCH_TIMEOUT=90

# Parse command line arguments
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

# Step 1: Check DISPLAY is set
log_info "Checking display configuration..."
if [ -z "${DISPLAY:-}" ]; then
    log_error "DISPLAY environment variable not set"
    log_error "Run: export DISPLAY=:99 (or run scripts/setup_cloud_env.sh)"
    exit 2
fi
log_info "DISPLAY=$DISPLAY"

# Verify display is accessible
if ! command -v xdpyinfo &> /dev/null; then
    log_error "xdpyinfo not found. Install x11-utils or equivalent."
    exit 2
fi

if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    log_error "Display $DISPLAY is not accessible"
    log_error "Ensure Xvfb is running: Xvfb $DISPLAY -screen 0 1920x1080x24 &"
    exit 2
fi

DIMENSIONS=$(xdpyinfo -display "$DISPLAY" | grep dimensions | awk '{print $2}')
log_success "Display accessible: $DIMENSIONS"

# Step 2: Check Python environment
log_info "Checking Python environment..."
if ! command -v python3 &> /dev/null; then
    log_error "python3 not found"
    exit 2
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
log_info "Python version: $PYTHON_VERSION"

# Step 3: Check RenForge module
log_info "Checking RenForge installation..."
export PYTHONPATH="${PROJECT_ROOT}/src"

if ! python3 -c "import renforge" 2>/dev/null; then
    log_error "RenForge module not found"
    log_error "Run: pip install -e '.[test,ui]' or uv sync --all-extras"
    exit 2
fi

log_success "RenForge module available"

# Step 4: Check Ren'Py SDK
log_info "Checking Ren'Py SDK installation..."
RENPY_VERSION="${RENPY_SDK_VERSION:-8.5.3}"

SDK_CHECK=$(python3 -c "
from renforge.sdk import get_or_install_sdk
sdk = get_or_install_sdk('$RENPY_VERSION')
print(sdk)
" 2>&1)

if [ $? -ne 0 ]; then
    log_error "Failed to get Ren'Py SDK: $SDK_CHECK"
    exit 1
fi

log_success "Ren'Py SDK $RENPY_VERSION: $SDK_CHECK"

# Step 5: Run minimal Ren'Py launch test
log_info "Running Ren'Py launch smoke test..."
log_info "This will launch the demo game and verify the bridge can connect"
log_info "Timeout: ${LAUNCH_TIMEOUT}s"

SMOKE_TEST_SCRIPT="${PROJECT_ROOT}/scripts/smoke_renpy_launch.py"

# Create inline smoke test if it doesn't exist
if [ ! -f "$SMOKE_TEST_SCRIPT" ]; then
    log_info "Creating inline smoke test..."
    SMOKE_TEST_SCRIPT=$(mktemp)
    trap "rm -f $SMOKE_TEST_SCRIPT" EXIT
    
    cat > "$SMOKE_TEST_SCRIPT" << 'PYTHON_EOF'
#!/usr/bin/env python3
"""Minimal smoke test for Ren'Py launch with bridge."""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from renforge.bridge.launcher import launch_with_bridge
from renforge.project import RenpyProject
from renforge.sdk import get_or_install_sdk

def smoke_test_launch(demo_path: Path, sdk_version: str, timeout: int) -> bool:
    """Launch demo game with bridge and verify basic connectivity."""
    print(f"[Smoke] Using demo project: {demo_path}")
    print(f"[Smoke] Ren'Py SDK version: {sdk_version}")
    
    sdk = get_or_install_sdk(sdk_version, project_root=demo_path)
    project = RenpyProject(demo_path)
    
    print(f"[Smoke] Launching game with bridge (timeout: {timeout}s)...")
    with launch_with_bridge(
        sdk,
        project,
        startup_timeout=timeout,
        editor=False,  # No editor for smoke test
    ) as session:
        print("[Smoke] Game launched successfully")
        
        # Basic connectivity check
        print("[Smoke] Testing bridge connectivity...")
        status = session.client.request("ping")
        if status.get("ok") is True:
            print("[Smoke] Bridge ping successful")
        else:
            print(f"[Smoke] Bridge ping failed: {status}")
            return False
        
        # Check that we can evaluate expressions
        print("[Smoke] Testing expression evaluation...")
        result = session.client.eval_expr("1 + 1")
        if result == 2:
            print("[Smoke] Expression evaluation successful")
        else:
            print(f"[Smoke] Expression evaluation unexpected result: {result}")
            return False
        
        # Give it a moment to stabilize
        time.sleep(1)
        
        print("[Smoke] All basic checks passed")
        return True

if __name__ == "__main__":
    import os
    demo = Path(__file__).resolve().parents[1] / "examples" / "demo_game"
    version = os.environ.get("RENPY_SDK_VERSION", "8.5.3")
    timeout = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    
    if not demo.exists():
        print(f"[Smoke] ERROR: Demo game not found at {demo}")
        sys.exit(1)
    
    success = smoke_test_launch(demo, version, timeout)
    sys.exit(0 if success else 1)
PYTHON_EOF
fi

# Run the smoke test
if python3 "$SMOKE_TEST_SCRIPT" "$LAUNCH_TIMEOUT"; then
    log_success "Ren'Py launch smoke test passed"
else
    log_error "Ren'Py launch smoke test failed"
    exit 1
fi

# Step 6: Summary
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
