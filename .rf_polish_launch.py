"""Leave demo_game running with the editor injected for interactive polish."""
from __future__ import annotations

import shutil
import signal
import sys
import time
from pathlib import Path

from renforge.bridge.launcher import launch_with_bridge
from renforge.editor_live_common import DEMO_COPY_IGNORE
from renforge.project import RenpyProject
from renforge.sdk import get_or_install_sdk

ROOT = Path(__file__).resolve().parent
DEMO = ROOT / "examples" / "demo_game"
WORK = ROOT / ".rf_verify_work" / "polish_demo"
PID_FILE = ROOT / ".rf_polish.pid"

def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DEMO, WORK, ignore=DEMO_COPY_IGNORE)
    shutil.rmtree(WORK / ".renforge", ignore_errors=True)

    sdk = get_or_install_sdk("8.5.3", project_root=WORK)
    project = RenpyProject(WORK)
    session = launch_with_bridge(sdk, project, startup_timeout=120, editor=True)
    PID_FILE.write_text(str(session.process.pid if hasattr(session, "process") else ""), encoding="utf-8")
    print(f"READY work={WORK}", flush=True)
    print(f"session={session}", flush=True)
    # Activate editor so polish starts on the live chrome, not the RF launcher.
    client = session.client
    for _ in range(80):
        if client.inspect_screen("_renforge_editor_launcher").get("active"):
            break
        time.sleep(0.25)
    try:
        client.click_element(text="RF", exact=True, screen="_renforge_editor_launcher")
    except Exception as exc:
        print(f"WARN activate click failed: {exc}", flush=True)
    for _ in range(80):
        if client.inspect_screen("_renforge_editor_overlay").get("active"):
            print("EDITOR_ACTIVE", flush=True)
            break
        time.sleep(0.05)
    else:
        print("EDITOR_NOT_ACTIVE (launcher still available)", flush=True)

    stop = False

    def _stop(*_args):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print("HOLDING session open — send SIGTERM/SIGINT to stop", flush=True)
    try:
        while not stop:
            if session.process is not None and session.process.poll() is not None:
                print(f"GAME_EXITED code={session.process.returncode}", flush=True)
                break
            time.sleep(0.5)
    finally:
        try:
            session.close()
        except Exception as exc:
            print(f"close error: {exc}", flush=True)
        PID_FILE.unlink(missing_ok=True)
        print("STOPPED", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
