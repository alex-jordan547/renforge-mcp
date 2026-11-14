"""Validate a project through a clean, temporary shadow copy."""

from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..editor.exceptions import EditorError
from ..editor.shadow import ShadowLintResult, validate_shadow_project
from ..project import RenpyProject
from ..sdk import get_or_install_sdk

_SUPPORTED_CHECKS = ("lint",)
_ARTIFACT_PREFIXES = ("zzrenforge_", "00renforge_session_")
DEFAULT_ATTESTATION_TIMEOUT_SECONDS = 30.0


def _normalize_checks(checks: list[str] | None) -> list[str]:
    if checks is None:
        return ["lint"]
    if not isinstance(checks, list):
        raise EditorError(
            "VALIDATE_CHECKS_INVALID",
            "checks must be a list of check names",
            {"checks": str(checks)},
        )
    normalized: list[str] = []
    for check in checks:
        if not isinstance(check, str):
            raise EditorError(
                "VALIDATE_CHECKS_INVALID",
                "checks must be a list of check names",
                {"checks": str(checks)},
            )
        value = check.strip().lower()
        if value not in _SUPPORTED_CHECKS:
            raise EditorError(
                "VALIDATE_CHECKS_UNSUPPORTED",
                "only the 'lint' check is supported",
                {"checks": checks, "supported": list(_SUPPORTED_CHECKS)},
            )
        if value not in normalized:
            normalized.append(value)
    if normalized != ["lint"]:
        raise EditorError(
            "VALIDATE_CHECKS_UNSUPPORTED",
            "checks must be omitted or contain only 'lint'",
            {"checks": checks, "supported": list(_SUPPORTED_CHECKS)},
        )
    return normalized


def _encode_staged(staged: dict[str, str] | None) -> dict[str, bytes]:
    if staged is None:
        return {}
    if not isinstance(staged, dict):
        raise EditorError(
            "VALIDATE_STAGED_INVALID",
            "staged must be an object mapping paths to text",
            {"staged_type": type(staged).__name__},
        )
    encoded: dict[str, bytes] = {}
    for path, content in staged.items():
        if not isinstance(path, str) or not isinstance(content, str):
            raise EditorError(
                "VALIDATE_STAGED_INVALID",
                "staged paths and contents must be strings",
                {"path": path if isinstance(path, str) else str(path)},
            )
        encoded[path] = content.encode("utf-8")
    return encoded


def _resolve_timeout(timeout: float) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise EditorError(
            "VALIDATE_TIMEOUT_INVALID",
            "timeout must be a number",
            {"timeout": str(timeout)},
        ) from exc
    if not math.isfinite(value):
        raise EditorError(
            "VALIDATE_TIMEOUT_INVALID",
            "timeout must be finite",
            {"timeout": str(timeout)},
        )
    if value == 0:
        return min(180.0, max(1.0, DEFAULT_ATTESTATION_TIMEOUT_SECONDS * 3))
    return min(600.0, max(1.0, value))


def _is_injected_artifact_name(name: Any) -> bool:
    """Only names carrying a known injected prefix may be excluded."""
    return isinstance(name, str) and name.startswith(_ARTIFACT_PREFIXES)


def _clean_tree_exclusions(project_root: Path) -> tuple[set[str], tuple[str, ...]]:
    """Return injected artifact names to omit from a clean shadow tree."""
    names: set[str] = set()
    manifest: Any = None
    try:
        from ..bridge.artifacts import load_validated_manifest

        manifest = load_validated_manifest(project_root)
    except Exception:
        # Corrupt or unavailable ownership metadata must not block lint. The
        # known prefixes below still keep the shadow free of injected files.
        manifest = None
    if isinstance(manifest, dict):
        sources = manifest.get("sources")
        if isinstance(sources, list):
            for entry in sources:
                if not isinstance(entry, dict):
                    continue
                basename = entry.get("basename")
                if _is_injected_artifact_name(basename):
                    names.add(basename)
                siblings = entry.get("generated_siblings")
                if isinstance(siblings, list):
                    names.update(item for item in siblings if _is_injected_artifact_name(item))
        asset_tree = manifest.get("asset_tree")
        if isinstance(asset_tree, dict):
            dirname = asset_tree.get("dirname")
            if _is_injected_artifact_name(dirname):
                names.add(dirname)
    return names, _ARTIFACT_PREFIXES


def _result_payload(
    result: ShadowLintResult,
    *,
    checks: list[str],
    keep_shadow: bool,
    sdk_version: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": result.ok,
        "checks": checks,
        "sdk_version": sdk_version,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "truncated": result.truncated,
        "complete": result.complete,
        "degraded": result.degraded,
        "files": result.files,
        "bytes": result.bytes,
        "placeholders": result.placeholders,
        "limits": dict(result.limits),
        "largest": dict(result.largest) if result.largest is not None else None,
        "skipped_links": [dict(item) for item in result.skipped_links],
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if keep_shadow:
        payload["shadow_path"] = result.shadow_path
    return payload


def validate_project(
    project_path: str,
    *,
    version: str = "stable",
    checks: list[str] | None = None,
    staged: dict[str, str] | None = None,
    keep_shadow: bool = False,
    timeout: float = 0,
) -> dict[str, Any]:
    """Lint a clean shadow copy of a project without touching its sources."""
    normalized_checks: list[str] = checks if isinstance(checks, list) else []
    temp_parent: Path | None = None
    shadow_root: Path | None = None
    try:
        normalized_checks = _normalize_checks(checks)
        staged_bytes = _encode_staged(staged)
        resolved_timeout = _resolve_timeout(timeout)
        try:
            project = RenpyProject(Path(project_path), create_cache=False)
        except (OSError, ValueError) as exc:
            return {
                "ok": False,
                "checks": normalized_checks,
                "error": str(exc),
                "code": "PROJECT_INVALID",
                "details": {"project_path": str(project_path)},
            }
        # Never discover a launcher inside the project being validated: a
        # project-local "SDK" would be project-controlled executable code.
        sdk = get_or_install_sdk(version or "stable")
        sdk_version = str(getattr(sdk, "version", version or "stable"))
        excluded_names, excluded_prefixes = _clean_tree_exclusions(project.root)
        temp_parent = Path(tempfile.mkdtemp(prefix="renforge-validate-"))
        if keep_shadow:
            # The caller cleans up the path we return, so the shadow must be
            # the directory mkdtemp created, not a child of it: deleting a
            # child would leave an empty parent behind on every call.
            temp_parent.rmdir()
            shadow_root = temp_parent
        else:
            shadow_root = temp_parent / "shadow"
        result = validate_shadow_project(
            project,
            sdk,
            shadow_root=shadow_root,
            staged_replacements=staged_bytes,
            timeout=resolved_timeout,
            excluded_names=excluded_names,
            excluded_prefixes=excluded_prefixes,
            keep_shadow=keep_shadow,
        )
        return _result_payload(
            result,
            checks=normalized_checks,
            keep_shadow=keep_shadow,
            sdk_version=sdk_version,
        )
    except EditorError as exc:
        payload: dict[str, Any] = {
            "ok": False,
            "checks": normalized_checks,
            "error": exc.message,
            "code": exc.code,
            "details": dict(exc.details),
        }
        shadow_path = shadow_root if shadow_root is not None else None
        if keep_shadow and shadow_path is not None and shadow_path.is_dir():
            payload["shadow_path"] = str(shadow_path)
        elif temp_parent is not None:
            shutil.rmtree(temp_parent, ignore_errors=True)
        return payload
    except BaseException:
        if temp_parent is not None:
            shutil.rmtree(temp_parent, ignore_errors=True)
        raise
    finally:
        if temp_parent is not None and not keep_shadow:
            shutil.rmtree(temp_parent, ignore_errors=True)


__all__ = ["validate_project"]
