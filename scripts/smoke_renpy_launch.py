#!/usr/bin/env python3
"""Minimal smoke test: boot a known Ren'Py project with the live bridge, then exit.

This is the committed launcher used by ``scripts/smoke_renpy_env.sh``. Keep the
demo path rooted at this file so a temp copy under ``/tmp`` cannot silently
point at the wrong tree.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def demo_project_path() -> Path:
    return _REPO_ROOT / "examples" / "demo_game"


def smoke_test_launch(demo_path: Path, sdk_version: str, timeout: int) -> bool:
    from renforge.bridge.launcher import launch_with_bridge
    from renforge.project import RenpyProject
    from renforge.sdk import get_or_install_sdk

    print(f"[Smoke] Using demo project: {demo_path}")
    print(f"[Smoke] Ren'Py SDK version: {sdk_version}")

    sdk = get_or_install_sdk(sdk_version, project_root=demo_path)
    project = RenpyProject(demo_path)

    print(f"[Smoke] Launching game with bridge (timeout: {timeout}s)...")
    with launch_with_bridge(
        sdk,
        project,
        startup_timeout=timeout,
        editor=False,
    ) as session:
        print("[Smoke] Game launched successfully")

        print("[Smoke] Testing bridge connectivity...")
        status = session.client.request("ping")
        if status.get("ok") is not True:
            print(f"[Smoke] Bridge ping failed: {status}")
            return False
        print("[Smoke] Bridge ping successful")

        print("[Smoke] Testing expression evaluation...")
        result = session.client.eval_expr("1 + 1")
        if result != 2:
            print(f"[Smoke] Expression evaluation unexpected result: {result}")
            return False
        print("[Smoke] Expression evaluation successful")

        time.sleep(1)
        print("[Smoke] All basic checks passed")
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Boot demo_game through the RenForge bridge.")
    parser.add_argument(
        "timeout",
        nargs="?",
        type=int,
        default=90,
        help="Bridge startup timeout in seconds (default: 90)",
    )
    parser.add_argument(
        "--demo",
        type=Path,
        default=None,
        help="Override the demo project path (default: examples/demo_game)",
    )
    args = parser.parse_args(argv)

    demo = (args.demo or demo_project_path()).resolve()
    version = os.environ.get("RENPY_SDK_VERSION", "8.5.3")

    if not demo.exists():
        print(f"[Smoke] ERROR: Demo game not found at {demo}", file=sys.stderr)
        return 1

    return 0 if smoke_test_launch(demo, version, args.timeout) else 1


if __name__ == "__main__":
    sys.exit(main())
