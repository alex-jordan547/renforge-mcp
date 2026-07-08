"""Asset analysis: find orphaned and missing images/audio in a Ren'Py project.

Heuristic and deliberately conservative — Ren'Py's image resolution is dynamic
(``show eileen happy`` maps to a defined image or a file like
``images/eileen happy.png``), so this reports *likely* orphans/missing rather
than a proof. It reads the ``game/`` tree and the ``.rpy`` sources; the engine
stays the source of truth via ``lint``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".tga", ".bmp"}
AUDIO_EXTS = {".ogg", ".opus", ".mp3", ".wav", ".flac", ".m4a", ".aac", ".mp2", ".wma"}
VIDEO_EXTS = {".webm", ".mp4", ".ogv", ".avi", ".mkv", ".mov", ".mpg", ".mpeg", ".flv"}
ASSET_EXTS = IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS

_QUOTED_RE = re.compile(r"""["']([^"'\n]+?)["']""")
_IMAGE_DEF_RE = re.compile(r"^\s*image\s+([^\n=:]+?)\s*(?:=|:)")
_SCENE_SHOW_RE = re.compile(r"^\s*(?:scene|show)\s+(.+?)\s*(?:#.*)?$")
# Tokens that end the image-name part of a scene/show statement.
_SHOW_STOP = {"at", "with", "as", "behind", "onlayer", "zorder", "expression"}


def _game_dir(project_path: str | Path) -> Path:
    return Path(project_path).expanduser().resolve() / "game"


def _iter_rpy(game: Path):
    for root, _dirs, files in os.walk(game):
        for name in files:
            if name.endswith(".rpy"):
                yield Path(root) / name


def _image_name_from_show(rest: str) -> str:
    tokens = rest.split()
    keep: list[str] = []
    for tok in tokens:
        if tok in _SHOW_STOP:
            break
        keep.append(tok)
    return " ".join(keep)


def analyze_assets(project_path: str | Path) -> dict[str, Any]:
    game = _game_dir(project_path)
    result: dict[str, Any] = {
        "asset_files": [],
        "orphans": [],
        "missing_files": [],
        "undefined_images": [],
    }
    if not game.is_dir():
        result["error"] = f"no game/ directory under {project_path}"
        return result

    # 1. Asset files on disk (relative to game/, posix-style).
    disk_files: list[str] = []
    for root, _dirs, files in os.walk(game):
        for name in files:
            if Path(name).suffix.lower() in ASSET_EXTS:
                rel = (Path(root) / name).relative_to(game).as_posix()
                disk_files.append(rel)
    disk_files.sort()
    result["asset_files"] = disk_files

    # 2. References from the scripts.
    quoted: set[str] = set()
    defined_images: set[str] = set()
    shown_images: set[str] = set()
    for rpy in _iter_rpy(game):
        try:
            text = rpy.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for m in _QUOTED_RE.finditer(line):
                quoted.add(m.group(1))
            dm = _IMAGE_DEF_RE.match(line)
            if dm:
                defined_images.add(dm.group(1).strip())
            sm = _SCENE_SHOW_RE.match(line)
            if sm:
                name = _image_name_from_show(sm.group(1))
                if name:
                    shown_images.add(name)

    quoted_basenames = {Path(q).name for q in quoted}

    def _referenced(rel: str) -> bool:
        base = Path(rel).name
        stem = Path(rel).stem  # image name candidate, e.g. "eileen happy"
        if rel in quoted or base in quoted_basenames:
            return True
        if any(q.endswith(rel) or q.endswith(base) for q in quoted):
            return True
        # Image files referenced via `scene/show <name>` or `image <name>`.
        if Path(rel).suffix.lower() in IMAGE_EXTS:
            if stem in shown_images or stem in defined_images:
                return True
        return False

    result["orphans"] = [rel for rel in disk_files if not _referenced(rel)]

    # 3. Missing: quoted references that look like *developer* asset paths but
    #    aren't on disk. We skip substitution patterns ("[prefix_]...") and the
    #    gui/ tree, whose images the GUI framework generates or renders by
    #    default — flagging those would be noise, not actionable findings.
    disk_set = set(disk_files)
    disk_basenames = {Path(f).name for f in disk_files}
    for q in sorted(quoted):
        if "[" in q or "]" in q:
            continue
        if q.startswith("gui/"):
            continue
        if Path(q).suffix.lower() in ASSET_EXTS:
            if q not in disk_set and Path(q).name not in disk_basenames:
                result["missing_files"].append(q)

    # 4. Images shown but neither defined nor backed by a file (Ren'Py would use
    #    a placeholder). Reported separately as a soft signal.
    disk_stems = {Path(f).stem for f in disk_files if Path(f).suffix.lower() in IMAGE_EXTS}
    for name in sorted(shown_images):
        if name in defined_images:
            continue
        # A single-tag show may resolve to "<first-tag>.png"; check tag stems too.
        first = name.split()[0] if name.split() else name
        if name in disk_stems or first in disk_stems or first in defined_images:
            continue
        result["undefined_images"].append(name)

    result["summary"] = {
        "asset_count": len(disk_files),
        "orphan_count": len(result["orphans"]),
        "missing_count": len(result["missing_files"]),
        "undefined_image_count": len(result["undefined_images"]),
    }
    return result


__all__ = ["analyze_assets"]
