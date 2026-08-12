# RenForge Cloud / CI Environment Setup

This guide explains how to set up a reproducible development environment for
RenForge in cloud environments like **Cursor Cloud Agents** or CI systems like
GitHub Actions.

## Quick Start

For a fully automated setup, run:

```bash
bash scripts/setup_cloud_env.sh
```

This script will:

1. Install system dependencies (Xvfb, X11 utilities)
2. Configure virtual display for headless Ren'Py testing
3. Install Python dependencies
4. Download and cache Ren'Py SDK 8.5.3
5. Verify the installation

After setup completes, verify everything works:

```bash
bash scripts/smoke_renpy_env.sh
```

## Cursor Cloud Agents Environment Configuration

For Cursor Cloud Agents, you can create a reusable environment that includes all
necessary dependencies and setup.

### Option 1: Using `environment.json` (Recommended)

Create a `.cursor/environment.json` file in your repository:

```json
{
  "name": "renforge",
  "install": "bash scripts/setup_cloud_env.sh",
  "start": "echo 'RenForge environment ready. Run: bash scripts/smoke_renpy_env.sh'"
}
```

Cursor Cloud will automatically run the `install` script when creating the
environment. The `start` script runs each time an agent boots in that
environment.

### Option 2: Saved Environment (Dashboard)

If you prefer to configure via the Cursor Dashboard:

1. Go to **Cloud Agents** → **Environments** → **New Environment**
2. Name it `renforge` (or any name you prefer)
3. Set the **Primary Repository** to `https://github.com/alex-jordan547/renforge-mcp`
4. Configure the **Install Script**:

   ```bash
   bash scripts/setup_cloud_env.sh
   ```

5. Configure the **Start Script** (optional):

   ```bash
   # Export environment variables for convenience
   export DISPLAY=:99
   export PYTHONPATH=/workspace/src
   
   echo "RenForge environment ready"
   echo "Run smoke test: bash scripts/smoke_renpy_env.sh"
   ```

6. Save the environment

Now when you create a Cloud Agent, select the `renforge` environment and it will
automatically have everything set up.

## Manual Setup Steps

If you need to set up manually or understand what the automation does:

### 1. System Dependencies

Install Xvfb for headless X11 display:

**Ubuntu/Debian:**

```bash
sudo apt-get update
sudo apt-get install -y xvfb x11-utils
```

**RHEL/CentOS/Fedora:**

```bash
sudo yum install -y xorg-x11-server-Xvfb xorg-x11-utils
```

### 2. Configure Virtual Display

Start Xvfb with a suitable resolution:

```bash
# Start Xvfb on display :99
Xvfb :99 -screen 0 1920x1080x24 &

# Wait for it to be ready
for i in $(seq 1 30); do
  xdpyinfo -display :99 >/dev/null 2>&1 && break
  sleep 0.5
done

# Export DISPLAY for all subsequent commands
export DISPLAY=:99
```

**Important:** The virtual screen must be large enough (1920x1080 recommended).
Smaller screens cause Ren'Py to shrink its window, breaking coordinate-based
tests.

### 3. Install Python Dependencies

Using `uv` (preferred):

```bash
uv sync --all-extras
```

Or using `pip`:

```bash
pip install -e ".[fastmcp,ui,test]"
```

### 4. Install Ren'Py SDK

The SDK is installed automatically on first use, but you can pre-install it:

```python
export PYTHONPATH=/workspace/src
python3 -c "
from renforge.sdk import get_or_install_sdk
sdk = get_or_install_sdk('8.5.3')
print(f'SDK installed at: {sdk}')
"
```

The SDK is cached at `~/.cache/renforge/sdks/8.5.3` by default. You can override
this with the `RENPY_SDK_CACHE_DIR` environment variable.

### 5. Build Frontend (Optional)

If you need the web dashboard UI:

```bash
cd ui
npm ci
npm run build
```

The generated assets go to `src/renforge/ui/static/` and are not committed to
git.

## Environment Variables

Key environment variables for cloud environments:

| Variable | Purpose | Default |
|----------|---------|---------|
| `DISPLAY` | X11 display for Ren'Py | Must be set (e.g., `:99`) |
| `PYTHONPATH` | Python module search path | Should include `src/` |
| `RENPY_SDK_CACHE_DIR` | Ren'Py SDK cache location | `~/.cache/renforge/sdks` |
| `RENPY_SDK_VERSION` | Override Ren'Py version | `8.5.3` |
| `RENFORGE_*_LIVE` | Enable specific live test suites | Unset (opt-in per suite) |

## Running Tests

After environment setup:

### All Unit Tests

```bash
pytest
```

### Live Editor Tests

Live editor tests require a full Ren'Py launch and are opt-in via environment
variables:

```bash
# Run all live editor test suites (takes ~6 minutes)
bash scripts/run_live_editor_suites.sh
```

Each suite has its own gate variable (e.g., `RENFORGE_TASK0_LIVE=1`) and the
runner script handles all of them, including retry logic for flaky tests.

### Single Live Suite

To run just one live suite:

```bash
RENFORGE_TASK0_LIVE=1 pytest tests/test_editor_task0_live.py
```

## Troubleshooting

### `DISPLAY` not set

**Error:** `DISPLAY environment variable not set`

**Solution:** Start Xvfb and export DISPLAY:

```bash
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99
```

### Display not accessible

**Error:** `Display :99 is not accessible`

**Solution:** Check if Xvfb is running:

```bash
ps aux | grep Xvfb
xdpyinfo -display :99
```

If not running, start it as shown above.

### Wrong screen dimensions

**Symptom:** Tests fail with coordinate mismatches

**Cause:** Xvfb started with too small a screen (e.g., default 1280x1024 or
smaller)

**Solution:** Kill Xvfb and restart with larger screen:

```bash
pkill Xvfb
Xvfb :99 -screen 0 1920x1080x24 &
```

### SDK download timeout

**Symptom:** Ren'Py SDK download hangs or times out

**Solution:** Check network connectivity. The SDK is ~500MB and downloads from
`https://www.renpy.org/dl/`. You can pre-download and cache it:

```bash
export RENPY_SDK_CACHE_DIR=/path/to/persistent/cache
python3 -c "from renforge.sdk import get_or_install_sdk; get_or_install_sdk('8.5.3')"
```

### Module import errors

**Error:** `ModuleNotFoundError: No module named 'renforge'`

**Solution:** Ensure PYTHONPATH includes the `src/` directory:

```bash
export PYTHONPATH=/workspace/src
```

Or install the package:

```bash
pip install -e .
```

## CI Integration Examples

### GitHub Actions

See `.github/workflows/live-editor.yml` for a complete working example. Key
points:

- Cache the Ren'Py SDK with `actions/cache@v4`
- Start Xvfb early with a large screen size
- Build the frontend before running tests
- Use `uv` for faster dependency installation

### GitLab CI

```yaml
test:live-editor:
  image: python:3.12
  before_script:
    - apt-get update && apt-get install -y xvfb x11-utils
    - Xvfb :99 -screen 0 1920x1080x24 &
    - export DISPLAY=:99
    - pip install uv
    - uv sync --all-extras
  script:
    - bash scripts/run_live_editor_suites.sh
  cache:
    paths:
      - .renpy-sdk-cache/
```

## SDK Caching Strategy

The Ren'Py SDK is ~500MB. For efficient cloud environments:

1. **Use persistent cache volumes** if your platform supports them
2. **Set `RENPY_SDK_CACHE_DIR`** to a persistent location:
   ```bash
   export RENPY_SDK_CACHE_DIR=/mnt/cache/renforge/sdks
   ```
3. **GitHub Actions:** Use `actions/cache@v4` (see `.github/workflows/live-editor.yml`)
4. **Cursor Cloud:** The environment's filesystem persists across agent runs in
   the same environment

## Architecture Notes

- The setup script is **idempotent** — safe to run multiple times
- Xvfb runs in the background and doesn't block
- The SDK installer uses inter-process locks for concurrent safety
- All live tests use a temporary copy of `examples/demo_game` to avoid
  cross-test contamination

## Related Documentation

- [CONTRIBUTING.md](../CONTRIBUTING.md) — Development setup for local machines
- [LIVE_EDITOR.md](LIVE_EDITOR.md) — Live Editor human and agent workflows
- [.github/workflows/live-editor.yml](../.github/workflows/live-editor.yml) —
  Working CI example
- [scripts/run_live_editor_suites.sh](../scripts/run_live_editor_suites.sh) —
  Live test runner with retry logic
