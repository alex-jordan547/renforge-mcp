"""SDK-backed project operations exposed as MCP tools: assets, translation,
build and documentation. Each returns a JSON-friendly dict and reports failures
as ``{"ok": False, "error": ...}`` rather than raising.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import build as _build
from .. import docs as _docs
from .. import translation as _tr
from ..assets import analyze_assets
from ..project import RenpyProject
from ..sdk import RenpySdk, get_or_install_sdk


def _project(project_path: str) -> tuple[RenpyProject | None, dict | None]:
    try:
        return RenpyProject(Path(project_path)), None
    except Exception as exc:
        return None, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _sdk_project(project_path: str, version: str) -> tuple[RenpySdk | None, RenpyProject | None, dict | None]:
    project, err = _project(project_path)
    if err:
        return None, None, err
    try:
        return get_or_install_sdk(version, project_root=project.abs_root), project, None
    except Exception as exc:
        return None, None, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _sdk(version: str) -> tuple[RenpySdk | None, dict | None]:
    try:
        return get_or_install_sdk(version), None
    except Exception as exc:
        return None, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# --- assets ------------------------------------------------------------------

def assets(project_path: str) -> dict[str, Any]:
    return analyze_assets(project_path)


# --- translation -------------------------------------------------------------

def languages(project_path: str) -> dict[str, Any]:
    return {"ok": True, "languages": _tr.list_languages(project_path)}


def translation_stats(project_path: str, language: str, version: str = "stable") -> dict[str, Any]:
    sdk, project, err = _sdk_project(project_path, version)
    if err:
        return err
    return _tr.translation_stats(sdk, project, language)


def generate_translations(project_path: str, language: str, version: str = "stable") -> dict[str, Any]:
    sdk, project, err = _sdk_project(project_path, version)
    if err:
        return err
    return _tr.generate_translations(sdk, project, language)


def export_dialogue(project_path: str, language: str = "None", version: str = "stable") -> dict[str, Any]:
    sdk, project, err = _sdk_project(project_path, version)
    if err:
        return err
    return _tr.export_dialogue(sdk, project, language)


# --- build -------------------------------------------------------------------

def web_build(project_path: str, destination: str = "", version: str = "stable") -> dict[str, Any]:
    sdk, project, err = _sdk_project(project_path, version)
    if err:
        return err
    return _build.web_build(sdk, project, destination=destination or None)


def distribute(project_path: str, package: str = "", destination: str = "", version: str = "stable") -> dict[str, Any]:
    sdk, project, err = _sdk_project(project_path, version)
    if err:
        return err
    packages = [package] if package else None
    return _build.distribute(sdk, project, packages=packages, destination=destination or None)


# --- documentation -----------------------------------------------------------

def search_docs(query: str, version: str = "stable") -> dict[str, Any]:
    sdk, err = _sdk(version)
    if err:
        return err
    return _docs.search_docs(sdk, query)


def get_doc(topic: str, version: str = "stable") -> dict[str, Any]:
    sdk, err = _sdk(version)
    if err:
        return err
    return _docs.get_doc(sdk, topic)


def list_docs(version: str = "stable") -> dict[str, Any]:
    sdk, err = _sdk(version)
    if err:
        return err
    return {"ok": True, "topics": _docs.list_docs(sdk)}


__all__ = [
    "assets",
    "languages",
    "translation_stats",
    "generate_translations",
    "export_dialogue",
    "web_build",
    "distribute",
    "search_docs",
    "get_doc",
    "list_docs",
]
