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

import hashlib
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
BRIDGE_DIR = REPO_ROOT / "src" / "renforge" / "bridge"

from renforge.bridge.launcher import _editor_payload, launch_with_bridge  # noqa: E402
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
        print(f"watching     {len(_watched())} source files → {injected.name}", flush=True)
        print("stop         Ctrl-C", flush=True)
        _watch_and_reload(session, injected)
    except KeyboardInterrupt:
        print("\nclosing…", flush=True)
    finally:
        session.close(timeout=10.0)
    return 0


def _watched() -> list[Path]:
    """Every file that ends up in the injected artifact."""
    return [BRIDGE_DIR / "editor.rpy", *sorted((BRIDGE_DIR / "screens").glob("*.rpy"))]


def _sources_stamp() -> tuple[int, ...]:
    """Change signature across all sources, so any panel file triggers a reload."""
    stamps = []
    for path in _watched():
        try:
            stamps.append(path.stat().st_mtime_ns)
        except FileNotFoundError:
            stamps.append(0)
    return tuple(stamps)


def _manifest_path(project_root: Path) -> Path:
    return project_root / ".renforge" / "editor-session.json"


def _injected_editor_path(project_root: Path) -> Path:
    """Resolve the injected copy from the session manifest the launcher wrote."""
    manifest = json.loads(_manifest_path(project_root).read_text(encoding="utf-8"))
    return project_root / "game" / manifest["basename"]


def _restamp_manifest(project_root: Path, payload: bytes) -> None:
    """Record the bytes we just wrote as the ones we own.

    Cleanup refuses to delete an injected file whose digest no longer matches
    the manifest — that guard is what keeps RenForge from touching a file a user
    edited. Hot reload rewrites that file on purpose, so it has to re-establish
    ownership rather than quietly break it.
    """
    path = _manifest_path(project_root)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["source_sha256"] = hashlib.sha256(payload).hexdigest()
    path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")


def _watch_and_reload(session: object, injected: Path) -> None:
    """Re-copy the editor source into the game on every edit, then hot-reload.

    The game reads its own injected copy, so reloading alone would replay the
    bytes that were current at launch. Rebuild first, reload second — and watch
    every panel file, not just editor.rpy, since the artifact is their sum.
    """
    last_seen = _sources_stamp()
    while True:
        time.sleep(0.5)
        current = _sources_stamp()
        if current == last_seen:
            continue
        last_seen = current
        payload = _editor_payload()
        injected.write_bytes(payload)
        _restamp_manifest(injected.parents[1], payload)
        try:
            session.client.control("reload_script")  # type: ignore[attr-defined]
            print(f"reloaded     {time.strftime('%H:%M:%S')}", flush=True)
        except Exception as exc:  # the game may be mid-restart; the next edit retries
            print(f"reload failed {type(exc).__name__}: {exc}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
