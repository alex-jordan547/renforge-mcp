#!/usr/bin/env python
"""Launch a game with the live editor injected, from the working tree.

The installed MCP server ships its own build, so it cannot show edits made to
``src/`` until it is reinstalled. This script imports the working tree directly,
which is what makes the editor UI work iterable: edit ``bridge/editor.rpy``,
call ``renforge_control(action="reload_script")``, see it.

    python scripts/dev_editor.py [project_path]

Runs until interrupted, then closes the session and removes every injected
artifact.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
EDITOR_SOURCE = REPO_ROOT / "src" / "renforge" / "bridge" / "editor.rpy"

from renforge.bridge.launcher import launch_with_bridge  # noqa: E402
from renforge.project import RenpyProject  # noqa: E402
from renforge.sdk import get_or_install_sdk  # noqa: E402

DEFAULT_PROJECT = REPO_ROOT / "examples" / "demo_game"


def main() -> int:
    project_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_PROJECT
    project = RenpyProject(project_path)
    sdk = get_or_install_sdk("stable", project_root=project.abs_root)

    session = launch_with_bridge(
        sdk,
        project,
        editor=True,
        savedir="temporary",
        display="auto",
        audio="auto",
    )
    try:
        injected = _injected_editor_path(project.root)
        print(f"project      {project.root}", flush=True)
        print(f"pid          {session.process.pid}", flush=True)
        print(f"editor       {'injected' if session.editor else 'MISSING'}"
              " — click RF, top right, to activate", flush=True)
        print(f"watching     {EDITOR_SOURCE.relative_to(REPO_ROOT)} → {injected.name}", flush=True)
        print("stop         Ctrl-C", flush=True)
        _watch_and_reload(session, injected)
    except KeyboardInterrupt:
        print("\nclosing…", flush=True)
    finally:
        session.close(timeout=10.0)
    return 0


def _injected_editor_path(project_root: Path) -> Path:
    """Resolve the injected copy from the session manifest the launcher wrote."""
    manifest = json.loads(
        (project_root / ".renforge" / "editor-session.json").read_text(encoding="utf-8")
    )
    return project_root / "game" / manifest["basename"]


def _watch_and_reload(session: object, injected: Path) -> None:
    """Re-copy the editor source into the game on every edit, then hot-reload.

    The game reads its own injected copy, so reloading alone would replay the
    bytes that were current at launch. Copy first, reload second.
    """
    last_seen = EDITOR_SOURCE.stat().st_mtime_ns
    while True:
        time.sleep(0.5)
        try:
            current = EDITOR_SOURCE.stat().st_mtime_ns
        except FileNotFoundError:
            continue
        if current == last_seen:
            continue
        last_seen = current
        shutil.copyfile(EDITOR_SOURCE, injected)
        try:
            session.client.control("reload_script")  # type: ignore[attr-defined]
            print(f"reloaded     {time.strftime('%H:%M:%S')}", flush=True)
        except Exception as exc:  # the game may be mid-restart; the next edit retries
            print(f"reload failed {type(exc).__name__}: {exc}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
